from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_market_observations_path() -> Path:
    return Path("config") / "market_observations.json"


def load_market_observations(path: Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        path = default_market_observations_path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError("market_observations.json must contain a list")
    return [entry for entry in (normalize_market_observation(item) for item in raw) if entry]


def append_market_observation(path: Path | None, raw: dict[str, Any]) -> dict[str, Any]:
    if path is None:
        path = default_market_observations_path()
    observation = normalize_market_observation(raw)
    if observation is None:
        raise ValueError("brand_alias, item_name, retail_price, and resale_price are required")
    observations = load_market_observations(path)
    observations.append(observation)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return observation


def normalize_market_observation(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    brand_alias = text(raw.get("brand_alias")).upper()
    item_name = text(raw.get("item_name"))
    retail_price = positive_float(raw.get("retail_price"))
    resale_price = positive_float(raw.get("resale_price"))
    if not brand_alias or not item_name or retail_price <= 0 or resale_price <= 0:
        return None
    premium_rate = (resale_price - retail_price) / retail_price
    return {
        "brand_alias": brand_alias,
        "item_name": item_name,
        "retail_price": retail_price,
        "resale_price": resale_price,
        "premium_rate": round(premium_rate, 4),
        "currency": text(raw.get("currency")) or "CNY",
        "condition": text(raw.get("condition")),
        "source": text(raw.get("source")),
        "url": text(raw.get("url")),
        "observed_at": text(raw.get("observed_at")),
        "notes": text(raw.get("notes")),
    }


def summarize_market_observations(
    observations: list[dict[str, Any]],
    brand_weights: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    weight_by_alias = {
        text(brand.get("alias")).upper(): clamp_int(brand.get("weight"), default=50)
        for brand in brand_weights or []
    }
    brand_rows: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        brand_rows.setdefault(observation["brand_alias"], []).append(observation)
    brands = []
    for alias, rows in brand_rows.items():
        premium_rates = [float(row["premium_rate"]) for row in rows]
        resale_prices = [float(row["resale_price"]) for row in rows]
        retail_prices = [float(row["retail_price"]) for row in rows]
        avg_premium_rate = sum(premium_rates) / len(premium_rates)
        brand_weight = weight_by_alias.get(alias, 50)
        brands.append(
            {
                "brand_alias": alias,
                "sample_count": len(rows),
                "avg_premium_rate": round(avg_premium_rate, 4),
                "max_premium_rate": round(max(premium_rates), 4),
                "avg_retail_price": round(sum(retail_prices) / len(retail_prices), 2),
                "avg_resale_price": round(sum(resale_prices) / len(resale_prices), 2),
                "brand_weight": brand_weight,
                "priority_score": premium_priority_score(avg_premium_rate, brand_weight, len(rows)),
                "currency": rows[0].get("currency") or "CNY",
            }
        )
    records = []
    for observation in observations:
        brand_weight = weight_by_alias.get(observation["brand_alias"], 50)
        records.append(
            {
                **observation,
                "brand_weight": brand_weight,
                "priority_score": premium_priority_score(float(observation["premium_rate"]), brand_weight, 1),
            }
        )
    return {
        "sample_count": len(observations),
        "brands": sorted(
            brands,
            key=lambda row: (int(row["priority_score"]), float(row["avg_premium_rate"]), int(row["sample_count"])),
            reverse=True,
        ),
        "records": sorted(records, key=lambda row: (int(row["priority_score"]), float(row["premium_rate"])), reverse=True)[:20],
    }


def build_opportunity_radar(
    brand_weights: list[dict[str, Any]],
    market_brands: list[dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    market_by_alias = {text(row.get("brand_alias")).upper(): row for row in market_brands}
    opportunities = []
    for brand in brand_weights:
        alias = text(brand.get("alias"))
        if not alias:
            continue
        market = market_by_alias.get(alias.upper(), {})
        sample_count = clamp_int(market.get("sample_count"), default=0)
        avg_premium_rate = float(market.get("avg_premium_rate") or 0)
        max_premium_rate = float(market.get("max_premium_rate") or 0)
        weight = clamp_int(brand.get("weight"), default=50)
        score = premium_priority_score(avg_premium_rate, weight, sample_count)
        band = opportunity_band(score, avg_premium_rate, sample_count, weight)
        opportunities.append(
            {
                "name": text(brand.get("name")) or alias,
                "alias": alias,
                "tier": text(brand.get("tier")) or "watch",
                "style": text(brand.get("style")) or "general",
                "brand_weight": weight,
                "sample_count": sample_count,
                "avg_premium_rate": round(avg_premium_rate, 4),
                "max_premium_rate": round(max_premium_rate, 4),
                "priority_score": score,
                "score_breakdown": premium_score_breakdown(avg_premium_rate, weight, sample_count),
                "band": band,
                "reason_codes": opportunity_reasons(avg_premium_rate, sample_count, weight),
            }
        )
    return sorted(
        opportunities,
        key=lambda row: (
            int(row["priority_score"]),
            int(row["brand_weight"]),
            int(row["sample_count"]),
            float(row["avg_premium_rate"]),
        ),
        reverse=True,
    )[:limit]


def build_pattern_radar(
    brand_weights: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    limit: int = 12,
) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []
    for brand in brand_weights:
        alias = text(brand.get("alias"))
        if not alias:
            continue
        weight = clamp_int(brand.get("weight"), default=50)
        for keyword in brand.get("market_keywords") or []:
            term = text(keyword)
            if not term:
                continue
            rows = [
                row for row in observations
                if text(row.get("brand_alias")).upper() == alias.upper()
                and keyword_in_observation(term, row)
            ]
            premium_rates = [float(row["premium_rate"]) for row in rows]
            avg_premium_rate = sum(premium_rates) / len(premium_rates) if premium_rates else 0.0
            score = premium_priority_score(avg_premium_rate, weight, len(rows))
            patterns.append(
                {
                    "name": text(brand.get("name")) or alias,
                    "alias": alias,
                    "keyword": term,
                    "brand_weight": weight,
                    "sample_count": len(rows),
                    "avg_premium_rate": round(avg_premium_rate, 4),
                    "max_premium_rate": round(max(premium_rates), 4) if premium_rates else 0,
                    "priority_score": score,
                    "band": opportunity_band(score, avg_premium_rate, len(rows), weight),
                    "reason_codes": opportunity_reasons(avg_premium_rate, len(rows), weight),
                    "evidence": pattern_evidence(rows),
                }
            )
    return sorted(
        patterns,
        key=lambda row: (
            int(row["sample_count"] > 0),
            int(row["priority_score"]),
            float(row["avg_premium_rate"]),
            int(row["brand_weight"]),
        ),
        reverse=True,
    )[:limit]


def keyword_in_observation(keyword: str, observation: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            text(observation.get("item_name")),
            text(observation.get("notes")),
            text(observation.get("source")),
            text(observation.get("url")),
        ]
    ).casefold()
    return keyword.casefold() in haystack


def pattern_evidence(rows: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    sorted_rows = sorted(rows, key=lambda row: float(row.get("premium_rate") or 0), reverse=True)
    return [
        {
            "item_name": text(row.get("item_name")),
            "premium_rate": float(row.get("premium_rate") or 0),
            "retail_price": float(row.get("retail_price") or 0),
            "resale_price": float(row.get("resale_price") or 0),
            "currency": text(row.get("currency")) or "CNY",
            "source": text(row.get("source")),
            "url": text(row.get("url")),
            "observed_at": text(row.get("observed_at")),
            "notes": text(row.get("notes")),
        }
        for row in sorted_rows[:limit]
    ]


def opportunity_band(score: int, avg_premium_rate: float, sample_count: int, brand_weight: int) -> str:
    if sample_count < 2 and brand_weight >= 85:
        return "collect_samples"
    if score >= 78 and avg_premium_rate >= 0.25 and sample_count >= 2:
        return "lead"
    if score >= 62 or brand_weight >= 85:
        return "watch"
    return "cooldown"


def opportunity_reasons(avg_premium_rate: float, sample_count: int, brand_weight: int) -> list[str]:
    reasons = []
    if brand_weight >= 90:
        reasons.append("core_brand")
    elif brand_weight >= 70:
        reasons.append("watch_brand")
    if sample_count < 2:
        reasons.append("needs_samples")
    elif sample_count >= 5:
        reasons.append("sample_supported")
    if avg_premium_rate >= 0.5:
        reasons.append("strong_premium")
    elif avg_premium_rate >= 0.25:
        reasons.append("positive_premium")
    elif avg_premium_rate < 0:
        reasons.append("discounted_resale")
    return reasons or ["baseline"]


def positive_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def premium_priority_score(premium_rate: float, brand_weight: int, sample_count: int) -> int:
    breakdown = premium_score_breakdown(premium_rate, brand_weight, sample_count)
    total = breakdown["premium_points"] + breakdown["brand_points"] + breakdown["sample_points"]
    return max(0, min(100, round(total)))


def premium_score_breakdown(premium_rate: float, brand_weight: int, sample_count: int) -> dict[str, int]:
    premium_points = max(0, premium_rate) * 55
    brand_points = clamp_int(brand_weight, default=50) * 0.4
    sample_points = min(10, max(0, sample_count) * 2)
    return {
        "premium_points": round(premium_points),
        "brand_points": round(brand_points),
        "sample_points": round(sample_points),
    }


def clamp_int(value: Any, default: int) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return default


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
