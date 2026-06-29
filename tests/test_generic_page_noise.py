import tempfile
import unittest
from pathlib import Path

import lolita_radar.adapters.generic_page as generic_page
from lolita_radar.adapters import SourceConfig
from lolita_radar.models import EventType
from lolita_radar.storage import connect, diff_and_store


class GenericPageNoiseTests(unittest.TestCase):
    def test_ignore_patterns_and_max_content_chars_shape_content_hash(self) -> None:
        config = SourceConfig(
            name="proxy",
            type="generic_page",
            url="https://example.com/proxy",
            keywords=["JSK", "预约"],
            options={
                "ignore_patterns": [r"updated at: [0-9: -]+"],
                "max_content_chars": 40,
                "title_template": "{source} watched page",
            },
        )
        original_fetch = generic_page.fetch_text
        try:
            generic_page.fetch_text = lambda *_args, **_kwargs: (
                "<html><body>updated at: 2026-06-30 10:00 JSK 预约 ABCDEFGHIJKLMNOPQRSTUVWXYZ</body></html>"
            )
            item = generic_page.GenericPageAdapter(config).fetch_items()[0]
        finally:
            generic_page.fetch_text = original_fetch

        self.assertEqual(item.title, "proxy watched page")
        self.assertEqual(item.metadata["matched_keywords"], ["JSK", "预约"])
        self.assertNotIn("updated at", item.content.lower())
        self.assertLessEqual(len(item.content), 40)

    def test_min_keyword_hits_filters_low_signal_page(self) -> None:
        config = SourceConfig(
            name="proxy",
            type="generic_page",
            url="https://example.com/proxy",
            keywords=["JSK", "预约"],
            options={"min_keyword_hits": 2},
        )
        original_fetch = generic_page.fetch_text
        try:
            generic_page.fetch_text = lambda *_args, **_kwargs: "<html><body>JSK only</body></html>"
            items = generic_page.GenericPageAdapter(config).fetch_items()
        finally:
            generic_page.fetch_text = original_fetch

        self.assertEqual(items, [])

    def test_content_change_alert_false_suppresses_content_changed_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "radar.sqlite")
            config = SourceConfig(
                name="proxy",
                type="generic_page",
                url="https://example.com/proxy",
                keywords=["JSK"],
                options={"content_change_alert": False, "title_template": "{source} page"},
            )
            original_fetch = generic_page.fetch_text
            try:
                generic_page.fetch_text = lambda *_args, **_kwargs: "<html><body>JSK first text</body></html>"
                first = generic_page.GenericPageAdapter(config).fetch_items()[0]
                generic_page.fetch_text = lambda *_args, **_kwargs: "<html><body>JSK changed text</body></html>"
                second = generic_page.GenericPageAdapter(config).fetch_items()[0]

                first_events = diff_and_store(connection, [first])
                second_events = diff_and_store(connection, [second])
            finally:
                generic_page.fetch_text = original_fetch
                connection.close()

            self.assertEqual([event.event_type for event in first_events], [EventType.NEW_ITEM])
            self.assertEqual(second_events, [])

    def test_timestamp_only_change_is_ignored_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "radar.sqlite")
            config = SourceConfig(
                name="proxy",
                type="generic_page",
                url="https://example.com/proxy",
                keywords=["JSK"],
                options={"title_template": "{source} page"},
            )
            original_fetch = generic_page.fetch_text
            try:
                generic_page.fetch_text = lambda *_args, **_kwargs: (
                    "<html><body>Login Cart updated at: 2026-06-30 10:00 JSK Shell Garden JSK.</body></html>"
                )
                first = generic_page.GenericPageAdapter(config).fetch_items()[0]
                generic_page.fetch_text = lambda *_args, **_kwargs: (
                    "<html><body>Login Cart updated at: 2026-06-30 11:00 JSK Shell Garden JSK.</body></html>"
                )
                second = generic_page.GenericPageAdapter(config).fetch_items()[0]

                first_events = diff_and_store(connection, [first])
                second_events = diff_and_store(connection, [second])
            finally:
                generic_page.fetch_text = original_fetch
                connection.close()

            self.assertNotIn("Login", first.content)
            self.assertNotIn("Cart", first.content)
            self.assertEqual(first.content_hash, second.content_hash)
            self.assertEqual([event.event_type for event in first_events], [EventType.NEW_ITEM])
            self.assertEqual(second_events, [])


if __name__ == "__main__":
    unittest.main()
