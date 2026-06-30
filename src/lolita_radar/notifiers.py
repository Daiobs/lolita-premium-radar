from __future__ import annotations

from typing import Protocol

from .models import RadarEvent
from .shop import build_drop_signal


class Notifier(Protocol):
    def notify(self, events: list[RadarEvent]) -> None:
        ...


class ConsoleNotifier:
    def notify(self, events: list[RadarEvent]) -> None:
        if not events:
            print("No new events.")
            return
        for event in events:
            print(format_event(event))
            if event.previous_title or event.previous_status:
                print(f"  previous: {event.previous_status} {event.previous_title}")


def build_notifiers_from_env() -> list[Notifier]:
    return [ConsoleNotifier()]


def notify_all(notifiers: list[Notifier], events: list[RadarEvent]) -> None:
    for notifier in notifiers:
        notifier.notify(events)


def format_event(event: RadarEvent) -> str:
    metadata = event.item.metadata or {}
    brand = str(metadata.get("brand") or event.source or "-")
    matched_keywords = metadata.get("matched_keywords") or []
    if isinstance(matched_keywords, list):
        matched_keyword_text = ", ".join(str(keyword) for keyword in matched_keywords[:8])
    else:
        matched_keyword_text = str(matched_keywords)
    price = str(metadata.get("price") or "")
    status = event.item.status.value
    lines = [
        f"{notification_kind(event)} · {brand}",
        event.item.title[:240],
        f"源头发布时间 / 掲載元日: {event.item.published_at or '-'}",
        f"状态 / 状態: {status_label(status)}",
        f"来源 / ソース: {event.source}",
    ]
    if price:
        lines.append(f"价格 / 価格: {price}")
    if matched_keyword_text:
        lines.append(f"关键词 / キーワード: {matched_keyword_text}")
    lines.append(f"链接 / URL: {event.item.url}")
    if event.event_type.value == "content_changed":
        lines.append(
            "变化 / 変更: "
            f"{short_hash(event.previous_content_hash)} -> {short_hash(event.item.content_hash)}"
        )
    return "\n".join(lines)


def notification_kind(event: RadarEvent) -> str:
    status = event.item.status.value
    if event.event_type.value == "content_changed":
        return "ALERT"
    if status == "shop_news":
        return "DROP" if build_drop_signal(event_row(event)) is not None else "ALERT"
    return {
        "new_arrival": "RELEASE",
        "preorder": "RELEASE",
        "restock": "RELEASE",
        "sold_out": "ALERT",
    }.get(status, "ALERT")


def event_row(event: RadarEvent) -> dict[str, object]:
    return {
        "source": event.source,
        "event_type": event.event_type.value,
        "status": event.item.status.value,
        "title": event.item.title,
        "url": event.item.url,
        "published_at": event.item.published_at,
        "metadata": dict(event.item.metadata or {}),
    }


def status_label(status: str) -> str:
    return {
        "new_arrival": "新作上架 / 新着",
        "preorder": "预约 / 予約",
        "restock": "再贩 / 再入荷",
        "shop_news": "店铺上新 / ショップ更新",
        "sold_out": "售罄 / 完売",
    }.get(status, status)


def short_hash(value: str) -> str:
    return (value or "-")[:10]
