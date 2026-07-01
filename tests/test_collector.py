import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from lolita_radar.collector import CollectorJob, FixtureMarketCollector, OfficialShopCollector, run_collector_job
from lolita_radar.models import ShopItem
from lolita_radar.storage import (
    connect,
    diff_and_store_shop_items,
    list_collector_jobs,
    list_collector_runs,
    list_market_samples,
    upsert_collector_job,
)


class CollectorTests(unittest.TestCase):
    def test_official_shop_collector_parses_product_cards(self) -> None:
        fixture = Path("tests/fixtures/official_shop_products.html")
        result = OfficialShopCollector().collect(
            CollectorJob(
                name="baby_new",
                collector_type="official_shop",
                url=str(fixture),
                options={"keywords": ["JSK", "Reservation"], "shop_name": "BABY", "platform": "official_store"},
            )
        )

        self.assertEqual(len(result.shop_items), 2)
        first = result.shop_items[0]
        self.assertEqual(first.shop_name, "BABY Official Store")
        self.assertEqual(first.platform, "official_store")
        self.assertEqual(first.title, "Usakumya JSK Reservation")
        self.assertEqual(first.price, "30800")
        self.assertEqual(first.currency, "JPY")
        self.assertEqual(first.availability, "in_stock")
        self.assertEqual(first.matched_keywords, ["JSK", "Reservation"])
        self.assertEqual(first.priority, "high")

    def test_shop_item_storage_creates_drop_price_and_stock_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "radar.sqlite")
            try:
                item = ShopItem(
                    shop_name="BABY",
                    platform="official_store",
                    title="Usakumya JSK",
                    price="30800",
                    currency="JPY",
                    item_url="https://example.com/usakumya",
                    availability="sold_out",
                    matched_keywords=["JSK"],
                    observed_at="2026-07-01",
                )
                first = diff_and_store_shop_items(connection, [item])
                price = diff_and_store_shop_items(connection, [replace(item, price="32800")])
            finally:
                connection.close()

        self.assertEqual([event.event_type.value for event in first], ["DROP"])
        self.assertEqual([event.event_type.value for event in price], ["PRICE_CHANGED"])

    def test_fixture_market_collector_and_runner_store_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "radar.sqlite")
            try:
                upsert_collector_job(connection, "mercari_fixture", "fixture_market", "tests/fixtures/market_samples.html")
                run = run_collector_job(
                    connection,
                    CollectorJob(name="mercari_fixture", collector_type="fixture_market", url="tests/fixtures/market_samples.html"),
                    FixtureMarketCollector(),
                )
                samples = list_market_samples(connection)
                runs = list_collector_runs(connection)
            finally:
                connection.close()

        self.assertTrue(run.ok)
        self.assertEqual(run.item_count, 4)
        self.assertEqual(len(samples), 4)
        self.assertEqual(runs[0]["status"], "ok")

    def test_collector_failure_is_recorded_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "radar.sqlite")
            try:
                upsert_collector_job(connection, "bad", "official_shop", "/missing/file.html")
                run = run_collector_job(
                    connection,
                    CollectorJob(name="bad", collector_type="official_shop", url="/missing/file.html"),
                    OfficialShopCollector(),
                )
                runs = list_collector_runs(connection)
            finally:
                connection.close()

        self.assertFalse(run.ok)
        self.assertEqual(run.status, "failed")
        self.assertIn("missing", runs[0]["error_message"])

    def test_consecutive_collector_failures_mark_job_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "radar.sqlite")
            try:
                upsert_collector_job(connection, "bad", "official_shop", "/missing/file.html")
                job = CollectorJob(name="bad", collector_type="official_shop", url="/missing/file.html")
                run_collector_job(connection, job, OfficialShopCollector())
                run_collector_job(connection, job, OfficialShopCollector())
                jobs = list_collector_jobs(connection, enabled_only=False)
            finally:
                connection.close()

        self.assertTrue(jobs[0]["degraded"])
        self.assertEqual(jobs[0]["consecutive_failures"], 2)


if __name__ == "__main__":
    unittest.main()
