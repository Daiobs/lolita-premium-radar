from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any


class ItemStatus(str, Enum):
    NEW_ARRIVAL = "new_arrival"
    PREORDER = "preorder"
    RESTOCK = "restock"
    SHOP_NEWS = "shop_news"


class EventType(str, Enum):
    NEW_ITEM = "new_item"
    UPDATE = "update"
    CONTENT_CHANGED = "content_changed"


class ShopEventType(str, Enum):
    DROP = "DROP"
    PRICE_CHANGED = "PRICE_CHANGED"
    STOCK_CHANGED = "STOCK_CHANGED"


@dataclass(frozen=True)
class RadarItem:
    source: str
    title: str
    url: str
    status: ItemStatus
    published_at: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def identity_hash(self) -> str:
        return item_identity_hash(self.source, self.url, self.title)

    @property
    def content_hash(self) -> str:
        return item_content_hash(self.content)


@dataclass(frozen=True)
class RadarEvent:
    source: str
    event_type: EventType
    item: RadarItem
    previous_title: str = ""
    previous_status: str = ""
    previous_content_hash: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class ShopItem:
    shop_name: str
    platform: str
    title: str
    price: str = ""
    currency: str = ""
    image_url: str = ""
    item_url: str = ""
    availability: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    observed_at: str = ""
    sale_at: str = ""
    remind_at: str = ""
    purchase_url: str = ""
    priority: str = ""

    @property
    def title_hash(self) -> str:
        return title_hash(self.title)

    @property
    def identity_key(self) -> str:
        return shop_item_identity_key(self.item_url, self.title)


@dataclass(frozen=True)
class ShopEvent:
    event_type: ShopEventType
    item: ShopItem
    previous_price: str = ""
    previous_availability: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class MarketSample:
    platform: str
    brand_alias: str
    pattern: str
    title: str
    asking_price: float
    currency: str = ""
    condition: str = ""
    url: str = ""
    image_url: str = ""
    observed_at: str = ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def item_identity_hash(source: str, url: str, title: str) -> str:
    key = f"{source.strip().lower()}|{(url or title).strip().lower()}"
    return sha256(key.encode("utf-8")).hexdigest()


def item_content_hash(content: str) -> str:
    normalized = " ".join(str(content or "").split())
    return sha256(normalized.encode("utf-8")).hexdigest()


def title_hash(title: str) -> str:
    normalized = " ".join(str(title or "").strip().lower().split())
    return sha256(normalized.encode("utf-8")).hexdigest()


def shop_item_identity_key(item_url: str, title: str) -> str:
    value = str(item_url or "").strip().lower() or title_hash(title)
    return sha256(value.encode("utf-8")).hexdigest()


def classify_title(title: str) -> ItemStatus:
    normalized = title.lower()
    preorder_tokens = ("pre-order", "preorder", "pre order", "reservation", "予約", "受注", "ご予約", "预定", "预约")
    restock_tokens = ("restock", "re-stock", "arrival again", "再入荷", "再贩", "再販", "补货")
    new_tokens = ("new arrival", "new item", "new release", "販売開始", "新作", "新品", "上新", "入荷")
    if any(token in normalized for token in preorder_tokens):
        return ItemStatus.PREORDER
    if any(token in normalized for token in restock_tokens):
        return ItemStatus.RESTOCK
    if any(token in normalized for token in new_tokens):
        return ItemStatus.NEW_ARRIVAL
    return ItemStatus.SHOP_NEWS
