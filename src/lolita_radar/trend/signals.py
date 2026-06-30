from __future__ import annotations

from typing import Any

from ..market import clamp_int, premium_priority_score, premium_score_breakdown, sample_quality_flags, sample_quality_score, text


def build_trend_candidates(
    brand_weights: list[dict[str, Any]],
    market_brands: list[dict[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    market_by_alias = {text(row.get("brand_alias")).upper(): row for row in market_brands}
    candidates = []
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
        band = trend_candidate_band(score, avg_premium_rate, sample_count, weight)
        candidates.append(
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
                "reason_codes": trend_candidate_reasons(avg_premium_rate, sample_count, weight),
            }
        )
    return sorted(
        candidates,
        key=lambda row: (
            int(row["priority_score"]),
            int(row["brand_weight"]),
            int(row["sample_count"]),
            float(row["avg_premium_rate"]),
        ),
        reverse=True,
    )[:limit]


def build_brand_signal_profile(
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
                "band": trend_candidate_band(score, avg_premium_rate, sample_count, weight),
                "reason_codes": trend_candidate_reasons(avg_premium_rate, sample_count, weight),
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


def build_sample_backlog(
    brand_weights: list[dict[str, Any]],
    market_brands: list[dict[str, Any]],
    limit: int = 9,
) -> list[dict[str, Any]]:
    market_by_alias = {text(row.get("brand_alias")).upper(): row for row in market_brands}
    backlog = []
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
        backlog.append(
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
                "urgency": sample_backlog_urgency(missing, weight, sample_count),
                "next_action": sample_backlog_action(missing, sample_count),
                "priority_score": sample_backlog_score(weight, missing, avg_premium_rate, sample_count),
                "market_keywords": [text(keyword) for keyword in brand.get("market_keywords") or [] if text(keyword)][:4],
                "watch_urls": brand.get("watch_urls") if isinstance(brand.get("watch_urls"), list) else [],
                "visual": brand.get("visual") if isinstance(brand.get("visual"), dict) else {},
            }
        )
    return sorted(
        backlog,
        key=lambda row: (
            sample_backlog_rank(text(row.get("urgency"))),
            int(row["priority_score"]),
            int(row["brand_weight"]),
            int(row["missing_samples"]),
        ),
        reverse=True,
    )[:limit]


def build_pattern_trends(
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
                    "band": trend_candidate_band(score, avg_premium_rate, len(rows), weight),
                    "reason_codes": trend_candidate_reasons(avg_premium_rate, len(rows), weight),
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


def trend_candidate_band(score: int, avg_premium_rate: float, sample_count: int, brand_weight: int) -> str:
    if sample_count < 2 and brand_weight >= 85:
        return "collect_samples"
    if score >= 78 and avg_premium_rate >= 0.25 and sample_count >= 2:
        return "lead"
    if score >= 62 or brand_weight >= 85:
        return "watch"
    return "cooldown"


def trend_candidate_reasons(avg_premium_rate: float, sample_count: int, brand_weight: int) -> list[str]:
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


def sample_target(weight: int, tier: str) -> int:
    if tier == "core" or weight >= 90:
        return 5
    if tier == "watch" or weight >= 70:
        return 3
    return 2


def sample_backlog_urgency(missing: int, weight: int, sample_count: int) -> str:
    if missing <= 0:
        return "complete"
    if weight >= 90 and sample_count < 2:
        return "critical"
    if weight >= 70:
        return "watch"
    return "backfill"


def sample_backlog_action(missing: int, sample_count: int) -> str:
    if missing <= 0:
        return "complete"
    if sample_count <= 0:
        return "seed"
    if sample_count < 2:
        return "pair"
    return "roundout"


def sample_backlog_rank(urgency: str) -> int:
    return {"critical": 4, "watch": 3, "backfill": 2, "complete": 1}.get(urgency, 0)


def sample_backlog_score(weight: int, missing: int, avg_premium_rate: float, sample_count: int) -> int:
    weight_points = clamp_int(weight, default=50) * 0.5
    gap_points = max(0, missing) * 8
    premium_points = max(0.0, avg_premium_rate) * 35
    seed_bonus = 10 if sample_count == 0 and weight >= 90 else 0
    return max(0, min(100, round(weight_points + gap_points + premium_points + seed_bonus)))
