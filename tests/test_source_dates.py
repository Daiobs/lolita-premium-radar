import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import lolita_radar.source_dates as source_dates


class SourceDateTests(unittest.TestCase):
    def test_current_source_date_accepts_today_and_iso_datetime(self) -> None:
        today = source_dates.current_source_date().strftime("%Y-%m-%d")

        self.assertTrue(source_dates.is_current_source_date(today))
        self.assertTrue(source_dates.is_current_source_date(f"{today}T10:30:00+09:00"))

    def test_current_source_date_rejects_missing_old_and_future_values(self) -> None:
        today = source_dates.current_source_date()
        old_date = (today - timedelta(days=120)).strftime("%Y-%m-%d")
        future_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        self.assertFalse(source_dates.is_current_source_date(""))
        self.assertFalse(source_dates.is_current_source_date("not-a-date"))
        self.assertFalse(source_dates.is_current_source_date(old_date))
        self.assertFalse(source_dates.is_current_source_date(future_date))

    def test_current_source_date_uses_local_day_instead_of_utc_day(self) -> None:
        class FakeDateTime:
            @classmethod
            def now(cls) -> datetime:
                return datetime(2026, 6, 30, 18, 30, tzinfo=timezone.utc)

        original_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Asia/Shanghai"
            if hasattr(time, "tzset"):
                time.tzset()
            with patch.object(source_dates, "datetime", FakeDateTime):
                self.assertEqual(source_dates.current_source_date().isoformat(), "2026-07-01")
                self.assertTrue(source_dates.is_current_source_date("2026-07-01"))
                self.assertFalse(source_dates.is_current_source_date("2026-07-02"))
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            if hasattr(time, "tzset"):
                time.tzset()


if __name__ == "__main__":
    unittest.main()
