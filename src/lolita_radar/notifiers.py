from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
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


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id

    def notify(self, events: list[RadarEvent]) -> None:
        for event in events:
            payload = urllib.parse.urlencode(
                {
                    "chat_id": self.chat_id,
                    "text": format_event(event),
                    "disable_web_page_preview": "false",
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data=payload,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=15):
                pass


class DiscordWebhookNotifier:
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def notify(self, events: list[RadarEvent]) -> None:
        for event in events:
            body = json.dumps({"content": format_event(event)}, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                self.webhook_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=15):
                pass


def build_notifiers_from_env() -> list[Notifier]:
    notifiers: list[Notifier] = [ConsoleNotifier()]
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if telegram_token and telegram_chat_id:
        notifiers.append(TelegramNotifier(telegram_token, telegram_chat_id))
    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if discord_webhook:
        notifiers.append(DiscordWebhookNotifier(discord_webhook))
    return notifiers


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
