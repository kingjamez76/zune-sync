"""Orchestration: Jellyfin -> transcode -> MusicBrainz -> Zune."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import formats
from .config import Config
from .device import Zune, safe_filename
from .jellyfin import Jellyfin, JellyfinError, Track
from .metadata import MusicBrainzResolver, TagSet, write_tags
from .state import State

log = logging.getLogger(__name__)

EXT_FROM_CONTAINER = {
    "mp3": ".mp3",
    "flac": ".flac",
    "m4a": ".m4a",
    "mp4": ".m4a",
    "aac": ".m4a",
    "ogg": ".ogg",
    "oga": ".ogg",
    "opus": ".opus",
    "wma": ".wma",
    "asf": ".wma",
    "wav": ".wav",
    "alac": ".m4a",
    "ape": ".ape",
    "wv": ".wv",
}


@dataclass
class Prepared:
    track: Track
    path: Path
    tags: TagSet
    transcoded: bool


class Syncer:
    def __init__(self, config: Config, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.jellyfin = Jellyfin(config.jellyfin.url, config.jellyfin.api_key)
        self.device = Zune(config.device.cli, config.device.batch_size)
        self.state = State(config.sync.state_file)
        self.resolver: MusicBrainzResolver | None = None
        if config.musicbrainz.enabled:
            self.resolver = MusicBrainzResolver(
                acoustid_api_key=config.musicbrainz.acoustid_api_key,
                rate_limit_seconds=config.musicbrainz.rate_limit_seconds,
                min_fingerprint_score=config.musicbrainz.min_fingerprint_score,
            )

    # -- collection -------------------------------------------------------

    def collect(self) -> dict[str, Track]:
        """Gather every selected track, merged and de-duplicated by Jellyfin id."""
        user_id = self.jellyfin.resolve_user(self.config.jellyfin.user)
        merged: dict[str, Track] = {}

        def absorb(tracks: list[Track], label: str) -> None:
            log.info("  %-28s %d track(s)", label, len(tracks))
            for track in tracks:
                existing = merged.get(track.id)
                if existing:
                    existing.playlists |= track.playlists
                else:
                    merged[track.id] = track

        log.info("collecting from Jellyfin:")
        for name in self.config.sync.playlists:
            try:
                absorb(self.jellyfin.playlist_tracks(user_id, name), f"playlist {name!r}")
            except JellyfinError as exc:
                log.error("  playlist %r: %s", name, exc)

        if self.config.sync.favorites:
            absorb(self.jellyfin.favorite_tracks(user_id), "favorites")

        for name in self.config.sync.artists:
            try:
                absorb(self.jellyfin.artist_tracks(user_id, name), f"artist {name!r}")
            except JellyfinError as exc:
                log.error("  artist %r: %s", name, exc)

        for name in self.config.sync.albums:
            try:
                absorb(self.jellyfin.album_tracks(user_id, name), f"album {name!r}")
            except JellyfinError as exc:
                log.error("  album %r: %s", name, exc)

        log.info("%d unique track(s) selected", len(merged))
        return merged

    def list_available(self) -> int:
        """Show what this Jellyfin server offers, and what is currently selected."""
        user_id = self.jellyfin.resolve_user(self.config.jellyfin.user)
        selected = {p.lower() for p in self.config.sync.playlists}

        playlists = self.jellyfin.list_playlists(user_id)
        log.info("playlists on %s:", self.config.jellyfin.url)
        if not playlists:
            log.info("  (none)")
        for name, count in playlists:
            mark = "[x]" if name.lower() in selected else "[ ]"
            log.info("  %s %-40s %3d track(s)", mark, name, count)

        counts = self.jellyfin.count_favorites(user_id)
        mark = "[x]" if self.config.sync.favorites else "[ ]"
        total = sum(counts.values())
        log.info(
            "\n  %s favorites: %d track(s), %d album(s), %d artist(s)",
            mark,
            counts.get("Audio", 0),
            counts.get("MusicAlbum", 0),
            counts.get("MusicArtist", 0),
        )
        if self.config.sync.favorites and not total:
            log.info("      (star anything in Jellyfin and it syncs on the next run)")

        log.info("\nalso selected:")
        log.info("  artists: %s", ", ".join(self.config.sync.artists) or "(none)")
        log.info("  albums : %s", ", ".join(self.config.sync.albums) or "(none)")
        log.info(
            "\nEdit the [sync] section of your config to change this, then run a sync.\n"
            "Names must match exactly — copy them from the list above."
        )
        return 0

    # -- preparation ------------------------------------------------------

    def prepare(self, track: Track) -> Prepared:
        cfg = self.config
        suffix = EXT_FROM_CONTAINER.get(track.container, f".{track.container or 'bin'}")
        source = cfg.download_dir / f"{track.id}{suffix}"

        if not source.exists():
            self.jellyfin.download(track, source)

        codec = formats.probe_codec(source)
        transcode = formats.needs_transcode(
            codec, cfg.transcode.passthrough, cfg.transcode.force_all_to_mp3
        )

        stem = f"{track.track_number or 0:02d} {track.describe()}"
        if transcode:
            staged = cfg.staged_dir / safe_filename(stem, ".mp3")
            log.info("    transcoding %s -> mp3 (V%d)", codec, cfg.transcode.mp3_quality)
            formats.to_mp3(source, staged, cfg.transcode.mp3_quality)
        else:
            staged = cfg.staged_dir / safe_filename(stem, source.suffix)
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged)

        tags = TagSet.from_track(track)
        if self.resolver:
            tags = self.resolver.resolve(track, staged)
            if tags.source != "jellyfin":
                log.info("    metadata: %s", tags.source)

        cover = None
        if cfg.musicbrainz.embed_album_art:
            cover = self.jellyfin.album_art(track)

        # Only clear the file's own tags when we have something authoritative to
        # put in their place; a transcode starts tagless either way.
        replace = transcode or (
            tags.source != "jellyfin" and cfg.musicbrainz.overwrite
        )
        write_tags(staged, tags, cover, replace=replace)
        return Prepared(track=track, path=staged, tags=tags, transcoded=transcode)

    # -- top level --------------------------------------------------------

    def run(
        self, limit: int | None = None, force: bool = False, prepare_only: bool = False
    ) -> int:
        cfg = self.config
        formats.require_tools()
        if not self.dry_run and not prepare_only:
            self.device.require()

        tracks = self.collect()
        if not tracks:
            log.warning("nothing to sync")
            return 0

        pending = [t for t in tracks.values() if force or not self.state.has(t.id)]
        skipped = len(tracks) - len(pending)
        if skipped:
            log.info("%d track(s) already on device, skipping", skipped)
        if limit is not None:
            pending = pending[:limit]
            log.info("limited to %d track(s) this run", len(pending))

        if not pending:
            log.info("device is up to date")
            self._record_playlists(tracks)
            if self.dry_run or prepare_only:
                return 0
            return self.sync_playlists(tracks)

        if self.dry_run:
            log.info("dry run — would sync:")
            for track in pending:
                log.info("  %s  [%s]", track.describe(), track.container or "?")
            return 0

        cfg.download_dir.mkdir(parents=True, exist_ok=True)
        cfg.staged_dir.mkdir(parents=True, exist_ok=True)

        prepared: list[Prepared] = []
        for index, track in enumerate(pending, start=1):
            log.info("[%d/%d] %s", index, len(pending), track.describe())
            try:
                prepared.append(self.prepare(track))
            except Exception as exc:  # noqa: BLE001 - one bad track must not stop the run
                log.error("    failed to prepare: %s", exc)

        if not prepared:
            log.error("nothing could be prepared")
            return 1

        if prepare_only:
            log.info("prepared %d track(s) in %s (not uploaded)", len(prepared), cfg.staged_dir)
            for item in prepared:
                log.info(
                    "  %-58s %s%s",
                    item.path.name,
                    item.tags.source,
                    " [transcoded]" if item.transcoded else "",
                )
            return 0

        log.info("uploading %d track(s) to the device", len(prepared))
        by_path = {p.path: p for p in prepared}
        uploaded, skipped, failed = self.device.import_tracks([p.path for p in prepared])

        # A skip means the device already had it, which is just as final as an
        # upload — record both so the next run stops re-preparing them.
        for path in uploaded + skipped:
            item = by_path[path]
            self.state.record(
                item.track.id,
                item.track.describe(),
                path.name,
                item.tags.source,
                object_id=self.device.object_ids.get(path),
            )
        for path in skipped:
            log.info("  already on device: %s", by_path[path].track.describe())
        for path, error in failed:
            log.error("  upload failed: %s (%s)", path.name, error)

        self._record_playlists(tracks)
        self.state.save()
        self.sync_playlists(tracks)

        if not cfg.sync.keep_staged:
            for path in uploaded + skipped:
                path.unlink(missing_ok=True)

        log.info(
            "done: %d uploaded, %d already present, %d failed",
            len(uploaded),
            len(skipped),
            len(failed),
        )
        return 0 if not failed else 1

    def sync_playlists(self, tracks: dict[str, Track]) -> int:
        """Rebuild the device's playlists from Jellyfin playlist membership.

        Works off object ids recorded in state, so it needs neither the audio
        files nor a fresh download — only tracks already on the device.
        """
        membership: dict[str, list[int]] = {}
        missing: dict[str, int] = {}
        for track in tracks.values():
            for name in track.playlists:
                oid = self.state.object_id(track.id)
                if oid is None:
                    missing[name] = missing.get(name, 0) + 1
                    continue
                membership.setdefault(name, []).append(oid)

        if not membership:
            log.info("no playlist membership to sync")
            return 0

        log.info("syncing %d playlist(s) to the device", len(membership))
        for name, ids in membership.items():
            gap = missing.get(name, 0)
            log.info(
                "  %-32s %d track(s)%s",
                name,
                len(ids),
                f" ({gap} not on device, omitted)" if gap else "",
            )

        errors = self.device.build_playlists(membership)
        for name, error in errors.items():
            log.error("  playlist %r failed: %s", name, error)
        return 1 if errors else 0

    def _record_playlists(self, tracks: dict[str, Track]) -> None:
        """Remember playlist membership for on-device playlist support later."""
        membership: dict[str, list[str]] = {}
        for track in tracks.values():
            for name in track.playlists:
                membership.setdefault(name, []).append(track.id)
        for name, ids in membership.items():
            self.state.record_playlist(name, ids)
        if membership:
            self.state.save()
