from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from ..source_dates import current_source_date, is_current_source_date


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
        if sample_count < 2:
            continue
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
                "title": f"{alias} asking price trend {direction}",
                "title_zh": f"{alias} 挂价趋势 {direction}",
                "title_ja": f"{alias} 出品価格トレンド {direction}",
                "use_localized_title": True,
                "meta": "asking price trend",
                "trend_basis": "asking_price",
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


def build_market_sample_trends(samples: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    if today is None:
        today = current_source_date()
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for sample in samples:
        key = (
            str(sample.get("brand_alias") or "").strip(),
            str(sample.get("pattern") or "").strip(),
            str(sample.get("platform") or "").strip(),
        )
        if not all(key):
            continue
        grouped.setdefault(key, []).append(sample)

    trends = []
    for (brand, pattern, platform), rows in grouped.items():
        current_rows = [row for row in rows if in_window(row.get("observed_at"), today - timedelta(days=6), today)]
        previous_rows = [row for row in rows if in_window(row.get("observed_at"), today - timedelta(days=13), today - timedelta(days=7))]
        if not current_rows:
            continue
        current_prices = [safe_float(row.get("asking_price")) for row in current_rows if safe_float(row.get("asking_price")) > 0]
        previous_prices = [safe_float(row.get("asking_price")) for row in previous_rows if safe_float(row.get("asking_price")) > 0]
        if not current_prices:
            continue
        current_median = median(current_prices)
        previous_median = median(previous_prices) if previous_prices else current_median
        delta = 0.0 if previous_median <= 0 else (current_median - previous_median) / previous_median
        direction = sample_trend_direction(delta)
        sample_count = len(current_prices)
        confidence = sample_trend_confidence(sample_count, previous_prices, delta)
        reasons = sample_trend_reasons(sample_count, previous_prices, delta)
        latest = sorted(current_rows, key=lambda row: str(row.get("observed_at") or ""), reverse=True)[0]
        trends.append(
            {
                "id": f"trend:{brand}:{pattern}:{platform}",
                "feed_type": "trend",
                "kind": direction,
                "trend": direction,
                "brand": brand,
                "pattern": pattern,
                "platform": platform,
                "title": f"{brand} {pattern} asking price trend {direction}",
                "title_zh": f"{brand} {pattern} 挂价趋势 {direction}",
                "title_ja": f"{brand} {pattern} 出品価格トレンド {direction}",
                "use_localized_title": True,
                "meta": " · ".join(part for part in ("asking price trend", platform) if part),
                "trend_basis": "asking_price",
                "time": str(latest.get("observed_at") or ""),
                "url": str(latest.get("url") or ""),
                "confidence": confidence,
                "avg_premium_rate": round(delta, 4),
                "price_delta": round(delta, 4),
                "sample_count": sample_count,
                "reason_codes": reasons,
                "visual": trend_visual(brand, direction),
            }
        )
    return sorted(trends, key=lambda row: (int(row["confidence"]), abs(float(row.get("price_delta") or 0))), reverse=True)


def trend_brand_rows(market_summary: dict[str, Any], brand_weights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weights_by_alias = {alias_key(row.get("alias")): row for row in brand_weights}
    record_urls_by_alias = market_record_urls(market_summary)
    rows = []
    for row in market_summary.get("brands", []):
        raw_alias = str(row.get("brand_alias") or "").strip()
        weight = weights_by_alias.get(alias_key(raw_alias), {})
        alias = str(weight.get("alias") or raw_alias).strip()
        if not alias:
            continue
        rows.append({**row, "brand_alias": alias, "url": str(row.get("url") or record_urls_by_alias.get(alias_key(alias), ""))})
    return rows


def market_record_urls(market_summary: dict[str, Any]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for record in market_summary.get("records", []):
        alias = alias_key(record.get("brand_alias"))
        url = str(record.get("url") or "")
        if alias and url.startswith(("http://", "https://")) and alias not in urls:
            urls[alias] = url
    return urls


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
        if alias and event.get("status") in RELEASE_STATUSES and is_current_release_event(event):
            key = alias_key(alias)
            counts[key] = counts.get(key, 0) + 1
    return counts


def is_current_release_event(event: dict[str, Any]) -> bool:
    return is_current_source_date(str(event.get("published_at") or ""))


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


def trend_visual(alias: str, direction: str) -> dict[str, str]:
    return {
        "initials": (alias[:2] or "TR").upper(),
        "mark": "T",
        "tone": direction or "trend",
        "image_url": "",
    }


def parse_sample_date(value: object) -> date | None:
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def in_window(value: object, start: date, end: date) -> bool:
    parsed = parse_sample_date(value)
    return parsed is not None and start <= parsed <= end


def sample_trend_direction(delta: float) -> str:
    if delta >= 0.15:
        return "rising"
    if delta <= -0.15:
        return "cooling"
    return "stable"


def sample_trend_confidence(sample_count: int, previous_prices: list[float], delta: float) -> int:
    sample_points = min(60, sample_count * 15)
    previous_points = 20 if previous_prices else 0
    movement_points = min(20, round(abs(delta) * 60))
    if sample_count < 3:
        return min(49, sample_points + previous_points + movement_points)
    return min(100, sample_points + previous_points + movement_points)


def sample_trend_reasons(sample_count: int, previous_prices: list[float], delta: float) -> list[str]:
    reasons = ["sample_supported" if sample_count >= 3 else "low_confidence"]
    if previous_prices:
        reasons.append("previous_window")
    reasons.append(
        "premium_rising"
        if delta >= 0.15
        else "premium_cooling"
        if delta <= -0.15
        else "premium_stable"
    )
    return reasons
