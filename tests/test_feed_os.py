import unittest

from lolita_radar.crawler import enrich_source_runs
from lolita_radar.feed import build_home_feed
from lolita_radar.trend import build_trend_feed


class FeedOsTests(unittest.TestCase):
    def test_home_feed_builds_four_streams(self) -> None:
        events = [
            {
                "source": "angelic_pretty",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "Shell Garden JSK",
                "url": "https://example.com/ap",
                "published_at": "2026-06-30",
                "created_at": "2026-06-30T10:00:00+00:00",
                "metadata": {"price": "¥38,280", "image_url": "https://example.com/shell.webp"},
            },
            {
                "source": "generic_page",
                "event_type": "content_changed",
                "status": "shop_news",
                "title": "Proxy JSK 预约",
                "url": "https://example.com/shop",
                "created_at": "2026-06-30T10:01:00+00:00",
                "metadata": {
                    "shop": {"name": "Tokyo Proxy", "url": "https://example.com/shop"},
                    "item": {"title": "Shell Garden JSK", "url": "https://example.com/shop/shell"},
                    "matched_keywords": ["JSK", "预约"],
                },
            },
        ]
        market_summary = {
            "brands": [
                {"brand_alias": "AP", "sample_count": 3, "avg_premium_rate": 0.45, "max_premium_rate": 0.7}
            ]
        }
        market_alerts = {"alerts": [{"kind": "sample_gap", "alias": "BABY", "reason": "core_needs_samples"}]}

        feed = build_home_feed(events, [], market_summary, market_alerts, [], [])

        self.assertEqual(feed["summary"]["drops"], 1)
        self.assertEqual(feed["summary"]["shops"], 1)
        self.assertEqual(feed["summary"]["trends"], 1)
        self.assertGreaterEqual(feed["summary"]["alerts"], 2)
        self.assertEqual(feed["streams"]["release"][0]["brand"], "AP")
        self.assertEqual(feed["streams"]["release"][0]["type"], "new_arrival")
        self.assertEqual(feed["streams"]["release"][0]["price"], "¥38,280")
        self.assertEqual(feed["streams"]["release"][0]["url"], "https://example.com/ap")
        self.assertEqual(feed["streams"]["release"][0]["visual"]["image_url"], "https://example.com/shell.webp")
        self.assertEqual(feed["streams"]["release"][0]["visual"]["mark"], "R")
        self.assertEqual(feed["streams"]["drop"][0]["feed_type"], "drop")
        self.assertEqual(feed["streams"]["drop"][0]["shop"], "Tokyo Proxy")
        self.assertEqual(feed["streams"]["drop"][0]["item"], "Shell Garden JSK")
        self.assertEqual(feed["streams"]["drop"][0]["urgency"], "high")
        self.assertEqual(feed["streams"]["drop"][0]["keywords"], ["JSK", "预约"])
        self.assertEqual(feed["streams"]["drop"][0]["visual"]["mark"], "D")
        self.assertIn("keywords: JSK, 预约", feed["streams"]["drop"][0]["meta"])
        self.assertIn("keyword_match", feed["streams"]["drop"][0]["reason_codes"])
        self.assertEqual(feed["streams"]["trend"][0]["kind"], "rising")
        self.assertEqual(feed["streams"]["trend"][0]["trend"], "rising")
        self.assertEqual(feed["streams"]["trend"][0]["price_delta"], 0.45)
        self.assertEqual(feed["streams"]["trend"][0]["visual"]["mark"], "T")
        alert_titles = {row["title"] for row in feed["streams"]["alert"]}
        self.assertIn("Shell Garden JSK", alert_titles)
        self.assertNotIn("Proxy JSK 预约", alert_titles)
        release_alert = next(row for row in feed["streams"]["alert"] if row["title"] == "Shell Garden JSK")
        self.assertEqual(release_alert["reason_codes"], ["new_release", "new_item", "new_arrival"])
        self.assertTrue(all(row["visual"]["mark"] for rows in feed["streams"].values() for row in rows))

    def test_release_feed_prefers_published_at_over_seen_time(self) -> None:
        events = [
            {
                "source": "angelic_pretty",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "2026.06.20 新作 JSK",
                "url": "https://example.com/ap",
                "published_at": "2026-06-20",
                "created_at": "2026-06-30T10:00:00+00:00",
            },
            {
                "source": "metamorphose",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "2026.06.22 New Arrival OP",
                "url": "https://example.com/meta",
                "published_at": "2026-06-22",
                "created_at": "2026-06-29T10:00:00+00:00",
            }
        ]

        feed = build_home_feed(events, [], {"brands": []}, {"alerts": []}, [], [])
        card = feed["streams"]["release"][0]

        self.assertEqual(card["title"], "2026.06.22 New Arrival OP")
        self.assertEqual(card["type"], "new_arrival")
        self.assertEqual(card["time"], "2026-06-22")
        self.assertEqual(card["time_kind"], "published")
        self.assertEqual(card["price"], "未取得")
        self.assertEqual(feed["streams"]["release"][1]["time"], "2026-06-20")
        self.assertEqual(feed["streams"]["release"][1]["title_zh"], "新作")
        self.assertIn("新作 / 新品", card["status_label"])

    def test_release_feed_requires_current_source_publish_time(self) -> None:
        events = [
            {
                "source": "angelic_pretty",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "Old 2025 JSK",
                "url": "https://example.com/ap/old",
                "published_at": "2025-12-31",
                "created_at": "2026-06-30T10:00:00+00:00",
            },
            {
                "source": "metamorphose",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "Missing date should not use seen time",
                "url": "https://example.com/meta/no-date",
                "created_at": "2026-06-30T10:05:00+00:00",
            },
            {
                "source": "baby_ssb",
                "event_type": "new_item",
                "status": "preorder",
                "title": "Current Usakumya",
                "url": "https://example.com/baby/current",
                "published_at": "2026-06-29",
                "created_at": "2026-06-30T10:10:00+00:00",
            },
        ]

        feed = build_home_feed(events, [], {"brands": []}, {"alerts": []}, [], [])
        release_titles = [row["title"] for row in feed["streams"]["release"]]

        self.assertEqual(release_titles, ["Current Usakumya"])
        self.assertEqual(feed["streams"]["release"][0]["time"], "2026-06-29")
        self.assertEqual(feed["streams"]["release"][0]["time_kind"], "published")

    def test_home_all_feed_is_limited_to_thirty_links(self) -> None:
        events = [
            {
                "source": "angelic_pretty",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": f"Release {index:02d}",
                "url": f"https://example.com/ap/{index:02d}",
                "published_at": f"2026-06-{(index % 28) + 1:02d}",
                "created_at": "2026-06-30T10:00:00+00:00",
            }
            for index in range(35)
        ]

        feed = build_home_feed(events, [], {"brands": []}, {"alerts": []}, [], [])

        self.assertEqual(len(feed["all"]), 30)
        self.assertTrue(all(row["url"] for row in feed["all"]))
        self.assertEqual(len({row["url"] for row in feed["all"]}), 30)

    def test_home_all_feed_uses_feed_type_priority(self) -> None:
        events = [
            {
                "source": "angelic_pretty",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "Release",
                "url": "https://example.com/release",
                "published_at": "2026-01-01",
            },
            {
                "source": "generic_page",
                "event_type": "content_changed",
                "status": "shop_news",
                "title": "Shop drop",
                "url": "https://example.com/drop",
                "metadata": {"matched_keywords": ["JSK"]},
            },
        ]
        source_runs = [
            {
                "source": "angelic_pretty",
                "status": "failed",
                "ok": False,
                "error_rate": 1.0,
                "checked_at": "2026-06-30T10:00:00+00:00",
                "error_message": "timeout",
            }
        ]
        market_summary = {"brands": [{"brand_alias": "AP", "sample_count": 4, "avg_premium_rate": 0.8}]}

        feed = build_home_feed(
            events,
            [],
            market_summary,
            {"alerts": []},
            [],
            source_runs,
            brand_weights=[{"alias": "AP", "watch_urls": [{"label": "market", "url": "https://example.com/market/ap"}]}],
            source_urls={"angelic_pretty": "https://example.com/ap-health"},
        )

        self.assertEqual([row["feed_type"] for row in feed["all"][:4]], ["release", "drop", "alert", "trend"])

    def test_alert_feed_normalizes_high_premium_and_sample_gap(self) -> None:
        market_alerts = {
            "alerts": [
                {
                    "kind": "sample_spike",
                    "severity": "critical",
                    "alias": "AP",
                    "item_name": "Shell Garden JSK",
                    "premium_rate": 0.82,
                    "reason": "collector_premium",
                },
                {
                    "kind": "brand_heat",
                    "severity": "watch",
                    "alias": "BABY",
                    "title": "BABY",
                    "premium_rate": 0.55,
                    "reason": "brand_hot_average",
                },
                {
                    "kind": "sample_gap",
                    "severity": "sample_gap",
                    "alias": "AATP",
                    "title": "ALICE and the PIRATES",
                    "reason": "core_needs_samples",
                },
            ]
        }

        feed = build_home_feed([], [], {"brands": []}, market_alerts, [], [])
        alerts = feed["streams"]["alert"]

        self.assertEqual(alerts[0]["kind"], "high_premium")
        self.assertEqual(alerts[0]["title"], "Shell Garden JSK")
        self.assertIn("82% premium", alerts[0]["meta"])
        self.assertIn("sample_spike", alerts[0]["reason_codes"])
        self.assertEqual(alerts[1]["kind"], "high_premium")
        self.assertIn("brand_heat", alerts[1]["reason_codes"])
        self.assertEqual(alerts[2]["kind"], "sample_gap")
        self.assertIn("sample_gap", alerts[2]["reason_codes"])

    def test_alert_feed_keeps_unknown_market_alert_kinds_out(self) -> None:
        market_alerts = {
            "alerts": [
                {
                    "kind": "debug_note",
                    "severity": "info",
                    "alias": "AP",
                    "title": "Internal debug",
                    "reason": "not_user_facing",
                },
                {
                    "kind": "sample_gap",
                    "severity": "sample_gap",
                    "alias": "BABY",
                    "title": "BABY",
                    "reason": "core_needs_samples",
                },
            ]
        }

        feed = build_home_feed([], [], {"brands": []}, market_alerts, [], [])
        alerts = feed["streams"]["alert"]

        self.assertEqual([alert["kind"] for alert in alerts], ["sample_gap"])
        self.assertEqual(alerts[0]["brand"], "BABY")

    def test_alert_feed_filters_market_kinds_before_limit(self) -> None:
        noisy_alerts = [
            {"kind": "debug_note", "severity": "info", "alias": f"debug-{index}", "title": "Debug"}
            for index in range(20)
        ]
        noisy_alerts.append(
            {
                "kind": "sample_gap",
                "severity": "sample_gap",
                "alias": "BABY",
                "title": "BABY",
                "reason": "core_needs_samples",
            }
        )

        feed = build_home_feed([], [], {"brands": []}, {"alerts": noisy_alerts}, [], [])
        alerts = feed["streams"]["alert"]

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["kind"], "sample_gap")
        self.assertEqual(alerts[0]["brand"], "BABY")

    def test_alert_feed_filters_release_events_before_limit(self) -> None:
        noisy_events = [
            {
                "source": "generic_page",
                "event_type": "content_changed",
                "status": "shop_news",
                "title": f"Shop noise {index}",
                "url": f"https://example.com/shop/{index}",
                "created_at": "2026-06-30T10:00:00+00:00",
            }
            for index in range(20)
        ]
        noisy_events.append(
            {
                "source": "angelic_pretty",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "Shell Garden JSK",
                "url": "https://example.com/ap/shell-garden",
                "published_at": "2026-06-30",
                "created_at": "2026-06-30T10:05:00+00:00",
            }
        )

        feed = build_home_feed(noisy_events, [], {"brands": []}, {"alerts": []}, [], [])
        alerts = feed["streams"]["alert"]

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["kind"], "new_release")
        self.assertEqual(alerts[0]["title"], "Shell Garden JSK")

    def test_release_feed_keeps_non_target_brand_sources_out(self) -> None:
        events = [
            {
                "source": "innocent_world",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "IW Rose JSK",
                "url": "https://example.com/iw",
                "created_at": "2026-06-30T10:00:00+00:00",
            },
            {
                "source": "metamorphose",
                "event_type": "new_item",
                "status": "new_arrival",
                "title": "Meta Rose JSK",
                "url": "https://example.com/meta",
                "published_at": "2026-06-30",
                "created_at": "2026-06-30T10:01:00+00:00",
            },
        ]

        feed = build_home_feed(events, [], {"brands": []}, {"alerts": []}, [], [])
        release_titles = [row["title"] for row in feed["streams"]["release"]]

        self.assertEqual(release_titles, ["Meta Rose JSK"])
        self.assertEqual(feed["summary"]["drops"], 1)

    def test_drop_feed_requires_generic_page_keyword_matches(self) -> None:
        events = [
            {
                "source": "generic_page",
                "event_type": "content_changed",
                "status": "shop_news",
                "title": "Proxy page changed",
                "url": "https://example.com/shop/no-keywords",
                "created_at": "2026-06-30T10:00:00+00:00",
                "metadata": {"matched_keywords": []},
            },
            {
                "source": "generic_page",
                "event_type": "content_changed",
                "status": "shop_news",
                "title": "Proxy JSK 预约",
                "url": "https://example.com/shop/jsk",
                "created_at": "2026-06-30T10:01:00+00:00",
                "metadata": {
                    "shop_name": "Proxy Shop",
                    "item_title": "Usakumya OP",
                    "matched_keywords": ["JSK", "预约"],
                },
            },
        ]

        feed = build_home_feed(events, [], {"brands": []}, {"alerts": []}, [], [])
        drop_titles = [row["title"] for row in feed["streams"]["drop"]]

        self.assertEqual(drop_titles, ["Usakumya OP"])
        self.assertEqual(feed["summary"]["shops"], 1)
        self.assertEqual(feed["streams"]["drop"][0]["shop"], "Proxy Shop")
        self.assertEqual(feed["streams"]["drop"][0]["urgency"], "high")
        self.assertIn("keyword_match", feed["streams"]["drop"][0]["reason_codes"])

    def test_drop_feed_accepts_named_generic_page_source_type(self) -> None:
        events = [
            {
                "source": "proxy_shop",
                "event_type": "new_item",
                "status": "shop_news",
                "title": "Shell Garden JSK 预约",
                "url": "https://example.com/shop/shell",
                "created_at": "2026-06-30T10:00:00+00:00",
                "metadata": {
                    "source_type": "generic_page",
                    "shop": {"name": "Tokyo Proxy", "url": "https://example.com/shop"},
                    "item": {"title": "Shell Garden JSK 预约", "url": "https://example.com/shop/shell"},
                    "image_url": "https://example.com/images/shell-jsk.webp",
                    "price": "¥12,800",
                    "matched_keywords": ["JSK", "预约"],
                },
            }
        ]

        feed = build_home_feed(events, [], {"brands": []}, {"alerts": []}, [], [])

        self.assertEqual(feed["summary"]["shops"], 1)
        self.assertEqual(feed["streams"]["drop"][0]["shop"], "Tokyo Proxy")
        self.assertEqual(feed["streams"]["drop"][0]["item"], "Shell Garden JSK 预约")
        self.assertEqual(feed["streams"]["drop"][0]["url"], "https://example.com/shop/shell")
        self.assertEqual(feed["streams"]["drop"][0]["price"], "¥12,800")
        self.assertEqual(feed["streams"]["drop"][0]["visual"]["image_url"], "https://example.com/images/shell-jsk.webp")

    def test_alert_feed_uses_latest_source_health_per_source(self) -> None:
        source_runs = [
            {
                "source": "angelic_pretty",
                "ok": True,
                "status": "degraded",
                "error_rate": 0.3,
                "latency_ms": 321,
                "checked_at": "2026-06-30T10:05:00+00:00",
                "error_message": "",
            },
            {
                "source": "angelic_pretty",
                "ok": False,
                "status": "failed",
                "error_rate": 0.6,
                "checked_at": "2026-06-30T10:00:00+00:00",
                "error_message": "timeout",
            },
            {
                "source": "baby_ssb",
                "ok": True,
                "status": "ok",
                "error_rate": 0.0,
                "checked_at": "2026-06-30T10:05:00+00:00",
                "error_message": "",
            },
            {
                "source": "baby_ssb",
                "ok": False,
                "status": "failed",
                "error_rate": 0.5,
                "checked_at": "2026-06-30T10:00:00+00:00",
                "error_message": "old timeout",
            },
        ]

        feed = build_home_feed(
            [],
            [],
            {"brands": []},
            {"alerts": []},
            [],
            source_runs,
            source_urls={"angelic_pretty": "https://example.com/ap/news"},
        )
        alerts = feed["streams"]["alert"]

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["brand"], "angelic_pretty")
        self.assertEqual(alerts[0]["kind"], "degraded")
        self.assertEqual(alerts[0]["url"], "https://example.com/ap/news")
        self.assertEqual(alerts[0]["reason_codes"], ["source_health"])
        self.assertIn("error_rate=0.3", alerts[0]["meta"])
        self.assertIn("latency_ms=321", alerts[0]["meta"])
        self.assertIn("item_count=0", alerts[0]["meta"])
        self.assertEqual(alerts[0]["time"], "2026-06-30T10:05:00+00:00")

    def test_alert_feed_uses_latest_source_health_when_runs_are_unsorted(self) -> None:
        source_runs = [
            {
                "source": "angelic_pretty",
                "ok": False,
                "status": "failed",
                "error_rate": 1.0,
                "checked_at": "2026-06-30T10:00:00+00:00",
                "error_message": "old timeout",
            },
            {
                "source": "angelic_pretty",
                "ok": True,
                "status": "ok",
                "error_rate": 0.0,
                "checked_at": "2026-06-30T10:05:00+00:00",
                "error_message": "",
            },
        ]

        feed = build_home_feed([], [], {"brands": []}, {"alerts": []}, [], source_runs)
        alerts = [alert for alert in feed["streams"]["alert"] if alert.get("reason_codes") == ["source_health"]]

        self.assertEqual(alerts, [])

    def test_trend_engine_outputs_direction_confidence_and_reasons(self) -> None:
        trends = build_trend_feed(
            {"brands": [{"brand_alias": "AP", "sample_count": 4, "avg_premium_rate": 0.5}]},
            [{"brand_alias": "AP", "direction": "rising", "observed_at": "2026-06-30"}],
            [{"source": "angelic_pretty", "status": "new_arrival"}],
            brand_weights=[
                {
                    "alias": "AP",
                    "weight": 100,
                    "watch_urls": [{"label": "闲鱼", "url": "https://www.goofish.com/search?q=AP"}],
                }
            ],
        )

        self.assertEqual(trends[0]["kind"], "rising")
        self.assertEqual(trends[0]["trend"], "rising")
        self.assertEqual(trends[0]["url"], "https://www.goofish.com/search?q=AP")
        self.assertGreaterEqual(trends[0]["confidence"], 60)
        self.assertEqual(trends[0]["avg_premium_rate"], 0.5)
        self.assertEqual(trends[0]["price_delta"], 0.5)
        self.assertEqual(trends[0]["sample_count"], 4)
        self.assertIn("reason: sample_supported, premium_rising", trends[0]["meta"])
        self.assertIn("sample_supported", trends[0]["reason_codes"])
        self.assertIn("premium_rising", trends[0]["reason_codes"])

    def test_trend_engine_allows_only_rule_directions(self) -> None:
        trends = build_trend_feed(
            {"brands": [{"brand_alias": "AP", "sample_count": 3, "avg_premium_rate": -0.2}]},
            [{"brand_alias": "AP", "direction": "spiking", "observed_at": "2026-06-30"}],
            [{"source": "angelic_pretty", "status": "shop_news"}],
        )

        self.assertEqual(trends[0]["kind"], "cooling")
        self.assertIn("premium_cooling", trends[0]["reason_codes"])
        self.assertNotIn("release_activity", trends[0]["reason_codes"])

    def test_trend_engine_sanitizes_invalid_market_numbers(self) -> None:
        trends = build_trend_feed(
            {"brands": [{"brand_alias": "AP", "sample_count": "-5", "avg_premium_rate": "NaN"}]},
            [{"brand_alias": "AP", "direction": "moonshot", "observed_at": "2026-06-30"}],
            [],
        )

        self.assertEqual(trends[0]["kind"], "stable")
        self.assertEqual(trends[0]["confidence"], 15)
        self.assertEqual(trends[0]["avg_premium_rate"], 0)
        self.assertEqual(trends[0]["sample_count"], 0)
        self.assertIn("sample_gap", trends[0]["reason_codes"])
        self.assertIn("premium_stable", trends[0]["reason_codes"])

    def test_trend_engine_outputs_sample_gap_for_weighted_brand_without_samples(self) -> None:
        trends = build_trend_feed(
            {"brands": []},
            [],
            [],
            brand_weights=[
                {
                    "alias": "AP",
                    "weight": 100,
                    "watch_urls": [{"label": "Mercari", "url": "https://jp.mercari.com/search?keyword=AP"}],
                }
            ],
        )

        self.assertEqual(trends[0]["brand"], "AP")
        self.assertEqual(trends[0]["kind"], "stable")
        self.assertEqual(trends[0]["url"], "https://jp.mercari.com/search?keyword=AP")
        self.assertIn("reason: sample_gap", trends[0]["meta"])
        self.assertIn("sample_gap", trends[0]["reason_codes"])

    def test_trend_engine_normalizes_brand_aliases_for_watch_urls_and_momentum(self) -> None:
        trends = build_trend_feed(
            {"brands": [{"brand_alias": " ap ", "sample_count": 3, "avg_premium_rate": 0.4}]},
            [{"brand_alias": "AP", "direction": "rising", "observed_at": "2026-06-30"}],
            [{"source": "angelic_pretty", "status": "new_arrival"}],
            brand_weights=[
                {
                    "alias": "AP",
                    "weight": 100,
                    "watch_urls": [{"label": "闲鱼", "url": "https://www.goofish.com/search?q=AP"}],
                }
            ],
        )

        self.assertEqual(len(trends), 1)
        self.assertEqual(trends[0]["brand"], "AP")
        self.assertEqual(trends[0]["id"], "trend:AP")
        self.assertEqual(trends[0]["kind"], "rising")
        self.assertEqual(trends[0]["url"], "https://www.goofish.com/search?q=AP")
        self.assertEqual(trends[0]["time"], "2026-06-30")
        self.assertIn("momentum_observed", trends[0]["reason_codes"])
        self.assertIn("release_activity", trends[0]["reason_codes"])

    def test_crawler_health_marks_failed_and_degraded(self) -> None:
        rows = enrich_source_runs(
            [
                {"source": "ap", "ok": True, "item_count": 0, "event_count": 0},
                {"source": "baby", "ok": False, "item_count": 0, "event_count": 0},
            ]
        )

        by_source = {row["source"]: row for row in rows}
        self.assertEqual(by_source["ap"]["status"], "degraded")
        self.assertEqual(by_source["baby"]["status"], "failed")
        self.assertEqual(by_source["baby"]["error_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
