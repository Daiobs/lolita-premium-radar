import tempfile
import unittest
from datetime import date
from pathlib import Path

from tb_new_arrival_alert.radar import (
    add_price_sample,
    add_release_candidates_as_items,
    analyze_aggregates,
    analyze_all,
    analyze_item,
    connect_radar_db,
    create_radar_item,
    delete_radar_item,
    delete_price_sample,
    delete_radar_review,
    import_radar_csv,
    list_collection_tasks,
    list_release_watch,
    list_price_samples,
    list_radar_items,
    list_radar_reviews,
    list_watch_recommendations,
    parse_release_item_candidates,
    parse_price_sample_candidates,
    premium_score,
    priority_band,
    summarize_radar_reviews,
    update_radar_item,
    update_price_sample,
    upsert_radar_review,
)


class RadarTests(unittest.TestCase):
    def test_analyzes_landed_cost_median_premium_and_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")
            item_id = create_radar_item(
                connection,
                brand_name="Angelic Pretty",
                series_name="Sample Print",
                item_name="JSK",
                category="JSK",
                colorway="Pink",
                original_price_jpy=30000,
                jpy_to_cny=0.05,
                japan_domestic_shipping_cny=100,
                international_shipping_cny=150,
                proxy_fee_cny=80,
                tax_or_buffer_cny=50,
                release_signal_score=90,
                source_url="https://example.com/release",
                release_date="2026-07-15",
            )
            add_price_sample(connection, item_id, "xianyu", listed_price_cny=2600, listing_status="sold", confidence=0.9)
            add_price_sample(connection, item_id, "wunderwelt", sold_price_cny=2800, listing_status="sold", confidence=0.8)
            add_price_sample(connection, item_id, "mercari", listed_price_cny=3000, listing_status="listed", confidence=0.7)
            add_price_sample(connection, item_id, "taobao", listed_price_cny=5000, listing_status="listed", confidence=0.9)
            add_price_sample(connection, item_id, "proxy", listed_price_cny=5200, listing_status="listed", confidence=0.9)

            result = analyze_item(connection, item_id)

            self.assertEqual(result.label, "Angelic Pretty / Sample Print / JSK / JSK / Pink")
            self.assertEqual(result.landed_cost_cny, 1880)
            self.assertEqual(result.market_median_cny, 2800)
            self.assertEqual(result.premium_cny, 920)
            self.assertEqual(result.premium_ratio, 0.4894)
            self.assertEqual(result.sample_count, 3)
            self.assertEqual(result.domestic_median_cny, 5100)
            self.assertEqual(result.domestic_markup_cny, 3220)
            self.assertEqual(result.domestic_markup_ratio, 1.7128)
            self.assertEqual(result.domestic_sample_count, 2)
            self.assertEqual(result.premium_score, 60)
            self.assertEqual(result.priority_band, "B")
            self.assertEqual(result.source_url, "https://example.com/release")
            self.assertEqual(result.release_date, "2026-07-15")

    def test_analyze_all_sorts_by_attention_score(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")
            low_id = create_radar_item(
                connection,
                brand_name="Brand Low",
                series_name="Plain",
                item_name="Skirt",
                category="skirt",
                original_price_jpy=20000,
            )
            high_id = create_radar_item(
                connection,
                brand_name="Brand High",
                series_name="Rare",
                item_name="OP",
                category="OP",
                original_price_jpy=20000,
                release_signal_score=100,
            )
            add_price_sample(connection, low_id, "xianyu", listed_price_cny=900, confidence=0.5)
            add_price_sample(connection, high_id, "xianyu", listed_price_cny=2500, listing_status="sold", confidence=0.9)
            add_price_sample(connection, high_id, "mercari", listed_price_cny=2600, listing_status="sold", confidence=0.9)
            add_price_sample(connection, high_id, "wunderwelt", listed_price_cny=2700, confidence=0.9)

            results = analyze_all(connection)

            self.assertEqual(results[0].item_id, high_id)
            self.assertEqual(results[1].item_id, low_id)

    def test_update_and_delete_radar_item_maintains_item_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")
            item_id = create_radar_item(
                connection,
                brand_name="Old Brand",
                series_name="Old Series",
                item_name="JSK",
                category="JSK",
                original_price_jpy=20000,
            )
            add_price_sample(connection, item_id, "xianyu", listed_price_cny=2500)

            updated = update_radar_item(
                connection,
                item_id=item_id,
                brand_name="New Brand",
                series_name="New Series",
                item_name="OP",
                category="OP",
                colorway="Black",
                original_price_jpy=30000,
                jpy_to_cny=0.05,
                japan_domestic_shipping_cny=100,
                international_shipping_cny=200,
                proxy_fee_cny=80,
                tax_or_buffer_cny=50,
                release_signal_score=80,
                source_url="https://example.com/new-release",
                release_date="2026-10-01",
            )
            items = list_radar_items(connection)
            result = analyze_item(connection, item_id)

            self.assertTrue(updated)
            self.assertEqual(items[0].label, "New Brand / New Series / OP / OP / Black")
            self.assertEqual(items[0].original_price_jpy, 30000)
            self.assertEqual(items[0].japan_domestic_shipping_cny, 100)
            self.assertEqual(items[0].source_url, "https://example.com/new-release")
            self.assertEqual(result.landed_cost_cny, 1930)
            self.assertEqual(result.release_date, "2026-10-01")

            self.assertTrue(delete_radar_item(connection, item_id))
            self.assertEqual(list_radar_items(connection), [])
            self.assertEqual(list_price_samples(connection), [])

    def test_release_watch_sorts_upcoming_before_recent_releases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")
            recent_id = create_radar_item(
                connection,
                brand_name="Brand Recent",
                series_name="Archive",
                item_name="OP",
                category="OP",
                original_price_jpy=20000,
                release_date="2026-01-01",
                source_url="https://example.com/recent",
            )
            later_id = create_radar_item(
                connection,
                brand_name="Brand Later",
                series_name="Future",
                item_name="JSK",
                category="JSK",
                original_price_jpy=20000,
                release_date="2026-01-20",
                source_url="https://example.com/later",
            )
            soon_id = create_radar_item(
                connection,
                brand_name="Brand Soon",
                series_name="Future",
                item_name="Skirt",
                category="skirt",
                original_price_jpy=20000,
                release_date="2026-01-08",
                source_url="https://example.com/soon",
            )
            add_price_sample(connection, recent_id, "xianyu", listed_price_cny=1800)
            add_price_sample(connection, later_id, "xianyu", listed_price_cny=1900)
            add_price_sample(connection, soon_id, "xianyu", listed_price_cny=2000)

            watch = list_release_watch(connection, today=date(2026, 1, 5))

            self.assertEqual([item.item_id for item in watch], [soon_id, later_id, recent_id])
            self.assertEqual(watch[0].days_until, 3)
            self.assertEqual(watch[0].release_status, "upcoming")
            self.assertEqual(watch[2].days_until, -4)
            self.assertEqual(watch[2].release_status, "released")

    def test_collection_tasks_prioritize_missing_data_and_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")
            missing_id = create_radar_item(
                connection,
                brand_name="Brand Missing",
                series_name="Future",
                item_name="JSK",
                category="JSK",
                release_signal_score=90,
            )
            partial_id = create_radar_item(
                connection,
                brand_name="Brand Partial",
                series_name="Known",
                item_name="OP",
                category="OP",
                original_price_jpy=30000,
                release_date="2026-11-01",
                source_url="https://example.com/release",
            )
            add_price_sample(connection, partial_id, "xianyu", listed_price_cny=2400)
            add_price_sample(connection, partial_id, "taobao", listed_price_cny=3000)

            tasks = list_collection_tasks(connection)
            missing_types = {task.task_type for task in tasks if task.item_id == missing_id}
            partial_types = {task.task_type for task in tasks if task.item_id == partial_id}

            self.assertIn("original_price", missing_types)
            self.assertIn("secondhand_samples", missing_types)
            self.assertIn("domestic_samples", missing_types)
            self.assertIn("release_date", missing_types)
            self.assertIn("source_url", missing_types)
            self.assertEqual(partial_types, {"secondhand_samples"})
            self.assertEqual(tasks[0].task_type, "original_price")
            task_by_type = {task.task_type: task for task in tasks if task.item_id == missing_id}
            self.assertEqual(task_by_type["original_price"].action_type, "edit_item")
            self.assertEqual(task_by_type["secondhand_samples"].action_type, "add_sample")
            self.assertEqual(task_by_type["secondhand_samples"].suggested_source_type, "xianyu")
            self.assertEqual(task_by_type["domestic_samples"].suggested_source_type, "taobao")

    def test_watch_recommendations_include_priority_and_upcoming_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")
            high_id = create_radar_item(
                connection,
                brand_name="Brand High",
                series_name="Rare",
                item_name="JSK",
                category="JSK",
                original_price_jpy=20000,
                release_date="2026-01-20",
                release_signal_score=100,
            )
            low_id = create_radar_item(
                connection,
                brand_name="Brand Low",
                series_name="Plain",
                item_name="Skirt",
                category="skirt",
                original_price_jpy=20000,
            )
            add_price_sample(connection, high_id, "xianyu", listed_price_cny=2600, listing_status="sold", confidence=0.9)
            add_price_sample(connection, high_id, "mercari", listed_price_cny=2700, confidence=0.9)
            add_price_sample(connection, high_id, "taobao", listed_price_cny=3200, confidence=0.8)
            add_price_sample(connection, low_id, "xianyu", listed_price_cny=900, confidence=0.4)

            recommendations = list_watch_recommendations(connection, today=date(2026, 1, 5))

            self.assertEqual(recommendations[0].item_id, high_id)
            self.assertIn("天内发售", recommendations[0].reason)
            self.assertEqual(recommendations[0].suggested_price_max, 3520)
            self.assertNotIn(low_id, [item.item_id for item in recommendations])

    def test_review_loop_records_observed_outcome_and_hit_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")
            item_id = create_radar_item(
                connection,
                brand_name="Angelic Pretty",
                series_name="Review Print",
                item_name="JSK",
                category="JSK",
                original_price_jpy=30000,
                jpy_to_cny=0.05,
            )
            add_price_sample(connection, item_id, "xianyu", listed_price_cny=2100, confidence=0.9)

            review = upsert_radar_review(
                connection,
                item_id=item_id,
                review_status="hit",
                observed_price_cny=2400,
                review_window_days=30,
                notes="30 days after domestic arrival",
            )
            reviews = list_radar_reviews(connection)
            stats = summarize_radar_reviews(reviews)

            self.assertEqual(review.review_status, "hit")
            self.assertEqual(review.observed_premium_ratio, 0.6)
            self.assertEqual(review.predicted_premium_ratio, 0.4)
            self.assertEqual(review.predicted_attention_score, analyze_item(connection, item_id).attention_score)
            self.assertEqual(review.review_window_days, 30)
            self.assertEqual(len(reviews), 1)
            self.assertEqual(stats.reviewed_count, 1)
            self.assertEqual(stats.hit_count, 1)
            self.assertEqual(stats.hit_rate, 1.0)
            self.assertEqual(stats.average_observed_premium_ratio, 0.6)

            updated = upsert_radar_review(
                connection,
                item_id=item_id,
                review_status="miss",
                observed_price_cny=1400,
                review_window_days=60,
            )
            stats = summarize_radar_reviews(list_radar_reviews(connection))

            self.assertEqual(updated.review_status, "miss")
            self.assertEqual(updated.observed_premium_ratio, -0.0667)
            self.assertEqual(stats.hit_count, 0)
            self.assertEqual(stats.miss_count, 1)
            self.assertEqual(stats.hit_rate, 0.0)
            self.assertTrue(delete_radar_review(connection, item_id))
            self.assertEqual(list_radar_reviews(connection), [])

    def test_analyze_aggregates_groups_brand_and_series(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")
            first_id = create_radar_item(
                connection,
                brand_name="Angelic Pretty",
                series_name="Dream Sample",
                item_name="JSK",
                category="JSK",
                original_price_jpy=20000,
                release_signal_score=90,
            )
            second_id = create_radar_item(
                connection,
                brand_name="Angelic Pretty",
                series_name="Dream Sample",
                item_name="OP",
                category="OP",
                original_price_jpy=22000,
                release_signal_score=70,
            )
            third_id = create_radar_item(
                connection,
                brand_name="Brand Low",
                series_name="Plain",
                item_name="Skirt",
                category="skirt",
                original_price_jpy=20000,
            )
            add_price_sample(connection, first_id, "xianyu", listed_price_cny=2500, listing_status="sold", confidence=0.9)
            add_price_sample(connection, second_id, "xianyu", listed_price_cny=2600, confidence=0.8)
            add_price_sample(connection, third_id, "xianyu", listed_price_cny=900, confidence=0.5)

            aggregates = analyze_aggregates(connection)
            aggregate_names = {aggregate.name: aggregate for aggregate in aggregates}

            self.assertIn("Angelic Pretty", aggregate_names)
            self.assertIn("Angelic Pretty / Dream Sample", aggregate_names)
            self.assertEqual(aggregate_names["Angelic Pretty"].item_count, 2)
            self.assertEqual(aggregate_names["Angelic Pretty"].secondhand_sample_count, 2)
            self.assertGreater(
                aggregate_names["Angelic Pretty"].attention_score,
                aggregate_names["Brand Low"].attention_score,
            )

    def test_score_boundaries(self) -> None:
        self.assertEqual(premium_score(0.8), 100)
        self.assertEqual(premium_score(0.5), 80)
        self.assertEqual(premium_score(0.3), 60)
        self.assertEqual(premium_score(0.1), 30)
        self.assertEqual(premium_score(0.09), 0)
        self.assertEqual(priority_band(75), "A")
        self.assertEqual(priority_band(55), "B")
        self.assertEqual(priority_band(35), "C")
        self.assertEqual(priority_band(34.99), "D")

    def test_import_radar_csv_reuses_items_and_adds_samples(self) -> None:
        csv_text = (
            "brand_name,series_name,item_name,category,colorway,original_price_jpy,"
            "jpy_to_cny,sample_source_type,listed_price_cny,listing_status,confidence\n"
            "Victorian Maiden,Rose Lace,Long OP,OP,Black,32000,0.05,xianyu,2600,listed,0.8\n"
            "Victorian Maiden,Rose Lace,Long OP,OP,Black,32000,0.05,mercari,2800,sold,0.9\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "radar.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")

            result = import_radar_csv(connection, csv_path)
            items = list_radar_items(connection)
            ranking = analyze_all(connection)

            self.assertEqual(result.rows_read, 2)
            self.assertEqual(result.items_created, 1)
            self.assertEqual(result.samples_created, 2)
            self.assertEqual(len(items), 1)
            self.assertEqual(ranking[0].sample_count, 2)
            self.assertEqual(ranking[0].market_median_cny, 2700)

    def test_parse_list_and_delete_price_samples(self) -> None:
        text = (
            "AP Sample Print JSK Pink ￥2800 https://example.com/xianyu-a\n"
            "AP Sample Print JSK Pink 已售 3,000元 https://example.com/xianyu-b\n"
            "no price line\n"
        )
        candidates = parse_price_sample_candidates(text, source_type="xianyu")

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].listed_price_cny, 2800)
        self.assertEqual(candidates[1].sold_price_cny, 3000)
        self.assertEqual(candidates[1].listing_status, "sold")

        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")
            item_id = create_radar_item(
                connection,
                brand_name="Angelic Pretty",
                series_name="Sample Print",
                item_name="JSK",
                category="JSK",
            )
            sample_id = add_price_sample(connection, item_id, "xianyu", listed_price_cny=2800, title="sample")
            samples = list_price_samples(connection)
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0].effective_price_cny, 2800)

            updated = update_price_sample(
                connection,
                sample_id=sample_id,
                item_id=item_id,
                source_type="taobao",
                listed_price_cny=3200,
                source_url="https://example.com/taobao",
                title="updated sample",
                condition="new",
                listing_status="listed",
                confidence=0.9,
            )
            samples = list_price_samples(connection)
            self.assertTrue(updated)
            self.assertEqual(samples[0].source_type, "taobao")
            self.assertEqual(samples[0].effective_price_cny, 3200)
            self.assertEqual(samples[0].title, "updated sample")
            self.assertEqual(samples[0].confidence, 0.9)

            self.assertTrue(delete_price_sample(connection, sample_id))
            self.assertEqual(list_price_samples(connection), [])

    def test_parse_and_save_release_item_candidates(self) -> None:
        text = (
            "2026年7月15日 Sample Print JSK Pink ¥32,780 https://example.com/release-a\n"
            "Sample Print OP Sax 36,080円\n"
            "no price line\n"
        )

        candidates = parse_release_item_candidates(
            text,
            brand_name="Angelic Pretty",
            default_series_name="Sample Print",
            default_source_url="https://example.com/release",
        )

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].original_price_jpy, 32780)
        self.assertEqual(candidates[0].release_date, "2026-07-15")
        self.assertEqual(candidates[0].source_url, "https://example.com/release-a")
        self.assertEqual(candidates[0].category, "JSK")
        self.assertEqual(candidates[1].original_price_jpy, 36080)
        self.assertEqual(candidates[1].category, "OP")

        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect_radar_db(Path(temp_dir) / "radar.sqlite")
            item_ids, created_count = add_release_candidates_as_items(connection, candidates, jpy_to_cny=0.05)
            items = list_radar_items(connection)

            self.assertEqual(created_count, 2)
            self.assertEqual(len(item_ids), 2)
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0].brand_name, "Angelic Pretty")
            self.assertEqual(items[0].original_price_jpy, 32780)


if __name__ == "__main__":
    unittest.main()
