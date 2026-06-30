from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus


WATCH_URL_TEMPLATES = [
    ("闲鱼", "https://www.goofish.com/search?q={query}"),
    ("淘宝", "https://s.taobao.com/search?q={query}"),
    ("Mercari", "https://jp.mercari.com/search?keyword={query}"),
    ("雅虎拍卖", "https://auctions.yahoo.co.jp/search/search?p={query}"),
]


DEFAULT_BRAND_WEIGHTS: list[dict[str, Any]] = [
    {
        "name": "Angelic Pretty",
        "alias": "AP",
        "weight": 100,
        "tier": "core",
        "style": "sweet print",
        "keywords": ["angelic pretty", "ap", "アンジェリックプリティ"],
        "market_keywords": ["贝壳", "白贝壳", "Holy Lantern", "Sugary Carnival", "Melty Cream Donut", "Wonder Cookie"],
        "visual": {
            "palette": "strawberry pink",
            "accent": "#b4576f",
            "paper": "#fff3f6",
            "motif": "ribbon / shell print",
            "radar_cue": "甜系原创印花与贝壳线优先",
        },
    },
    {
        "name": "BABY, THE STARS SHINE BRIGHT",
        "alias": "BABY",
        "weight": 95,
        "tier": "core",
        "style": "classic sweet",
        "keywords": ["baby the stars shine bright", "btssb", "baby"],
        "market_keywords": ["うさくみゃ", "Usakumya", "Kumakumya", "Baby Doll", "Elizabeth", "Little Red Riding Hood"],
        "visual": {
            "palette": "ivory rose",
            "accent": "#a9782c",
            "paper": "#fff8ec",
            "motif": "kumya / mascot",
            "radar_cue": "经典甜系与熊包线索优先补样本",
        },
    },
    {
        "name": "ALICE and the PIRATES",
        "alias": "AATP",
        "weight": 90,
        "tier": "core",
        "style": "gothic prince",
        "keywords": ["alice and the pirates", "aatp", "pirates"],
        "market_keywords": ["海盗", "Vampire Requiem", "Chess Game of Destiny", "Midsummer Night's Dream"],
        "visual": {
            "palette": "pirate wine",
            "accent": "#611b31",
            "paper": "#fff3f5",
            "motif": "crest / cross",
            "radar_cue": "哥特王子系与吸血鬼/棋盘款重点观察",
        },
    },
    {
        "name": "Metamorphose temps de fille",
        "alias": "Meta",
        "weight": 80,
        "tier": "watch",
        "style": "release/restock",
        "keywords": ["metamorphose", "meta", "metamor"],
        "market_keywords": ["Swan Lake", "Pintuck", "Gobelin", "Lucky Pack"],
        "visual": {
            "palette": "mint release",
            "accent": "#0f6760",
            "paper": "#f1fbf8",
            "motif": "swan / restock",
            "radar_cue": "上新、再贩和福袋线索重点同步",
        },
    },
    {
        "name": "Moi-meme-Moitie",
        "alias": "MMM",
        "weight": 75,
        "tier": "watch",
        "style": "gothic",
        "keywords": ["moi-meme-moitie", "moitie", "mmm"],
        "market_keywords": ["Iron Gate", "Stained Glass", "Silent Moon", "十字架"],
        "visual": {
            "palette": "cathedral wine",
            "accent": "#50304d",
            "paper": "#f7f1fa",
            "motif": "gate / stained glass",
            "radar_cue": "哥特圣堂元素与稀有印花重点观察",
        },
    },
    {
        "name": "Innocent World",
        "alias": "IW",
        "weight": 65,
        "tier": "archive",
        "style": "classic",
        "keywords": ["innocent world", "iw"],
        "market_keywords": ["Rose Basket", "Lotus", "Classical", "圆领"],
        "visual": {
            "palette": "classical gold",
            "accent": "#8d6a28",
            "paper": "#fff8ec",
            "motif": "basket / collar",
            "radar_cue": "古典花篮、圆领与低频样本归档",
        },
    },
    {
        "name": "Victorian Maiden",
        "alias": "VM",
        "weight": 65,
        "tier": "archive",
        "style": "classic",
        "keywords": ["victorian maiden", "vm"],
        "market_keywords": ["Regimental", "Rose", "Frill", "classic"],
        "visual": {
            "palette": "victorian mauve",
            "accent": "#8c5468",
            "paper": "#fff4f7",
            "motif": "rose / frill",
            "radar_cue": "古典玫瑰、军服感与褶边款补证据",
        },
    },
    {
        "name": "Mary Magdalene",
        "alias": "MM",
        "weight": 65,
        "tier": "archive",
        "style": "classic",
        "keywords": ["mary magdalene", "mm"],
        "market_keywords": ["Elodie", "Rose Basket", "Fleur", "classical"],
        "visual": {
            "palette": "antique rose",
            "accent": "#9b5961",
            "paper": "#fff5f2",
            "motif": "fleur / antique rose",
            "radar_cue": "古典花名款与停产品线做长期档案",
        },
    },
    {
        "name": "Juliette et Justine",
        "alias": "JetJ",
        "weight": 65,
        "tier": "archive",
        "style": "art print",
        "keywords": ["juliette et justine", "jetj"],
        "market_keywords": ["La Danse", "Le Cadre", "art print", "肖像"],
        "visual": {
            "palette": "museum teal",
            "accent": "#426a70",
            "paper": "#f1fbfb",
            "motif": "frame / portrait",
            "radar_cue": "艺术印花与肖像款按作品线归档",
        },
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


def save_brand_weights(path: Path | None, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if path is None:
        path = default_brand_weights_path()
    brands = load_brand_weights(path)
    weights_by_alias = {
        text(row.get("alias")).upper(): clamp_int(row.get("weight"), default=50)
        for row in updates
        if isinstance(row, dict) and text(row.get("alias"))
    }
    if not weights_by_alias:
        raise ValueError("brand weight update must include at least one alias")
    updated = []
    for brand in brands:
        alias_key = text(brand.get("alias")).upper()
        if alias_key in weights_by_alias:
            brand = {**brand, "weight": weights_by_alias[alias_key]}
        updated.append(brand)
    updated = normalize_brand_weights(updated)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return updated


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
        market_keywords = raw.get("market_keywords") or []
        if not isinstance(market_keywords, list):
            market_keywords = []
        brand = {
            "name": name,
            "alias": alias,
            "weight": clamp_int(raw.get("weight"), default=50),
            "tier": text(raw.get("tier")) or "watch",
            "style": text(raw.get("style")) or "general",
            "keywords": sorted({text(keyword).lower() for keyword in keywords if text(keyword)}),
            "market_keywords": ordered_unique_texts(market_keywords),
            "visual": normalize_brand_visual(raw.get("visual")),
            "watch_urls": normalize_watch_urls(raw.get("watch_urls"))
            or default_watch_urls(name),
        }
        brand["keywords"] = sorted({*brand["keywords"], name.lower(), alias.lower()})
        brands.append(brand)
    return sorted(brands, key=lambda brand: int(brand["weight"]), reverse=True)


def build_watch_signals(
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


def ordered_unique_texts(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = text(value)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def normalize_brand_visual(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    visual: dict[str, str] = {}
    for key in ["palette", "motif", "radar_cue"]:
        value = text(raw.get(key))
        if value:
            visual[key] = value
    for key in ["accent", "paper"]:
        value = text(raw.get(key))
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            visual[key] = value
    return visual


def normalize_watch_urls(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    watch_urls: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        label = text(row.get("label"))
        url = text(row.get("url"))
        if not label or not re.fullmatch(r"https?://\S+", url):
            continue
        key = (label.casefold(), url)
        if key in seen:
            continue
        seen.add(key)
        watch_urls.append({"label": label, "url": url})
    return watch_urls


def default_watch_urls(name: str) -> list[dict[str, str]]:
    query = quote_plus(f"{name} lolita")
    return [{"label": label, "url": template.format(query=query)} for label, template in WATCH_URL_TEMPLATES]


def clamp_int(value: Any, default: int) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return default


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
