import unittest
from pathlib import Path


class ReadmeTests(unittest.TestCase):
    def test_24h_loop_example_writes_auditable_log_and_exit_files(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("# Lolita Feed OS", readme)
        self.assertNotIn("# Lolita Radar OS", readme)
        self.assertIn("run-loop \\", readme)
        self.assertIn("--log-file .data/soak/lolita-radar-os-24h.log", readme)
        self.assertIn("--exit-file .data/soak/lolita-radar-os-24h.exit", readme)
        self.assertIn("--expected-cycles 288", readme)
        self.assertIn("verify-loop \\", readme)
        self.assertIn("cycle | checked_at | ok | event_count | error_message", readme)
        self.assertIn("source runs must fall inside that same window", readme)
        self.assertIn("Duplicate cycle", readme)
        self.assertIn("partially missing cycle `checked_at` values", readme)
        self.assertIn("values outside the evidence window are rejected", readme)
        self.assertIn("duplicate_cycles", readme)
        self.assertIn("missing_cycle_timestamps", readme)
        self.assertIn("cycle_time_mismatches", readme)
        self.assertIn("pull_request", readme)
        self.assertIn("Feed OS audit JSON", readme)
        self.assertIn("Alert Feed: system-level market and source-health warnings", readme)
        self.assertNotIn("Alert Feed: new releases", readme)
        self.assertIn("GET /api/feed", readme)
        self.assertIn("GET /api/state", readme)
        self.assertIn("same public Feed OS payload", readme)
        self.assertIn("Internal state blocks", readme)
        self.assertIn("opportunity_radar", readme)
        self.assertIn("not exposed by", readme)
        self.assertIn("page_level", readme)
        self.assertIn("without becoming Drop Feed item cards", readme)
        self.assertIn("DROP candidates require concrete item context", readme)


if __name__ == "__main__":
    unittest.main()
