from __future__ import annotations

from typing import Any


def enrich_source_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    recent_by_source: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        recent_by_source.setdefault(str(run.get("source") or ""), []).append(run)
    for run in runs:
        source_runs = recent_by_source.get(str(run.get("source") or ""), [])
        error_rate = source_error_rate(source_runs)
        status = source_status(run, error_rate)
        enriched.append({**run, "status": status, "error_rate": error_rate})
    return enriched


def source_error_rate(runs: list[dict[str, Any]]) -> float:
    if not runs:
        return 0.0
    failed = sum(1 for run in runs if not bool(run.get("ok")))
    return round(failed / len(runs), 4)


def source_status(run: dict[str, Any], error_rate: float) -> str:
    if not bool(run.get("ok")):
        return "failed"
    if error_rate >= 0.25 or int(run.get("item_count") or 0) == 0:
        return "degraded"
    return "ok"
