import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import lolita_radar.core.audit as audit_module
from lolita_radar.cli import format_feed_os_audit, format_feed_os_audit_json, main
from lolita_radar.core import audit_feed_os
from lolita_radar.models import ItemStatus, RadarItem
from lolita_radar.storage import connect, diff_and_store, record_source_run


class FeedOsAuditTests(unittest.TestCase):
    def test_audit_reports_missing_loop_evidence_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root)
            db_path = root / "radar.sqlite"

            audit = audit_feed_os(config_path=config_path, db_path=db_path, expected_cycles=2)
            text = format_feed_os_audit(audit)

            self.assertFalse(audit.complete)
            self.assertIn("status: incomplete", text)
            self.assertIn("missing | stable_loop_evidence", text)
            self.assertIn("provide --loop-log", text)
            self.assertIn("pass | product_constraints", text)

    def test_product_constraint_audit_rejects_forbidden_direction_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_root = root / "src" / "lolita_radar"
            package_root.mkdir(parents=True)
            (package_root / "bad.py").write_text("openai = True\ncheckout_submit = True\n", encoding="utf-8")

            check = audit_module.audit_product_constraints(root)

            self.assertEqual(check.status, "fail")
            self.assertIn("forbidden product direction", check.detail)

    def test_home_feed_ui_audit_requires_image_card_tokens(self) -> None:
        original_html = audit_module.FEED_INDEX_HTML
        try:
            audit_module.FEED_INDEX_HTML = original_html.replace("visual.image_url", "")

            check = audit_module.audit_frontend_feed_os()
        finally:
            audit_module.FEED_INDEX_HTML = original_html

        self.assertEqual(check.status, "fail")
        self.assertIn("visual.image_url", check.detail)

    def test_feed_contract_requires_release_visual_image(self) -> None:
        original_sample_home_feed = audit_module.sample_home_feed
        try:
            audit_module.sample_home_feed = lambda: {
                "summary": {"releases": 1, "drops": 1, "trends": 1, "alerts": 1},
                "streams": {
                    "release": [
                        {
                            "feed_type": "release",
                            "brand": "AP",
                            "title": "Shell Garden JSK",
                            "type": "new_arrival",
                            "time": "2026-06-30",
                            "price": "¥38,280",
                            "url": "https://example.com/ap/shell",
                            "visual": {"initials": "AP"},
                        }
                    ],
                    "drop": [
                        {
                            "feed_type": "drop",
                            "shop": "Tokyo Proxy",
                            "item": "Shell Garden JSK",
                            "keywords": ["JSK"],
                            "urgency": "high",
                            "url": "https://example.com/drop",
                        }
                    ],
                    "trend": [
                        {
                            "feed_type": "trend",
                            "brand": "AP",
                            "trend": "rising",
                            "confidence": 80,
                            "price_delta": 0.5,
                            "reason_codes": ["sample_supported"],
                            "url": "https://example.com/market",
                        }
                    ],
                    "alert": [
                        {
                            "feed_type": "alert",
                            "kind": "new_release",
                            "title": "Shell Garden JSK",
                            "reason_codes": ["new_release"],
                            "url": "https://example.com/ap/shell",
                        }
                    ],
                },
                "all": [
                    {"feed_type": "release", "url": "https://example.com/ap/shell"},
                    {"feed_type": "drop", "url": "https://example.com/drop"},
                    {"feed_type": "alert", "url": "https://example.com/ap/shell"},
                    {"feed_type": "trend", "url": "https://example.com/market"},
                ],
            }

            check = audit_module.audit_feed_contract()
        finally:
            audit_module.sample_home_feed = original_sample_home_feed

        self.assertEqual(check.status, "fail")
        self.assertIn("release.visual.image_url", check.detail)

    def test_feed_contract_requires_summary_fields(self) -> None:
        original_sample_home_feed = audit_module.sample_home_feed
        try:
            feed = audit_module.sample_home_feed()
            feed["summary"].pop("releases", None)
            audit_module.sample_home_feed = lambda: feed

            check = audit_module.audit_feed_contract()
        finally:
            audit_module.sample_home_feed = original_sample_home_feed

        self.assertEqual(check.status, "fail")
        self.assertIn("releases", check.detail)

    def test_generic_shop_item_extraction_audit_checks_drop_card_context(self) -> None:
        check = audit_module.audit_generic_shop_item_extraction()

        self.assertEqual(check.status, "pass")
        self.assertEqual(check.name, "generic_shop_item_extraction")
        self.assertIn("source time, image, price", check.detail)

    def test_trend_engine_audit_checks_three_directions_and_release_activity_input(self) -> None:
        check = audit_module.audit_trend_engine()

        self.assertEqual(check.status, "pass")
        self.assertIn("rising/cooling/stable", check.detail)
        self.assertIn("release activity", check.detail)
        self.assertIn("stale release filtering", check.detail)

    def test_trend_engine_audit_rejects_missing_direction_brand(self) -> None:
        original_build_trend_feed = audit_module.build_trend_feed
        try:
            audit_module.build_trend_feed = lambda *_args, **_kwargs: [
                {
                    "brand": "AP",
                    "trend": "rising",
                    "confidence": 70,
                    "reason_codes": ["sample_supported", "premium_rising", "release_activity"],
                }
            ]

            check = audit_module.audit_trend_engine()
        finally:
            audit_module.build_trend_feed = original_build_trend_feed

        self.assertEqual(check.status, "fail")
        self.assertIn("missing trend brands", check.detail)

    def test_trend_engine_audit_rejects_missing_release_activity(self) -> None:
        original_build_trend_feed = audit_module.build_trend_feed
        try:
            audit_module.build_trend_feed = lambda *_args, **_kwargs: [
                {
                    "brand": "AP",
                    "trend": "rising",
                    "confidence": 70,
                    "reason_codes": ["sample_supported", "premium_rising", "momentum_observed"],
                },
                {
                    "brand": "Meta",
                    "trend": "cooling",
                    "confidence": 50,
                    "reason_codes": ["sample_supported", "premium_cooling"],
                },
                {
                    "brand": "BABY",
                    "trend": "stable",
                    "confidence": 0,
                    "reason_codes": ["sample_gap", "premium_stable"],
                },
            ]

            check = audit_module.audit_trend_engine()
        finally:
            audit_module.build_trend_feed = original_build_trend_feed

        self.assertEqual(check.status, "fail")
        self.assertIn("release events", check.detail)

    def test_trend_engine_audit_rejects_stale_release_activity(self) -> None:
        original_build_trend_feed = audit_module.build_trend_feed

        def fake_build_trend_feed(_market, _momentum, events, **_kwargs):
            ap_reasons = ["sample_supported", "premium_rising", "momentum_observed"]
            if events:
                ap_reasons.append("release_activity")
            return [
                {
                    "brand": "AP",
                    "trend": "rising",
                    "confidence": 75 if events else 70,
                    "reason_codes": ap_reasons,
                },
                {
                    "brand": "Meta",
                    "trend": "cooling",
                    "confidence": 50,
                    "reason_codes": ["sample_supported", "premium_cooling"],
                },
                {
                    "brand": "BABY",
                    "trend": "stable",
                    "confidence": 0,
                    "reason_codes": ["sample_gap", "premium_stable"],
                },
            ]

        try:
            audit_module.build_trend_feed = fake_build_trend_feed

            check = audit_module.audit_trend_engine()
        finally:
            audit_module.build_trend_feed = original_build_trend_feed

        self.assertEqual(check.status, "fail")
        self.assertIn("stale release events", check.detail)

    def test_audit_passes_with_complete_loop_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root)
            db_path = root / "radar.sqlite"
            log_path = root / "loop.log"
            exit_path = root / "loop.exit"
            log_path.write_text(
                "\n".join(
                    [
                        "# started_at: 2026-06-30T00:00:00+00:00",
                        "cycle | ok | event_count | error_message",
                        "1 | ok | 1 | ",
                        "2 | ok | 0 | ",
                        "# finished_at: 2026-06-30T00:05:00+00:00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            exit_path.write_text("0\n", encoding="utf-8")
            connection = connect(db_path)
            try:
                record_source_run(
                    connection,
                    "angelic_pretty",
                    ok=True,
                    status="ok",
                    item_count=1,
                    event_count=1,
                    latency_ms=20,
                    checked_at="2026-06-30T00:00:00+00:00",
                )
                record_source_run(
                    connection,
                    "angelic_pretty",
                    ok=True,
                    status="ok",
                    item_count=1,
                    event_count=0,
                    latency_ms=18,
                    checked_at="2026-06-30T00:05:00+00:00",
                )
                connection.commit()
            finally:
                connection.close()

            audit = audit_feed_os(
                config_path=config_path,
                db_path=db_path,
                loop_log_path=log_path,
                loop_exit_path=exit_path,
                expected_cycles=2,
                min_duration_seconds=0,
            )

            self.assertTrue(audit.complete)
            self.assertIn("status: complete", format_feed_os_audit(audit))
            self.assertIn("pass | stable_loop_evidence", format_feed_os_audit(audit))
            payload = json.loads(format_feed_os_audit_json(audit))
            stable_check = next(check for check in payload["checks"] if check["name"] == "stable_loop_evidence")
            self.assertEqual(stable_check["status"], "pass")
            self.assertEqual(stable_check["evidence"]["status"], "complete")
            self.assertEqual(stable_check["evidence"]["duration_seconds"], 300)
            self.assertEqual(stable_check["evidence"]["duplicate_cycles"], [])
            self.assertEqual(stable_check["evidence"]["source_cycle_counts"], {"angelic_pretty": 2})
            self.assertEqual(stable_check["evidence"]["unhealthy_source_runs"], {})

    def test_audit_reports_duplicate_loop_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root)
            db_path = root / "radar.sqlite"
            log_path = root / "loop.log"
            exit_path = root / "loop.exit"
            log_path.write_text(
                "\n".join(
                    [
                        "# started_at: 2026-06-30T00:00:00+00:00",
                        "cycle | ok | event_count | error_message",
                        "1 | ok | 1 | ",
                        "2 | ok | 0 | ",
                        "2 | ok | 0 | ",
                        "# finished_at: 2026-06-30T00:05:00+00:00",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            exit_path.write_text("0\n", encoding="utf-8")
            connection = connect(db_path)
            try:
                record_source_run(
                    connection,
                    "angelic_pretty",
                    ok=True,
                    status="ok",
                    item_count=1,
                    checked_at="2026-06-30T00:00:00+00:00",
                )
                record_source_run(
                    connection,
                    "angelic_pretty",
                    ok=True,
                    status="ok",
                    item_count=1,
                    checked_at="2026-06-30T00:05:00+00:00",
                )
                connection.commit()
            finally:
                connection.close()

            audit = audit_feed_os(
                config_path=config_path,
                db_path=db_path,
                loop_log_path=log_path,
                loop_exit_path=exit_path,
                expected_cycles=2,
                min_duration_seconds=0,
            )

            payload = json.loads(format_feed_os_audit_json(audit))
            stable_check = next(check for check in payload["checks"] if check["name"] == "stable_loop_evidence")
            self.assertFalse(audit.complete)
            self.assertEqual(stable_check["status"], "fail")
            self.assertIn("duplicate=[2]", stable_check["detail"])
            self.assertIn("missing_cycle_timestamps=[]", stable_check["detail"])
            self.assertIn("cycle_time_mismatches=[]", stable_check["detail"])
            self.assertEqual(stable_check["evidence"]["duplicate_cycles"], [2])
            self.assertEqual(stable_check["evidence"]["missing_cycle_timestamps"], [])
            self.assertEqual(stable_check["evidence"]["cycle_time_mismatches"], [])

    def test_audit_checks_runtime_feed_state_from_current_config_and_db(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root)
            brands_path = root / "brands.json"
            market_path = root / "market.json"
            db_path = root / "radar.sqlite"
            brands_path.write_text(
                json.dumps(
                    [
                        {
                            "alias": "AP",
                            "name": "Angelic Pretty",
                            "weight": 100,
                            "watch_urls": [{"label": "market", "url": "https://example.com/market/ap"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            market_path.write_text(
                json.dumps(
                    [
                        {
                            "brand_alias": "AP",
                            "item_name": "Shell Garden JSK",
                            "retail_price": 2000,
                            "resale_price": 3000,
                            "url": "https://example.com/resale/shell",
                        },
                        {
                            "brand_alias": "AP",
                            "item_name": "Shell Garden OP",
                            "retail_price": 1800,
                            "resale_price": 2700,
                            "url": "https://example.com/resale/op",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            connection = connect(db_path)
            try:
                diff_and_store(
                    connection,
                    [
                        RadarItem(
                            source="angelic_pretty",
                            title="Shell Garden JSK",
                            url="https://example.com/ap/shell",
                            status=ItemStatus.NEW_ARRIVAL,
                            published_at="2026-06-30",
                            metadata={"price": "¥38,280"},
                        ),
                        RadarItem(
                            source="generic_page",
                            title="Proxy Shell Garden JSK",
                            url="https://example.com/proxy/shell",
                            status=ItemStatus.SHOP_NEWS,
                            published_at="2026-06-30",
                            metadata={
                                "shop": {"name": "Proxy Shop", "url": "https://example.com/proxy"},
                                "item": {"title": "Shell Garden JSK", "url": "https://example.com/proxy/shell"},
                                "matched_keywords": ["JSK", "预约"],
                            },
                        ),
                    ],
                )
                record_source_run(
                    connection,
                    "angelic_pretty",
                    ok=False,
                    status="failed",
                    error_rate=1.0,
                    latency_ms=800,
                    item_count=0,
                    error_message="timeout",
                )
                connection.commit()
            finally:
                connection.close()

            audit = audit_feed_os(
                config_path=config_path,
                db_path=db_path,
                brands_path=brands_path,
                market_path=market_path,
                expected_cycles=2,
                min_duration_seconds=0,
            )
            text = format_feed_os_audit(audit)

            self.assertIn("pass | runtime_feed_state", text)
            self.assertIn("release=1", text)
            self.assertIn("drop=1", text)
            self.assertIn("trend=1", text)
            self.assertIn("alert=", text)

    def test_runtime_feed_audit_rejects_navigation_noise(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: self.runtime_state(
                {
                    "feed_type": "release",
                    "brand": "AP",
                    "title": "Login",
                    "type": "new_arrival",
                    "time": f"{datetime.now(timezone.utc).year}-06-30",
                    "price": "未取得",
                    "url": "https://example.com/login",
                }
            )
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("navigation noise", check.detail)

    def test_runtime_feed_audit_rejects_stale_release_time(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: self.runtime_state(
                {
                    "feed_type": "release",
                    "brand": "AP",
                    "title": "Old Release JSK",
                    "type": "new_arrival",
                    "time": "2025-12-31",
                    "price": "未取得",
                    "url": "https://example.com/ap/old",
                }
            )
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("stale source time", check.detail)

    def test_runtime_feed_audit_rejects_stale_release_alert_time(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: {
                "feed": {
                    "summary": {"releases": 0, "drops": 0, "trends": 0, "alerts": 1, "shops": 0},
                    "streams": {
                        "release": [],
                        "drop": [],
                        "trend": [],
                        "alert": [
                            {
                                "feed_type": "alert",
                                "kind": "new_release",
                                "title": "Old Release JSK",
                                "reason_codes": ["new_release"],
                                "time": "2025-12-31",
                                "url": "https://example.com/ap/old",
                                "visual": self.visual("AL", "!", "new_release"),
                            }
                        ],
                    },
                    "all": [{"feed_type": "alert", "url": "https://example.com/ap/old"}],
                }
            }
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("stream alert row has stale source time", check.detail)

    def test_runtime_feed_audit_allows_old_source_health_alert_time(self) -> None:
        streams = {
            "release": [],
            "drop": [],
            "trend": [],
            "alert": [
                {
                    "feed_type": "alert",
                    "kind": "failed",
                    "title": "angelic_pretty failed",
                    "reason_codes": ["source_health"],
                    "time": "2025-12-31T00:00:00+00:00",
                    "url": "https://example.com/ap",
                    "visual": self.visual("AL", "!", "failed"),
                }
            ],
        }

        self.assertEqual(audit_module.runtime_feed_noise_problem(streams), "")

    def test_runtime_feed_audit_rejects_missing_card_visual(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: self.runtime_state(
                {
                    "feed_type": "release",
                    "brand": "AP",
                    "title": "Shell Garden JSK",
                    "type": "new_arrival",
                    "time": f"{datetime.now(timezone.utc).year}-06-30",
                    "price": "未取得",
                    "url": "https://example.com/ap/shell",
                    "visual": {},
                },
                add_visual=False,
            )
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("invalid visual", check.detail)

    def test_runtime_feed_audit_checks_all_rows_not_only_first_items(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        current_year = datetime.now(timezone.utc).year
        try:
            audit_module.get_feed_state = lambda **_kwargs: {
                "feed": {
                    "summary": {"releases": 4, "drops": 0, "trends": 0, "alerts": 0, "shops": 0},
                    "streams": {
                        "release": [
                            {
                                "feed_type": "release",
                                "brand": "AP",
                                "title": f"Release {index}",
                                "type": "new_arrival",
                                "time": f"{current_year}-06-{index + 1:02d}",
                                "price": "未取得",
                                "url": f"https://example.com/ap/{index}",
                                "visual": self.visual("AP", "R", "new_arrival"),
                            }
                            for index in range(4)
                        ],
                        "drop": [],
                        "trend": [],
                        "alert": [],
                    },
                    "all": [
                        {
                            "feed_type": "release",
                            "url": f"https://example.com/ap/{index}",
                        }
                        for index in range(4)
                    ],
                }
            }
            state = audit_module.get_feed_state()
            state["feed"]["streams"]["release"][3]["price"] = ""
            audit_module.get_feed_state = lambda **_kwargs: state
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("stream release row missing fields: price", check.detail)

    def test_runtime_feed_audit_rejects_summary_count_mismatch(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: {
                "feed": {
                    "summary": {"releases": 2, "drops": 0, "trends": 0, "alerts": 0, "shops": 0},
                    "streams": {
                        "release": [],
                        "drop": [],
                        "trend": [],
                        "alert": [],
                    },
                    "all": [],
                }
            }
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("summary releases=2", check.detail)

    def test_runtime_feed_audit_rejects_invalid_trend_values(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: {
                "feed": {
                    "summary": {"releases": 0, "drops": 0, "trends": 1, "alerts": 0, "shops": 0},
                    "streams": {
                        "release": [],
                        "drop": [],
                        "trend": [
                            {
                                "feed_type": "trend",
                                "brand": "AP",
                                "trend": "rising",
                                "confidence": 120,
                                "price_delta": 0.5,
                                "reason_codes": ["sample_supported"],
                                "url": "https://example.com/market",
                                "visual": self.visual("AP", "T", "rising"),
                            }
                        ],
                        "alert": [],
                    },
                    "all": [{"feed_type": "trend", "url": "https://example.com/market"}],
                }
            }
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("invalid confidence", check.detail)

    def test_runtime_feed_audit_rejects_invalid_drop_values(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: {
                "feed": {
                    "summary": {"releases": 0, "drops": 1, "trends": 0, "alerts": 0, "shops": 1},
                    "streams": {
                        "release": [],
                        "drop": [
                            {
                                "feed_type": "drop",
                                "shop": "Proxy",
                                "item": "Shell Garden JSK",
                                "keywords": ["JSK"],
                                "urgency": "soon",
                                "url": "https://example.com/drop",
                                "visual": self.visual("SH", "D", "shop_news"),
                            }
                        ],
                        "trend": [],
                        "alert": [],
                    },
                    "all": [{"feed_type": "drop", "url": "https://example.com/drop"}],
                }
            }
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("invalid urgency", check.detail)

    def test_runtime_feed_audit_rejects_invalid_drop_context_values(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: self.drop_runtime_state(
                {
                    "price": 12800,
                    "time": "2026-06-30",
                    "time_kind": "seen",
                    "visual": self.visual("SH", "D", "shop_news"),
                }
            )
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("invalid price", check.detail)

    def test_runtime_feed_audit_rejects_missing_drop_source_time(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: self.drop_runtime_state(
                {
                    "price": "¥12,800",
                    "visual": self.visual("SH", "D", "shop_news"),
                }
            )
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("missing source time", check.detail)

    def test_runtime_feed_audit_rejects_invalid_drop_image_url(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: self.drop_runtime_state(
                {
                    "price": "¥12,800",
                    "time": "2026-06-30",
                    "time_kind": "published",
                    "visual": {"initials": "SH", "mark": "D", "tone": "shop_news", "image_url": "/relative.webp"},
                }
            )
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("invalid image_url", check.detail)

    def test_runtime_feed_audit_rejects_invalid_drop_time_kind(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        try:
            audit_module.get_feed_state = lambda **_kwargs: self.drop_runtime_state(
                {
                    "price": "¥12,800",
                    "time": "2026-06-30",
                    "time_kind": "seen",
                    "visual": self.visual("SH", "D", "shop_news"),
                }
            )
            check = audit_module.audit_runtime_feed_state(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
            )
        finally:
            audit_module.get_feed_state = original_get_feed_state

        self.assertEqual(check.status, "fail")
        self.assertIn("invalid time_kind", check.detail)

    def test_runtime_feed_payload_audit_rejects_full_state_leak(self) -> None:
        original_get_feed_payload = audit_module.get_feed_payload
        expected_feed = {
            "summary": {"releases": 0, "drops": 0, "trends": 0, "alerts": 0, "shops": 0},
            "streams": {"release": [], "drop": [], "trend": [], "alert": []},
            "all": [],
        }
        try:
            audit_module.get_feed_payload = lambda **_kwargs: {
                "ok": True,
                "counts": {},
                "feed": expected_feed,
                "items": [],
                "events": [],
            }
            problem = audit_module.runtime_feed_payload_problem(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
                brands_path=None,
                market_path=None,
                expected_feed=expected_feed,
            )
        finally:
            audit_module.get_feed_payload = original_get_feed_payload

        self.assertIn("leaks full state keys", problem)
        self.assertIn("items", problem)
        self.assertIn("events", problem)

    def test_runtime_feed_payload_audit_rejects_feed_mismatch(self) -> None:
        original_get_feed_payload = audit_module.get_feed_payload
        expected_feed = {
            "summary": {"releases": 0, "drops": 0, "trends": 0, "alerts": 0, "shops": 0},
            "streams": {"release": [], "drop": [], "trend": [], "alert": []},
            "all": [],
        }
        try:
            audit_module.get_feed_payload = lambda **_kwargs: {
                "ok": True,
                "counts": {},
                "feed": {
                    "summary": {"releases": 1, "drops": 0, "trends": 0, "alerts": 0, "shops": 0},
                    "streams": {"release": [], "drop": [], "trend": [], "alert": []},
                    "all": [],
                },
            }
            problem = audit_module.runtime_feed_payload_problem(
                config_path=Path("config/sources.yaml"),
                db_path=Path(".data/test.sqlite"),
                brands_path=None,
                market_path=None,
                expected_feed=expected_feed,
            )
        finally:
            audit_module.get_feed_payload = original_get_feed_payload

        self.assertIn("does not match", problem)

    def test_cli_audit_returns_nonzero_when_evidence_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root)
            db_path = root / "radar.sqlite"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "audit-feed-os",
                        "--config",
                        str(config_path),
                        "--db",
                        str(db_path),
                        "--expected-cycles",
                        "2",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("status: incomplete", stdout.getvalue())
            self.assertIn("missing | stable_loop_evidence", stdout.getvalue())

    def test_cli_audit_can_emit_machine_readable_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root)
            db_path = root / "radar.sqlite"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "audit-feed-os",
                        "--config",
                        str(config_path),
                        "--db",
                        str(db_path),
                        "--expected-cycles",
                        "2",
                        "--json",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertEqual(payload["status"], "incomplete")
            self.assertFalse(payload["complete"])
            self.assertEqual(payload["counts"]["missing"], 1)
            self.assertIn("checks", payload)
            stable_check = next(check for check in payload["checks"] if check["name"] == "stable_loop_evidence")
            self.assertEqual(stable_check["status"], "missing")
            self.assertTrue(stable_check["evidence"]["required"]["loop_log"])
            self.assertTrue(stable_check["evidence"]["required"]["loop_exit_file"])
            self.assertTrue(stable_check["evidence"]["required"]["source_runs"])
            self.assertEqual(stable_check["evidence"]["expected_cycles"], 2)
            self.assertEqual(stable_check["evidence"]["min_duration_seconds"], 86400)
            self.assertIn("no duplicate cycles", stable_check["evidence"]["required_checks"])

    def test_format_feed_os_audit_json_includes_counts_and_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audit = audit_feed_os(config_path=self.write_config(root), db_path=root / "radar.sqlite", expected_cycles=2)

            payload = json.loads(format_feed_os_audit_json(audit))

            self.assertEqual(payload["status"], audit.status)
            self.assertEqual(payload["complete"], audit.complete)
            self.assertEqual(payload["counts"], audit.counts())
            self.assertEqual(payload["checks"][0]["name"], audit.checks[0].name)

    def write_config(self, root: Path) -> Path:
        config_path = root / "sources.yaml"
        config_path.write_text(
            """
sources:
  angelic_pretty:
    type: angelic_pretty
    enabled: true
    url: "https://example.com/ap"
""".strip(),
            encoding="utf-8",
        )
        return config_path

    def runtime_state(self, release_row: dict, add_visual: bool = True) -> dict:
        if add_visual and "visual" not in release_row:
            release_row = {**release_row, "visual": self.visual("AP", "R", str(release_row.get("type") or "release"))}
        return {
            "feed": {
                "summary": {"releases": 1, "drops": 0, "trends": 0, "alerts": 0, "shops": 0},
                "streams": {
                    "release": [release_row],
                    "drop": [],
                    "trend": [],
                    "alert": [],
                },
                "all": [release_row],
            }
        }

    def drop_runtime_state(self, overrides: dict) -> dict:
        row = {
            "feed_type": "drop",
            "shop": "Tokyo Proxy",
            "item": "Shell Garden JSK",
            "keywords": ["JSK"],
            "urgency": "high",
            "url": "https://example.com/drop",
            "visual": self.visual("SH", "D", "shop_news"),
            **overrides,
        }
        return {
            "feed": {
                "summary": {"releases": 0, "drops": 1, "trends": 0, "alerts": 0, "shops": 1},
                "streams": {"release": [], "drop": [row], "trend": [], "alert": []},
                "all": [row],
            }
        }

    def visual(self, initials: str, mark: str, tone: str) -> dict[str, str]:
        return {"initials": initials, "mark": mark, "tone": tone, "image_url": ""}


if __name__ == "__main__":
    unittest.main()
