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


def summarize_market_observations(observations: list[dict[str, Any]]) -> dict[str, Any]:
    brand_rows: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        brand_rows.setdefault(observation["brand_alias"], []).append(observation)
    brands = []
    for alias, rows in brand_rows.items():
        premium_rates = [float(row["premium_rate"]) for row in rows]
        resale_prices = [float(row["resale_price"]) for row in rows]
        retail_prices = [float(row["retail_price"]) for row in rows]
        brands.append(
            {
                "brand_alias": alias,
                "sample_count": len(rows),
                "avg_premium_rate": round(sum(premium_rates) / len(premium_rates), 4),
                "max_premium_rate": round(max(premium_rates), 4),
                "avg_retail_price": round(sum(retail_prices) / len(retail_prices), 2),
                "avg_resale_price": round(sum(resale_prices) / len(resale_prices), 2),
                "currency": rows[0].get("currency") or "CNY",
            }
        )
    return {
        "sample_count": len(observations),
        "brands": sorted(brands, key=lambda row: (float(row["avg_premium_rate"]), int(row["sample_count"])), reverse=True),
        "records": sorted(observations, key=lambda row: float(row["premium_rate"]), reverse=True)[:20],
    }


def positive_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
