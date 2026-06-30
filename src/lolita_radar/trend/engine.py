from __future__ import annotations

import math
from typing import Any


TREND_DIRECTIONS = {"rising", "stable", "cooling"}
RELEASE_STATUSES = {"new_arrival", "preorder", "restock"}


def build_trend_feed(
    market_summary: dict[str, Any],
    momentum: list[dict[str, Any]],
    events: list[dict[str, Any]],
    brand_weights: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    momentum_by_alias = {alias_key(row.get("brand_alias")): row for row in momentum}
    release_counts = count_release_events(events)
    trends = []
    brands = trend_brand_rows(market_summary, brand_weights or [])
    for brand in brands:
        alias = str(brand.get("brand_alias") or "").strip()
        if not alias:
            continue
        sample_count = safe_count(brand.get("sample_count"))
        avg_premium = safe_float(brand.get("avg_premium_rate"))
        movement = momentum_by_alias.get(alias_key(alias), {})
        direction = normalize_direction(movement.get("direction"), avg_premium, sample_count)
        release_count = release_counts.get(alias_key(alias), 0)
        confidence = trend_confidence(sample_count, avg_premium, movement, release_count)
        reasons = trend_reasons(sample_count, avg_premium, movement, release_count)
        trends.append(
            {
                "id": f"trend:{alias}",
                "feed_type": "trend",
                "kind": direction,
                "trend": direction,
                "brand": alias,
                "title": f"{alias} {direction}",
                "meta": trend_meta(avg_premium, sample_count, reasons),
                "time": str(movement.get("observed_at") or ""),
                "url": str(brand.get("url") or ""),
                "confidence": confidence,
                "avg_premium_rate": round(avg_premium, 4),
                "price_delta": round(avg_premium, 4),
                "sample_count": sample_count,
                "reason_codes": reasons,
                "visual": trend_visual(alias, direction),
            }
        )
    return sorted(trends, key=lambda row: (int(row["confidence"]), float(row.get("avg_premium_rate") or 0)), reverse=True)


def trend_brand_rows(market_summary: dict[str, Any], brand_weights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weights_by_alias = {alias_key(row.get("alias")): row for row in brand_weights}
    rows = []
    for row in market_summary.get("brands", []):
        raw_alias = str(row.get("brand_alias") or "").strip()
        weight = weights_by_alias.get(alias_key(raw_alias), {})
        alias = str(weight.get("alias") or raw_alias).strip()
        if not alias:
            continue
        rows.append({**row, "brand_alias": alias, "url": str(row.get("url") or primary_watch_url(weight))})
    seen = {alias_key(row.get("brand_alias")) for row in rows}
    for brand in brand_weights:
        alias = str(brand.get("alias") or "").strip()
        key = alias_key(alias)
        if not alias or key in seen:
            continue
        rows.append(
            {
                "brand_alias": alias,
                "sample_count": 0,
                "avg_premium_rate": 0,
                "max_premium_rate": 0,
                "url": primary_watch_url(brand),
            }
        )
    return rows


def alias_key(value: object) -> str:
    return str(value or "").strip().casefold()


def primary_watch_url(brand: dict[str, Any]) -> str:
    watch_urls = brand.get("watch_urls")
    if not isinstance(watch_urls, list):
        return ""
    for row in watch_urls:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        if url.startswith(("http://", "https://")):
            return url
    return ""


def safe_count(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def safe_float(value: object) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


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
        if alias and event.get("status") in RELEASE_STATUSES:
            key = alias_key(alias)
            counts[key] = counts.get(key, 0) + 1
    return counts


def normalize_direction(raw_direction: object, avg_premium: float, sample_count: int) -> str:
    direction = str(raw_direction or "").strip().lower()
    if direction in TREND_DIRECTIONS:
        return direction
    return trend_direction(avg_premium, sample_count)


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


def trend_meta(avg_premium: float, sample_count: int, reasons: list[str]) -> str:
    reason_text = ", ".join(reasons[:3])
    parts = [f"{format_percent(avg_premium)} avg premium", f"{sample_count} samples"]
    if reason_text:
        parts.append(f"reason: {reason_text}")
    return " · ".join(parts)


def trend_visual(alias: str, direction: str) -> dict[str, str]:
    return {
        "initials": (alias[:2] or "TR").upper(),
        "mark": "T",
        "tone": direction or "trend",
        "image_url": "",
    }
