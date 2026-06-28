from datetime import datetime
from typing import Iterable

from .extractors import extract_items
from .fetchers import Fetcher
from .matcher import matches_target
from .models import Item, Target
from .notify import Notifier, notify_all
from .storage import SeenStore


class Monitor:
    def __init__(
        self,
        fetcher: Fetcher,
        store: SeenStore,
        notifiers: Iterable[Notifier],
        notify_on_first_scan: bool = False,
    ) -> None:
        self.fetcher = fetcher
        self.store = store
        self.notifiers = list(notifiers)
        self.notify_on_first_scan = notify_on_first_scan

    def check_target(self, target: Target) -> list[Item]:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] checking {target.name}")

        html_text = self.fetcher.fetch(target.url)
        extracted = extract_items(html_text, target.url)
        matched = [item for item in extracted if matches_target(item, target)]

        had_baseline = self.store.has_baseline(target.name)
        new_items = self.store.new_items(target.name, matched)
        self.store.mark_seen(target.name, matched)
        self.store.save()

        should_notify = had_baseline or self.notify_on_first_scan
        if should_notify:
            for item in new_items:
                notify_all(self.notifiers, target, item)

        print(
            f"      found={len(extracted)} matched={len(matched)} "
            f"new={len(new_items)} notified={len(new_items) if should_notify else 0}"
        )
        return new_items if should_notify else []

    def check_all(self, targets: Iterable[Target]) -> list[Item]:
        all_new: list[Item] = []
        for target in targets:
            if not target.enabled:
                continue
            try:
                all_new.extend(self.check_target(target))
            except Exception as exc:
                print(f"[WARN] failed checking {target.name}: {exc}")
        return all_new

