import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tb_new_arrival_alert.models import Item, Target
from tb_new_arrival_alert.monitor import Monitor
from tb_new_arrival_alert.storage import SeenStore


class FakeFetcher:
    def __init__(self, pages: list[str]) -> None:
        self.pages = pages

    def fetch(self, url: str) -> str:
        return self.pages.pop(0)


class CollectingNotifier:
    def __init__(self) -> None:
        self.items: list[Item] = []

    def send(self, target: Target, item: Item) -> None:
        self.items.append(item)


def item_html(item_id: str, title: str) -> str:
    return f'<a href="https://item.taobao.com/item.htm?id={item_id}">{title}</a>'


class MonitorTests(unittest.TestCase):
    def test_first_scan_builds_baseline_without_notification(self) -> None:
        first_page = item_html("100000000001", "星夜柄 JSK 现货 ￥399")
        second_page = first_page + item_html("100000000002", "花园柄 OP 现货 ￥499")
        target = Target(
            name="sample",
            url="https://shop.example.taobao.com/search.htm",
            enabled=True,
            include_keywords=("现货",),
            exclude_keywords=(),
            price_min=None,
            price_max=600,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            notifier = CollectingNotifier()
            monitor = Monitor(
                fetcher=FakeFetcher([first_page, second_page]),
                store=SeenStore(Path(temp_dir) / "seen.json"),
                notifiers=[notifier],
                notify_on_first_scan=False,
            )

            with contextlib.redirect_stdout(io.StringIO()):
                monitor.check_target(target)
            self.assertEqual(notifier.items, [])

            with contextlib.redirect_stdout(io.StringIO()):
                monitor.check_target(target)
            self.assertEqual([item.item_id for item in notifier.items], ["100000000002"])


if __name__ == "__main__":
    unittest.main()
