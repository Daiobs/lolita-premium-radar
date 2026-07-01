from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from ..models import MarketSample, ShopItem, utc_now_iso
from .base import CollectorJob, CollectorResult
from .official_shop import load_html, matched_keywords, priority_for_keywords


class ClosetChildMarketCollector:
    collector_type = "closet_child_market"

    def collect(self, job: CollectorJob) -> CollectorResult:
        html = load_html(job.url)
        return parse_closet_child(job, html)


def parse_closet_child(job: CollectorJob, html: str) -> CollectorResult:
    shop_name = str(job.options.get("shop_name") or "Closet Child")
    platform = str(job.options.get("platform") or "closet_child")
    currency = str(job.options.get("currency") or "JPY")
    condition = str(job.options.get("condition") or "used")
    pattern_fallback = str(job.options.get("pattern") or "new_arrivals")
    group_pattern = str(job.options.get("group_pattern") or "").strip()
    keywords = [str(item) for item in job.options.get("keywords", [])] if isinstance(job.options.get("keywords"), list) else []
    observed_at = str(job.options.get("observed_at") or utc_now_iso()[:10])
    base_url = str(job.options.get("base_url") or job.url)
    shop_items: list[ShopItem] = []
    market_samples: list[MarketSample] = []

    for block in split_item_blocks(html):
        url = first_match(r'href=["\']([^"\']*/product/\d+[^"\']*)', block)
        title = clean_html(first_match(r'<span[^>]*class=["\'][^"\']*goods_name[^"\']*["\'][^>]*>(.*?)</span>', block))
        price = normalize_price(first_match(r'<span[^>]*class=["\'][^"\']*figure[^"\']*["\'][^>]*>\s*([\d,]+)', block))
        image_url = first_match(r'data-src=["\']([^"\']+)["\']', block) or first_match(r'<img[^>]+src=["\']([^"\']+)["\']', block)
        if not title or not url or not price:
            continue
        absolute_url = urljoin(base_url, url)
        absolute_image = urljoin(base_url, image_url) if image_url else ""
        brand_alias = brand_from_title(title)
        matched = matched_keywords(title, keywords)
        shop_items.append(
            ShopItem(
                shop_name=shop_name,
                platform=platform,
                title=title,
                price=price,
                currency=currency,
                image_url=absolute_image,
                item_url=absolute_url,
                availability="in_stock",
                matched_keywords=matched,
                observed_at=observed_at,
                purchase_url=absolute_url,
                priority=priority_for_keywords(matched),
            )
        )
        if brand_alias:
            market_samples.append(
                MarketSample(
                    platform=platform,
                    brand_alias=brand_alias,
                    pattern=group_pattern or pattern_from_title(title) or pattern_fallback,
                    title=title,
                    asking_price=float(price),
                    currency=currency,
                    condition=condition,
                    url=absolute_url,
                    image_url=absolute_image,
                    observed_at=observed_at,
                )
            )
    warnings = [] if shop_items else ["no Closet Child item cards parsed"]
    return CollectorResult(shop_items=shop_items, market_samples=market_samples, warnings=warnings)


def split_item_blocks(html: str) -> list[str]:
    marker = r'<div[^>]*class=["\'][^"\']*\bitem_data\b[^"\']*["\'][^>]*>'
    blocks = re.findall(rf"{marker}.*?(?={marker}|\Z)", html, re.I | re.S)
    if blocks:
        return blocks
    return re.findall(r'<article\b.*?</article>', html, re.I | re.S)


def first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.I | re.S)
    return unescape(match.group(1).strip()) if match else ""


def clean_html(raw: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", unescape(raw or "")).split())


def normalize_price(raw: str) -> str:
    return re.sub(r"[^\d.]", "", raw or "")


def brand_from_title(title: str) -> str:
    normalized = title.casefold()
    if "angelic pretty" in normalized:
        return "AP"
    if "alice and the pirates" in normalized or "pirates" in normalized:
        return "AATP"
    if "baby" in normalized or "btssb" in normalized:
        return "BABY"
    if "metamorphose" in normalized:
        return "META"
    if "moi-meme-moitie" in normalized or "moi-même-moitié" in normalized or "moitie" in normalized:
        return "MMM"
    if "innocent world" in normalized:
        return "IW"
    return ""


def pattern_from_title(title: str) -> str:
    parts = [part.strip() for part in re.split(r"\s*/\s*", title, maxsplit=1)]
    if len(parts) == 2 and parts[1]:
        return parts[1][:80]
    return ""
