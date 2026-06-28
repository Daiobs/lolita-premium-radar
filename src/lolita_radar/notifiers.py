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
            prefix = "NEW" if event.event_type.value == "new_item" else "UPDATE"
            print(f"[{prefix}] {event.source} {event.item.status.value}: {event.item.title}")
            print(f"  {event.item.url}")
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
    label = "New item" if event.event_type.value == "new_item" else "Updated item"
    return (
        f"{label}: {event.source}\n"
        f"{event.item.status.value}: {event.item.title}\n"
        f"{event.item.url}"
    )
