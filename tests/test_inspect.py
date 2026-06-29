import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import lolita_radar.runner as runner
from lolita_radar.cli import main
from lolita_radar.models import ItemStatus, RadarItem


class InspectFakeAdapter:
    def __init__(self, config) -> None:
        self.config = config

    def fetch_items(self) -> list[RadarItem]:
        return [
            RadarItem(
                source=self.config.name,
                title="New Arrival: Inspect JSK",
                url=f"{self.config.url}/inspect",
                status=ItemStatus.NEW_ARRIVAL,
                published_at="2026-06-30",
                content="inspect fixture",
            )
        ]


class InspectTests(unittest.TestCase):
    def test_inspect_outputs_items_without_database_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            config_path.write_text(
                """
sources:
  inspect_source:
    type: inspect_fake
    enabled: true
    url: "https://example.com/source"
    keywords: []
""".strip(),
                encoding="utf-8",
            )
            original = dict(runner.ADAPTERS)
            cwd = Path.cwd()
            stdout = io.StringIO()
            try:
                runner.ADAPTERS.update({"inspect_fake": InspectFakeAdapter})
                os.chdir(root)
                with redirect_stdout(stdout):
                    exit_code = main(["inspect", "--config", str(config_path), "--all", "--limit", "1"])
            finally:
                os.chdir(cwd)
                runner.ADAPTERS.clear()
                runner.ADAPTERS.update(original)

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("source: inspect_source", output)
            self.assertIn("parsed_item_count: 1", output)
            self.assertIn("new_arrival | New Arrival: Inspect JSK", output)
            self.assertFalse((root / ".data" / "lolita_radar.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
