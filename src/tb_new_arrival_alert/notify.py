import json
import sys
import urllib.request
from typing import Any, Dict, Iterable, Protocol

from .models import Item, Target


class Notifier(Protocol):
    def send(self, target: Target, item: Item) -> None:
        ...


class ConsoleNotifier:
    def send(self, target: Target, item: Item) -> None:
        price = f" ¥{item.price:g}" if item.price is not None else ""
        print(f"[NEW] {target.name}: {item.title}{price}\n      {item.url}", flush=True)


class WebhookNotifier:
    def __init__(self, url: str, timeout_seconds: int = 10) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def send(self, target: Target, item: Item) -> None:
        payload = {
            "target": target.name,
            "title": item.title,
            "url": item.url,
            "price": item.price,
            "item_id": item.item_id,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response.read()
        except Exception as exc:
            print(f"[WARN] webhook failed for {item.item_id}: {exc}", file=sys.stderr)


def make_notifiers(config: Dict[str, Any]) -> list[Notifier]:
    notifiers: list[Notifier] = []
    for raw in config.get("notifications", [{"type": "console", "enabled": True}]):
        if not raw.get("enabled", True):
            continue
        kind = str(raw.get("type", "console")).lower()
        if kind == "console":
            notifiers.append(ConsoleNotifier())
        elif kind == "webhook":
            url = str(raw.get("url", "")).strip()
            if not url:
                print("[WARN] webhook notifier missing url; skipped", file=sys.stderr)
                continue
            notifiers.append(WebhookNotifier(url=url))
        else:
            print(f"[WARN] unknown notifier type {kind}; skipped", file=sys.stderr)
    return notifiers


def notify_all(notifiers: Iterable[Notifier], target: Target, item: Item) -> None:
    for notifier in notifiers:
        notifier.send(target, item)

