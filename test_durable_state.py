from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from durable_state import export_state, import_state
from klaxon_facebook_test import (
    initialize_state_database,
    load_processed_post_ids,
    read_watcher_run_counter,
    record_processed_post_id,
    record_scheduled_outage,
)


class DurableStateTests(unittest.TestCase):
    def test_round_trip_preserves_alert_and_schedule_state_not_health_counter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            original = directory_path / "original.sqlite3"
            restored = directory_path / "restored.sqlite3"
            durable_json = directory_path / "facebook_state.json"
            initialize_state_database(original)
            record_processed_post_id(original, "known-post", classification="scheduled")
            post = {
                "id": "scheduled-post",
                "caption": "Scheduled interruption in Dauis on August 16, 2026 from 8:00 AM to 10:00 AM.",
                "url": "https://facebook.test/scheduled-post",
                "published_philippines": "2026-08-13T09:00:00+08:00",
            }
            classification = {
                "outage_type": "scheduled",
                "matched_location_terms": ["Dauis"],
                "scheduled_outage": {
                    "announced_date": "2026-08-13",
                    "date_start": "2026-08-16",
                    "date_end": "2026-08-16",
                    "start_time": "8:00 AM",
                    "end_time": "10:00 AM",
                    "date_status": "known",
                },
            }
            record_scheduled_outage(original, post, classification)
            self.assertTrue(export_state(original, durable_json))
            self.assertFalse(export_state(original, durable_json))

            initialize_state_database(restored)
            self.assertTrue(import_state(durable_json, restored))
            self.assertEqual(load_processed_post_ids(restored), {"known-post"})
            self.assertEqual(read_watcher_run_counter(restored)["run_count"], 0)

    def test_missing_state_branch_file_leaves_local_state_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.sqlite3"
            record_processed_post_id(state_file, "local-post")
            self.assertFalse(import_state(Path(directory) / "missing.json", state_file))
            self.assertEqual(load_processed_post_ids(state_file), {"local-post"})


if __name__ == "__main__":
    unittest.main()
