import unittest
from unittest.mock import patch

from lolita_radar.models import EventType, ItemStatus, RadarEvent, RadarItem
from lolita_radar.notifiers import ConsoleNotifier, build_notifiers_from_env, format_event


class NotificationTests(unittest.TestCase):
    def test_notification_contains_actionable_fields(self) -> None:
        item = RadarItem(
            source="angelic_pretty",
            title="New Arrival: Shell JSK",
            url="https://example.com/shell",
            status=ItemStatus.NEW_ARRIVAL,
            published_at="2026-06-30",
            metadata={"brand": "Angelic Pretty", "matched_keywords": ["Shell", "JSK"]},
        )

        text = format_event(RadarEvent(source=item.source, event_type=EventType.NEW_ITEM, item=item))

        self.assertIn("brand: Angelic Pretty", text)
        self.assertIn("source: angelic_pretty", text)
        self.assertIn("event_type: new_item", text)
        self.assertIn("status: new_arrival", text)
        self.assertIn("title: New Arrival: Shell JSK", text)
        self.assertIn("published_at: 2026-06-30", text)
        self.assertIn("url: https://example.com/shell", text)
        self.assertIn("matched_keywords: Shell, JSK", text)

    def test_content_changed_notification_includes_short_hashes(self) -> None:
        item = RadarItem(
            source="generic_page",
            title="Shop page",
            url="https://example.com/shop",
            status=ItemStatus.SHOP_NEWS,
            content="new content",
        )

        text = format_event(
            RadarEvent(
                source=item.source,
                event_type=EventType.CONTENT_CHANGED,
                item=item,
                previous_content_hash="abcdef1234567890",
            )
        )

        self.assertIn("event_type: content_changed", text)
        self.assertIn("content_hash: abcdef1234 ->", text)

    def test_build_notifiers_from_env_stays_local_only(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "token",
                "TELEGRAM_CHAT_ID": "chat",
                "DISCORD_WEBHOOK_URL": "https://example.com/webhook",
            },
        ):
            notifiers = build_notifiers_from_env()

        self.assertEqual(len(notifiers), 1)
        self.assertIsInstance(notifiers[0], ConsoleNotifier)


if __name__ == "__main__":
    unittest.main()
