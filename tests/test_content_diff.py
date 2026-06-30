import tempfile
import unittest
from pathlib import Path

from lolita_radar.models import EventType, ItemStatus, RadarItem
from lolita_radar.storage import connect, diff_and_store


class ContentDiffTests(unittest.TestCase):
    def test_content_change_creates_one_event_without_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "radar.sqlite")
            try:
                first = RadarItem(
                    source="generic_page",
                    title="Proxy Shop Page",
                    url="https://example.com/shop",
                    status=ItemStatus.SHOP_NEWS,
                    content="Shell JSK preorder starts at 20:00.",
                )
                changed = RadarItem(
                    source="generic_page",
                    title="Proxy Shop Page",
                    url="https://example.com/shop",
                    status=ItemStatus.SHOP_NEWS,
                    content="Shell JSK preorder starts at 20:00. New color added.",
                )

                self.assertEqual([event.event_type for event in diff_and_store(connection, [first])], [EventType.NEW_ITEM])
                self.assertEqual(diff_and_store(connection, [first]), [])

                changed_events = diff_and_store(connection, [changed])
                repeated_events = diff_and_store(connection, [changed])

                self.assertEqual([event.event_type for event in changed_events], [EventType.CONTENT_CHANGED])
                self.assertEqual(changed_events[0].previous_content_hash, first.content_hash)
                self.assertEqual(repeated_events, [])
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
