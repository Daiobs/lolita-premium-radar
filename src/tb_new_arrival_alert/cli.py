from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from .config import (
    ConfigError,
    get_data_dir,
    get_poll_interval,
    get_targets,
    init_config,
    load_config,
)
from .fetchers import make_fetcher
from .monitor import Monitor
from .notify import make_notifiers
from .radar import connect_radar_db, import_radar_csv
from .storage import SeenStore
from .web import DEFAULT_WEB_PORT, run_web


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tb-new-arrival-alert")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a starter config file")
    init_parser.add_argument("--output", default="config.json")

    once_parser = subparsers.add_parser("once", help="run one scan")
    once_parser.add_argument("--config", default="config.json")

    run_parser = subparsers.add_parser("run", help="run continuous scans")
    run_parser.add_argument("--config", default="config.json")

    web_parser = subparsers.add_parser("web", help="start the local web dashboard")
    web_parser.add_argument("--config", default="config.json")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)

    sample_parser = subparsers.add_parser("sample", help="copy a sample HTML fixture")
    sample_parser.add_argument("--output", default="sample-taobao-page.html")

    radar_import_parser = subparsers.add_parser("radar-import", help="import radar rows from CSV")
    radar_import_parser.add_argument("--config", default="config.json")
    radar_import_parser.add_argument("--csv", required=True)

    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            init_config(Path(args.output))
            print(f"created {args.output}")
            return 0
        if args.command == "sample":
            return copy_sample(Path(args.output))
        if args.command == "once":
            return run_once(Path(args.config))
        if args.command == "run":
            return run_forever(Path(args.config))
        if args.command == "web":
            return run_web(Path(args.config), host=args.host, port=args.port)
        if args.command == "radar-import":
            return import_radar_command(Path(args.config), Path(args.csv))
    except ConfigError as exc:
        print(f"config error: {exc}")
        return 2

    return 1


def build_monitor(config_path: Path) -> tuple[Monitor, list]:
    config = load_config(config_path)
    data_dir = get_data_dir(config, config_path)
    store = SeenStore(data_dir / "seen.json")
    monitor = Monitor(
        fetcher=make_fetcher(config),
        store=store,
        notifiers=make_notifiers(config),
        notify_on_first_scan=bool(config.get("notify_on_first_scan", False)),
    )
    return monitor, get_targets(config)


def run_once(config_path: Path) -> int:
    monitor, targets = build_monitor(config_path)
    monitor.check_all(targets)
    return 0


def run_forever(config_path: Path) -> int:
    config = load_config(config_path)
    interval = get_poll_interval(config)
    while True:
        monitor, targets = build_monitor(config_path)
        monitor.check_all(targets)
        time.sleep(interval)


def copy_sample(output_path: Path) -> int:
    if output_path.exists():
        print(f"refusing to overwrite existing file: {output_path}")
        return 2
    package_root = Path(__file__).resolve().parents[2]
    source = package_root / "tests" / "fixtures" / "sample_shop.html"
    shutil.copyfile(source, output_path)
    print(f"created {output_path}")
    return 0


def import_radar_command(config_path: Path, csv_path: Path) -> int:
    config = load_config(config_path)
    data_dir = get_data_dir(config, config_path)
    if not csv_path.is_absolute() and not csv_path.exists():
        csv_path = config_path.parent / csv_path
    connection = connect_radar_db(data_dir / "radar.sqlite")
    try:
        result = import_radar_csv(connection, csv_path)
    finally:
        connection.close()
    print(
        "imported radar CSV: "
        f"rows={result.rows_read} items_created={result.items_created} "
        f"samples_created={result.samples_created}"
    )
    return 0
