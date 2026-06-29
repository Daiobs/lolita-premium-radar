import tempfile
import unittest
from pathlib import Path

import lolita_radar.runner as runner
from lolita_radar.models import ItemStatus, RadarItem
from lolita_radar.storage import connect, list_source_runs


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
