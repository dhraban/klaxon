import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from pagasa_cyclone_check import (
    BOHOL_PROXIMITY_THRESHOLD_KM,
    BOHOL_REFERENCE_LATITUDE,
    BOHOL_REFERENCE_LONGITUDE,
    build_pushover_message,
    build_result,
    calculate_forecast_proximity,
    notification_priority,
    next_due_at,
    parse_html,
    send_test_pushover,
    send_pushover_for_result,
    state_from_result,
)


NO_ACTIVE = """
<html><body>
<h1>Tropical Cyclone Bulletin</h1>
<p>No Active Tropical Cyclone within the Philippine Area of Responsibility</p>
</body></html>
"""

ACTIVE_IN_PAR = """
<html><body>
<h1>Tropical Cyclone Bulletin</h1>
<p>Tropical Storm TESTING is within the Philippine Area of Responsibility.</p>
<p>Issued at 5:00 AM, 13 August 2026. Valid for broadcast until the next bulletin.</p>
<p>12-Hour Forecast 5:00 PM 13 August 2026 12.5 124.5 East of Bohol</p>
<p>24-Hour Forecast 5:00 AM 14 August 2026 13.0 124.0 Northeast of Bohol</p>
<p>TROPICAL CYCLONE WIND SIGNALS IN EFFECT: None</p>
</body></html>
"""

BOHOL_SIGNAL = """
<html><body>
<h1>Tropical Cyclone Bulletin</h1>
<p>Typhoon TESTING is inside PAR.</p>
<p>Issued at 6:00 AM, 13 August 2026. Valid for broadcast until the next bulletin.</p>
<p>12-Hour Forecast 6:00 PM 13 August 2026 10.5 124.0 Near Bohol</p>
<p>TROPICAL CYCLONE WIND SIGNALS IN EFFECT</p>
<p>TCWS No. 2: Bohol, Cebu, and nearby areas</p>
<p>OTHER HAZARDS AFFECTING LAND AREAS</p>
</body></html>
"""


class PagasaCheckerTests(unittest.TestCase):
    def test_no_active_par_uses_daily_cadence(self) -> None:
        text, _ = parse_html(NO_ACTIVE)
        result = build_result(text, checked_at="2026-08-13T00:00:00Z")
        self.assertFalse(result["active_cyclone"])
        self.assertEqual(result["par_status"], "none_active")
        self.assertEqual(result["cadence"], "daily")

    def test_daily_detector_does_not_send_routine_pushover(self) -> None:
        from pagasa_cyclone_check import main

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            html_path = directory_path / "bulletin.html"
            state_path = directory_path / "state.json"
            output_path = directory_path / "output.json"
            html_path.write_text(NO_ACTIVE, encoding="utf-8")
            old_argv = sys.argv
            try:
                sys.argv = [
                    "pagasa_cyclone_check.py",
                    "--mode",
                    "daily-detector",
                    "--html-file",
                    str(html_path),
                    "--state-file",
                    str(state_path),
                    "--output",
                    str(output_path),
                    "--send-pushover",
                ]
                with patch("pagasa_cyclone_check.send_pushover_for_result") as send_mock:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(), 0)
                    send_mock.assert_not_called()
            finally:
                sys.argv = old_argv
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertTrue(result["checked"])
            self.assertFalse(result["pushover"]["sent"])

    def test_quiet_monitor_does_not_send_standalone_pushover(self) -> None:
        from pagasa_cyclone_check import main

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            html_path = directory_path / "bulletin.html"
            state_path = directory_path / "state.json"
            output_path = directory_path / "output.json"
            html_path.write_text(NO_ACTIVE, encoding="utf-8")
            state_path.write_text(
                json.dumps(
                    {
                        "monitoring_enabled": True,
                        "active_cyclone": True,
                        "storm_name": "TESTING",
                        "par_status": "inside",
                        "cadence": "every_3_hours",
                        "next_monitor_check_at_utc": "2000-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            old_argv = sys.argv
            try:
                sys.argv = [
                    "pagasa_cyclone_check.py",
                    "--mode",
                    "monitor",
                    "--html-file",
                    str(html_path),
                    "--state-file",
                    str(state_path),
                    "--output",
                    str(output_path),
                    "--send-pushover",
                ]
                with patch("pagasa_cyclone_check.send_pushover_for_result") as send_mock:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(), 0)
                    send_mock.assert_not_called()
            finally:
                sys.argv = old_argv
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertTrue(result["checked"])
            self.assertFalse(result["pushover"]["sent"])
            self.assertIn("outside an active cyclone in PAR", result["pushover"]["reason"])

    def test_active_cyclone_in_par_uses_three_hour_cadence(self) -> None:
        text, _ = parse_html(ACTIVE_IN_PAR)
        result = build_result(text, checked_at="2026-08-13T00:00:00Z")
        self.assertTrue(result["active_cyclone"])
        self.assertEqual(result["storm_name"], "TESTING")
        self.assertEqual(result["par_status"], "inside")
        self.assertEqual(result["cadence"], "every_3_hours")
        self.assertEqual(len(result["forecast_positions"]), 2)
        self.assertEqual(result["forecast_positions"][0]["latitude"], 12.5)
        self.assertFalse(result["bohol_under_wind_signal"])

    def test_bohol_wind_signal_uses_hourly_cadence(self) -> None:
        text, _ = parse_html(BOHOL_SIGNAL)
        result = build_result(text, checked_at="2026-08-13T00:00:00Z")
        self.assertTrue(result["bohol_under_wind_signal"])
        self.assertEqual(result["bohol_wind_signal"], 2)
        self.assertEqual(result["cadence"], "hourly")

    def test_adaptive_state_skipping_is_safe(self) -> None:
        due = next_due_at(datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc), "daily")
        self.assertEqual(due, "2026-08-14T00:00:00Z")

    def test_detector_enables_separate_monitor_for_par_cyclone(self) -> None:
        text, _ = parse_html(ACTIVE_IN_PAR)
        result = build_result(text, checked_at="2026-08-13T00:00:00Z")
        state = state_from_result(result, datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc))
        self.assertTrue(state["monitoring_enabled"])
        self.assertEqual(state["cadence"], "every_3_hours")
        self.assertEqual(state["next_monitor_check_at_utc"], "2026-08-13T03:00:00Z")

    def test_detector_disables_monitor_when_par_is_clear(self) -> None:
        text, _ = parse_html(NO_ACTIVE)
        result = build_result(text, checked_at="2026-08-13T00:00:00Z")
        state = state_from_result(result, datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc))
        self.assertFalse(state["monitoring_enabled"])
        self.assertIsNone(state["next_monitor_check_at_utc"])

    def test_disabled_monitor_is_a_no_fetch_noop(self) -> None:
        from pagasa_cyclone_check import main

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            state_path = directory_path / "state.json"
            output_path = directory_path / "output.json"
            state_path.write_text(
                json.dumps(
                    {
                        "monitoring_enabled": False,
                        "active_cyclone": False,
                        "par_status": "none_active",
                    }
                ),
                encoding="utf-8",
            )
            old_argv = sys.argv
            try:
                sys.argv = [
                    "pagasa_cyclone_check.py",
                    "--mode",
                    "monitor",
                    "--state-file",
                    str(state_path),
                    "--output",
                    str(output_path),
                ]
                with patch("pagasa_cyclone_check.send_pushover_for_result") as send_mock:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(), 0)
                    send_mock.assert_not_called()
            finally:
                sys.argv = old_argv
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(result["checked"])
            self.assertIn("no PAGASA fetch", result["summary"])

    def test_daily_and_three_hour_checks_use_priority_zero(self) -> None:
        text, _ = parse_html(ACTIVE_IN_PAR)
        result = build_result(text, checked_at="2026-08-13T00:00:00Z")
        self.assertEqual(notification_priority("daily-detector", {}, result), 0)
        prior_state = {
            "cadence": "every_3_hours",
            "bohol_under_wind_signal": False,
        }
        self.assertEqual(notification_priority("monitor", prior_state, result), 0)

    def test_due_hourly_bohol_check_uses_priority_one(self) -> None:
        text, _ = parse_html(BOHOL_SIGNAL)
        result = build_result(text, checked_at="2026-08-13T00:00:00Z")
        prior_state = {
            "cadence": "hourly",
            "bohol_under_wind_signal": True,
        }
        calls = []

        def fake_send(**kwargs):
            calls.append(kwargs)
            return {"sent": True, "request_id": "test"}

        delivery = send_pushover_for_result(
            result,
            mode="monitor",
            prior_state=prior_state,
            send_function=fake_send,
        )
        self.assertEqual(delivery["priority"], 1)
        self.assertEqual(calls[0]["priority"], 1)

    def test_newly_detected_bohol_wind_signal_uses_priority_one(self) -> None:
        text, _ = parse_html(BOHOL_SIGNAL)
        result = build_result(text, checked_at="2026-08-13T00:00:00Z")
        prior_state = {
            "cadence": "every_3_hours",
            "bohol_under_wind_signal": False,
        }
        self.assertEqual(notification_priority("monitor", prior_state, result), 1)

    def test_sample_pushover_is_labeled_and_priority_zero(self) -> None:
        calls = []

        def fake_send(**kwargs):
            calls.append(kwargs)
            return {"sent": True, "request_id": "test"}

        delivery = send_test_pushover(fake_send)
        self.assertTrue(delivery["test_sample"])
        self.assertEqual(delivery["priority"], 0)
        self.assertEqual(calls[0]["priority"], 0)
        self.assertTrue(calls[0]["title"].startswith("TEST ONLY"))
        self.assertIn("not a real current threat", calls[0]["message"])

    def test_standalone_sender_refuses_quiet_or_out_of_par_results(self) -> None:
        calls = []

        def fake_send(**kwargs):
            calls.append(kwargs)
            return {"sent": True}

        for result in (
            {"active_cyclone": False, "par_status": "none_active"},
            {"active_cyclone": True, "par_status": "outside"},
        ):
            delivery = send_pushover_for_result(
                result,
                mode="monitor",
                prior_state={},
                send_function=fake_send,
            )
            self.assertFalse(delivery["sent"])
        self.assertEqual(calls, [])

    def test_forecast_proximity_finds_earliest_point_within_threshold(self) -> None:
        proximity = calculate_forecast_proximity(
            [
                {
                    "valid_at": "12:00 PM 13 August 2026",
                    "latitude": 10.5,
                    "longitude": 124.5,
                },
                {
                    "valid_at": "6:00 AM 14 August 2026",
                    "latitude": 10.0,
                    "longitude": 124.0,
                },
                {
                    "valid_at": "6:00 PM 14 August 2026",
                    "latitude": 9.7,
                    "longitude": 124.0,
                },
            ],
            "2026-08-13T00:00:00Z",
        )
        self.assertTrue(proximity["within_threshold"])
        self.assertEqual(proximity["forecast_valid_at"], "2026-08-13T12:00:00+08:00")
        self.assertEqual(proximity["relative_time"], "in about 4 hours")
        self.assertLessEqual(proximity["distance_km"], BOHOL_PROXIMITY_THRESHOLD_KM)

    def test_forecast_proximity_handles_no_match_and_bad_coordinates(self) -> None:
        proximity = calculate_forecast_proximity(
            [
                {"valid_at": "12:00 PM 13 August 2026", "latitude": 95, "longitude": 124},
                {"valid_at": "not a timestamp", "latitude": 10, "longitude": 124},
                {"valid_at": "12:00 PM 13 August 2026", "latitude": 20, "longitude": 150},
            ],
            "2026-08-13T00:00:00Z",
        )
        self.assertFalse(proximity["within_threshold"])
        self.assertEqual(proximity["threshold_km"], BOHOL_PROXIMITY_THRESHOLD_KM)

    def test_forecast_proximity_exact_reference_point_is_inside(self) -> None:
        proximity = calculate_forecast_proximity(
            [
                {
                    "valid_at": "12:00 PM 13 August 2026",
                    "latitude": BOHOL_REFERENCE_LATITUDE,
                    "longitude": BOHOL_REFERENCE_LONGITUDE,
                }
            ],
            "2026-08-13T00:00:00Z",
            threshold_km=0,
        )
        self.assertTrue(proximity["within_threshold"])
        self.assertEqual(proximity["distance_km"], 0)

    def test_notification_wording_uses_forecast_center_and_relative_time(self) -> None:
        result = {
            "storm_name": "TESTING",
            "summary": "TEST ONLY: sample active cyclone inside PAR.",
            "par_status": "inside",
            "issued_at": "6:00 AM, 13 August 2026",
            "bohol_wind_signal": None,
            "forecast_positions": [],
            "forecast_proximity_to_bohol": {
                "reference": "Dauis, Bohol",
                "threshold_km": 250.0,
                "within_threshold": True,
                "relative_time": "in about 36 hours",
                "distance_km": 184,
            },
        }
        _, message = build_pushover_message(result)
        self.assertIn("Earliest forecast center within 250 km", message)
        self.assertIn("in about 36 hours", message)
        self.assertIn("about 184 km away", message)
        self.assertNotIn("landfall", message.lower())

    def test_notification_wording_states_no_close_forecast(self) -> None:
        result = {
            "storm_name": "TESTING",
            "summary": "Sample active cyclone inside PAR.",
            "par_status": "inside",
            "issued_at": "6:00 AM, 13 August 2026",
            "bohol_wind_signal": None,
            "forecast_positions": [],
            "forecast_proximity_to_bohol": {
                "reference": "Dauis, Bohol",
                "threshold_km": 250.0,
                "within_threshold": False,
            },
        }
        _, message = build_pushover_message(result)
        self.assertIn(
            "PAGASA's published forecast positions do not currently bring the center within 250 km of Bohol.",
            message,
        )

    def test_noop_monitor_does_not_send_pushover(self) -> None:
        from pagasa_cyclone_check import main

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            state_path = directory_path / "state.json"
            output_path = directory_path / "output.json"
            state_path.write_text(
                json.dumps(
                    {
                        "monitoring_enabled": False,
                        "active_cyclone": False,
                        "par_status": "none_active",
                    }
                ),
                encoding="utf-8",
            )
            old_argv = sys.argv
            try:
                sys.argv = [
                    "pagasa_cyclone_check.py",
                    "--mode",
                    "monitor",
                    "--state-file",
                    str(state_path),
                    "--output",
                    str(output_path),
                    "--send-pushover",
                ]
                with patch("pagasa_cyclone_check.send_pushover_for_result") as send_mock:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(), 0)
                    send_mock.assert_not_called()
            finally:
                sys.argv = old_argv
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(result["checked"])
            self.assertNotIn("pushover", result)


if __name__ == "__main__":
    unittest.main()
