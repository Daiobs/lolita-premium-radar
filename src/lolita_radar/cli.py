from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import Callable

from .brands import default_brand_weights_path
from .collector import DEFAULT_COLLECTOR_JOBS, CollectorJob, CollectorRun, collector_for_type, run_collector_job
from .collector.runner import guard_collector_baseline_only
from .config import default_config_path
from .core import FeedOsAudit, audit_feed_os
from .market import default_market_observations_path
from .models import utc_now_iso
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
from .web import DEFAULT_WEB_PORT, get_feed_state, run_web
from .storage import connect, count_collector_jobs, list_collector_jobs, upsert_collector_job


DEFAULT_DB_PATH = Path(".data") / "lolita_radar.sqlite"
DEFAULT_LOOP_CYCLES = 288
DEFAULT_LOOP_MIN_DURATION_SECONDS = 24 * 60 * 60


class LoopSignalInterrupt(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = int(signum)
        self.exit_code = 128 + self.signum
        try:
            self.signal_name = signal.Signals(signum).name
        except ValueError:
            self.signal_name = f"signal {signum}"
        super().__init__(self.signal_name)


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

    collect_parser = subparsers.add_parser("collect", help="run enabled server collectors")
    collect_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    collect_parser.add_argument("--job", help="collector job name to run")
    collect_parser.add_argument("--baseline-only", action="store_true", help="store collector state without shop events")
    collect_parser.add_argument(
        "--force-baseline",
        action="store_true",
        help="allow baseline-only to overwrite existing collector shop state",
    )

    seed_collectors_parser = subparsers.add_parser("seed-collectors", help="add default public collector jobs")
    seed_collectors_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)

    run_once_parser = subparsers.add_parser("run-once", help="check sources, seed collectors when needed, collect, and summarize feeds")
    run_once_parser.add_argument("--config", type=Path, default=default_config_path())
    run_once_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    run_once_parser.add_argument("--brands", type=Path, default=default_brand_weights_path())
    run_once_parser.add_argument("--market", type=Path, default=default_market_observations_path())
    run_once_parser.add_argument("--notify", action="store_true", help="send notifications for release/source checks")

    loop_parser = subparsers.add_parser("run-loop", help="run repeated feed checks for long-running operation")
    loop_parser.add_argument("--config", type=Path, default=default_config_path())
    loop_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    loop_parser.add_argument(
        "--cycles",
        type=int,
        default=DEFAULT_LOOP_CYCLES,
        help="number of check cycles; 288 at 5 minutes covers 24h",
    )
    loop_parser.add_argument("--interval-seconds", type=int, default=300)
    loop_parser.add_argument("--notify", action="store_true", help="send notifications during the loop")
    loop_parser.add_argument("--include-collectors", action="store_true", help="seed missing collectors and collect each cycle")
    loop_parser.add_argument("--log-file", type=Path, help="write the loop audit table for later verify-loop audit")
    loop_parser.add_argument("--exit-file", type=Path, help="write the loop exit code for later verify-loop audit")

    verify_loop_parser = subparsers.add_parser("verify-loop", help="verify a long-running check loop log and database")
    verify_loop_parser.add_argument("--config", type=Path, default=default_config_path())
    verify_loop_parser.add_argument("--db", type=Path, required=True)
    verify_loop_parser.add_argument("--log", type=Path, required=True)
    verify_loop_parser.add_argument("--exit-file", type=Path)
    verify_loop_parser.add_argument("--expected-cycles", type=int, default=DEFAULT_LOOP_CYCLES)
    verify_loop_parser.add_argument(
        "--min-duration-seconds",
        type=int,
        default=DEFAULT_LOOP_MIN_DURATION_SECONDS,
        help="minimum elapsed time required in the loop log; default is 86400 seconds",
    )
    verify_loop_parser.add_argument("--json", action="store_true", help="print machine-readable verification output")

    audit_parser = subparsers.add_parser("audit-feed-os", help="audit Feed OS product acceptance evidence")
    audit_parser.add_argument("--config", type=Path, default=default_config_path())
    audit_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    audit_parser.add_argument("--brands", type=Path, default=default_brand_weights_path())
    audit_parser.add_argument("--market", type=Path, default=default_market_observations_path())
    audit_parser.add_argument("--loop-log", type=Path)
    audit_parser.add_argument("--loop-exit-file", type=Path)
    audit_parser.add_argument("--expected-cycles", type=int, default=DEFAULT_LOOP_CYCLES)
    audit_parser.add_argument(
        "--min-duration-seconds",
        type=int,
        default=DEFAULT_LOOP_MIN_DURATION_SECONDS,
        help="minimum elapsed time required when loop evidence is provided",
    )
    audit_parser.add_argument("--json", action="store_true", help="print machine-readable JSON audit output")

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
    if args.command == "collect":
        connection = connect(args.db)
        try:
            if args.baseline_only and not args.force_baseline:
                guard_collector_baseline_only(connection)
            runs = collect_enabled_collectors(connection, job_name=args.job, baseline_only=args.baseline_only)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        finally:
            connection.close()
        print(format_collector_runs(runs))
        return 0 if all(run.ok for run in runs) else 1
    if args.command == "seed-collectors":
        connection = connect(args.db)
        try:
            seeded = seed_default_collectors(connection)
        finally:
            connection.close()
        print(f"seeded_collector_jobs={seeded}")
        return 0
    if args.command == "run-once":
        try:
            summary = run_once(
                config_path=args.config,
                db_path=args.db,
                brands_path=args.brands,
                market_path=args.market,
                notify=args.notify,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(format_run_once_summary(summary))
        return 0 if summary["collector_status_counts"].get("failed", 0) == 0 else 1
    if args.command == "run-loop":
        header = "cycle | checked_at | ok | event_count | error_message"
        loop_started_at = utc_now_iso()
        print(header, flush=True)
        write_loop_log_header(args.log_file, header, loop_started_at)

        def on_loop_result(result: CheckLoopResult) -> None:
            line = format_loop_result_line(result)
            print(line, flush=True)
            append_loop_log_line(args.log_file, line)

        restore_loop_signal_handlers = install_loop_signal_handlers()
        try:
            results = run_check_loop(
                config_path=args.config,
                db_path=args.db,
                cycles=args.cycles,
                interval_seconds=args.interval_seconds,
                notify=args.notify,
                on_result=on_loop_result,
                after_check=build_loop_collector_callback(args.db) if args.include_collectors else None,
            )
        except LoopSignalInterrupt as exc:
            print(f"interrupted by {exc.signal_name}", file=sys.stderr)
            append_loop_log_metadata(args.log_file, "finished_at", utc_now_iso())
            write_exit_file(args.exit_file, exc.exit_code)
            return exc.exit_code
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            append_loop_log_metadata(args.log_file, "finished_at", utc_now_iso())
            write_exit_file(args.exit_file, 130)
            return 130
        finally:
            restore_loop_signal_handlers()
        append_loop_log_metadata(args.log_file, "finished_at", utc_now_iso())
        exit_code = 0 if all(result.ok for result in results) else 1
        write_exit_file(args.exit_file, exit_code)
        return exit_code
    if args.command == "verify-loop":
        verification = verify_check_loop(
            config_path=args.config,
            db_path=args.db,
            log_path=args.log,
            expected_cycles=args.expected_cycles,
            exit_path=args.exit_file,
            min_duration_seconds=args.min_duration_seconds,
        )
        print(format_loop_verification_json(verification) if args.json else format_loop_verification(verification))
        return 0 if verification.complete else 1
    if args.command == "audit-feed-os":
        audit = audit_feed_os(
            config_path=args.config,
            db_path=args.db,
            brands_path=args.brands,
            market_path=args.market,
            loop_log_path=args.loop_log,
            loop_exit_path=args.loop_exit_file,
            expected_cycles=args.expected_cycles,
            min_duration_seconds=args.min_duration_seconds,
        )
        print(format_feed_os_audit_json(audit) if args.json else format_feed_os_audit(audit))
        return 0 if audit.complete else 1
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


def seed_default_collectors(connection) -> int:
    for job in DEFAULT_COLLECTOR_JOBS:
        upsert_collector_job(
            connection,
            name=str(job["name"]),
            collector_type=str(job["collector_type"]),
            url=str(job.get("url") or ""),
            enabled=bool(job.get("enabled", True)),
            options=dict(job.get("options") or {}),
        )
    return len(DEFAULT_COLLECTOR_JOBS)


def ensure_default_collectors(connection) -> int:
    if count_collector_jobs(connection) > 0:
        return 0
    return seed_default_collectors(connection)


def collect_enabled_collectors(connection, job_name: str | None = None, baseline_only: bool = False) -> list[CollectorRun]:
    jobs = list_collector_jobs(connection, enabled_only=True)
    if job_name:
        jobs = [job for job in jobs if job["name"] == job_name]
    runs: list[CollectorRun] = []
    for row in jobs:
        job = CollectorJob(
            name=str(row["name"]),
            collector_type=str(row["collector_type"]),
            url=str(row.get("url") or ""),
            enabled=bool(row.get("enabled", True)),
            options=dict(row.get("options") or {}),
        )
        runs.append(run_collector_job(connection, job, collector_for_type(job.collector_type), baseline_only=baseline_only))
    return runs


def run_once(
    config_path: Path,
    db_path: Path,
    brands_path: Path,
    market_path: Path,
    notify: bool = False,
) -> dict[str, object]:
    release_events = check_sources(config_path=config_path, db_path=db_path, source_name=None, notify=notify)
    connection = connect(db_path)
    try:
        seeded = ensure_default_collectors(connection)
        collector_runs = collect_enabled_collectors(connection)
    finally:
        connection.close()
    feed_state = get_feed_state(config_path=config_path, db_path=db_path, brands_path=brands_path, market_path=market_path)
    streams = feed_state["feed"]["streams"]
    return {
        "release_event_count": len(release_events),
        "seeded_collector_jobs": seeded,
        "feed_counts": {name: len(rows) for name, rows in streams.items()},
        "collector_status_counts": collector_status_counts(collector_runs),
        "collector_runs": collector_runs,
    }


def build_loop_collector_callback(db_path: Path) -> Callable[[], tuple[bool, str]]:
    def collect_for_cycle() -> tuple[bool, str]:
        connection = connect(db_path)
        try:
            ensure_default_collectors(connection)
            runs = collect_enabled_collectors(connection)
        finally:
            connection.close()
        failed = [run for run in runs if not run.ok]
        degraded = [run for run in runs if run.status == "degraded"]
        if failed:
            return False, "failed collectors: " + ", ".join(run.job_name for run in failed)
        if degraded:
            return False, "degraded collectors: " + ", ".join(run.job_name for run in degraded)
        return True, ""

    return collect_for_cycle


def collector_status_counts(runs: list[CollectorRun]) -> dict[str, int]:
    counts = {"ok": 0, "degraded": 0, "failed": 0}
    for run in runs:
        status = run.status if run.status in counts else ("ok" if run.ok else "failed")
        counts[status] += 1
    return counts


def format_run_once_summary(summary: dict[str, object]) -> str:
    feed_counts = summary.get("feed_counts")
    collector_counts = summary.get("collector_status_counts")
    if not isinstance(feed_counts, dict):
        feed_counts = {}
    if not isinstance(collector_counts, dict):
        collector_counts = {}
    lines = [
        "run_once: ok",
        f"release_events={summary.get('release_event_count', 0)}",
        f"seeded_collector_jobs={summary.get('seeded_collector_jobs', 0)}",
        "feed_counts: "
        f"release={feed_counts.get('release', 0)} "
        f"drop={feed_counts.get('drop', 0)} "
        f"trend={feed_counts.get('trend', 0)} "
        f"alert={feed_counts.get('alert', 0)}",
        "collector_counts: "
        f"ok={collector_counts.get('ok', 0)} "
        f"degraded={collector_counts.get('degraded', 0)} "
        f"failed={collector_counts.get('failed', 0)}",
    ]
    return "\n".join(lines)


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


def format_collector_runs(runs: list) -> str:
    if not runs:
        return "collector_runs: []"
    lines = ["job | type | status | item_count | latency_ms | error"]
    for run in runs:
        lines.append(
            f"{run.job_name} | {run.collector_type} | {run.status} | {run.item_count} | {run.latency_ms} | {run.error_message}"
        )
    return "\n".join(lines)


def write_exit_file(path: Path | None, exit_code: int) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{int(exit_code)}\n", encoding="utf-8")


def install_loop_signal_handlers() -> Callable[[], None]:
    previous_handlers: dict[int, signal.Handlers] = {}

    def handle_signal(signum: int, frame: FrameType | None) -> None:
        raise LoopSignalInterrupt(signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[int(signum)] = signal.getsignal(signum)
            signal.signal(signum, handle_signal)
        except (OSError, RuntimeError, ValueError):
            continue

    def restore() -> None:
        for signum, previous_handler in previous_handlers.items():
            try:
                signal.signal(signum, previous_handler)
            except (OSError, RuntimeError, ValueError):
                continue

    return restore


def write_loop_log_header(path: Path | None, header: str, started_at: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# started_at: {started_at}\n{header}\n", encoding="utf-8")


def append_loop_log_line(path: Path | None, line: str) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{line}\n")


def append_loop_log_metadata(path: Path | None, key: str, value: str) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"# {key}: {value}\n")


def format_health_rows(rows: list[dict[str, object]]) -> str:
    lines = ["source | status | ok | error_rate | latency_ms | item_count | event_count | checked_at | error_message"]
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
                    str(row.get("latency_ms", 0)),
                    str(row["item_count"]),
                    str(row["event_count"]),
                    str(row["checked_at"] or "-"),
                    str(row["error_message"] or ""),
                ]
            )
        )
    return "\n".join(lines)


def format_loop_results(results: list[CheckLoopResult]) -> str:
    lines = ["cycle | checked_at | ok | event_count | error_message"]
    for result in results:
        lines.append(format_loop_result_line(result))
    return "\n".join(lines)


def format_loop_result_line(result: CheckLoopResult) -> str:
    return " | ".join(
        [
            str(result.cycle),
            result.checked_at,
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
        f"window_start: {verification.window_start or '-'}",
        f"window_end: {verification.window_end or '-'}",
        f"min_duration_seconds: {verification.min_duration_seconds}",
        f"duration_seconds: {verification.duration_seconds}",
        f"exit_code: {exit_code}",
        "failed_cycles: "
        + (", ".join(str(cycle) for cycle in verification.failed_cycles) if verification.failed_cycles else "[]"),
        "missing_cycles: "
        + (", ".join(str(cycle) for cycle in verification.missing_cycles) if verification.missing_cycles else "[]"),
        "duplicate_cycles: "
        + (", ".join(str(cycle) for cycle in verification.duplicate_cycles) if verification.duplicate_cycles else "[]"),
        "missing_cycle_timestamps: "
        + (
            ", ".join(str(cycle) for cycle in verification.missing_cycle_timestamps)
            if verification.missing_cycle_timestamps
            else "[]"
        ),
        "cycle_time_mismatches: "
        + (
            ", ".join(str(cycle) for cycle in verification.cycle_time_mismatches)
            if verification.cycle_time_mismatches
            else "[]"
        ),
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
    lines.append("source_health:")
    for source in verification.expected_sources:
        summary = verification.source_health_summary.get(source, {})
        lines.append(
            "  - "
            + source
            + ": "
            + ", ".join(
                [
                    f"runs={summary.get('runs', 0)}",
                    f"max_latency_ms={summary.get('max_latency_ms', 0)}",
                    f"min_item_count={summary.get('min_item_count', 0)}",
                    f"max_error_rate={summary.get('max_error_rate', 0)}",
                    f"last_status={summary.get('last_status', '') or '-'}",
                ]
            )
        )
    return "\n".join(lines)


def format_loop_verification_json(verification: CheckLoopVerification) -> str:
    return json.dumps(verification.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def format_feed_os_audit(audit: FeedOsAudit) -> str:
    counts = audit.counts()
    lines = [
        f"status: {audit.status}",
        f"passed: {counts.get('pass', 0)}",
        f"failed: {counts.get('fail', 0)}",
        f"missing: {counts.get('missing', 0)}",
        "checks:",
    ]
    for check in audit.checks:
        lines.append(f"  - {check.status} | {check.name} | {check.detail}")
    return "\n".join(lines)


def format_feed_os_audit_json(audit: FeedOsAudit) -> str:
    return json.dumps(audit.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
