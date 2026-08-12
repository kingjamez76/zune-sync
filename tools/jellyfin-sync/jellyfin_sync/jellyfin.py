"""Minimal Jellyfin API client — just enough to enumerate and fetch music."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import requests

log = logging.getLogger(__name__)

ITEM_FIELDS = ",".join(
    [
        "Path",
        "MediaSources",
        "ProviderIds",
        "Genres",
        "ProductionYear",
        "IndexNumber",
        "ParentIndexNumber",
        "AlbumArtist",
        "Album",
        "AlbumId",
        "Artists",
        "PremiereDate",
    ]
)

PAGE_SIZE = 200


class JellyfinError(Exception):
    pass


@dataclass
class Track:
    """A Jellyfin audio item, normalised to the fields we care about."""

    id: str
    title: str
    album: str
    album_artist: str
    artists: list[str]
    album_id: str | None
    track_number: int | None
    disc_number: int | None
    year: int | None
    genres: list[str]
    container: str
    codec: str
    size: int | None
    mb_ids: dict[str, str] = field(default_factory=dict)
    # Playlists this track was pulled in by; recorded so device-side playlists
    # can be built later without a second crawl.
    playlists: set[str] = field(default_factory=set)

    @property
    def artist(self) -> str:
        return self.artists[0] if self.artists else self.album_artist

    def describe(self) -> str:
        who = self.album_artist or self.artist or "Unknown Artist"
        return f"{who} - {self.title}"


def _parse_track(item: dict) -> Track:
    sources = item.get("MediaSources") or []
    source = sources[0] if sources else {}
    container = (source.get("Container") or "").lower()

    codec = ""
    for stream in source.get("MediaStreams") or []:
        if stream.get("Type") == "Audio":
            codec = (stream.get("Codec") or "").lower()
            break

    providers = item.get("ProviderIds") or {}
    mb_ids = {
        key: value
        for key, value in providers.items()
        if key.lower().startswith("musicbrainz") and value
    }

    return Track(
        id=item["Id"],
        title=item.get("Name") or "",
        album=item.get("Album") or "",
        album_artist=item.get("AlbumArtist") or "",
        artists=list(item.get("Artists") or []),
        album_id=item.get("AlbumId"),
        track_number=item.get("IndexNumber"),
        disc_number=item.get("ParentIndexNumber"),
        year=item.get("ProductionYear"),
        genres=list(item.get("Genres") or []),
        container=container,
        codec=codec,
        size=source.get("Size"),
    )


class Jellyfin:
    def __init__(self, url: str, api_key: str, timeout: int = 60):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Emby-Token": api_key,
                "Accept": "application/json",
            }
        )
        self._user_id: str | None = None

    # -- plumbing ---------------------------------------------------------

    def _get(self, path: str, **params) -> dict:
        resp = self.session.get(f"{self.url}{path}", params=params, timeout=self.timeout)
        if resp.status_code == 401:
            raise JellyfinError("Jellyfin rejected the API key (401)")
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()

    def _paged_items(self, path: str, **params) -> list[dict]:
        """Walk StartIndex/Limit pagination until the server stops returning items."""
        out: list[dict] = []
        start = 0
        while True:
            page = self._get(path, StartIndex=start, Limit=PAGE_SIZE, **params)
            items = page.get("Items", [])
            out.extend(items)
            total = page.get("TotalRecordCount")
            start += len(items)
            if not items or (total is not None and start >= total):
                break
        return out

    # -- identity ---------------------------------------------------------

    def resolve_user(self, username: str) -> str:
        """Map a username to its id. Falls back to the sole user if unambiguous."""
        if self._user_id:
            return self._user_id

        users = self._get("/Users")
        if isinstance(users, dict):  # some versions wrap it
            users = users.get("Items", [])
        if not users:
            raise JellyfinError("no users visible to this API key")

        if username:
            for user in users:
                if user.get("Name", "").lower() == username.lower():
                    self._user_id = user["Id"]
                    return self._user_id
            known = ", ".join(u.get("Name", "?") for u in users)
            raise JellyfinError(f"no Jellyfin user named {username!r} (found: {known})")

        if len(users) == 1:
            self._user_id = users[0]["Id"]
            log.info("using Jellyfin user %r", users[0].get("Name"))
            return self._user_id

        known = ", ".join(u.get("Name", "?") for u in users)
        raise JellyfinError(f"set jellyfin.user — this server has several users ({known})")

    # -- enumeration ------------------------------------------------------

    def playlist_tracks(self, user_id: str, name: str) -> list[Track]:
        playlists = self._paged_items(
            "/Items",
            userId=user_id,
            Recursive="true",
            IncludeItemTypes="Playlist",
        )
        match = next((p for p in playlists if p.get("Name", "").lower() == name.lower()), None)
        if match is None:
            known = ", ".join(sorted(p.get("Name", "?") for p in playlists)) or "none"
            raise JellyfinError(f"no playlist named {name!r} (found: {known})")

        items = self._paged_items(
            f"/Playlists/{match['Id']}/Items", userId=user_id, Fields=ITEM_FIELDS
        )
        tracks = [_parse_track(i) for i in items if i.get("Type") == "Audio"]
        for track in tracks:
            track.playlists.add(match.get("Name", name))
        return tracks

    def favorite_tracks(self, user_id: str) -> list[Track]:
        items = self._paged_items(
            "/Items",
            userId=user_id,
            Recursive="true",
            IncludeItemTypes="Audio",
            Filters="IsFavorite",
            Fields=ITEM_FIELDS,
        )
        return [_parse_track(i) for i in items]

    def artist_tracks(self, user_id: str, name: str) -> list[Track]:
        artists = self._get("/Artists", userId=user_id, searchTerm=name).get("Items", [])
        match = next((a for a in artists if a.get("Name", "").lower() == name.lower()), None)
        if match is None:
            match = artists[0] if artists else None
        if match is None:
            raise JellyfinError(f"no artist matching {name!r}")

        items = self._paged_items(
            "/Items",
            userId=user_id,
            Recursive="true",
            IncludeItemTypes="Audio",
            ArtistIds=match["Id"],
            Fields=ITEM_FIELDS,
        )
        return [_parse_track(i) for i in items]

    def album_tracks(self, user_id: str, name: str) -> list[Track]:
        albums = self._paged_items(
            "/Items",
            userId=user_id,
            Recursive="true",
            IncludeItemTypes="MusicAlbum",
            searchTerm=name,
        )
        match = next((a for a in albums if a.get("Name", "").lower() == name.lower()), None)
        if match is None:
            match = albums[0] if albums else None
        if match is None:
            raise JellyfinError(f"no album matching {name!r}")

        items = self._paged_items(
            "/Items",
            userId=user_id,
            Recursive="true",
            IncludeItemTypes="Audio",
            ParentId=match["Id"],
            Fields=ITEM_FIELDS,
        )
        return [_parse_track(i) for i in items]

    # -- fetching ---------------------------------------------------------

    def download(self, track: Track, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{self.url}/Items/{track.id}/Download"
        with self.session.get(url, stream=True, timeout=self.timeout) as resp:
            resp.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
            tmp.replace(dest)
        return dest

    def album_art(self, track: Track, max_width: int = 600) -> bytes | None:
        """Primary image for the track's album, as JPEG/PNG bytes."""
        item_id = track.album_id or track.id
        url = f"{self.url}/Items/{item_id}/Images/Primary"
        try:
            resp = self.session.get(
                url, params={"maxWidth": max_width}, timeout=self.timeout
            )
        except requests.RequestException as exc:
            log.debug("album art fetch failed for %s: %s", track.describe(), exc)
            return None
        if resp.status_code != 200 or not resp.content:
            return None
        return resp.content
