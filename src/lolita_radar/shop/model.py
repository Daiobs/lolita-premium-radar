from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DROP_KEYWORDS = ("JSK", "OP", "再贩", "再販", "预约", "予約", "尾款")
HIGH_URGENCY_KEYWORDS = {"再贩", "再販", "预约", "予約", "尾款"}


@dataclass(frozen=True)
class Shop:
    name: str
    url: str = ""


@dataclass(frozen=True)
class ShopItem:
    title: str
    url: str
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class DropSignal:
    shop: Shop
    item: ShopItem
    urgency: str
    reason_codes: tuple[str, ...]


def build_drop_signal(row: dict[str, Any]) -> DropSignal | None:
    keywords = matched_drop_keywords(row)
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    if str(row.get("source") or "") != "generic_page" and str(metadata.get("source_type") or "") != "generic_page":
        return None
    if not keywords and not is_new_shop_item(row, metadata):
        return None
    if not has_shop_item(metadata):
        return None
    shop = shop_from_metadata(row, metadata)
    item = item_from_metadata(row, metadata, keywords)
    reasons = drop_reasons(row, keywords)
    return DropSignal(shop=shop, item=item, urgency=drop_urgency(row, keywords), reason_codes=tuple(reasons))


def is_new_shop_item(row: dict[str, Any], metadata: dict[str, Any]) -> bool:
    if str(row.get("event_type") or "") != "new_item":
        return False
    return has_shop_item(metadata)


def has_shop_item(metadata: dict[str, Any]) -> bool:
    raw_item = metadata.get("item")
    if isinstance(raw_item, dict) and str(raw_item.get("title") or "").strip():
        return True
    return bool(str(metadata.get("item_title") or "").strip())


def matched_drop_keywords(row: dict[str, Any]) -> tuple[str, ...]:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return ()
    raw = metadata.get("matched_keywords") or metadata.get("drop_keywords") or []
    if isinstance(raw, list):
        values = [str(keyword).strip() for keyword in raw]
    else:
        values = [str(raw).strip()]
    keywords = [keyword for keyword in values if keyword and is_drop_keyword(keyword)]
    return tuple(dict.fromkeys(keywords))


def is_drop_keyword(keyword: str) -> bool:
    normalized = keyword.lower()
    return any(token.lower() == normalized for token in DROP_KEYWORDS)


def shop_from_metadata(row: dict[str, Any], metadata: dict[str, Any]) -> Shop:
    raw_shop = metadata.get("shop")
    if isinstance(raw_shop, dict):
        name = str(raw_shop.get("name") or "").strip()
        url = str(raw_shop.get("url") or "").strip()
    else:
        name = str(metadata.get("shop_name") or "").strip()
        url = str(metadata.get("shop_url") or "").strip()
    if not name:
        name = str(row.get("source") or "Shop")
    return Shop(name=name, url=url or str(row.get("url") or ""))


def item_from_metadata(row: dict[str, Any], metadata: dict[str, Any], keywords: tuple[str, ...]) -> ShopItem:
    raw_item = metadata.get("item")
    if isinstance(raw_item, dict):
        title = str(raw_item.get("title") or "").strip()
        url = str(raw_item.get("url") or "").strip()
    else:
        title = str(metadata.get("item_title") or "").strip()
        url = str(metadata.get("item_url") or "").strip()
    if not title:
        title = str(row.get("title") or "").strip()
    return ShopItem(title=title, url=url or str(row.get("url") or ""), keywords=keywords)


def drop_urgency(row: dict[str, Any], keywords: tuple[str, ...]) -> str:
    if str(row.get("event_type") or "") == "new_item":
        return "high"
    if any(keyword in HIGH_URGENCY_KEYWORDS for keyword in keywords):
        return "high"
    if str(row.get("event_type") or "") == "content_changed":
        return "medium"
    return "low"


def drop_reasons(row: dict[str, Any], keywords: tuple[str, ...]) -> list[str]:
    reasons = []
    if keywords:
        reasons.extend(["keyword_match", *[f"kw:{keyword}" for keyword in keywords[:6]]])
    event_type = str(row.get("event_type") or "")
    if event_type == "new_item":
        reasons.insert(0, "new_shop_item")
    elif event_type == "content_changed":
        reasons.insert(0, "shop_item_changed")
    return reasons
