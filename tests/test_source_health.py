import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import signal

import lolita_radar.cli as cli
import lolita_radar.runner as runner
from lolita_radar.cli import DEFAULT_LOOP_CYCLES, format_health_rows, format_loop_results, main
from lolita_radar.runner import CheckLoopResult
from lolita_radar.models import ItemStatus, RadarItem
from lolita_radar.storage import connect, list_source_runs, record_source_run


class FakeGoodAdapter:
    def __init__(self, config) -> None:
        self.config = config

    def fetch_items(self) -> list[RadarItem]:
        return [
            RadarItem(
                source=self.config.name,
                title="New Arrival: Test JSK",
                url=f"{self.config.url}/new",
                status=ItemStatus.NEW_ARRIVAL,
                content="fixture content",
            )
        ]


class FakeBadAdapter:
    def __init__(self, config) -> None:
        self.config = config

    def fetch_items(self) -> list[RadarItem]:
        raise RuntimeError("adapter boom")


class SourceHealthTests(unittest.TestCase):
    def test_successful_source_run_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good"})
            db_path = root / "radar.sqlite"
            original = dict(runner.ADAPTERS)
            try:
                runner.ADAPTERS.update({"fake_good": FakeGoodAdapter})
                events = runner.check_sources(config_path=config_path, db_path=db_path, notify=False)
                connection = connect(db_path)
                try:
                    runs = list_source_runs(connection)
                finally:
                    connection.close()
            finally:
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

            self.assertEqual(len(events), 1)
            self.assertEqual(runs[0]["source"], "good")
            self.assertTrue(runs[0]["ok"])
            self.assertEqual(runs[0]["item_count"], 1)
            self.assertEqual(runs[0]["event_count"], 1)
            self.assertGreaterEqual(runs[0]["latency_ms"], 0)
            self.assertEqual(runs[0]["error_message"], "")

    def test_failed_source_does_not_stop_check_all_and_records_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good", "bad": "fake_bad"})
            db_path = root / "radar.sqlite"
            original = dict(runner.ADAPTERS)
            try:
                runner.ADAPTERS.update({"fake_good": FakeGoodAdapter, "fake_bad": FakeBadAdapter})
                events = runner.check_sources(config_path=config_path, db_path=db_path, notify=False)
                connection = connect(db_path)
                try:
                    runs = list_source_runs(connection)
                finally:
                    connection.close()
            finally:
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

            self.assertEqual(len(events), 1)
            by_source = {run["source"]: run for run in runs}
            self.assertTrue(by_source["good"]["ok"])
            self.assertFalse(by_source["bad"]["ok"])
            self.assertIn("adapter boom", by_source["bad"]["error_message"])

    def test_latest_source_health_uses_recent_error_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"flaky": "fake_good"})
            db_path = root / "radar.sqlite"
            connection = connect(db_path)
            try:
                record_source_run(
                    connection,
                    "flaky",
                    ok=False,
                    status="failed",
                    error_rate=1.0,
                    error_message="timeout",
                    checked_at="2026-06-30T00:00:00+00:00",
                )
                record_source_run(
                    connection,
                    "flaky",
                    ok=True,
                    status="ok",
                    error_rate=0.0,
                    item_count=1,
                    checked_at="2026-06-30T00:05:00+00:00",
                )
                connection.commit()
            finally:
                connection.close()

            rows = runner.latest_source_health(config_path=config_path, db_path=db_path)

            self.assertEqual(rows[0]["source"], "flaky")
            self.assertTrue(rows[0]["ok"])
            self.assertEqual(rows[0]["status"], "degraded")
            self.assertEqual(rows[0]["error_rate"], 0.5)
            self.assertEqual(rows[0]["latency_ms"], 0)

    def test_source_run_storage_normalizes_status_and_error_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "radar.sqlite"
            connection = connect(db_path)
            try:
                record_source_run(
                    connection,
                    "good",
                    ok=True,
                    status="strange",
                    error_rate=float("nan"),
                    latency_ms=float("nan"),
                    item_count=1,
                )
                record_source_run(
                    connection,
                    "bad",
                    ok=False,
                    status="ok",
                    error_rate=2.5,
                    latency_ms=-10,
                    error_message="boom",
                )
                connection.commit()
                rows = list_source_runs(connection)
            finally:
                connection.close()

            by_source = {row["source"]: row for row in rows}
            self.assertEqual(by_source["good"]["status"], "ok")
            self.assertEqual(by_source["good"]["error_rate"], 0.0)
            self.assertEqual(by_source["good"]["latency_ms"], 0)
            self.assertEqual(by_source["bad"]["status"], "failed")
            self.assertEqual(by_source["bad"]["error_rate"], 1.0)
            self.assertEqual(by_source["bad"]["latency_ms"], 0)

    def test_format_health_rows_includes_latency_ms(self) -> None:
        text = format_health_rows(
            [
                {
                    "source": "good",
                    "status": "ok",
                    "ok": True,
                    "error_rate": 0.0,
                    "latency_ms": 42,
                    "item_count": 1,
                    "event_count": 0,
                    "checked_at": "2026-06-30T00:00:00+00:00",
                    "error_message": "",
                }
            ]
        )

        self.assertIn("latency_ms", text.splitlines()[0])
        self.assertIn("42", text)

    def test_run_loop_runs_multiple_cycles_without_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good"})
            db_path = root / "radar.sqlite"
            log_path = root / "loop.log"
            exit_path = root / "loop.exit"
            original = dict(runner.ADAPTERS)
            stdout = io.StringIO()
            try:
                runner.ADAPTERS.update({"fake_good": FakeGoodAdapter})
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "run-loop",
                            "--config",
                            str(config_path),
                            "--db",
                            str(db_path),
                            "--cycles",
                            "2",
                            "--interval-seconds",
                            "0",
                            "--log-file",
                            str(log_path),
                            "--exit-file",
                            str(exit_path),
                        ]
                    )
            finally:
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("cycle | ok | event_count | error_message", output)
            self.assertIn("1 | ok", output)
            self.assertIn("2 | ok", output)
            self.assertIn("cycle | ok | event_count | error_message", log_path.read_text(encoding="utf-8"))
            self.assertIn("1 | ok", log_path.read_text(encoding="utf-8"))
            self.assertIn("2 | ok", log_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_path.read_text(encoding="utf-8"), "0\n")

            verify_stdout = io.StringIO()
            with redirect_stdout(verify_stdout):
                verify_exit_code = main(
                    [
                        "verify-loop",
                        "--config",
                        str(config_path),
                        "--db",
                        str(db_path),
                        "--log",
                        str(log_path),
                        "--exit-file",
                        str(exit_path),
                        "--expected-cycles",
                        "2",
                    ]
                )

            self.assertEqual(verify_exit_code, 0)
            self.assertIn("status: complete", verify_stdout.getvalue())

    def test_run_loop_writes_failed_exit_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"bad": "fake_bad"})
            db_path = root / "radar.sqlite"
            log_path = root / "logs" / "loop.log"
            exit_path = root / "logs" / "loop.exit"
            original = dict(runner.ADAPTERS)
            stdout = io.StringIO()
            try:
                runner.ADAPTERS.update({"fake_bad": FakeBadAdapter})
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "run-loop",
                            "--config",
                            str(config_path),
                            "--db",
                            str(db_path),
                            "--cycles",
                            "1",
                            "--interval-seconds",
                            "0",
                            "--log-file",
                            str(log_path),
                            "--exit-file",
                            str(exit_path),
                        ]
                    )
            finally:
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

            self.assertEqual(exit_code, 1)
            self.assertIn("1 | failed", stdout.getvalue())
            self.assertIn("1 | failed", log_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_path.read_text(encoding="utf-8"), "1\n")

    def test_run_loop_writes_sigterm_exit_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good"})
            db_path = root / "radar.sqlite"
            log_path = root / "loop.log"
            exit_path = root / "loop.exit"
            stdout = io.StringIO()
            stderr = io.StringIO()

            def terminate_loop(**kwargs) -> list[CheckLoopResult]:
                os.kill(os.getpid(), signal.SIGTERM)
                raise AssertionError("SIGTERM handler should interrupt run-loop")

            original_run_check_loop = cli.run_check_loop
            try:
                cli.run_check_loop = terminate_loop
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "run-loop",
                            "--config",
                            str(config_path),
                            "--db",
                            str(db_path),
                            "--cycles",
                            "2",
                            "--interval-seconds",
                            "0",
                            "--log-file",
                            str(log_path),
                            "--exit-file",
                            str(exit_path),
                        ]
                    )
            finally:
                cli.run_check_loop = original_run_check_loop

            self.assertEqual(exit_code, 143)
            self.assertIn("interrupted by SIGTERM", stderr.getvalue())
            self.assertEqual(exit_path.read_text(encoding="utf-8"), "143\n")
            self.assertIn("cycle | ok | event_count | error_message", log_path.read_text(encoding="utf-8"))

    def test_run_loop_ignores_old_source_failure_after_latest_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good"})
            db_path = root / "radar.sqlite"
            exit_path = root / "loop.exit"
            connection = connect(db_path)
            try:
                record_source_run(
                    connection,
                    "good",
                    ok=False,
                    status="failed",
                    error_message="old timeout",
                    checked_at="2026-06-29T23:50:00+00:00",
                )
                connection.commit()
            finally:
                connection.close()
            original = dict(runner.ADAPTERS)
            stdout = io.StringIO()
            try:
                runner.ADAPTERS.update({"fake_good": FakeGoodAdapter})
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "run-loop",
                            "--config",
                            str(config_path),
                            "--db",
                            str(db_path),
                            "--cycles",
                            "1",
                            "--interval-seconds",
                            "0",
                            "--exit-file",
                            str(exit_path),
                        ]
                    )
            finally:
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

            self.assertEqual(exit_code, 0)
            self.assertIn("1 | ok", stdout.getvalue())
            self.assertEqual(exit_path.read_text(encoding="utf-8"), "0\n")

    def test_loop_result_formatter_keeps_audit_table_shape(self) -> None:
        output = format_loop_results(
            [
                CheckLoopResult(cycle=1, ok=True, event_count=2),
                CheckLoopResult(cycle=2, ok=False, event_count=0, error_message="boom"),
            ]
        )

        self.assertIn("cycle | ok | event_count | error_message", output)
        self.assertIn("1 | ok | 2 |", output)
        self.assertIn("2 | failed | 0 | boom", output)

    def test_loop_default_cycles_cover_24h_at_five_minutes(self) -> None:
        self.assertEqual(DEFAULT_LOOP_CYCLES, 288)

    def test_loop_help_exposes_audit_file_options(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            main(["run-loop", "--help"])

        self.assertEqual(raised.exception.code, 0)
        output = stdout.getvalue()
        self.assertIn("--log-file", output)
        self.assertIn("--exit-file", output)
        self.assertIn("--cycles", output)
        self.assertIn("--interval-seconds", output)

    def test_verify_loop_help_exposes_audit_inputs(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            main(["verify-loop", "--help"])

        self.assertEqual(raised.exception.code, 0)
        output = stdout.getvalue()
        self.assertIn("--log", output)
        self.assertIn("--db", output)
        self.assertIn("--exit-file", output)
        self.assertIn("--expected-cycles", output)

    def test_verify_loop_reports_complete_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good", "other": "fake_good"})
            db_path = root / "radar.sqlite"
            log_path = root / "loop.log"
            exit_path = root / "loop.exit"
            log_path.write_text(
                "\n".join(
                    [
                        "cycle | ok | event_count | error_message",
                        "1 | ok | 2 |",
                        "2 | ok | 0 |",
                    ]
                ),
                encoding="utf-8",
            )
            exit_path.write_text("0\n", encoding="utf-8")
            connection = connect(db_path)
            try:
                record_source_run(connection, "good", ok=True, item_count=2, latency_ms=100)
                record_source_run(connection, "other", ok=True, item_count=1, latency_ms=120)
                record_source_run(connection, "good", ok=True, item_count=1, latency_ms=250)
                record_source_run(connection, "other", ok=True, item_count=3, latency_ms=90)
                connection.commit()
            finally:
                connection.close()
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "verify-loop",
                        "--config",
                        str(config_path),
                        "--db",
                        str(db_path),
                        "--log",
                        str(log_path),
                        "--exit-file",
                        str(exit_path),
                        "--expected-cycles",
                        "2",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("status: complete", stdout.getvalue())
            self.assertIn("missing_cycles: []", stdout.getvalue())
            self.assertIn("unhealthy_source_runs: []", stdout.getvalue())
            self.assertIn("good: 2", stdout.getvalue())
            self.assertIn("other: 2", stdout.getvalue())
            self.assertIn("source_health:", stdout.getvalue())
            self.assertIn("good: runs=2, max_latency_ms=250, min_item_count=1", stdout.getvalue())
            self.assertIn("other: runs=2, max_latency_ms=120, min_item_count=1", stdout.getvalue())
            self.assertIn("max_error_rate=0.0", stdout.getvalue())

    def test_verify_loop_reports_missing_cycle_even_when_log_line_count_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good"})
            db_path = root / "radar.sqlite"
            log_path = root / "loop.log"
            exit_path = root / "loop.exit"
            log_path.write_text(
                "\n".join(
                    [
                        "cycle | ok | event_count | error_message",
                        "1 | ok | 2 |",
                        "1 | ok | 0 |",
                    ]
                ),
                encoding="utf-8",
            )
            exit_path.write_text("0\n", encoding="utf-8")
            connection = connect(db_path)
            try:
                record_source_run(connection, "good", ok=True, item_count=1)
                record_source_run(connection, "good", ok=True, item_count=1)
                connection.commit()
            finally:
                connection.close()
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "verify-loop",
                        "--config",
                        str(config_path),
                        "--db",
                        str(db_path),
                        "--log",
                        str(log_path),
                        "--exit-file",
                        str(exit_path),
                        "--expected-cycles",
                        "2",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("status: incomplete", stdout.getvalue())
            self.assertIn("observed_cycles: 2", stdout.getvalue())
            self.assertIn("missing_cycles: 2", stdout.getvalue())

    def test_verify_loop_reports_unhealthy_source_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good", "bad": "fake_good"})
            db_path = root / "radar.sqlite"
            log_path = root / "loop.log"
            exit_path = root / "loop.exit"
            log_path.write_text(
                "\n".join(
                    [
                        "cycle | ok | event_count | error_message",
                        "1 | ok | 2 |",
                        "2 | ok | 0 |",
                    ]
                ),
                encoding="utf-8",
            )
            exit_path.write_text("0\n", encoding="utf-8")
            connection = connect(db_path)
            try:
                record_source_run(connection, "good", ok=True, item_count=1)
                record_source_run(connection, "good", ok=True, item_count=1)
                record_source_run(connection, "bad", ok=True, status="ok", item_count=1)
                record_source_run(connection, "bad", ok=False, status="failed", error_message="timeout")
                connection.commit()
            finally:
                connection.close()
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "verify-loop",
                        "--config",
                        str(config_path),
                        "--db",
                        str(db_path),
                        "--log",
                        str(log_path),
                        "--exit-file",
                        str(exit_path),
                        "--expected-cycles",
                        "2",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("status: failed", stdout.getvalue())
            self.assertIn("unhealthy_source_runs: bad:1", stdout.getvalue())

    def test_verify_loop_ignores_unhealthy_runs_outside_recent_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good"})
            db_path = root / "radar.sqlite"
            log_path = root / "loop.log"
            exit_path = root / "loop.exit"
            log_path.write_text(
                "\n".join(
                    [
                        "cycle | ok | event_count | error_message",
                        "1 | ok | 2 |",
                        "2 | ok | 0 |",
                    ]
                ),
                encoding="utf-8",
            )
            exit_path.write_text("0\n", encoding="utf-8")
            connection = connect(db_path)
            try:
                record_source_run(
                    connection,
                    "good",
                    ok=False,
                    status="failed",
                    error_message="old timeout",
                    checked_at="2026-06-29T23:50:00+00:00",
                )
                record_source_run(
                    connection,
                    "good",
                    ok=True,
                    status="ok",
                    item_count=1,
                    checked_at="2026-06-30T00:00:00+00:00",
                )
                record_source_run(
                    connection,
                    "good",
                    ok=True,
                    status="ok",
                    item_count=1,
                    checked_at="2026-06-30T00:05:00+00:00",
                )
                connection.commit()
            finally:
                connection.close()
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "verify-loop",
                        "--config",
                        str(config_path),
                        "--db",
                        str(db_path),
                        "--log",
                        str(log_path),
                        "--exit-file",
                        str(exit_path),
                        "--expected-cycles",
                        "2",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("status: complete", stdout.getvalue())
            self.assertIn("unhealthy_source_runs: []", stdout.getvalue())
            self.assertIn("good: 2", stdout.getvalue())

    def test_verify_loop_reports_incomplete_without_exit_and_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good"})
            db_path = root / "radar.sqlite"
            log_path = root / "loop.log"
            log_path.write_text("cycle | ok | event_count | error_message\n1 | ok | 1 |\n", encoding="utf-8")
            connection = connect(db_path)
            try:
                record_source_run(connection, "good", ok=True, item_count=1)
                connection.commit()
            finally:
                connection.close()
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "verify-loop",
                        "--config",
                        str(config_path),
                        "--db",
                        str(db_path),
                        "--log",
                        str(log_path),
                        "--expected-cycles",
                        "2",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("status: incomplete", stdout.getvalue())
            self.assertIn("observed_cycles: 1", stdout.getvalue())
            self.assertIn("exit_code: -", stdout.getvalue())

    def test_verify_loop_reports_failed_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root, {"good": "fake_good"})
            db_path = root / "radar.sqlite"
            log_path = root / "loop.log"
            exit_path = root / "loop.exit"
            log_path.write_text(
                "cycle | ok | event_count | error_message\n1 | ok | 1 |\n2 | failed | 0 | boom\n",
                encoding="utf-8",
            )
            exit_path.write_text("1\n", encoding="utf-8")
            connection = connect(db_path)
            try:
                record_source_run(connection, "good", ok=True, item_count=1)
                record_source_run(connection, "good", ok=False, error_message="boom")
                connection.commit()
            finally:
                connection.close()
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "verify-loop",
                        "--config",
                        str(config_path),
                        "--db",
                        str(db_path),
                        "--log",
                        str(log_path),
                        "--exit-file",
                        str(exit_path),
                        "--expected-cycles",
                        "2",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("status: failed", stdout.getvalue())
            self.assertIn("failed_cycles: 2", stdout.getvalue())

    def write_config(self, root: Path, sources: dict[str, str]) -> Path:
        body = ["sources:"]
        for name, source_type in sources.items():
            body.extend(
                [
                    f"  {name}:",
                    f"    type: {source_type}",
                    "    enabled: true",
                    f"    url: \"https://example.com/{name}\"",
                    "    keywords: []",
                ]
            )
        path = root / "sources.yaml"
        path.write_text("\n".join(body), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
