import unittest
from pathlib import Path


class WorkflowTests(unittest.TestCase):
    def test_check_workflow_runs_tests_and_feed_os_audit(self) -> None:
        workflow = Path(".github/workflows/check.yml").read_text(encoding="utf-8")

        self.assertIn("pull_request:", workflow)
        self.assertIn("python -m unittest discover -s tests", workflow)
        self.assertIn("python -m lolita_radar.cli audit-feed-os --json > feed-os-audit.json", workflow)
        self.assertIn("counts.get(\"fail\", 0)", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("feed-os-audit.json", workflow)


if __name__ == "__main__":
    unittest.main()
