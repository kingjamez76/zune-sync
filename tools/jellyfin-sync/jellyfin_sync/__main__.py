"""Command line entry point: python -m jellyfin_sync"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import ConfigError, load
from .device import DeviceError, Zune
from .formats import TranscodeError
from .jellyfin import JellyfinError
from .sync import Syncer

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jellyfin-sync",
        description="Sync music from a Jellyfin server to a Microsoft Zune.",
    )
    parser.add_argument(
        "-c", "--config", type=Path, default=DEFAULT_CONFIG, help="path to config.toml"
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="list what would be synced, change nothing"
    )
    parser.add_argument("--limit", type=int, help="only process N tracks this run")
    parser.add_argument(
        "--force", action="store_true", help="re-sync tracks already recorded as uploaded"
    )
    parser.add_argument(
        "--device-info",
        action="store_true",
        help="dump the connected device's MTP capabilities and exit",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )

    try:
        if args.device_info:
            config = load(args.config) if args.config.exists() else None
            cli = config.device.cli if config else "aft-mtp-cli"
            print(Zune(cli).device_info())
            return 0

        config = load(args.config)
        return Syncer(config, dry_run=args.dry_run).run(limit=args.limit, force=args.force)

    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except (JellyfinError, DeviceError, TranscodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
