"""Upload to the Zune via aft-mtp-cli.

Tracks are imported in batches because `zune-import` loads the on-device media
library on first use; one invocation per track would reload it every time.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# tools/jellyfin-sync/jellyfin_sync/device.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


class DeviceError(Exception):
    pass


def quote(path: Path) -> str:
    """Quote a path for aft-mtp-cli's tokenizer (double quotes, backslash escapes)."""
    escaped = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def safe_filename(name: str, suffix: str) -> str:
    """A filename that survives the CLI tokenizer and old MTP stacks intact.

    The Zune displays tags rather than filenames, so mangling here costs nothing.
    """
    stem = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._") or "track"
    return f"{stem[:120]}{suffix}"


class Zune:
    def __init__(self, cli: str = "aft-mtp-cli", batch_size: int = 25):
        self.cli = cli
        self.batch_size = max(1, batch_size)

    def require(self) -> str:
        """Locate aft-mtp-cli.

        This repo's own build wins over anything on PATH: a distro-packaged
        aft-mtp-cli would otherwise silently shadow local C++ changes.
        """
        if Path(self.cli).is_file():
            return self.cli

        local = REPO_ROOT / "build" / "cli" / "aft-mtp-cli"
        if local.is_file():
            return str(local)

        found = shutil.which(self.cli)
        if found:
            log.warning(
                "using %s from PATH — this repo has no build, so local C++ changes "
                "will not be in effect",
                found,
            )
            return found

        raise DeviceError(
            f"{self.cli!r} not found on PATH and no build at {local} — "
            f"build it with:\n"
            f"  cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release "
            f"-DBUILD_QT_UI=OFF -DBUILD_FUSE=OFF -DBUILD_PYTHON=OFF\n"
            f"  cmake --build build\n"
            f"or set device.cli to its path"
        )

    def _run(self, commands: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
        cli = self.require()
        log.debug("running %s with %d command(s)", cli, len(commands))
        return subprocess.run(
            [cli, *commands], capture_output=True, text=True, timeout=timeout
        )

    def device_info(self) -> str:
        """Raw `device-info` output — the authority on what this device supports."""
        result = self._run(["device-info"], timeout=120)
        if result.returncode != 0:
            raise DeviceError(
                f"device-info failed (is the Zune connected?): {result.stderr.strip()[:400]}"
            )
        return result.stdout

    def import_tracks(self, paths: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
        """Import files in batches. Returns (uploaded, [(path, error), ...])."""
        uploaded: list[Path] = []
        failed: list[tuple[Path, str]] = []

        for start in range(0, len(paths), self.batch_size):
            batch = paths[start : start + self.batch_size]
            commands = [f"zune-import {quote(p)}" for p in batch]
            try:
                result = self._run(commands)
            except subprocess.TimeoutExpired:
                failed.extend((p, "aft-mtp-cli timed out") for p in batch)
                continue

            output = f"{result.stdout}\n{result.stderr}"
            if result.returncode != 0:
                # The CLI stops at the first failing command, so the rest of the
                # batch is unknown rather than failed — retry it individually.
                log.warning("batch failed, retrying %d tracks individually", len(batch))
                for path in batch:
                    ok, err = self._import_one(path)
                    (uploaded.append(path) if ok else failed.append((path, err)))
                continue

            for path in batch:
                if _reports_error(output, path):
                    failed.append((path, _error_line(output, path)))
                else:
                    uploaded.append(path)

        return uploaded, failed

    def _import_one(self, path: Path) -> tuple[bool, str]:
        try:
            result = self._run([f"zune-import {quote(path)}"], timeout=600)
        except subprocess.TimeoutExpired:
            return False, "aft-mtp-cli timed out"
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip().splitlines()
            return False, message[-1][:300] if message else "unknown error"
        return True, ""


def _reports_error(output: str, path: Path) -> bool:
    name = path.name
    return any(
        name in line and re.search(r"error|failed|exception", line, re.IGNORECASE)
        for line in output.splitlines()
    )


def _error_line(output: str, path: Path) -> str:
    for line in output.splitlines():
        if path.name in line and re.search(r"error|failed|exception", line, re.IGNORECASE):
            return line.strip()[:300]
    return "unknown error"
