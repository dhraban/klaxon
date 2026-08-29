from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from klaxon_facebook_test import record_scheduled_watcher_run, read_watcher_run_counter
from watcher_health import main


class WatcherHealthTests(unittest.TestCase):
    def run_audit(self, run_count: int) -> tuple[int, dict[str, object], dict[str, object]]:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.sqlite3"
            output_path = Path(directory) / "watcher_health.json"
            for _ in range(run_count):
                record_scheduled_watcher_run(state_path)
            old_argv = sys.argv
            try:
                sys.argv = [
                    "watcher_health.py",
                    "--state-file",
                    str(state_path),
                    "--audit-and-reset",
                    "--expected-runs",
                    "4",
                    "--minimum-fraction",
                    "0.90",
                    "--output",
                    str(output_path),
                ]
                result_code = main()
            finally:
                sys.argv = old_argv
            result = json.loads(output_path.read_text(encoding="utf-8"))
            counter = read_watcher_run_counter(state_path)
            return result_code, result, counter

    def test_successful_audit_persists_healthy_result_and_resets_counter(self) -> None:
        result_code, result, counter = self.run_audit(4)
        self.assertEqual(result_code, 0)
        self.assertTrue(result["audit_succeeded"])
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["run_count"], 4)
        self.assertEqual(counter["run_count"], 0)

    def test_degraded_audit_persists_warning_result_without_sending(self) -> None:
        result_code, result, counter = self.run_audit(2)
        self.assertEqual(result_code, 0)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["run_count"], 2)
        self.assertEqual(counter["run_count"], 0)


if __name__ == "__main__":
    unittest.main()
