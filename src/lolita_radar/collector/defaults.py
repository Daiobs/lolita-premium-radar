from __future__ import annotations

from typing import Any


DEFAULT_COLLECTOR_JOBS: list[dict[str, Any]] = [
    {
        "name": "baby_official_store",
        "collector_type": "official_shop",
        "url": "https://store.babyssb.co.jp/en/products.json?limit=20",
        "enabled": True,
        "options": {
            "parser": "shopify_products_json",
            "shop_name": "BABY Official Store",
            "platform": "official_store",
            "currency": "JPY",
            "base_url": "https://store.babyssb.co.jp/en",
            "max_age_days": 180,
            "keywords": ["JSK", "OP", "Jumperskirt", "Onepiece", "Reservation", "Pre-order", "予約"],
        },
    },
    {
        "name": "baby_sf_new_arrivals",
        "collector_type": "official_shop",
        "url": "https://shop.baby-aatp.com/collections/new/products.json?limit=20",
        "enabled": True,
        "options": {
            "parser": "shopify_products_json",
            "shop_name": "BABY SF Official Shop",
            "platform": "official_store",
            "currency": "USD",
            "base_url": "https://shop.baby-aatp.com",
            "max_age_days": 180,
            "keywords": ["JSK", "OP", "Jumperskirt", "Onepiece", "Reservation", "Pre-order", "PIRATES", "予約"],
        },
    },
    {
        "name": "closet_child_new_arrivals",
        "collector_type": "closet_child_market",
        "url": "https://www.closetchildonlineshop.com/",
        "enabled": True,
        "options": {
            "shop_name": "Closet Child",
            "platform": "closet_child",
            "currency": "JPY",
            "condition": "used",
            "pattern": "new_arrivals",
            "keywords": ["JSK", "OP", "ワンピース", "ジャンパースカート", "Moitie", "Angelic Pretty", "BABY"],
        },
    },
    {
        "name": "wunderwelt_new_arrivals",
        "collector_type": "wunderwelt_market",
        "url": "https://www.wunderwelt.jp/en/products.json?limit=20",
        "enabled": True,
        "options": {
            "parser": "shopify_products_json",
            "shop_name": "Wunderwelt",
            "platform": "wunderwelt",
            "currency": "JPY",
            "condition": "used",
            "base_url": "https://www.wunderwelt.jp/en",
            "keywords": ["JSK", "OP", "dress", "ワンピース", "ジャンパースカート", "Angelic Pretty", "BABY"],
        },
    },
    {
        "name": "lace_market_new_arrivals",
        "collector_type": "lace_market",
        "url": "https://egl.circlly.com/new-arrivals",
        "enabled": False,
        "options": {
            "shop_name": "Lace Market",
            "platform": "lace_market",
            "currency": "USD",
            "condition": "used",
            "group_pattern": "new_arrivals",
            "keywords": ["JSK", "OP", "dress", "Angelic Pretty", "BABY"],
        },
    },
]
