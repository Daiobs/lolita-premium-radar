import json
import socket
import tempfile
import unittest
from pathlib import Path

from tb_new_arrival_alert.radar import add_price_sample, connect_radar_db, create_radar_item
from tb_new_arrival_alert.web import (
    add_radar_sample_from_payload,
    collect_release_items_from_payload,
    collect_radar_samples_from_payload,
    create_radar_item_from_payload,
    create_watch_target_from_payload,
    delete_radar_item_from_payload,
    delete_radar_review_from_payload,
    delete_radar_sample_from_payload,
    get_radar,
    import_radar_from_payload,
    make_handler,
    open_server,
    scan_once,
    update_radar_item_from_payload,
    update_radar_sample_from_payload,
    upsert_radar_review_from_payload,
)


class WebApiTests(unittest.TestCase):
    def test_scan_once_returns_structured_results(self) -> None:
        base_config = json.loads(Path("tests/fixtures/sample_config.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            base_config["data_dir"] = str(Path(temp_dir) / "data")
            config_path.write_text(json.dumps(base_config, ensure_ascii=False), encoding="utf-8")

            result = scan_once(config_path, send_notifications=False)

            self.assertTrue(result["ok"])
            self.assertEqual(result["total_new"], 1)
            self.assertEqual(result["results"][0]["found"], 3)
            self.assertEqual(result["results"][0]["matched"], 1)
            self.assertEqual(result["results"][0]["items"][0]["item_id"], "100000000001")

    def test_open_server_falls_back_when_port_is_busy(self) -> None:
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            busy_port = occupied.getsockname()[1]

            handler = make_handler(Path("tests/fixtures/sample_config.json"))
            server, actual_port = open_server("127.0.0.1", busy_port, handler, fallback_attempts=3)
            try:
                self.assertNotEqual(actual_port, busy_port)
            finally:
                server.server_close()

    def test_get_radar_returns_results_from_config_data_dir(self) -> None:
        base_config = json.loads(Path("tests/fixtures/sample_config.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            data_dir = Path(temp_dir) / "data"
            base_config["data_dir"] = str(data_dir)
            config_path.write_text(json.dumps(base_config, ensure_ascii=False), encoding="utf-8")

            connection = connect_radar_db(data_dir / "radar.sqlite")
            try:
                item_id = create_radar_item(
                    connection,
                    brand_name="BABY",
                    series_name="Sample",
                    item_name="OP",
                    category="OP",
                    original_price_jpy=25000,
                    release_date="2026-08-10",
                    source_url="https://example.com/baby-release",
                )
                add_price_sample(connection, item_id, "xianyu", listed_price_cny=2000)
            finally:
                connection.close()

            payload = get_radar(config_path)

            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["results"]), 1)
            self.assertGreaterEqual(len(payload["aggregates"]), 2)
            self.assertEqual(payload["results"][0]["label"], "BABY / Sample / OP / OP")
            self.assertEqual(payload["results"][0]["release_date"], "2026-08-10")
            self.assertEqual(payload["results"][0]["source_url"], "https://example.com/baby-release")
            self.assertEqual(payload["items"][0]["label"], "BABY / Sample / OP / OP")
            self.assertEqual(payload["items"][0]["release_date"], "2026-08-10")
            self.assertEqual(payload["release_watch"][0]["item_id"], item_id)
            self.assertEqual(payload["release_watch"][0]["release_date"], "2026-08-10")
            task_types = {task["task_type"] for task in payload["collection_tasks"]}
            self.assertIn("secondhand_samples", task_types)
            self.assertIn("domestic_samples", task_types)
            sample_tasks = [task for task in payload["collection_tasks"] if task["task_type"] == "secondhand_samples"]
            self.assertEqual(sample_tasks[0]["action_type"], "add_sample")
            self.assertEqual(sample_tasks[0]["suggested_source_type"], "xianyu")
            self.assertEqual(payload["watch_recommendations"][0]["item_id"], item_id)
            self.assertIn("suggested_price_max", payload["watch_recommendations"][0])
            self.assertEqual(payload["reviews"], [])
            self.assertEqual(payload["review_stats"]["total_count"], 0)

    def test_radar_write_apis_create_item_sample_and_import(self) -> None:
        base_config = json.loads(Path("tests/fixtures/sample_config.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            base_config["data_dir"] = str(Path(temp_dir) / "data")
            config_path.write_text(json.dumps(base_config, ensure_ascii=False), encoding="utf-8")

            item_payload = create_radar_item_from_payload(
                config_path,
                {
                    "brand_name": "Metamorphose",
                    "series_name": "Sample Series",
                    "item_name": "JSK",
                    "category": "JSK",
                    "colorway": "Green",
                    "original_price_jpy": 28000,
                    "jpy_to_cny": 0.05,
                    "release_date": "2026-09-01",
                    "source_url": "https://example.com/meta-release",
                },
            )
            item_id = item_payload["created_item_id"]
            sample_payload = add_radar_sample_from_payload(
                config_path,
                {
                    "item_id": item_id,
                    "source_type": "xianyu",
                    "listed_price_cny": 2300,
                    "listing_status": "listed",
                    "confidence": 0.8,
                },
            )

            csv_path = Path(temp_dir) / "more.csv"
            csv_path.write_text(
                "brand_name,item_name,category,sample_source_type,listed_price_cny\n"
                "Innocent World,Sample OP,OP,xianyu,1800\n",
                encoding="utf-8",
            )
            import_payload = import_radar_from_payload(config_path, {"path": str(csv_path)})

            self.assertGreater(item_id, 0)
            self.assertEqual(item_payload["release_watch"][0]["release_date"], "2026-09-01")
            self.assertGreater(sample_payload["created_sample_id"], 0)
            self.assertEqual(import_payload["import"]["items_created"], 1)
            self.assertEqual(len(import_payload["results"]), 2)

    def test_radar_item_update_and_delete_apis_refresh_payload(self) -> None:
        base_config = json.loads(Path("tests/fixtures/sample_config.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            base_config["data_dir"] = str(Path(temp_dir) / "data")
            config_path.write_text(json.dumps(base_config, ensure_ascii=False), encoding="utf-8")

            item_payload = create_radar_item_from_payload(
                config_path,
                {
                    "brand_name": "Old Brand",
                    "series_name": "Old",
                    "item_name": "JSK",
                    "category": "JSK",
                    "original_price_jpy": 20000,
                },
            )
            item_id = item_payload["created_item_id"]
            add_radar_sample_from_payload(
                config_path,
                {
                    "item_id": item_id,
                    "source_type": "xianyu",
                    "listed_price_cny": 2400,
                },
            )

            updated_payload = update_radar_item_from_payload(
                config_path,
                {
                    "item_id": item_id,
                    "brand_name": "New Brand",
                    "series_name": "New",
                    "item_name": "OP",
                    "category": "OP",
                    "colorway": "Navy",
                    "original_price_jpy": 30000,
                    "jpy_to_cny": 0.05,
                    "release_date": "2026-10-10",
                    "source_url": "https://example.com/updated",
                },
            )
            delete_payload = delete_radar_item_from_payload(config_path, {"item_id": item_id})

            self.assertEqual(updated_payload["updated_item_id"], item_id)
            self.assertEqual(updated_payload["items"][0]["label"], "New Brand / New / OP / OP / Navy")
            self.assertEqual(updated_payload["items"][0]["original_price_jpy"], 30000)
            self.assertEqual(updated_payload["release_watch"][0]["release_date"], "2026-10-10")
            self.assertTrue(delete_payload["deleted"])
            self.assertEqual(delete_payload["items"], [])
            self.assertEqual(delete_payload["samples"], [])

    def test_radar_sample_update_api_refreshes_scores_and_ledger(self) -> None:
        base_config = json.loads(Path("tests/fixtures/sample_config.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            base_config["data_dir"] = str(Path(temp_dir) / "data")
            config_path.write_text(json.dumps(base_config, ensure_ascii=False), encoding="utf-8")

            item_payload = create_radar_item_from_payload(
                config_path,
                {
                    "brand_name": "BABY",
                    "series_name": "Sample",
                    "item_name": "OP",
                    "category": "OP",
                    "original_price_jpy": 30000,
                    "jpy_to_cny": 0.05,
                },
            )
            item_id = item_payload["created_item_id"]
            sample_payload = add_radar_sample_from_payload(
                config_path,
                {
                    "item_id": item_id,
                    "source_type": "xianyu",
                    "listed_price_cny": 2000,
                    "listing_status": "listed",
                    "confidence": 0.7,
                },
            )
            sample_id = sample_payload["created_sample_id"]

            updated_payload = update_radar_sample_from_payload(
                config_path,
                {
                    "sample_id": sample_id,
                    "item_id": item_id,
                    "source_type": "taobao",
                    "listed_price_cny": 3600,
                    "listing_status": "listed",
                    "confidence": 0.9,
                    "title": "updated taobao sample",
                    "source_url": "https://example.com/sample",
                },
            )

            self.assertEqual(updated_payload["updated_sample_id"], sample_id)
            self.assertEqual(updated_payload["samples"][0]["source_type"], "taobao")
            self.assertEqual(updated_payload["samples"][0]["effective_price_cny"], 3600)
            self.assertEqual(updated_payload["samples"][0]["title"], "updated taobao sample")
            self.assertEqual(updated_payload["results"][0]["sample_count"], 0)
            self.assertEqual(updated_payload["results"][0]["domestic_sample_count"], 1)
            self.assertEqual(updated_payload["results"][0]["domestic_median_cny"], 3600)

    def test_radar_review_api_records_and_deletes_outcomes(self) -> None:
        base_config = json.loads(Path("tests/fixtures/sample_config.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            base_config["data_dir"] = str(Path(temp_dir) / "data")
            config_path.write_text(json.dumps(base_config, ensure_ascii=False), encoding="utf-8")

            item_payload = create_radar_item_from_payload(
                config_path,
                {
                    "brand_name": "Angelic Pretty",
                    "series_name": "Review Print",
                    "item_name": "JSK",
                    "category": "JSK",
                    "original_price_jpy": 30000,
                    "jpy_to_cny": 0.05,
                },
            )
            item_id = item_payload["created_item_id"]
            add_radar_sample_from_payload(
                config_path,
                {
                    "item_id": item_id,
                    "source_type": "xianyu",
                    "listed_price_cny": 2100,
                },
            )

            review_payload = upsert_radar_review_from_payload(
                config_path,
                {
                    "item_id": item_id,
                    "review_status": "hit",
                    "observed_price_cny": 2400,
                    "review_window_days": 30,
                    "notes": "30-day check",
                },
            )
            delete_payload = delete_radar_review_from_payload(config_path, {"item_id": item_id})

            self.assertEqual(review_payload["reviewed_item_id"], item_id)
            self.assertEqual(review_payload["reviews"][0]["review_status"], "hit")
            self.assertEqual(review_payload["reviews"][0]["observed_premium_ratio"], 0.6)
            self.assertEqual(review_payload["reviews"][0]["predicted_premium_ratio"], 0.4)
            self.assertEqual(review_payload["review_stats"]["hit_count"], 1)
            self.assertEqual(review_payload["review_stats"]["hit_rate"], 1.0)
            self.assertTrue(delete_payload["deleted"])
            self.assertEqual(delete_payload["reviews"], [])
            self.assertEqual(delete_payload["review_stats"]["total_count"], 0)

    def test_create_watch_target_from_radar_item_updates_config(self) -> None:
        base_config = json.loads(Path("tests/fixtures/sample_config.json").read_text(encoding="utf-8"))
        base_config["targets"] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            base_config["data_dir"] = str(Path(temp_dir) / "data")
            config_path.write_text(json.dumps(base_config, ensure_ascii=False), encoding="utf-8")

            item_payload = create_radar_item_from_payload(
                config_path,
                {
                    "brand_name": "Angelic Pretty",
                    "series_name": "Dream Sample",
                    "item_name": "JSK",
                    "category": "JSK",
                    "colorway": "Pink",
                    "original_price_jpy": 30000,
                    "release_date": "2026-07-15",
                },
            )

            payload = create_watch_target_from_payload(
                config_path,
                {
                    "item_id": item_payload["created_item_id"],
                    "url": "https://shop.example.taobao.com/search.htm",
                    "price_max": 2500,
                },
            )
            saved_config = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertTrue(payload["created"])
            self.assertEqual(len(saved_config["targets"]), 1)
            self.assertEqual(saved_config["targets"][0]["url"], "https://shop.example.taobao.com/search.htm")
            self.assertEqual(saved_config["targets"][0]["price_max"], 2500)
            self.assertIn("Dream Sample", saved_config["targets"][0]["include_keywords"])
            radar_payload = get_radar(config_path)
            watched = radar_payload["watch_recommendations"][0]
            self.assertTrue(watched["already_watched"])
            self.assertEqual(watched["monitor_target_name"], saved_config["targets"][0]["name"])
            self.assertEqual(watched["action_label"], "已监控")

            duplicate = create_watch_target_from_payload(
                config_path,
                {
                    "item_id": item_payload["created_item_id"],
                    "url": "https://shop.example.taobao.com/search.htm",
                },
            )
            saved_config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertFalse(duplicate["created"])
            self.assertEqual(len(saved_config["targets"]), 1)

    def test_collect_and_delete_radar_samples_from_pasted_text(self) -> None:
        base_config = json.loads(Path("tests/fixtures/sample_config.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            base_config["data_dir"] = str(Path(temp_dir) / "data")
            config_path.write_text(json.dumps(base_config, ensure_ascii=False), encoding="utf-8")

            item_payload = create_radar_item_from_payload(
                config_path,
                {
                    "brand_name": "BABY",
                    "series_name": "Collected",
                    "item_name": "OP",
                    "category": "OP",
                    "original_price_jpy": 30000,
                },
            )
            collect_payload = collect_radar_samples_from_payload(
                config_path,
                {
                    "item_id": item_payload["created_item_id"],
                    "source_type": "xianyu",
                    "text": "BABY Collected OP ￥2400 https://example.com/a\nBABY Collected OP 已售 ￥2600",
                },
            )

            self.assertEqual(collect_payload["collected"]["saved_count"], 2)
            self.assertEqual(len(collect_payload["samples"]), 2)

            sample_id = collect_payload["samples"][0]["id"]
            delete_payload = delete_radar_sample_from_payload(config_path, {"sample_id": sample_id})
            self.assertTrue(delete_payload["deleted"])
            self.assertEqual(len(delete_payload["samples"]), 1)

    def test_collect_release_items_from_pasted_text(self) -> None:
        base_config = json.loads(Path("tests/fixtures/sample_config.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            base_config["data_dir"] = str(Path(temp_dir) / "data")
            config_path.write_text(json.dumps(base_config, ensure_ascii=False), encoding="utf-8")

            payload = collect_release_items_from_payload(
                config_path,
                {
                    "brand_name": "Angelic Pretty",
                    "series_name": "Sample Print",
                    "release_date": "2026-07-15",
                    "source_url": "https://example.com/release",
                    "jpy_to_cny": 0.05,
                    "release_signal_score": 85,
                    "text": (
                        "Sample Print JSK Pink ¥32,780\n"
                        "Sample Print OP Sax 36,080円 https://example.com/op\n"
                    ),
                },
            )

            self.assertEqual(payload["release_collected"]["saved_count"], 2)
            self.assertEqual(payload["release_collected"]["created_count"], 2)
            self.assertEqual(len(payload["items"]), 2)
            self.assertEqual(payload["items"][0]["original_price_jpy"], 32780)
            self.assertEqual(payload["items"][0]["release_date"], "2026-07-15")
            self.assertEqual(payload["release_watch"][0]["release_date"], "2026-07-15")


if __name__ == "__main__":
    unittest.main()
