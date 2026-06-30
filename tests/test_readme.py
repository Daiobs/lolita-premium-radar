import unittest
from pathlib import Path


class ReadmeTests(unittest.TestCase):
    def test_24h_loop_example_writes_auditable_log_and_exit_files(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("run-loop \\", readme)
        self.assertIn("--log-file .data/soak/lolita-radar-os-24h.log", readme)
        self.assertIn("--exit-file .data/soak/lolita-radar-os-24h.exit", readme)
        self.assertIn("--expected-cycles 288", readme)
        self.assertIn("verify-loop \\", readme)
        self.assertIn("cycle | checked_at | ok | event_count | error_message", readme)
        self.assertIn("source runs must fall inside that same window", readme)
        self.assertIn("Duplicate cycle", readme)
        self.assertIn("cycle `checked_at` values outside the evidence window", readme)
        self.assertIn("duplicate_cycles", readme)
        self.assertIn("cycle_time_mismatches", readme)
        self.assertIn("pull_request", readme)
        self.assertIn("Feed OS audit JSON", readme)


if __name__ == "__main__":
    unittest.main()
