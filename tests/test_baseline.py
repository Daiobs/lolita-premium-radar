import tempfile
import unittest
from contextlib import redirect_stdout
import io
from pathlib import Path

import lolita_radar.runner as runner
from lolita_radar.cli import main
from lolita_radar.models import ItemStatus, RadarItem
from lolita_radar.storage import connect, list_events, storage_counts


class BaselineFakeAdapter:
    def __init__(self, config) -> None:
        self.config = config

    def fetch_items(self) -> list[RadarItem]:
        return [
            RadarItem(
                source=self.config.name,
                title="New Arrival: Baseline JSK",
                url=f"{self.config.url}/baseline",
                status=ItemStatus.NEW_ARRIVAL,
                content="baseline content",
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


if __name__ == "__main__":
    unittest.main()
