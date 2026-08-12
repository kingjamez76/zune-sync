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
            return 0

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
        uploaded, failed = self.device.import_tracks([p.path for p in prepared])

        for path in uploaded:
            item = by_path[path]
            self.state.record(
                item.track.id, item.track.describe(), path.name, item.tags.source
            )
        for path, error in failed:
            log.error("  upload failed: %s (%s)", path.name, error)

        self._record_playlists(tracks)
        self.state.save()

        if not cfg.sync.keep_staged:
            for path in uploaded:
                path.unlink(missing_ok=True)

        log.info("done: %d uploaded, %d failed", len(uploaded), len(failed))
        return 0 if not failed else 1

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
