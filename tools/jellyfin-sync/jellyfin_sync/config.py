"""Configuration loading for jellyfin-sync."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


def _expand(p: str) -> Path:
    return Path(p).expanduser()


@dataclass
class JellyfinConfig:
    url: str
    api_key: str
    user: str


@dataclass
class SyncConfig:
    playlists: list[str] = field(default_factory=list)
    favorites: bool = False
    artists: list[str] = field(default_factory=list)
    albums: list[str] = field(default_factory=list)
    work_dir: Path = Path("~/.cache/zune-sync").expanduser()
    state_file: Path = Path("~/.local/state/zune-sync/state.json").expanduser()
    keep_staged: bool = False


@dataclass
class TranscodeConfig:
    passthrough: list[str] = field(default_factory=lambda: ["mp3", "aac", "wma"])
    mp3_quality: int = 0
    force_all_to_mp3: bool = False


@dataclass
class MusicBrainzConfig:
    enabled: bool = True
    overwrite: bool = True
    acoustid_api_key: str = ""
    rate_limit_seconds: float = 1.0
    min_fingerprint_score: float = 0.85
    embed_album_art: bool = True


@dataclass
class DeviceConfig:
    cli: str = "aft-mtp-cli"
    batch_size: int = 25


@dataclass
class Config:
    jellyfin: JellyfinConfig
    sync: SyncConfig
    transcode: TranscodeConfig
    musicbrainz: MusicBrainzConfig
    device: DeviceConfig

    @property
    def download_dir(self) -> Path:
        return self.sync.work_dir / "download"

    @property
    def staged_dir(self) -> Path:
        return self.sync.work_dir / "staged"


def load(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(
            f"no config at {path}\n"
            f"Copy config.example.toml to {path.name} and fill in your Jellyfin URL and API key."
        )

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    jf = raw.get("jellyfin", {})
    url = str(jf.get("url", "")).rstrip("/")
    api_key = str(jf.get("api_key", ""))
    if not url:
        raise ConfigError("jellyfin.url is required")
    if not api_key:
        raise ConfigError("jellyfin.api_key is required (Jellyfin Dashboard -> API Keys)")

    sync_raw = raw.get("sync", {})
    sync = SyncConfig(
        playlists=list(sync_raw.get("playlists", [])),
        favorites=bool(sync_raw.get("favorites", False)),
        artists=list(sync_raw.get("artists", [])),
        albums=list(sync_raw.get("albums", [])),
        work_dir=_expand(sync_raw.get("work_dir", "~/.cache/zune-sync")),
        state_file=_expand(sync_raw.get("state_file", "~/.local/state/zune-sync/state.json")),
        keep_staged=bool(sync_raw.get("keep_staged", False)),
    )
    if not (sync.playlists or sync.favorites or sync.artists or sync.albums):
        raise ConfigError(
            "nothing selected to sync — set at least one of "
            "sync.playlists, sync.favorites, sync.artists or sync.albums"
        )

    tr_raw = raw.get("transcode", {})
    transcode = TranscodeConfig(
        passthrough=[c.lower() for c in tr_raw.get("passthrough", ["mp3", "aac", "wma"])],
        mp3_quality=int(tr_raw.get("mp3_quality", 0)),
        force_all_to_mp3=bool(tr_raw.get("force_all_to_mp3", False)),
    )

    mb_raw = raw.get("musicbrainz", {})
    musicbrainz = MusicBrainzConfig(
        enabled=bool(mb_raw.get("enabled", True)),
        overwrite=bool(mb_raw.get("overwrite", True)),
        acoustid_api_key=str(mb_raw.get("acoustid_api_key", "")),
        rate_limit_seconds=float(mb_raw.get("rate_limit_seconds", 1.0)),
        min_fingerprint_score=float(mb_raw.get("min_fingerprint_score", 0.85)),
        embed_album_art=bool(mb_raw.get("embed_album_art", True)),
    )

    dev_raw = raw.get("device", {})
    device = DeviceConfig(
        cli=str(dev_raw.get("cli", "aft-mtp-cli")),
        batch_size=max(1, int(dev_raw.get("batch_size", 25))),
    )

    return Config(
        jellyfin=JellyfinConfig(url=url, api_key=api_key, user=str(jf.get("user", ""))),
        sync=sync,
        transcode=transcode,
        musicbrainz=musicbrainz,
        device=device,
    )
