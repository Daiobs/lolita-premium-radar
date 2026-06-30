import unittest
from datetime import timedelta

from lolita_radar.source_dates import current_source_date, is_current_source_date


class SourceDateTests(unittest.TestCase):
    def test_current_source_date_accepts_today_and_iso_datetime(self) -> None:
        today = current_source_date().strftime("%Y-%m-%d")

        self.assertTrue(is_current_source_date(today))
        self.assertTrue(is_current_source_date(f"{today}T10:30:00+09:00"))

    def test_current_source_date_rejects_missing_old_and_future_values(self) -> None:
        today = current_source_date()
        old_date = (today - timedelta(days=120)).strftime("%Y-%m-%d")
        future_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        self.assertFalse(is_current_source_date(""))
        self.assertFalse(is_current_source_date("not-a-date"))
        self.assertFalse(is_current_source_date(old_date))
        self.assertFalse(is_current_source_date(future_date))


if __name__ == "__main__":
    unittest.main()
