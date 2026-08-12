"""Records what has already been pushed to the device, so re-runs are incremental."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

VERSION = 1


class State:
    def __init__(self, path: Path):
        self.path = path
        self.tracks: dict[str, dict] = {}
        self.playlists: dict[str, list[str]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("could not read state at %s (%s) — starting fresh", self.path, exc)
            return
        if data.get("version") != VERSION:
            log.warning("state file version %s is not %s — starting fresh", data.get("version"), VERSION)
            return
        self.tracks = data.get("tracks", {})
        self.playlists = data.get("playlists", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": VERSION,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tracks": self.tracks,
            "playlists": self.playlists,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def has(self, track_id: str) -> bool:
        return track_id in self.tracks

    def record(
        self,
        track_id: str,
        description: str,
        filename: str,
        source: str,
        object_id: int | None = None,
    ) -> None:
        entry = {
            "description": description,
            "filename": filename,
            "metadata_source": source,
            "uploaded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        # The device object id is what playlists reference, so keeping it here
        # means playlists can be rebuilt without re-downloading anything.
        if object_id is not None:
            entry["object_id"] = object_id
        elif track_id in self.tracks and "object_id" in self.tracks[track_id]:
            entry["object_id"] = self.tracks[track_id]["object_id"]
        self.tracks[track_id] = entry

    def object_id(self, track_id: str) -> int | None:
        return self.tracks.get(track_id, {}).get("object_id")

    def record_playlist(self, name: str, track_ids: list[str]) -> None:
        """Kept for the device-side playlist support that isn't built yet."""
        self.playlists[name] = track_ids

    def forget(self, track_id: str) -> None:
        self.tracks.pop(track_id, None)
