import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from lolita_radar.cli import format_feed_os_audit, main
from lolita_radar.core import audit_feed_os
from lolita_radar.storage import connect, record_source_run


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
            )

            self.assertTrue(audit.complete)
            self.assertIn("status: complete", format_feed_os_audit(audit))
            self.assertIn("pass | stable_loop_evidence", format_feed_os_audit(audit))

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


if __name__ == "__main__":
    unittest.main()
