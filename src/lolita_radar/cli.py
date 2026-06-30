from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .brands import default_brand_weights_path
from .config import default_config_path
from .market import default_market_observations_path
from .runner import (
    CheckLoopResult,
    CheckLoopVerification,
    InspectResult,
    check_sources,
    inspect_sources,
    latest_source_health,
    run_check_loop,
    verify_check_loop,
)
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
    check_parser.add_argument("--baseline-only", action="store_true", help="store fetched items without events or notifications")
    check_parser.add_argument(
        "--force-baseline",
        action="store_true",
        help="allow baseline-only to overwrite existing tracked state for selected sources",
    )
    check_parser.add_argument(
        "--suppress-initial-notify",
        action="store_true",
        help="write events but skip notifications for this run",
    )

    inspect_parser = subparsers.add_parser("inspect", help="fetch and parse sources without writing the database")
    inspect_group = inspect_parser.add_mutually_exclusive_group(required=True)
    inspect_group.add_argument("--source", help="source name from config/sources.yaml")
    inspect_group.add_argument("--all", action="store_true", help="inspect all enabled sources")
    inspect_parser.add_argument("--config", type=Path, default=default_config_path())
    inspect_parser.add_argument("--limit", type=int, default=10)

    health_parser = subparsers.add_parser("health", help="show latest source run health")
    health_parser.add_argument("--config", type=Path, default=default_config_path())
    health_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)

    loop_parser = subparsers.add_parser("run-loop", help="run repeated feed checks for long-running operation")
    loop_parser.add_argument("--config", type=Path, default=default_config_path())
    loop_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    loop_parser.add_argument("--cycles", type=int, default=288, help="number of check cycles; 288 at 5 minutes covers 24h")
    loop_parser.add_argument("--interval-seconds", type=int, default=300)
    loop_parser.add_argument("--notify", action="store_true", help="send notifications during the loop")

    verify_loop_parser = subparsers.add_parser("verify-loop", help="verify a long-running check loop log and database")
    verify_loop_parser.add_argument("--config", type=Path, default=default_config_path())
    verify_loop_parser.add_argument("--db", type=Path, required=True)
    verify_loop_parser.add_argument("--log", type=Path, required=True)
    verify_loop_parser.add_argument("--exit-file", type=Path)
    verify_loop_parser.add_argument("--expected-cycles", type=int, default=96)

    web_parser = subparsers.add_parser("web", help="start the local feed app")
    web_parser.add_argument("--config", type=Path, default=default_config_path())
    web_parser.add_argument("--brands", type=Path, default=default_brand_weights_path())
    web_parser.add_argument("--market", type=Path, default=default_market_observations_path())
    web_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)

    args = parser.parse_args(argv)
    if args.command == "check":
        try:
            events = check_sources(
                config_path=args.config,
                db_path=args.db,
                source_name=args.source,
                notify=not (args.no_notify or args.suppress_initial_notify or args.baseline_only),
                baseline_only=args.baseline_only,
                force_baseline=args.force_baseline,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"events={len(events)}")
        return 0
    if args.command == "inspect":
        results = inspect_sources(config_path=args.config, source_name=args.source)
        print(format_inspect_results(results, limit=max(0, args.limit)))
        return 0
    if args.command == "health":
        print(format_health_rows(latest_source_health(config_path=args.config, db_path=args.db)))
        return 0
    if args.command == "run-loop":
        print("cycle | ok | event_count | error_message", flush=True)
        try:
            results = run_check_loop(
                config_path=args.config,
                db_path=args.db,
                cycles=args.cycles,
                interval_seconds=args.interval_seconds,
                notify=args.notify,
                on_result=lambda result: print(format_loop_result_line(result), flush=True),
            )
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            return 130
        return 0 if all(result.ok for result in results) else 1
    if args.command == "verify-loop":
        verification = verify_check_loop(
            config_path=args.config,
            db_path=args.db,
            log_path=args.log,
            expected_cycles=args.expected_cycles,
            exit_path=args.exit_file,
        )
        print(format_loop_verification(verification))
        return 0 if verification.complete else 1
    if args.command == "web":
        return run_web(
            config_path=args.config,
            db_path=args.db,
            brands_path=args.brands,
            market_path=args.market,
            host=args.host,
            port=args.port,
        )
    return 1


def format_inspect_results(results: list[InspectResult], limit: int) -> str:
    blocks = []
    for result in results:
        source = result.source
        lines = [
            f"source: {source.name}",
            f"url: {source.url}",
            f"fetched: {'ok' if result.ok else 'error'}",
            f"parsed_item_count: {len(result.items)}",
        ]
        if result.error_message:
            lines.append(f"error: {result.error_message}")
        warnings = list(result.warnings)
        if warnings:
            lines.append("warnings:")
            lines.extend(f"  - {warning}" for warning in warnings)
        else:
            lines.append("warnings: []")
        shown_items = result.items[:limit]
        if shown_items:
            lines.append("items:")
            for item in shown_items:
                lines.append(
                    "  - "
                    f"{item.status.value} | {item.title} | {item.published_at or '-'} | {item.url}"
                )
        else:
            lines.append("items: []")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def format_health_rows(rows: list[dict[str, object]]) -> str:
    lines = ["source | status | ok | error_rate | item_count | event_count | checked_at | error_message"]
    for row in rows:
        ok = row["ok"]
        ok_text = "ok" if ok is True else "failed" if ok is False else "no_run"
        lines.append(
            " | ".join(
                [
                    str(row["source"]),
                    str(row.get("status") or ok_text),
                    ok_text,
                    str(row.get("error_rate", 0)),
                    str(row["item_count"]),
                    str(row["event_count"]),
                    str(row["checked_at"] or "-"),
                    str(row["error_message"] or ""),
                ]
            )
        )
    return "\n".join(lines)


def format_loop_results(results: list[CheckLoopResult]) -> str:
    lines = ["cycle | ok | event_count | error_message"]
    for result in results:
        lines.append(format_loop_result_line(result))
    return "\n".join(lines)


def format_loop_result_line(result: CheckLoopResult) -> str:
    return " | ".join(
        [
            str(result.cycle),
            "ok" if result.ok else "failed",
            str(result.event_count),
            result.error_message,
        ]
    )


def format_loop_verification(verification: CheckLoopVerification) -> str:
    exit_code = "-" if verification.exit_code is None else str(verification.exit_code)
    lines = [
        f"status: {verification.status}",
        f"expected_cycles: {verification.expected_cycles}",
        f"observed_cycles: {verification.observed_cycles}",
        f"exit_code: {exit_code}",
        "failed_cycles: "
        + (", ".join(str(cycle) for cycle in verification.failed_cycles) if verification.failed_cycles else "[]"),
        "missing_cycles: "
        + (", ".join(str(cycle) for cycle in verification.missing_cycles) if verification.missing_cycles else "[]"),
        "unhealthy_source_runs: "
        + (
            ", ".join(
                f"{source}:{verification.unhealthy_source_runs[source]}"
                for source in sorted(verification.unhealthy_source_runs)
            )
            if verification.unhealthy_source_runs
            else "[]"
        ),
        "source_cycles:",
    ]
    for source in verification.expected_sources:
        lines.append(f"  - {source}: {verification.source_cycle_counts.get(source, 0)}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
