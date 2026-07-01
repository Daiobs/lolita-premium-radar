from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import urljoin

from ..models import ShopItem, utc_now_iso
from .base import CollectorJob, CollectorResult
from .html_cards import CardParser
from .http import DEFAULT_USER_AGENT, fetch_text


class OfficialShopCollector:
    collector_type = "official_shop"

    def collect(self, job: CollectorJob) -> CollectorResult:
        fetch = fetch_for_job(job)
        html = fetch.text
        if fetch.warnings and not html:
            return CollectorResult(warnings=fetch.warnings)
        if str(job.options.get("parser") or "").strip() == "shopify_products_json" or job.url.endswith(".json") or "products.json" in job.url:
            result = collect_shopify_products(job, html)
            return CollectorResult(shop_items=result.shop_items, market_samples=result.market_samples, warnings=fetch.warnings + result.warnings)
        parser = CardParser("product-card")
        parser.feed(html)
        keywords = [str(item) for item in job.options.get("keywords", [])] if isinstance(job.options.get("keywords"), list) else []
        shop_name = str(job.options.get("shop_name") or job.name)
        platform = str(job.options.get("platform") or "official_shop")
        observed_at = str(job.options.get("observed_at") or utc_now_iso()[:10])
        items = []
        for card in parser.cards:
            title = str(card.get("title") or "").strip()
            item_url = urljoin(job.url, str(card.get("url") or ""))
            if not title or not item_url:
                continue
            matched = matched_keywords(title, keywords)
            items.append(
                ShopItem(
                    shop_name=str(card.get("shop") or shop_name),
                    platform=str(card.get("platform") or platform),
                    title=title,
                    price=str(card.get("price") or ""),
                    currency=str(card.get("currency") or ""),
                    image_url=urljoin(job.url, str(card.get("image") or "")) if card.get("image") else "",
                    item_url=item_url,
                    availability=str(card.get("availability") or ""),
                    matched_keywords=matched,
                    observed_at=str(card.get("observed-at") or observed_at),
                    sale_at=str(card.get("sale-at") or ""),
                    remind_at=str(card.get("remind-at") or ""),
                    purchase_url=urljoin(job.url, str(card.get("purchase-url") or item_url)),
                    priority=str(card.get("priority") or priority_for_keywords(matched)),
                )
            )
        return CollectorResult(shop_items=items, warnings=fetch.warnings)


def collect_shopify_products(job: CollectorJob, raw: str) -> CollectorResult:
    payload = json.loads(raw)
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list):
        return CollectorResult(warnings=["shopify payload has no products list"])
    shop_name = str(job.options.get("shop_name") or job.name)
    platform = str(job.options.get("platform") or "official_store")
    currency = str(job.options.get("currency") or "JPY")
    keywords = [str(item) for item in job.options.get("keywords", [])] if isinstance(job.options.get("keywords"), list) else []
    base_url = str(job.options.get("base_url") or job.url.split("/products.json", 1)[0])
    observed_at = str(job.options.get("observed_at") or utc_now_iso()[:10])
    max_age_days = safe_positive_int(job.options.get("max_age_days"))
    items: list[ShopItem] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        title = str(product.get("title") or "").strip()
        handle = str(product.get("handle") or "").strip()
        variants = product.get("variants") if isinstance(product.get("variants"), list) else []
        first_variant = next((variant for variant in variants if isinstance(variant, dict)), {})
        price = str(first_variant.get("price") or "")
        available = any(bool(variant.get("available")) for variant in variants if isinstance(variant, dict))
        images = product.get("images") if isinstance(product.get("images"), list) else []
        image = next((str(row.get("src") or "") for row in images if isinstance(row, dict) and row.get("src")), "")
        product_url = urljoin(base_url + "/", f"products/{handle}" if handle else "")
        published_at = str(product.get("published_at") or observed_at)[:10]
        if max_age_days is not None and not is_recent_source_date(published_at, max_age_days):
            continue
        matched = matched_keywords(" ".join([title, " ".join(str(tag) for tag in product.get("tags", []))]), keywords)
        if not title or not handle:
            continue
        items.append(
            ShopItem(
                shop_name=shop_name,
                platform=platform,
                title=title,
                price=price,
                currency=currency,
                image_url=image,
                item_url=product_url,
                availability="in_stock" if available else "sold_out",
                matched_keywords=matched,
                observed_at=published_at,
                purchase_url=product_url,
                priority=priority_for_keywords(matched),
            )
        )
    return CollectorResult(shop_items=items)


def load_html(url: str) -> str:
    return fetch_text(url).text


def fetch_for_job(job: CollectorJob):
    return fetch_text(
        job.url,
        user_agent=str(job.options.get("user_agent") or DEFAULT_USER_AGENT),
        timeout=safe_positive_int(job.options.get("timeout")) or 20,
        retries=safe_positive_int(job.options.get("retries")) or 1,
        backoff=safe_float(job.options.get("backoff"), default=0.25),
    )


def matched_keywords(title: str, keywords: list[str]) -> list[str]:
    haystack = title.casefold()
    return [keyword for keyword in keywords if keyword.casefold() in haystack]


def priority_for_keywords(keywords: list[str]) -> str:
    lowered = {keyword.casefold() for keyword in keywords}
    if lowered & {"jsk", "op", "予約", "preorder", "new arrival"}:
        return "high"
    return "medium"


def safe_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def is_recent_source_date(raw: str, max_age_days: int) -> bool:
    try:
        source_date = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return True
    return source_date >= date.today() - timedelta(days=max_age_days)


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
