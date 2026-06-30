from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Callable

from .adapters import (
    AliceAndThePiratesAdapter,
    AngelicPrettyAdapter,
    BabySsbAdapter,
    GenericPageAdapter,
    InnocentWorldAdapter,
    MetamorphoseAdapter,
    MoitieAdapter,
    SourceAdapter,
    SourceConfig,
)
from .config import load_sources
from .crawler import enrich_source_runs
from .models import RadarEvent, RadarItem, utc_now_iso
from .notifiers import build_notifiers_from_env, notify_all
from .storage import (
    connect,
    count_items_for_sources,
    diff_and_store,
    list_latest_source_runs,
    list_source_runs,
    record_source_run,
)


ADAPTERS: dict[str, type[SourceAdapter]] = {
    "alice_and_the_pirates": AliceAndThePiratesAdapter,
    "angelic_pretty": AngelicPrettyAdapter,
    "baby_ssb": BabySsbAdapter,
    "metamorphose": MetamorphoseAdapter,
    "generic_page": GenericPageAdapter,
    "innocent_world": InnocentWorldAdapter,
    "moitie": MoitieAdapter,
}


@dataclass(frozen=True)
class InspectResult:
    source: SourceConfig
    ok: bool
    items: list[RadarItem]
    error_message: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckLoopResult:
    cycle: int
    ok: bool
    event_count: int
    error_message: str = ""
    checked_at: str = ""


@dataclass(frozen=True)
class CheckLoopVerification:
    status: str
    complete: bool
    expected_cycles: int
    observed_cycles: int
    window_start: str
    window_end: str
    min_duration_seconds: int
    duration_seconds: int
    failed_cycles: tuple[int, ...]
    missing_cycles: tuple[int, ...]
    duplicate_cycles: tuple[int, ...]
    exit_code: int | None
    expected_sources: tuple[str, ...]
    source_cycle_counts: dict[str, int]
    unhealthy_source_runs: dict[str, int]
    source_health_summary: dict[str, dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "complete": self.complete,
            "expected_cycles": self.expected_cycles,
            "observed_cycles": self.observed_cycles,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "min_duration_seconds": self.min_duration_seconds,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "failed_cycles": list(self.failed_cycles),
            "missing_cycles": list(self.missing_cycles),
            "duplicate_cycles": list(self.duplicate_cycles),
            "expected_sources": list(self.expected_sources),
            "source_cycle_counts": self.source_cycle_counts,
            "unhealthy_source_runs": self.unhealthy_source_runs,
            "source_health_summary": self.source_health_summary,
        }


def build_adapter(config: SourceConfig) -> SourceAdapter:
    adapter_cls = ADAPTERS.get(config.type)
    if adapter_cls is None:
        raise ValueError(f"Unknown source type for {config.name}: {config.type}")
    return adapter_cls(config)


def check_sources(
    config_path: Path,
    db_path: Path,
    source_name: str | None = None,
    notify: bool = True,
    baseline_only: bool = False,
    force_baseline: bool = False,
) -> list[RadarEvent]:
    sources = load_sources(config_path)
    selected = select_sources(sources, source_name)
    connection = connect(db_path)
    try:
        if baseline_only and not force_baseline:
            guard_baseline_only(connection, selected)
        all_events: list[RadarEvent] = []
        for source in selected:
            started_at = time.monotonic()
            try:
                adapter = build_adapter(source)
                items = adapter.fetch_items()
                events = diff_and_store(connection, items, write_events=not baseline_only)
            except Exception as exc:
                record_source_run(
                    connection,
                    source.name,
                    ok=False,
                    status="failed",
                    error_rate=1.0,
                    latency_ms=elapsed_ms(started_at),
                    error_message=str(exc),
                )
                connection.commit()
                if source_name:
                    raise
                continue
            status = "ok" if items else "degraded"
            record_source_run(
                connection,
                source.name,
                ok=True,
                status=status,
                error_rate=0.0,
                latency_ms=elapsed_ms(started_at),
                item_count=len(items),
                event_count=len(events),
            )
            connection.commit()
            all_events.extend(events)
    finally:
        connection.close()
    if notify and not baseline_only:
        notify_all(build_notifiers_from_env(), all_events)
    return all_events


def guard_baseline_only(connection, selected: list[SourceConfig]) -> None:
    existing = {
        source: count
        for source, count in count_items_for_sources(connection, [source.name for source in selected]).items()
        if count > 0
    }
    if existing:
        sources = ", ".join(f"{source}({count})" for source, count in sorted(existing.items()))
        raise ValueError(
            "baseline-only is intended for first deployment; use --force-baseline to overwrite existing tracked state. "
            f"Existing tracked sources: {sources}"
        )


def inspect_sources(config_path: Path, source_name: str | None = None) -> list[InspectResult]:
    sources = load_sources(config_path)
    selected = select_sources(sources, source_name)
    results: list[InspectResult] = []
    for source in selected:
        try:
            adapter = build_adapter(source)
            items = adapter.fetch_items()
        except Exception as exc:
            results.append(
                InspectResult(
                    source=source,
                    ok=False,
                    items=[],
                    error_message=str(exc),
                    warnings=(f"fetch failed: {exc}",),
                )
            )
            continue
        results.append(InspectResult(source=source, ok=True, items=items, warnings=inspect_warnings(items)))
    return results


def inspect_warnings(items: list[RadarItem]) -> tuple[str, ...]:
    warnings: list[str] = []
    if not items:
        warnings.append("empty result")
    missing_date_count = sum(1 for item in items if not item.published_at)
    if missing_date_count:
        warnings.append(f"missing dates: {missing_date_count} item(s)")
    navigation_tokens = ("login", "cart", "privacy", "contact", "shop list", "company", "account")
    possible_navigation = [
        item.title
        for item in items
        if any(token in f"{item.title} {item.url}".lower() for token in navigation_tokens)
    ]
    if possible_navigation:
        warnings.append(f"possible navigation links: {len(possible_navigation)}")
    return tuple(warnings)


def latest_source_health(config_path: Path, db_path: Path) -> list[dict[str, object]]:
    sources = load_sources(config_path)
    connection = connect(db_path)
    try:
        latest = latest_enriched_source_runs(list_source_runs(connection, limit=100))
    finally:
        connection.close()
    rows: list[dict[str, object]] = []
    for source in sources.values():
        run = latest.get(source.name)
        rows.append(
            {
                "source": source.name,
                "enabled": source.enabled,
                "ok": run["ok"] if run else None,
                "status": run["status"] if run else "no_run",
                "error_rate": run["error_rate"] if run else 0,
                "latency_ms": run["latency_ms"] if run else 0,
                "item_count": run["item_count"] if run else 0,
                "event_count": run["event_count"] if run else 0,
                "checked_at": run["checked_at"] if run else "",
                "error_message": run["error_message"] if run else "no run recorded",
            }
        )
    return rows


def latest_enriched_source_runs(runs: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for run in enrich_source_runs(runs):
        source = str(run.get("source") or "")
        if source and source not in latest:
            latest[source] = run
    return latest


def elapsed_ms(started_at: float) -> int:
    return max(0, int(round((time.monotonic() - started_at) * 1000)))


def run_check_loop(
    config_path: Path,
    db_path: Path,
    cycles: int = 288,
    interval_seconds: int = 300,
    notify: bool = False,
    on_result: Callable[[CheckLoopResult], None] | None = None,
) -> list[CheckLoopResult]:
    total_cycles = max(1, int(cycles))
    sleep_seconds = max(0, int(interval_seconds))
    results: list[CheckLoopResult] = []
    for index in range(total_cycles):
        cycle = index + 1
        checked_at = utc_now_iso()
        try:
            events = check_sources(config_path=config_path, db_path=db_path, source_name=None, notify=notify)
        except Exception as exc:
            result = CheckLoopResult(cycle=cycle, ok=False, event_count=0, error_message=str(exc), checked_at=checked_at)
        else:
            unhealthy_sources = latest_unhealthy_sources(config_path, db_path)
            if unhealthy_sources:
                result = CheckLoopResult(
                    cycle=cycle,
                    ok=False,
                    event_count=len(events),
                    error_message="unhealthy sources: " + ", ".join(unhealthy_sources),
                    checked_at=checked_at,
                )
            else:
                result = CheckLoopResult(cycle=cycle, ok=True, event_count=len(events), checked_at=checked_at)
        results.append(result)
        if on_result is not None:
            on_result(result)
        if cycle < total_cycles and sleep_seconds:
            time.sleep(sleep_seconds)
    return results


def latest_unhealthy_sources(config_path: Path, db_path: Path) -> list[str]:
    expected_sources = {source.name for source in select_sources(load_sources(config_path), None)}
    connection = connect(db_path)
    try:
        rows = list_latest_source_runs(connection)
    finally:
        connection.close()
    return [
        str(row["source"])
        for row in rows
        if str(row.get("source") or "") in expected_sources and is_unhealthy_source_run(row)
    ]


def verify_check_loop(
    config_path: Path,
    db_path: Path,
    log_path: Path,
    expected_cycles: int,
    exit_path: Path | None = None,
    min_duration_seconds: int = 0,
) -> CheckLoopVerification:
    expected = max(1, int(expected_cycles))
    min_duration = max(0, int(min_duration_seconds))
    results = parse_check_loop_log(log_path)
    duration_seconds = loop_log_duration_seconds(log_path)
    window_start, window_end = loop_log_window(log_path)
    failed_cycles = tuple(result.cycle for result in results if not result.ok)
    observed_cycle_numbers = {result.cycle for result in results}
    missing_cycles = tuple(cycle for cycle in range(1, expected + 1) if cycle not in observed_cycle_numbers)
    duplicate_cycles = duplicate_cycle_numbers(results)
    sources = tuple(source.name for source in select_sources(load_sources(config_path), None))
    source_runs = recent_source_runs_by_source(db_path, sources, expected, window_start=window_start, window_end=window_end)
    source_cycle_counts = {source: len(source_runs.get(source, [])) for source in sources}
    unhealthy_source_runs = count_unhealthy_source_runs(source_runs)
    source_health_summary = summarize_source_runs(source_runs)
    exit_code = read_exit_code(exit_path) if exit_path else None
    enough_log_cycles = len(results) >= expected
    enough_duration = min_duration == 0 or duration_seconds >= min_duration
    enough_source_runs = all(source_cycle_counts.get(source, 0) >= expected for source in sources)
    healthy_source_runs = not unhealthy_source_runs
    complete = (
        exit_code == 0
        and enough_log_cycles
        and enough_duration
        and not missing_cycles
        and not duplicate_cycles
        and not failed_cycles
        and enough_source_runs
        and healthy_source_runs
    )
    if complete:
        status = "complete"
    elif exit_code not in (None, 0) or failed_cycles or unhealthy_source_runs:
        status = "failed"
    else:
        status = "incomplete"
    return CheckLoopVerification(
        status=status,
        complete=complete,
        expected_cycles=expected,
        observed_cycles=len(results),
        window_start=format_datetime(window_start),
        window_end=format_datetime(window_end),
        min_duration_seconds=min_duration,
        duration_seconds=duration_seconds,
        failed_cycles=failed_cycles,
        missing_cycles=missing_cycles,
        duplicate_cycles=duplicate_cycles,
        exit_code=exit_code,
        expected_sources=sources,
        source_cycle_counts=source_cycle_counts,
        unhealthy_source_runs=unhealthy_source_runs,
        source_health_summary=source_health_summary,
    )


def duplicate_cycle_numbers(results: list[CheckLoopResult]) -> tuple[int, ...]:
    seen = set()
    duplicates = []
    for result in results:
        if result.cycle in seen and result.cycle not in duplicates:
            duplicates.append(result.cycle)
        seen.add(result.cycle)
    return tuple(duplicates)


def parse_check_loop_log(path: Path) -> list[CheckLoopResult]:
    if not path.exists():
        return []
    results: list[CheckLoopResult] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        cycle = int(parts[0])
        if len(parts) >= 4 and parts[2] in {"ok", "failed"}:
            checked_at = parts[1]
            ok_index = 2
            event_count_index = 3
            error_index = 4
        else:
            checked_at = ""
            ok_index = 1
            event_count_index = 2
            error_index = 3
        ok = parts[ok_index] == "ok"
        try:
            event_count = int(parts[event_count_index])
        except ValueError:
            event_count = 0
        error_message = parts[error_index] if len(parts) > error_index else ""
        results.append(
            CheckLoopResult(
                cycle=cycle,
                ok=ok,
                event_count=event_count,
                error_message=error_message,
                checked_at=checked_at,
            )
        )
    return results


def loop_log_duration_seconds(path: Path) -> int:
    started_at, finished_at = loop_log_window(path)
    if started_at is None or finished_at is None:
        return 0
    seconds = int((finished_at - started_at).total_seconds())
    return max(0, seconds)


def loop_log_window(path: Path) -> tuple[datetime | None, datetime | None]:
    metadata = parse_check_loop_metadata(path)
    started_at = parse_iso_datetime(metadata.get("started_at", ""))
    finished_at = parse_iso_datetime(metadata.get("finished_at", ""))
    return started_at, finished_at


def parse_check_loop_metadata(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    metadata: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("#"):
            continue
        raw = line.lstrip("#").strip()
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def recent_source_runs_by_source(
    db_path: Path,
    sources: tuple[str, ...],
    limit_per_source: int,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, list[dict[str, object]]]:
    connection = connect(db_path)
    try:
        runs: dict[str, list[dict[str, object]]] = {source: [] for source in sources}
        limit = max(1, int(limit_per_source))
        for source in sources:
            rows = connection.execute(
                """
                SELECT source, checked_at, ok, status, error_rate, latency_ms, item_count, event_count, error_message
                FROM source_runs
                WHERE source = ?
                ORDER BY checked_at DESC, id DESC
                """,
                (source,),
            ).fetchall()
            filtered_rows = [
                {**dict(row), "ok": bool(row["ok"])}
                for row in rows
                if source_run_in_window(str(row["checked_at"] or ""), window_start, window_end)
            ]
            runs[source] = filtered_rows[:limit]
        return runs
    finally:
        connection.close()


def source_run_in_window(checked_at: str, window_start: datetime | None, window_end: datetime | None) -> bool:
    if window_start is None or window_end is None:
        return True
    checked = parse_iso_datetime(checked_at)
    if checked is None:
        return False
    return window_start <= checked <= window_end


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def count_unhealthy_source_runs(source_runs: dict[str, list[dict[str, object]]]) -> dict[str, int]:
    counts = {}
    for source, runs in source_runs.items():
        count = sum(1 for run in runs if is_unhealthy_source_run(run))
        if count:
            counts[source] = count
    return counts


def summarize_source_runs(source_runs: dict[str, list[dict[str, object]]]) -> dict[str, dict[str, object]]:
    summary: dict[str, dict[str, object]] = {}
    for source, runs in source_runs.items():
        summary[source] = {
            "runs": len(runs),
            "max_latency_ms": max_int(run.get("latency_ms") for run in runs),
            "min_item_count": min_int(run.get("item_count") for run in runs),
            "max_error_rate": max_float(run.get("error_rate") for run in runs),
            "last_status": str(runs[0].get("status") or "") if runs else "",
        }
    return summary


def max_int(values) -> int:
    numbers = [safe_int(value) for value in values]
    return max(numbers) if numbers else 0


def min_int(values) -> int:
    numbers = [safe_int(value) for value in values]
    return min(numbers) if numbers else 0


def max_float(values) -> float:
    numbers = [safe_float(value) for value in values]
    return round(max(numbers), 4) if numbers else 0.0


def safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def is_unhealthy_source_run(run: dict[str, object]) -> bool:
    return not bool(run.get("ok")) or str(run.get("status") or "") in {"failed", "degraded"}


def read_exit_code(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return -1


def select_sources(sources: dict[str, SourceConfig], source_name: str | None) -> list[SourceConfig]:
    if source_name:
        source = sources.get(source_name)
        if source is None:
            raise ValueError(f"Source not found: {source_name}")
        return [source]
    return [source for source in sources.values() if source.enabled]
