from __future__ import annotations

from typing import Protocol

from .models import RadarEvent


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
    brand = str(metadata.get("brand") or "-")
    matched_keywords = metadata.get("matched_keywords") or []
    if isinstance(matched_keywords, list):
        matched_keyword_text = ", ".join(str(keyword) for keyword in matched_keywords[:8])
    else:
        matched_keyword_text = str(matched_keywords)
    lines = [
        f"brand: {brand}",
        f"source: {event.source}",
        f"event_type: {event.event_type.value}",
        f"status: {event.item.status.value}",
        f"title: {event.item.title[:240]}",
        f"published_at: {event.item.published_at or '-'}",
        f"url: {event.item.url}",
    ]
    if matched_keyword_text:
        lines.append(f"matched_keywords: {matched_keyword_text}")
    if event.event_type.value == "content_changed":
        lines.append(
            "content_hash: "
            f"{short_hash(event.previous_content_hash)} -> {short_hash(event.item.content_hash)}"
        )
    return "\n".join(lines)


def short_hash(value: str) -> str:
    return (value or "-")[:10]
