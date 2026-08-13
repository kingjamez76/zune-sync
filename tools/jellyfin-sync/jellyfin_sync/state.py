"""Records what has already been pushed to each device, so re-runs are incremental.

State is keyed on the device's serial number. Object ids and "already synced"
are both device-specific claims: a track on one Zune says nothing about another,
and an object id from one device points at nothing (or worse, at something else)
on a second.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

VERSION = 2
UNKNOWN_DEVICE = "unknown-device"


class State:
    def __init__(self, path: Path):
        self.path = path
        self.devices: dict[str, dict] = {}
        self._device = UNKNOWN_DEVICE
        self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("could not read state at %s (%s) — starting fresh", self.path, exc)
            return

        version = data.get("version")
        if version == VERSION:
            self.devices = data.get("devices", {})
            return

        # Version 1 kept a single flat set of tracks with no idea which device
        # they went to. There is no safe way to attribute them, and the device
        # itself is the authority anyway — zune-import skips tracks already
        # present — so the worst a fresh start costs is re-preparing files.
        if version == 1:
            count = len(data.get("tracks", {}))
            log.warning(
                "state file is the older per-computer format (%d track(s)); "
                "starting fresh with per-device state. Nothing will be duplicated: "
                "the device skips tracks it already has.",
                count,
            )
            return

        log.warning("state file version %s is not %s — starting fresh", version, VERSION)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": VERSION,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "devices": self.devices,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp.replace(self.path)

    # -- device selection -------------------------------------------------

    def select_device(self, serial: str) -> None:
        self._device = serial or UNKNOWN_DEVICE
        self.devices.setdefault(self._device, {"tracks": {}, "playlists": {}})
        known = len(self._bucket["tracks"])
        log.info("device %s: %d track(s) previously synced", self._device, known)

    @property
    def _bucket(self) -> dict:
        return self.devices.setdefault(self._device, {"tracks": {}, "playlists": {}})

    @property
    def tracks(self) -> dict[str, dict]:
        return self._bucket["tracks"]

    @property
    def playlists(self) -> dict[str, list[str]]:
        return self._bucket["playlists"]

    # -- records ----------------------------------------------------------

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
        self.playlists[name] = track_ids

    def forget(self, track_id: str) -> None:
        self.tracks.pop(track_id, None)
