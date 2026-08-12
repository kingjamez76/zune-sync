"""MusicBrainz resolution and tag writing.

Matching order, best first:
  1. MusicBrainz IDs Jellyfin already stores for the item
  2. AcoustID audio fingerprint (needs an API key; fpcalc must be installed)
  3. MusicBrainz text search on artist/title/album
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import musicbrainzngs
from mutagen.asf import ASF
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

from .jellyfin import Track

log = logging.getLogger(__name__)

USER_AGENT = ("zune-sync", "0.1", "https://github.com/kingjamez76/zune-sync")


@dataclass
class TagSet:
    """The tags actually written to the file, and thus what the Zune will show."""

    title: str
    artist: str
    album_artist: str
    album: str
    track_number: int | None = None
    total_tracks: int | None = None
    disc_number: int | None = None
    year: int | None = None
    genre: str = ""
    source: str = "jellyfin"  # jellyfin | musicbrainz:<how>

    @classmethod
    def from_track(cls, track: Track) -> TagSet:
        return cls(
            title=track.title,
            artist=track.artist,
            album_artist=track.album_artist or track.artist,
            album=track.album,
            track_number=track.track_number,
            disc_number=track.disc_number,
            year=track.year,
            genre=track.genres[0] if track.genres else "",
        )


class MusicBrainzResolver:
    def __init__(
        self,
        acoustid_api_key: str = "",
        rate_limit_seconds: float = 1.0,
        min_fingerprint_score: float = 0.85,
    ):
        musicbrainzngs.set_useragent(*USER_AGENT)
        musicbrainzngs.set_rate_limit(limit_or_interval=max(rate_limit_seconds, 1.0))
        self.acoustid_api_key = acoustid_api_key
        self.min_fingerprint_score = min_fingerprint_score

        self._acoustid = None
        if acoustid_api_key:
            try:
                import acoustid  # noqa: PLC0415 - optional dependency

                self._acoustid = acoustid
            except ImportError:
                log.warning("acoustid_api_key set but pyacoustid is not installed")

    # -- matching ---------------------------------------------------------

    def _recording_id(self, track: Track, path: Path) -> tuple[str | None, str]:
        """Find a MusicBrainz recording id for this track. Returns (id, how)."""
        existing = track.mb_ids.get("MusicBrainzTrack")
        if existing:
            return existing, "jellyfin-id"

        if self._acoustid:
            try:
                for score, rec_id, _title, _artist in self._acoustid.match(
                    self.acoustid_api_key, str(path)
                ):
                    if score >= self.min_fingerprint_score:
                        return rec_id, f"fingerprint({score:.2f})"
                    break  # results are score-ordered; the first is the best there is
            except Exception as exc:  # noqa: BLE001 - network/fpcalc failures are non-fatal
                log.debug("acoustid lookup failed for %s: %s", track.describe(), exc)

        # Title-only search is not safe: with no artist to constrain it, a title
        # like "Epic Sax Guy (AKAY EDM Remix)" happily matches a different remix
        # at a high score. Require an artist, or keep the tags we already have.
        if not (track.artist or track.album_artist):
            log.debug("no artist on %s — skipping text search", track.title)
            return None, "none"

        query = {"recording": track.title, "artist": track.artist or track.album_artist}
        if track.album:
            query["release"] = track.album
        try:
            result = musicbrainzngs.search_recordings(limit=5, **query)
        except Exception as exc:  # noqa: BLE001
            log.debug("musicbrainz search failed for %s: %s", track.describe(), exc)
            return None, "none"

        for recording in result.get("recording-list", []):
            # search scores are strings 0-100; be conservative about accepting a text match
            if int(recording.get("ext:score", 0)) >= 90:
                return recording["id"], "search"
        return None, "none"

    def resolve(self, track: Track, path: Path) -> TagSet:
        """Return canonical tags, falling back to Jellyfin's metadata on any miss."""
        fallback = TagSet.from_track(track)

        rec_id, how = self._recording_id(track, path)
        if not rec_id:
            log.debug("no musicbrainz match for %s", track.describe())
            return fallback

        try:
            recording = musicbrainzngs.get_recording_by_id(
                rec_id, includes=["artists", "releases", "artist-credits", "tags"]
            )["recording"]
        except Exception as exc:  # noqa: BLE001
            log.debug("musicbrainz recording fetch failed for %s: %s", track.describe(), exc)
            return fallback

        tags = TagSet(
            title=recording.get("title") or fallback.title,
            artist=_credit(recording.get("artist-credit")) or fallback.artist,
            album_artist=fallback.album_artist,
            album=fallback.album,
            track_number=fallback.track_number,
            total_tracks=fallback.total_tracks,
            disc_number=fallback.disc_number,
            year=fallback.year,
            genre=_top_tag(recording.get("tag-list")) or fallback.genre,
            source=f"musicbrainz:{how}",
        )

        release = self._pick_release(recording, track)
        if release:
            self._apply_release(tags, release, rec_id, fallback)

        return tags

    def _pick_release(self, recording: dict, track: Track) -> dict | None:
        releases = recording.get("release-list") or []
        if not releases:
            return None

        wanted_id = track.mb_ids.get("MusicBrainzAlbum")
        if wanted_id:
            for release in releases:
                if release.get("id") == wanted_id:
                    return release

        if track.album:
            for release in releases:
                if (release.get("title") or "").lower() == track.album.lower():
                    return release

        # Otherwise take the earliest release rather than whatever MusicBrainz
        # happens to list first — that is routinely a much later reissue, which
        # is how a 2010 single ends up tagged 2024.
        def sort_key(release: dict) -> str:
            date = release.get("date") or ""
            return date if len(date) >= 4 else "9999"

        return min(releases, key=sort_key)

    def _apply_release(
        self, tags: TagSet, release: dict, rec_id: str, fallback: TagSet
    ) -> None:
        tags.album = release.get("title") or fallback.album

        date = release.get("date") or ""
        if date[:4].isdigit():
            tags.year = int(date[:4])

        try:
            full = musicbrainzngs.get_release_by_id(
                release["id"],
                includes=["recordings", "artist-credits", "media", "release-groups"],
            )["release"]
        except Exception as exc:  # noqa: BLE001
            log.debug("musicbrainz release fetch failed for %s: %s", release.get("title"), exc)
            return

        # The release-group's first-release-date is the original year; a specific
        # release may be a reissue decades later.
        first_release = (full.get("release-group") or {}).get("first-release-date") or ""
        if first_release[:4].isdigit():
            tags.year = int(first_release[:4])

        # This is the whole point of the exercise: a real album artist, distinct
        # from the track artist, so "feat." credits survive without breaking browse-by-artist.
        tags.album_artist = _credit(full.get("artist-credit")) or tags.album_artist

        for medium in full.get("medium-list") or []:
            for entry in medium.get("track-list") or []:
                if (entry.get("recording") or {}).get("id") != rec_id:
                    continue
                if str(entry.get("position", "")).isdigit():
                    tags.track_number = int(entry["position"])
                if str(medium.get("position", "")).isdigit():
                    tags.disc_number = int(medium["position"])
                if str(medium.get("track-count", "")).isdigit():
                    tags.total_tracks = int(medium["track-count"])
                return


def _credit(artist_credit) -> str:
    """Flatten a MusicBrainz artist-credit list into a display string."""
    if not artist_credit:
        return ""
    parts: list[str] = []
    for entry in artist_credit:
        if isinstance(entry, str):
            parts.append(entry)
        elif isinstance(entry, dict):
            artist = entry.get("artist") or {}
            parts.append(entry.get("name") or artist.get("name") or "")
            if entry.get("joinphrase"):
                parts.append(entry["joinphrase"])
    return "".join(parts).strip()


def _top_tag(tag_list) -> str:
    if not tag_list:
        return ""
    best = max(tag_list, key=lambda t: int(t.get("count", 0)))
    return (best.get("name") or "").title()


# -- writing --------------------------------------------------------------


def write_tags(
    path: Path, tags: TagSet, cover: bytes | None = None, replace: bool = True
) -> None:
    """Write tags to a file.

    With replace=False the file's existing tags are kept and only fields we
    actually have are set. That matters for passthrough files we did not
    transcode: if MusicBrainz found nothing and Jellyfin's metadata is thin,
    clearing the file's own tags would destroy the only metadata there is.
    """
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _write_mp3(path, tags, cover, replace)
    elif suffix in (".m4a", ".mp4", ".aac"):
        _write_mp4(path, tags, cover, replace)
    elif suffix == ".wma":
        _write_asf(path, tags)
    else:
        log.warning("don't know how to tag %s, leaving as-is", path.name)


def _common(tags: TagSet) -> dict[str, str]:
    out = {
        "title": tags.title,
        "artist": tags.artist,
        "albumartist": tags.album_artist,
        "album": tags.album,
    }
    if tags.track_number:
        out["tracknumber"] = (
            f"{tags.track_number}/{tags.total_tracks}"
            if tags.total_tracks
            else str(tags.track_number)
        )
    if tags.disc_number:
        out["discnumber"] = str(tags.disc_number)
    if tags.year:
        out["date"] = str(tags.year)
    if tags.genre:
        out["genre"] = tags.genre
    return {k: v for k, v in out.items() if v}


def _write_mp3(path: Path, tags: TagSet, cover: bytes | None, replace: bool = True) -> None:
    try:
        audio = EasyID3(path)
    except ID3NoHeaderError:
        audio = EasyID3()
        audio.save(path)
        audio = EasyID3(path)

    if replace:
        audio.delete()
    for key, value in _common(tags).items():
        audio[key] = value
    audio.save(path, v2_version=3)  # v2.3 — friendlier to old players than v2.4

    if cover:
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            id3 = ID3()
        id3.delall("APIC")
        id3.add(
            APIC(
                encoding=0,
                mime=_mime(cover),
                type=3,  # front cover
                desc="Cover",
                data=cover,
            )
        )
        id3.save(path, v2_version=3)
        MP3(path)  # cheap sanity check that the file still parses


def _write_mp4(path: Path, tags: TagSet, cover: bytes | None, replace: bool = True) -> None:
    audio = EasyMP4(path)
    if replace:
        audio.delete()
    for key, value in _common(tags).items():
        try:
            audio[key] = value
        except (KeyError, ValueError):
            log.debug("mp4 container does not support tag %r", key)
    audio.save()

    if cover:
        mp4 = MP4(path)
        fmt = MP4Cover.FORMAT_PNG if _mime(cover) == "image/png" else MP4Cover.FORMAT_JPEG
        mp4["covr"] = [MP4Cover(cover, imageformat=fmt)]
        mp4.save()


def _write_asf(path: Path, tags: TagSet) -> None:
    audio = ASF(path)
    mapping = {
        "Title": tags.title,
        "Author": tags.artist,
        "WM/AlbumArtist": tags.album_artist,
        "WM/AlbumTitle": tags.album,
    }
    if tags.track_number:
        mapping["WM/TrackNumber"] = str(tags.track_number)
    if tags.year:
        mapping["WM/Year"] = str(tags.year)
    if tags.genre:
        mapping["WM/Genre"] = tags.genre
    for key, value in mapping.items():
        if value:
            audio[key] = value
    audio.save()
    log.debug("cover art not embedded for WMA: %s", path.name)


def _mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
