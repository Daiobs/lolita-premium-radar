from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin

from ..models import ShopItem, utc_now_iso
from .base import CollectorJob, CollectorResult
from .html_cards import CardParser


class OfficialShopCollector:
    collector_type = "official_shop"

    def collect(self, job: CollectorJob) -> CollectorResult:
        html = load_html(job.url)
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
        return CollectorResult(shop_items=items)


def load_html(url: str) -> str:
    if url.startswith("file://"):
        return Path(url.removeprefix("file://")).read_text(encoding="utf-8")
    path = Path(url)
    if path.exists():
        return path.read_text(encoding="utf-8")
    from urllib.request import urlopen

    with urlopen(url, timeout=20) as response:
        return response.read().decode("utf-8", "replace")


def matched_keywords(title: str, keywords: list[str]) -> list[str]:
    haystack = title.casefold()
    return [keyword for keyword in keywords if keyword.casefold() in haystack]


def priority_for_keywords(keywords: list[str]) -> str:
    lowered = {keyword.casefold() for keyword in keywords}
    if lowered & {"jsk", "op", "予約", "preorder", "new arrival"}:
        return "high"
    return "normal"
