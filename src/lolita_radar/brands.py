from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


DEFAULT_BRAND_WEIGHTS: list[dict[str, Any]] = [
    {
        "name": "Angelic Pretty",
        "alias": "AP",
        "weight": 100,
        "tier": "core",
        "style": "sweet print",
        "keywords": ["angelic pretty", "ap", "アンジェリックプリティ"],
    },
    {
        "name": "BABY, THE STARS SHINE BRIGHT",
        "alias": "BABY",
        "weight": 95,
        "tier": "core",
        "style": "classic sweet",
        "keywords": ["baby the stars shine bright", "btssb", "baby"],
    },
    {
        "name": "ALICE and the PIRATES",
        "alias": "AATP",
        "weight": 90,
        "tier": "core",
        "style": "gothic prince",
        "keywords": ["alice and the pirates", "aatp", "pirates"],
    },
    {
        "name": "Metamorphose temps de fille",
        "alias": "Meta",
        "weight": 80,
        "tier": "watch",
        "style": "release/restock",
        "keywords": ["metamorphose", "meta", "metamor"],
    },
    {
        "name": "Moi-meme-Moitie",
        "alias": "MMM",
        "weight": 75,
        "tier": "watch",
        "style": "gothic",
        "keywords": ["moi-meme-moitie", "moitie", "mmm"],
    },
    {
        "name": "Innocent World",
        "alias": "IW",
        "weight": 65,
        "tier": "archive",
        "style": "classic",
        "keywords": ["innocent world", "iw"],
    },
    {
        "name": "Victorian Maiden",
        "alias": "VM",
        "weight": 65,
        "tier": "archive",
        "style": "classic",
        "keywords": ["victorian maiden", "vm"],
    },
    {
        "name": "Mary Magdalene",
        "alias": "MM",
        "weight": 65,
        "tier": "archive",
        "style": "classic",
        "keywords": ["mary magdalene", "mm"],
    },
    {
        "name": "Juliette et Justine",
        "alias": "JetJ",
        "weight": 65,
        "tier": "archive",
        "style": "art print",
        "keywords": ["juliette et justine", "jetj"],
    },
]


def default_brand_weights_path() -> Path:
    return Path("config") / "brand_weights.json"


def load_brand_weights(path: Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        path = default_brand_weights_path()
    if not path.exists():
        return normalize_brand_weights(DEFAULT_BRAND_WEIGHTS)
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError("brand_weights.json must contain a list")
    return normalize_brand_weights(raw)


def normalize_brand_weights(rows: list[Any]) -> list[dict[str, Any]]:
    brands: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        name = text(raw.get("name"))
        alias = text(raw.get("alias")) or name
        if not name or not alias:
            continue
        keywords = raw.get("keywords") or []
        if not isinstance(keywords, list):
            keywords = []
        brand = {
            "name": name,
            "alias": alias,
            "weight": clamp_int(raw.get("weight"), default=50),
            "tier": text(raw.get("tier")) or "watch",
            "style": text(raw.get("style")) or "general",
            "keywords": sorted({text(keyword).lower() for keyword in keywords if text(keyword)}),
        }
        brand["keywords"] = sorted({*brand["keywords"], name.lower(), alias.lower()})
        brands.append(brand)
    return sorted(brands, key=lambda brand: int(brand["weight"]), reverse=True)


def build_focus_queue(
    brand_weights: list[dict[str, Any]],
    items: list[dict[str, Any]],
    events: list[dict[str, Any]],
    market_brands: list[dict[str, Any]] | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    market_by_alias = {text(row.get("brand_alias")).upper(): row for row in market_brands or []}
    for brand in brand_weights:
        item_count = count_matches(brand, items)
        event_count = count_matches(brand, events)
        market = market_by_alias.get(text(brand.get("alias")).upper(), {})
        market_count = int(market.get("sample_count") or 0)
        premium_rate = float(market.get("avg_premium_rate") or 0)
        observed_bonus = min(30, item_count * 3 + event_count * 2)
        market_bonus = min(25, int(max(0, premium_rate) * 50) + min(10, market_count * 2))
        score = min(100, int(brand["weight"]) + observed_bonus + market_bonus)
        if brand.get("tier") == "core" or item_count or event_count or market_count:
            queue.append(
                {
                    "name": brand["name"],
                    "alias": brand["alias"],
                    "weight": brand["weight"],
                    "tier": brand["tier"],
                    "style": brand["style"],
                    "score": score,
                    "item_count": item_count,
                    "event_count": event_count,
                    "market_count": market_count,
                    "avg_premium_rate": round(premium_rate, 4),
                }
            )
    return sorted(queue, key=lambda row: (int(row["score"]), int(row["weight"])), reverse=True)[:limit]


def count_matches(brand: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if row_matches_brand(brand, row))


def row_matches_brand(brand: dict[str, Any], row: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            text(row.get("source")),
            text(row.get("title")),
            text(row.get("url")),
            text(row.get("status")),
        ]
    ).lower()
    return any(keyword_matches(keyword, haystack) for keyword in brand.get("keywords", []))


def keyword_matches(keyword: str, haystack: str) -> bool:
    if not keyword:
        return False
    if keyword.isascii() and keyword.replace("-", "").replace(" ", "").isalnum() and len(keyword) <= 3:
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", haystack) is not None
    return keyword in haystack


def clamp_int(value: Any, default: int) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return default


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
