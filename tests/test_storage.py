import tempfile
import unittest
from pathlib import Path

from lolita_radar.models import ItemStatus, RadarItem, item_identity_hash
from lolita_radar.storage import connect, diff_and_store


class StorageTests(unittest.TestCase):
    def test_identity_hash_uses_source_and_url(self) -> None:
        first = item_identity_hash("metamorphose", "https://example.com/a", "Old Title")
        second = item_identity_hash("metamorphose", "https://example.com/a", "New Title")
        other_source = item_identity_hash("other", "https://example.com/a", "Old Title")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other_source)

    def test_diff_creates_new_and_update_events_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "radar.sqlite")
            item = RadarItem(
                source="metamorphose",
                title="New Arrival: Rose JSK",
                url="https://example.com/news/rose",
                status=ItemStatus.NEW_ARRIVAL,
            )

            first_events = diff_and_store(connection, [item])
            duplicate_events = diff_and_store(connection, [item])
            updated_events = diff_and_store(
                connection,
                [
                    RadarItem(
                        source="metamorphose",
                        title="Restock: Rose JSK",
                        url="https://example.com/news/rose",
                        status=ItemStatus.RESTOCK,
                    )
                ],
            )

            self.assertEqual([event.event_type.value for event in first_events], ["new_item"])
            self.assertEqual(duplicate_events, [])
            self.assertEqual([event.event_type.value for event in updated_events], ["update"])
            self.assertEqual(updated_events[0].previous_status, "new_arrival")
            self.assertEqual(updated_events[0].previous_title, "New Arrival: Rose JSK")
            connection.close()


if __name__ == "__main__":
    unittest.main()
