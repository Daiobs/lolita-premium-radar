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


@dataclass(frozen=True)
class RadarEvent:
    source: str
    event_type: EventType
    item: RadarItem
    previous_title: str = ""
    previous_status: str = ""
    created_at: str = ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def item_identity_hash(source: str, url: str, title: str) -> str:
    key = f"{source.strip().lower()}|{(url or title).strip().lower()}"
    return sha256(key.encode("utf-8")).hexdigest()


def classify_title(title: str) -> ItemStatus:
    normalized = title.lower()
    preorder_tokens = ("pre-order", "preorder", "pre order", "reservation", "予約", "受注", "预定", "预约")
    restock_tokens = ("restock", "re-stock", "arrival again", "再入荷", "再贩", "再販", "补货")
    new_tokens = ("new arrival", "new item", "new release", "新作", "新品", "上新", "入荷")
    if any(token in normalized for token in preorder_tokens):
        return ItemStatus.PREORDER
    if any(token in normalized for token in restock_tokens):
        return ItemStatus.RESTOCK
    if any(token in normalized for token in new_tokens):
        return ItemStatus.NEW_ARRIVAL
    return ItemStatus.SHOP_NEWS
