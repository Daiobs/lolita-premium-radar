import tempfile
import unittest
import io
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from pathlib import Path

import lolita_radar.runner as runner
from lolita_radar.cli import main
from lolita_radar.models import ItemStatus, RadarItem
from lolita_radar.storage import connect, list_events, list_items, storage_counts, upsert_collector_job


class BaselineFakeAdapter:
    content = "baseline content"

    def __init__(self, config) -> None:
        self.config = config

    def fetch_items(self) -> list[RadarItem]:
        return [
            RadarItem(
                source=self.config.name,
                title="New Arrival: Baseline JSK",
                url=f"{self.config.url}/baseline",
                status=ItemStatus.NEW_ARRIVAL,
                content=self.content,
            )
        ]


class BaselineTests(unittest.TestCase):
    def test_baseline_only_writes_items_without_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            config_path.write_text(
                """
sources:
  baseline_source:
    type: baseline_fake
    enabled: true
    url: "https://example.com/source"
    keywords: []
""".strip(),
                encoding="utf-8",
            )
            original = dict(runner.ADAPTERS)
            try:
                runner.ADAPTERS.update({"baseline_fake": BaselineFakeAdapter})
                events = runner.check_sources(config_path=config_path, db_path=db_path, notify=False, baseline_only=True)
                connection = connect(db_path)
                try:
                    counts = storage_counts(connection)
                    stored_events = list_events(connection)
                finally:
                    connection.close()
            finally:
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

            self.assertEqual(events, [])
            self.assertEqual(counts["items"], 1)
            self.assertEqual(counts["events"], 0)
            self.assertEqual(stored_events, [])

    def test_existing_source_baseline_without_force_fails_without_updating_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root)
            db_path = root / "radar.sqlite"
            original = dict(runner.ADAPTERS)
            old_content = BaselineFakeAdapter.content
            try:
                runner.ADAPTERS.update({"baseline_fake": BaselineFakeAdapter})
                BaselineFakeAdapter.content = "first baseline content"
                runner.check_sources(config_path=config_path, db_path=db_path, notify=False, baseline_only=True)
                old_hash = self.item_hash(db_path)

                BaselineFakeAdapter.content = "changed content that must not be absorbed"
                with self.assertRaisesRegex(ValueError, "baseline-only is intended for first deployment"):
                    runner.check_sources(config_path=config_path, db_path=db_path, notify=False, baseline_only=True)

                self.assertEqual(self.item_hash(db_path), old_hash)
            finally:
                BaselineFakeAdapter.content = old_content
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

    def test_force_baseline_allows_existing_source_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root)
            db_path = root / "radar.sqlite"
            original = dict(runner.ADAPTERS)
            old_content = BaselineFakeAdapter.content
            try:
                runner.ADAPTERS.update({"baseline_fake": BaselineFakeAdapter})
                BaselineFakeAdapter.content = "first baseline content"
                runner.check_sources(config_path=config_path, db_path=db_path, notify=False, baseline_only=True)
                old_hash = self.item_hash(db_path)

                BaselineFakeAdapter.content = "forced baseline content"
                events = runner.check_sources(
                    config_path=config_path,
                    db_path=db_path,
                    notify=False,
                    baseline_only=True,
                    force_baseline=True,
                )
                connection = connect(db_path)
                try:
                    stored_events = list_events(connection)
                finally:
                    connection.close()
            finally:
                BaselineFakeAdapter.content = old_content
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

            self.assertEqual(events, [])
            self.assertNotEqual(self.item_hash(db_path), old_hash)
            self.assertEqual(stored_events, [])

    def test_cli_baseline_guardrail_returns_non_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self.write_config(root)
            db_path = root / "radar.sqlite"
            original = dict(runner.ADAPTERS)
            stderr = io.StringIO()
            try:
                runner.ADAPTERS.update({"baseline_fake": BaselineFakeAdapter})
                runner.check_sources(config_path=config_path, db_path=db_path, notify=False, baseline_only=True)
                with redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "check",
                            "--config",
                            str(config_path),
                            "--db",
                            str(db_path),
                            "--all",
                            "--baseline-only",
                        ]
                    )
            finally:
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

            self.assertEqual(exit_code, 2)
            self.assertIn("baseline-only is intended for first deployment", stderr.getvalue())

    def test_cli_collect_baseline_guardrail_returns_non_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "radar.sqlite"
            connection = connect(db_path)
            try:
                upsert_collector_job(connection, "baby", "official_shop", "tests/fixtures/official_shop_products.html")
            finally:
                connection.close()

            self.assertEqual(main(["collect", "--db", str(db_path), "--baseline-only"]), 0)
            old_counts = self.counts(db_path)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["collect", "--db", str(db_path), "--baseline-only"])

            self.assertEqual(exit_code, 2)
            self.assertIn("baseline-only is intended for first collector deployment", stderr.getvalue())
            self.assertEqual(self.counts(db_path), old_counts)

    def test_suppress_initial_notify_writes_events_without_notifying(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            config_path.write_text(
                """
sources:
  baseline_source:
    type: baseline_fake
    enabled: true
    url: "https://example.com/source"
    keywords: []
""".strip(),
                encoding="utf-8",
            )
            original_adapters = dict(runner.ADAPTERS)
            original_notify_all = runner.notify_all
            notify_calls = []
            stdout = io.StringIO()
            try:
                runner.ADAPTERS.update({"baseline_fake": BaselineFakeAdapter})
                runner.notify_all = lambda _notifiers, events: notify_calls.append(events)
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "check",
                            "--config",
                            str(config_path),
                            "--db",
                            str(db_path),
                            "--all",
                            "--suppress-initial-notify",
                        ]
                    )
                connection = connect(db_path)
                try:
                    stored_events = list_events(connection)
                finally:
                    connection.close()
            finally:
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original_adapters)
                runner.notify_all = original_notify_all

            self.assertEqual(exit_code, 0)
            self.assertIn("events=1", stdout.getvalue())
            self.assertEqual(len(stored_events), 1)
            self.assertEqual(notify_calls, [])

    def write_config(self, root: Path) -> Path:
        config_path = root / "sources.yaml"
        config_path.write_text(
            """
sources:
  baseline_source:
    type: baseline_fake
    enabled: true
    url: "https://example.com/source"
    keywords: []
""".strip(),
            encoding="utf-8",
        )
        return config_path

    def item_hash(self, db_path: Path) -> str:
        connection = connect(db_path)
        try:
            return str(list_items(connection)[0]["content_hash"])
        finally:
            connection.close()

    def counts(self, db_path: Path) -> dict[str, int]:
        connection = connect(db_path)
        try:
            return storage_counts(connection)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
