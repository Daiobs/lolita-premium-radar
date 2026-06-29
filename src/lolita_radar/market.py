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
        avg_retail_price = sum(retail_prices) / len(retail_prices)
        avg_resale_price = sum(resale_prices) / len(resale_prices)
        avg_spread = avg_resale_price - avg_retail_price
        brand_weight = weight_by_alias.get(alias, 50)
        brands.append(
            {
                "brand_alias": alias,
                "sample_count": len(rows),
                "avg_premium_rate": round(avg_premium_rate, 4),
                "max_premium_rate": round(max(premium_rates), 4),
                "avg_retail_price": round(avg_retail_price, 2),
                "avg_resale_price": round(avg_resale_price, 2),
                "avg_spread": round(avg_spread, 2),
                "min_retail_price": round(min(retail_prices), 2),
                "max_retail_price": round(max(retail_prices), 2),
                "min_resale_price": round(min(resale_prices), 2),
                "max_resale_price": round(max(resale_prices), 2),
                "premium_band": premium_band(avg_premium_rate),
                "brand_weight": brand_weight,
                "priority_score": premium_priority_score(avg_premium_rate, brand_weight, len(rows)),
                "currency": rows[0].get("currency") or "CNY",
            }
        )
    records = []
    for observation in observations:
        brand_weight = weight_by_alias.get(observation["brand_alias"], 50)
        premium_rate = float(observation["premium_rate"])
        records.append(
            {
                **observation,
                "brand_weight": brand_weight,
                "priority_score": premium_priority_score(premium_rate, brand_weight, 1),
                "premium_band": premium_band(premium_rate),
                "quality_score": sample_quality_score(observation),
                "quality_flags": sample_quality_flags(observation),
            }
        )
    return {
        "sample_count": len(observations),
        "quality": summarize_sample_quality(observations),
        "premium_bands": summarize_premium_bands(records),
        "brands": sorted(
            brands,
            key=lambda row: (int(row["priority_score"]), float(row["avg_premium_rate"]), int(row["sample_count"])),
            reverse=True,
        ),
        "records": sorted(records, key=lambda row: (int(row["priority_score"]), float(row["premium_rate"])), reverse=True)[:20],
    }


def build_market_momentum(
    observations: list[dict[str, Any]],
    brand_weights: list[dict[str, Any]] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    weight_by_alias = {
        text(brand.get("alias")).upper(): clamp_int(brand.get("weight"), default=50)
        for brand in brand_weights or []
    }
    brand_rows: dict[str, list[dict[str, Any]]] = {}
    for index, observation in enumerate(observations):
        row = {**observation, "_index": index}
        brand_rows.setdefault(text(observation.get("brand_alias")).upper(), []).append(row)

    momentum = []
    for alias, rows in brand_rows.items():
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=observation_sort_key)
        latest = ordered[-1]
        previous = ordered[:-1]
        previous_average = sum(float(row.get("premium_rate") or 0) for row in previous) / len(previous)
        latest_premium = float(latest.get("premium_rate") or 0)
        delta = latest_premium - previous_average
        weight = weight_by_alias.get(alias, 50)
        momentum.append(
            {
                "brand_alias": alias,
                "latest_item": text(latest.get("item_name")),
                "latest_premium_rate": round(latest_premium, 4),
                "previous_premium_rate": round(previous_average, 4),
                "delta": round(delta, 4),
                "direction": momentum_direction(delta),
                "sample_count": len(rows),
                "brand_weight": weight,
                "priority_score": momentum_priority_score(delta, latest_premium, weight, len(rows)),
                "observed_at": text(latest.get("observed_at")),
                "source": text(latest.get("source")),
                "currency": text(latest.get("currency")) or "CNY",
            }
        )
    return sorted(
        momentum,
        key=lambda row: (
            int(row["priority_score"]),
            abs(float(row["delta"])),
            float(row["latest_premium_rate"]),
            int(row["brand_weight"]),
        ),
        reverse=True,
    )[:limit]


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
                "visual": brand.get("visual") if isinstance(brand.get("visual"), dict) else {},
                "watch_urls": brand.get("watch_urls") if isinstance(brand.get("watch_urls"), list) else [],
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


def build_brand_weight_profile(
    brand_weights: list[dict[str, Any]],
    market_brands: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    market_by_alias = {text(row.get("brand_alias")).upper(): row for row in market_brands}
    profile = []
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
        profile.append(
            {
                "name": text(brand.get("name")) or alias,
                "alias": alias,
                "tier": text(brand.get("tier")) or "watch",
                "style": text(brand.get("style")) or "general",
                "visual": brand.get("visual") if isinstance(brand.get("visual"), dict) else {},
                "watch_urls": brand.get("watch_urls") if isinstance(brand.get("watch_urls"), list) else [],
                "brand_weight": weight,
                "weight_band": weight_band(weight),
                "weight_role": weight_role(weight),
                "market_keywords": [text(keyword) for keyword in brand.get("market_keywords") or [] if text(keyword)],
                "sample_count": sample_count,
                "avg_premium_rate": round(avg_premium_rate, 4),
                "max_premium_rate": round(max_premium_rate, 4),
                "evidence_level": evidence_level(sample_count),
                "evidence_score": evidence_score(sample_count),
                "priority_score": score,
                "score_breakdown": premium_score_breakdown(avg_premium_rate, weight, sample_count),
                "band": opportunity_band(score, avg_premium_rate, sample_count, weight),
                "reason_codes": opportunity_reasons(avg_premium_rate, sample_count, weight),
            }
        )
    return sorted(
        profile,
        key=lambda row: (
            int(row["brand_weight"]),
            int(row["priority_score"]),
            int(row["sample_count"]),
            float(row["avg_premium_rate"]),
        ),
        reverse=True,
    )


def build_sample_collection_plan(
    brand_weights: list[dict[str, Any]],
    market_brands: list[dict[str, Any]],
    limit: int = 9,
) -> list[dict[str, Any]]:
    market_by_alias = {text(row.get("brand_alias")).upper(): row for row in market_brands}
    plan = []
    for brand in brand_weights:
        alias = text(brand.get("alias"))
        if not alias:
            continue
        weight = clamp_int(brand.get("weight"), default=50)
        tier = text(brand.get("tier")) or weight_band(weight)
        market = market_by_alias.get(alias.upper(), {})
        sample_count = clamp_int(market.get("sample_count"), default=0)
        target = sample_target(weight, tier)
        missing = max(0, target - sample_count)
        avg_premium_rate = float(market.get("avg_premium_rate") or 0)
        if missing <= 0 and avg_premium_rate < 0.25:
            continue
        plan.append(
            {
                "name": text(brand.get("name")) or alias,
                "alias": alias,
                "tier": tier,
                "style": text(brand.get("style")) or "general",
                "brand_weight": weight,
                "sample_count": sample_count,
                "target_samples": target,
                "missing_samples": missing,
                "avg_premium_rate": round(avg_premium_rate, 4),
                "urgency": sample_plan_urgency(missing, weight, sample_count),
                "next_action": sample_plan_action(missing, sample_count),
                "priority_score": sample_plan_score(weight, missing, avg_premium_rate, sample_count),
                "market_keywords": [text(keyword) for keyword in brand.get("market_keywords") or [] if text(keyword)][:4],
                "watch_urls": brand.get("watch_urls") if isinstance(brand.get("watch_urls"), list) else [],
                "visual": brand.get("visual") if isinstance(brand.get("visual"), dict) else {},
            }
        )
    return sorted(
        plan,
        key=lambda row: (
            sample_plan_rank(text(row.get("urgency"))),
            int(row["priority_score"]),
            int(row["brand_weight"]),
            int(row["missing_samples"]),
        ),
        reverse=True,
    )[:limit]


def build_market_alerts(
    brand_weights: list[dict[str, Any]],
    market_summary: dict[str, Any],
    limit: int = 8,
) -> dict[str, Any]:
    market_by_alias = {text(row.get("brand_alias")).upper(): row for row in market_summary.get("brands", [])}
    alerts = []
    for record in market_summary.get("records", []):
        premium_rate = float(record.get("premium_rate") or 0)
        priority_score = clamp_int(record.get("priority_score"), default=0)
        quality_score = clamp_int(record.get("quality_score"), default=0)
        band = text(record.get("premium_band"))
        if band in {"collector", "hot"} or priority_score >= 72:
            alerts.append(
                {
                    "kind": "sample_spike",
                    "severity": alert_severity(priority_score, quality_score, band),
                    "alias": text(record.get("brand_alias")),
                    "title": text(record.get("item_name")),
                    "score": priority_score,
                    "premium_rate": round(premium_rate, 4),
                    "premium_band": band or premium_band(premium_rate),
                    "quality_score": quality_score,
                    "reason": sample_alert_reason(band, priority_score, quality_score),
                }
            )
    for brand in brand_weights:
        alias = text(brand.get("alias"))
        if not alias:
            continue
        weight = clamp_int(brand.get("weight"), default=50)
        market = market_by_alias.get(alias.upper(), {})
        sample_count = clamp_int(market.get("sample_count"), default=0)
        avg_premium_rate = float(market.get("avg_premium_rate") or 0)
        priority_score = premium_priority_score(avg_premium_rate, weight, sample_count)
        if sample_count >= 2 and avg_premium_rate >= 0.5:
            alerts.append(
                {
                    "kind": "brand_heat",
                    "severity": "critical" if priority_score >= 78 else "watch",
                    "alias": alias,
                    "title": text(brand.get("name")) or alias,
                    "score": priority_score,
                    "premium_rate": round(avg_premium_rate, 4),
                    "sample_count": sample_count,
                    "reason": "brand_hot_average",
                }
            )
        elif sample_count < 2 and weight >= 85:
            alerts.append(
                {
                    "kind": "sample_gap",
                    "severity": "sample_gap",
                    "alias": alias,
                    "title": text(brand.get("name")) or alias,
                    "score": weight,
                    "premium_rate": round(avg_premium_rate, 4),
                    "sample_count": sample_count,
                    "reason": "core_needs_samples",
                }
            )
    sorted_alerts = sorted(
        alerts,
        key=lambda row: (alert_rank(text(row.get("severity"))), int(row.get("score") or 0)),
        reverse=True,
    )[:limit]
    return {
        "summary": {
            "total": len(alerts),
            "critical": sum(1 for row in alerts if row.get("severity") == "critical"),
            "watch": sum(1 for row in alerts if row.get("severity") == "watch"),
            "sample_gap": sum(1 for row in alerts if row.get("severity") == "sample_gap"),
        },
        "alerts": sorted_alerts,
    }


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
            "quality_score": sample_quality_score(row),
            "quality_flags": sample_quality_flags(row),
        }
        for row in sorted_rows[:limit]
    ]


def summarize_sample_quality(observations: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(observations)
    scores = [sample_quality_score(row) for row in observations]
    linked_count = sum(1 for row in observations if text(row.get("url")))
    sourced_count = sum(1 for row in observations if text(row.get("source")))
    dated_count = sum(1 for row in observations if text(row.get("observed_at")))
    noted_count = sum(1 for row in observations if text(row.get("notes")))
    weak_count = sum(1 for score in scores if score < 60)
    return {
        "sample_count": total,
        "avg_quality_score": round(sum(scores) / total) if total else 0,
        "linked_count": linked_count,
        "sourced_count": sourced_count,
        "dated_count": dated_count,
        "noted_count": noted_count,
        "weak_count": weak_count,
    }


def summarize_premium_bands(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bands = ["collector", "hot", "premium", "near_retail", "discount"]
    counts = {band: 0 for band in bands}
    for record in records:
        band = text(record.get("premium_band"))
        if band in counts:
            counts[band] += 1
    return [{"band": band, "count": counts[band]} for band in bands]


def sample_quality_score(observation: dict[str, Any]) -> int:
    score = 30
    if text(observation.get("source")):
        score += 15
    if text(observation.get("url")):
        score += 25
    if text(observation.get("observed_at")):
        score += 15
    if text(observation.get("condition")):
        score += 8
    if text(observation.get("notes")):
        score += 7
    return max(0, min(100, score))


def sample_quality_flags(observation: dict[str, Any]) -> list[str]:
    flags = []
    if not text(observation.get("source")):
        flags.append("missing_source")
    if not text(observation.get("url")):
        flags.append("missing_url")
    if not text(observation.get("observed_at")):
        flags.append("missing_date")
    if not text(observation.get("condition")):
        flags.append("missing_condition")
    if not text(observation.get("notes")):
        flags.append("missing_notes")
    return flags


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


def weight_band(weight: int) -> str:
    if weight >= 90:
        return "core"
    if weight >= 70:
        return "watch"
    return "archive"


def weight_role(weight: int) -> str:
    if weight >= 90:
        return "release_priority"
    if weight >= 70:
        return "premium_watch"
    return "evidence_sampling"


def evidence_level(sample_count: int) -> str:
    if sample_count >= 5:
        return "ready"
    if sample_count >= 2:
        return "thin"
    return "missing"


def evidence_score(sample_count: int) -> int:
    return max(0, min(100, sample_count * 20))


def premium_band(premium_rate: float) -> str:
    if premium_rate >= 0.8:
        return "collector"
    if premium_rate >= 0.5:
        return "hot"
    if premium_rate >= 0.25:
        return "premium"
    if premium_rate >= -0.1:
        return "near_retail"
    return "discount"


def alert_severity(priority_score: int, quality_score: int, band: str) -> str:
    if band == "collector" and quality_score >= 60:
        return "critical"
    if priority_score >= 72 and quality_score >= 60:
        return "critical"
    return "watch"


def sample_alert_reason(band: str, priority_score: int, quality_score: int) -> str:
    if quality_score < 60:
        return "weak_evidence_spike"
    if band == "collector":
        return "collector_premium"
    if band == "hot":
        return "hot_premium"
    if priority_score >= 72:
        return "weighted_spike"
    return "premium_watch"


def alert_rank(severity: str) -> int:
    return {"critical": 3, "watch": 2, "sample_gap": 1}.get(severity, 0)


def sample_target(weight: int, tier: str) -> int:
    if tier == "core" or weight >= 90:
        return 5
    if tier == "watch" or weight >= 70:
        return 3
    return 2


def sample_plan_urgency(missing: int, weight: int, sample_count: int) -> str:
    if missing <= 0:
        return "complete"
    if weight >= 90 and sample_count < 2:
        return "critical"
    if weight >= 70:
        return "watch"
    return "backfill"


def sample_plan_action(missing: int, sample_count: int) -> str:
    if missing <= 0:
        return "complete"
    if sample_count <= 0:
        return "seed"
    if sample_count < 2:
        return "pair"
    return "roundout"


def sample_plan_rank(urgency: str) -> int:
    return {"critical": 4, "watch": 3, "backfill": 2, "complete": 1}.get(urgency, 0)


def sample_plan_score(weight: int, missing: int, avg_premium_rate: float, sample_count: int) -> int:
    weight_points = clamp_int(weight, default=50) * 0.5
    gap_points = max(0, missing) * 8
    premium_points = max(0.0, avg_premium_rate) * 35
    seed_bonus = 10 if sample_count == 0 and weight >= 90 else 0
    return max(0, min(100, round(weight_points + gap_points + premium_points + seed_bonus)))


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


def momentum_priority_score(delta: float, latest_premium_rate: float, brand_weight: int, sample_count: int) -> int:
    direction_points = max(0, delta) * 35
    latest_points = max(0, latest_premium_rate) * 30
    brand_points = clamp_int(brand_weight, default=50) * 0.25
    sample_points = min(10, max(0, sample_count) * 2)
    return max(0, min(100, round(direction_points + latest_points + brand_points + sample_points)))


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


def observation_sort_key(observation: dict[str, Any]) -> tuple[str, int]:
    return (text(observation.get("observed_at")), int(observation.get("_index") or 0))


def momentum_direction(delta: float) -> str:
    if delta >= 0.15:
        return "rising"
    if delta <= -0.15:
        return "cooling"
    return "steady"


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
