from __future__ import annotations

import argparse
from pathlib import Path

from .config import default_config_path
from .runner import check_sources


DEFAULT_DB_PATH = Path(".data") / "lolita_radar.sqlite"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lolita-premium-radar")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="check one source or all enabled sources")
    group = check_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", help="source name from config/sources.yaml")
    group.add_argument("--all", action="store_true", help="check all enabled sources")
    check_parser.add_argument("--config", type=Path, default=default_config_path())
    check_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    check_parser.add_argument("--no-notify", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "check":
        events = check_sources(
            config_path=args.config,
            db_path=args.db,
            source_name=args.source,
            notify=not args.no_notify,
        )
        print(f"events={len(events)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
