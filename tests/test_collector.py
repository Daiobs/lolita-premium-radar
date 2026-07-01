import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from lolita_radar.collector import (
    DEFAULT_COLLECTOR_JOBS,
    ClosetChildMarketCollector,
    CollectorJob,
    FixtureMarketCollector,
    LaceMarketCollector,
    OfficialShopCollector,
    WunderweltMarketCollector,
    run_collector_job,
)
from lolita_radar.models import ShopItem
from lolita_radar.storage import (
    connect,
    diff_and_store_shop_items,
    list_collector_jobs,
    list_collector_runs,
    list_market_samples,
    list_shop_events,
    list_shop_items,
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

    def test_official_shop_collector_parses_shopify_products_json(self) -> None:
        result = OfficialShopCollector().collect(
            CollectorJob(
                name="baby_json",
                collector_type="official_shop",
                url="tests/fixtures/shopify_products.json",
                options={
                    "parser": "shopify_products_json",
                    "shop_name": "BABY Official Store",
                    "platform": "official_store",
                    "currency": "JPY",
                    "base_url": "https://store.babyssb.co.jp/en",
                    "keywords": ["JSK", "Reservation"],
                },
            )
        )

        self.assertEqual(len(result.shop_items), 2)
        first = result.shop_items[0]
        self.assertEqual(first.title, "Royal Bear JSK Reservation")
        self.assertEqual(first.price, "32780")
        self.assertEqual(first.currency, "JPY")
        self.assertEqual(first.availability, "in_stock")
        self.assertEqual(first.item_url, "https://store.babyssb.co.jp/en/products/royal-bear-jsk-reservation")
        self.assertEqual(first.image_url, "https://example.com/royal-bear.webp")
        self.assertEqual(first.observed_at, "2026-07-01")
        self.assertEqual(first.priority, "high")

    def test_closet_child_collector_parses_real_item_cards(self) -> None:
        result = ClosetChildMarketCollector().collect(
            CollectorJob(
                name="closet_child",
                collector_type="closet_child_market",
                url="tests/fixtures/closet_child_new.html",
                options={
                    "shop_name": "Closet Child",
                    "platform": "closet_child",
                    "currency": "JPY",
                    "observed_at": "2026-07-01",
                    "keywords": ["ワンピース", "Angelic Pretty"],
                },
            )
        )

        self.assertEqual(len(result.shop_items), 2)
        self.assertEqual(len(result.market_samples), 2)
        first = result.shop_items[0]
        self.assertEqual(first.title, "Moi-meme-Moitie / サイドギャザー十字架ワンピース")
        self.assertEqual(first.price, "77000")
        self.assertEqual(first.currency, "JPY")
        self.assertEqual(first.availability, "in_stock")
        self.assertEqual(first.item_url, "https://www.closetchildonlineshop.com/product/943274")
        self.assertEqual(first.image_url, "https://www.closetchildonlineshop.com/data/closetchild/product/20260701.jpg")
        self.assertEqual(first.matched_keywords, ["ワンピース"])
        sample = result.market_samples[0]
        self.assertEqual(sample.platform, "closet_child")
        self.assertEqual(sample.brand_alias, "MMM")
        self.assertEqual(sample.asking_price, 77000.0)
        self.assertEqual(sample.condition, "used")

    def test_default_collector_jobs_include_real_public_sources(self) -> None:
        names = {str(job["name"]) for job in DEFAULT_COLLECTOR_JOBS}
        self.assertIn("baby_official_store", names)
        self.assertIn("baby_sf_new_arrivals", names)
        self.assertIn("closet_child_new_arrivals", names)
        self.assertIn("wunderwelt_new_arrivals", names)

    def test_wunderwelt_market_collector_parses_public_cards(self) -> None:
        result = WunderweltMarketCollector().collect(
            CollectorJob(
                name="wunderwelt",
                collector_type="wunderwelt_market",
                url="tests/fixtures/wunderwelt_market_cards.html",
                options={"keywords": ["dress"]},
            )
        )

        self.assertEqual(len(result.shop_items), 1)
        self.assertEqual(len(result.market_samples), 1)
        self.assertEqual(result.shop_items[0].shop_name, "Wunderwelt")
        self.assertEqual(result.market_samples[0].brand_alias, "VM")
        self.assertEqual(result.market_samples[0].asking_price, 39800.0)

    def test_lace_market_collector_parses_public_cards(self) -> None:
        result = LaceMarketCollector().collect(
            CollectorJob(
                name="lace",
                collector_type="lace_market",
                url="tests/fixtures/lace_market_cards.html",
                options={"keywords": ["JSK"]},
            )
        )

        self.assertEqual(len(result.shop_items), 1)
        self.assertEqual(len(result.market_samples), 1)
        self.assertEqual(result.shop_items[0].currency, "USD")
        self.assertEqual(result.market_samples[0].brand_alias, "AP")
        self.assertEqual(result.market_samples[0].pattern, "Shell Garden")

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

    def test_collector_baseline_writes_items_without_shop_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "radar.sqlite")
            try:
                run = run_collector_job(
                    connection,
                    CollectorJob(name="baby_new", collector_type="official_shop", url="tests/fixtures/official_shop_products.html"),
                    OfficialShopCollector(),
                    baseline_only=True,
                )
                events = list_shop_events(connection)
                items = list_shop_items(connection)
            finally:
                connection.close()

        self.assertTrue(run.ok)
        self.assertEqual(len(items), 2)
        self.assertEqual(events, [])

    def test_normal_collect_after_baseline_creates_drop_for_new_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "radar.sqlite")
            try:
                run_collector_job(
                    connection,
                    CollectorJob(name="one", collector_type="official_shop", url="tests/fixtures/official_shop_one_product.html"),
                    OfficialShopCollector(),
                    baseline_only=True,
                )
                run_collector_job(
                    connection,
                    CollectorJob(name="two", collector_type="official_shop", url="tests/fixtures/official_shop_products.html"),
                    OfficialShopCollector(),
                )
                events = list_shop_events(connection)
            finally:
                connection.close()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "DROP")

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
