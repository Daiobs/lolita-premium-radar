from __future__ import annotations

from typing import Any


def build_trend_feed(
    market_summary: dict[str, Any],
    momentum: list[dict[str, Any]],
    events: list[dict[str, Any]],
    brand_weights: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    momentum_by_alias = {str(row.get("brand_alias") or ""): row for row in momentum}
    release_counts = count_release_events(events)
    trends = []
    brands = trend_brand_rows(market_summary, brand_weights or [])
    for brand in brands:
        alias = str(brand.get("brand_alias") or "")
        if not alias:
            continue
        sample_count = int(brand.get("sample_count") or 0)
        avg_premium = float(brand.get("avg_premium_rate") or 0)
        movement = momentum_by_alias.get(alias, {})
        direction = str(movement.get("direction") or trend_direction(avg_premium, sample_count))
        confidence = trend_confidence(sample_count, avg_premium, movement, release_counts.get(alias, 0))
        trends.append(
            {
                "id": f"trend:{alias}",
                "feed_type": "trend",
                "kind": direction,
                "brand": alias,
                "title": f"{alias} {direction}",
                "meta": f"{format_percent(avg_premium)} avg premium · {sample_count} samples",
                "time": str(movement.get("observed_at") or ""),
                "url": str(brand.get("url") or ""),
                "confidence": confidence,
                "reason_codes": trend_reasons(sample_count, avg_premium, movement, release_counts.get(alias, 0)),
            }
        )
    return sorted(trends, key=lambda row: (int(row["confidence"]), float(row.get("avg_premium_rate") or 0)), reverse=True)


def trend_brand_rows(market_summary: dict[str, Any], brand_weights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(market_summary.get("brands", []))
    seen = {str(row.get("brand_alias") or "") for row in rows}
    for brand in brand_weights:
        alias = str(brand.get("alias") or "")
        if not alias or alias in seen:
            continue
        rows.append(
            {
                "brand_alias": alias,
                "sample_count": 0,
                "avg_premium_rate": 0,
                "max_premium_rate": 0,
                "url": "",
            }
        )
    return rows


def count_release_events(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    aliases = {
        "angelic_pretty": "AP",
        "baby_ssb": "BABY",
        "alice_and_the_pirates": "AATP",
        "metamorphose": "Meta",
        "moitie": "MMM",
    }
    for event in events:
        source = str(event.get("source") or "")
        alias = aliases.get(source)
        if alias:
            counts[alias] = counts.get(alias, 0) + 1
    return counts


def trend_direction(avg_premium: float, sample_count: int) -> str:
    if sample_count < 2:
        return "stable"
    if avg_premium >= 0.35:
        return "rising"
    if avg_premium < -0.05:
        return "cooling"
    return "stable"


def trend_confidence(sample_count: int, avg_premium: float, movement: dict[str, Any], release_count: int) -> int:
    sample_points = min(45, sample_count * 12)
    premium_points = min(30, round(abs(avg_premium) * 60))
    movement_points = 15 if movement else 0
    release_points = min(10, release_count * 5)
    return max(0, min(100, sample_points + premium_points + movement_points + release_points))


def trend_reasons(
    sample_count: int,
    avg_premium: float,
    movement: dict[str, Any],
    release_count: int,
) -> list[str]:
    reasons = []
    if sample_count < 2:
        reasons.append("sample_gap")
    else:
        reasons.append("sample_supported")
    if avg_premium >= 0.35:
        reasons.append("premium_rising")
    elif avg_premium < -0.05:
        reasons.append("premium_cooling")
    else:
        reasons.append("premium_stable")
    if movement:
        reasons.append("momentum_observed")
    if release_count:
        reasons.append("release_activity")
    return reasons


def format_percent(value: float) -> str:
    return f"{round(value * 100)}%"
