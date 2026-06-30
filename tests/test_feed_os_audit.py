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
                        "cycle | ok | event_count | error_message",
                        "1 | ok | 1 | ",
                        "2 | ok | 0 | ",
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

    def test_runtime_feed_audit_checks_all_rows_not_only_first_items(self) -> None:
        original_get_feed_state = audit_module.get_feed_state
        current_year = datetime.now(timezone.utc).year
        try:
            audit_module.get_feed_state = lambda **_kwargs: {
                "feed": {
                    "summary": {"drops": 4, "shops": 0, "trends": 0, "alerts": 0},
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
            self.assertTrue(any(check["name"] == "stable_loop_evidence" for check in payload["checks"]))

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

    def runtime_state(self, release_row: dict) -> dict:
        return {
            "feed": {
                "summary": {"drops": 1, "shops": 0, "trends": 0, "alerts": 0},
                "streams": {
                    "release": [release_row],
                    "drop": [],
                    "trend": [],
                    "alert": [],
                },
                "all": [release_row],
            }
        }


if __name__ == "__main__":
    unittest.main()
