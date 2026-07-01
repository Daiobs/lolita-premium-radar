from __future__ import annotations

import json
from urllib.parse import urljoin

from ..models import MarketSample, ShopItem, utc_now_iso
from .base import CollectorJob, CollectorResult
from .html_cards import CardParser
from .official_shop import fetch_for_job, matched_keywords, priority_for_keywords


class PublicMarketCardCollector:
    collector_type = "public_market"
    default_shop_name = "Public Market"
    default_platform = "public_market"

    def collect(self, job: CollectorJob) -> CollectorResult:
        fetch = fetch_for_job(job)
        if fetch.warnings and not fetch.text:
            return CollectorResult(warnings=fetch.warnings)
        if str(job.options.get("parser") or "").strip() == "shopify_products_json" or "products.json" in job.url:
            result = parse_shopify_market_products(job, fetch.text)
        else:
            result = parse_market_cards(job, fetch.text)
        return CollectorResult(shop_items=result.shop_items, market_samples=result.market_samples, warnings=fetch.warnings + result.warnings)


class WunderweltMarketCollector(PublicMarketCardCollector):
    collector_type = "wunderwelt_market"
    default_shop_name = "Wunderwelt"
    default_platform = "wunderwelt"


class LaceMarketCollector(PublicMarketCardCollector):
    collector_type = "lace_market"
    default_shop_name = "Lace Market"
    default_platform = "lace_market"


def parse_market_cards(job: CollectorJob, html: str) -> CollectorResult:
    parser = CardParser("market-card")
    parser.feed(html)
    items: list[ShopItem] = []
    samples: list[MarketSample] = []
    shop_name = str(job.options.get("shop_name") or job.name)
    platform = str(job.options.get("platform") or job.collector_type)
    currency = str(job.options.get("currency") or "JPY")
    condition = str(job.options.get("condition") or "used")
    observed_at = str(job.options.get("observed_at") or utc_now_iso()[:10])
    keywords = [str(item) for item in job.options.get("keywords", [])] if isinstance(job.options.get("keywords"), list) else []
    group_pattern = str(job.options.get("group_pattern") or "").strip()
    for card in parser.cards:
        title = str(card.get("title") or "").strip()
        url = urljoin(job.url, str(card.get("url") or ""))
        price = normalize_price(str(card.get("price") or card.get("asking-price") or ""))
        brand = normalize_brand(str(card.get("brand") or ""))
        if not title or not url or not price:
            continue
        matched = matched_keywords(title, keywords)
        image_url = urljoin(job.url, str(card.get("image") or "")) if card.get("image") else ""
        items.append(
            ShopItem(
                shop_name=str(card.get("shop") or shop_name),
                platform=str(card.get("platform") or platform),
                title=title,
                price=price,
                currency=str(card.get("currency") or currency),
                image_url=image_url,
                item_url=url,
                availability=str(card.get("availability") or "in_stock"),
                matched_keywords=matched,
                observed_at=str(card.get("observed-at") or observed_at),
                purchase_url=url,
                priority=priority_for_keywords(matched),
            )
        )
        if brand:
            samples.append(
                MarketSample(
                    platform=str(card.get("platform") or platform),
                    brand_alias=brand,
                    pattern=group_pattern or str(card.get("pattern") or "new_arrivals"),
                    title=title,
                    asking_price=float(price),
                    currency=str(card.get("currency") or currency),
                    condition=str(card.get("condition") or condition),
                    url=url,
                    image_url=image_url,
                    observed_at=str(card.get("observed-at") or observed_at),
                )
            )
    return CollectorResult(shop_items=items, market_samples=samples, warnings=[] if items or samples else ["no public market item cards parsed"])


def parse_shopify_market_products(job: CollectorJob, raw: str) -> CollectorResult:
    payload = json.loads(raw)
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list):
        return CollectorResult(warnings=["shopify market payload has no products list"])
    shop_name = str(job.options.get("shop_name") or job.name)
    platform = str(job.options.get("platform") or job.collector_type)
    currency = str(job.options.get("currency") or "JPY")
    condition = str(job.options.get("condition") or "used")
    base_url = str(job.options.get("base_url") or job.url.split("/products.json", 1)[0])
    observed_fallback = str(job.options.get("observed_at") or utc_now_iso()[:10])
    group_pattern = str(job.options.get("group_pattern") or "new_arrivals")
    keywords = [str(item) for item in job.options.get("keywords", [])] if isinstance(job.options.get("keywords"), list) else []
    items: list[ShopItem] = []
    samples: list[MarketSample] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        title = str(product.get("title") or "").strip()
        handle = str(product.get("handle") or "").strip()
        if not title or not handle:
            continue
        variants = product.get("variants") if isinstance(product.get("variants"), list) else []
        first_variant = next((variant for variant in variants if isinstance(variant, dict)), {})
        price = normalize_price(str(first_variant.get("price") or ""))
        if not price:
            continue
        available = any(bool(variant.get("available")) for variant in variants if isinstance(variant, dict))
        images = product.get("images") if isinstance(product.get("images"), list) else []
        image_url = next((str(row.get("src") or "") for row in images if isinstance(row, dict) and row.get("src")), "")
        url = urljoin(base_url + "/", f"products/{handle}")
        observed_at = str(product.get("published_at") or observed_fallback)[:10]
        matched = matched_keywords(title, keywords)
        brand = normalize_brand(str(product.get("vendor") or ""))
        items.append(
            ShopItem(
                shop_name=shop_name,
                platform=platform,
                title=title,
                price=price,
                currency=currency,
                image_url=image_url,
                item_url=url,
                availability="in_stock" if available else "sold_out",
                matched_keywords=matched,
                observed_at=observed_at,
                purchase_url=url,
                priority=priority_for_keywords(matched),
            )
        )
        if brand:
            samples.append(
                MarketSample(
                    platform=platform,
                    brand_alias=brand,
                    pattern=group_pattern,
                    title=title,
                    asking_price=float(price),
                    currency=currency,
                    condition=condition,
                    url=url,
                    image_url=image_url,
                    observed_at=observed_at,
                )
            )
    return CollectorResult(shop_items=items, market_samples=samples)


def normalize_price(raw: str) -> str:
    return "".join(char for char in raw if char.isdigit() or char == ".")


def normalize_brand(raw: str) -> str:
    normalized = raw.casefold()
    if "angelic pretty" in normalized:
        return "AP"
    if "alice and the pirates" in normalized or "pirates" in normalized:
        return "AATP"
    if "baby" in normalized or "btssb" in normalized:
        return "BABY"
    if "metamorphose" in normalized:
        return "META"
    if "moi meme moitie" in normalized or "moi-meme-moitie" in normalized or "moi-même-moitié" in normalized or "moitie" in normalized:
        return "MMM"
    if "innocent world" in normalized:
        return "IW"
    if "victorian maiden" in normalized:
        return "VM"
    return raw.strip().upper()[:24] if raw.strip() else ""
