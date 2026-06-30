import unittest

from lolita_radar.models import EventType, ItemStatus, RadarEvent, RadarItem
from lolita_radar.notifiers import format_event


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


if __name__ == "__main__":
    unittest.main()
