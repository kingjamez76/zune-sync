"""Probing and transcoding. The Zune plays MP3, unprotected WMA and unprotected AAC."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# ffprobe codec names -> the family names used in config.transcode.passthrough
CODEC_ALIASES = {
    "wmav1": "wma",
    "wmav2": "wma",
    "wmapro": "wmapro",  # deliberately NOT wma: the Zune cannot play WMA Pro
    "mp3float": "mp3",
}


class TranscodeError(Exception):
    pass


def require_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise TranscodeError(f"{tool} not found on PATH — install ffmpeg")


def probe_codec(path: Path) -> str:
    """Return the normalised audio codec name of a file."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise TranscodeError(f"ffprobe failed on {path.name}: {result.stderr.strip()}")

    try:
        streams = json.loads(result.stdout).get("streams", [])
        codec = (streams[0].get("codec_name") or "").lower() if streams else ""
    except (json.JSONDecodeError, IndexError, AttributeError) as exc:
        raise TranscodeError(f"could not parse ffprobe output for {path.name}: {exc}") from exc

    if not codec:
        raise TranscodeError(f"no audio stream found in {path.name}")
    return CODEC_ALIASES.get(codec, codec)


def needs_transcode(codec: str, passthrough: list[str], force_all: bool) -> bool:
    if force_all:
        return codec != "mp3"
    return codec not in passthrough


def to_mp3(src: Path, dest: Path, quality: int = 0) -> Path:
    """Transcode to VBR MP3. Tags are written separately, so metadata is dropped here."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.mp3")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",  # drop embedded art; re-embedded later from Jellyfin/MusicBrainz
        "-map_metadata",
        "-1",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        str(quality),
        str(tmp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise TranscodeError(f"ffmpeg failed on {src.name}: {result.stderr.strip()[:500]}")

    tmp.replace(dest)
    return dest
