from __future__ import annotations

import argparse
from pathlib import Path

from .brands import default_brand_weights_path
from .config import default_config_path
from .runner import check_sources
from .web import DEFAULT_WEB_PORT, run_web


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

    web_parser = subparsers.add_parser("web", help="start the local web dashboard")
    web_parser.add_argument("--config", type=Path, default=default_config_path())
    web_parser.add_argument("--brands", type=Path, default=default_brand_weights_path())
    web_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)

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
    if args.command == "web":
        return run_web(
            config_path=args.config,
            db_path=args.db,
            brands_path=args.brands,
            host=args.host,
            port=args.port,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
