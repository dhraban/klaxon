from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs

from klaxon_facebook_test import build_pushover_message, deliver_post_if_new
from pushover_client import send_notification


def classification(
    *,
    outage_type: str = "scheduled",
    locations: list[str] | None = None,
    is_power_interruption: bool = True,
) -> dict[str, object]:
    return {
        "outage_type": outage_type,
        "matched_location_terms": locations if locations is not None else ["Dauis"],
        "is_power_interruption": is_power_interruption,
    }


class FacebookNotificationTests(unittest.TestCase):
    def test_scheduled_alert_has_requested_html_order(self) -> None:
        title, message = build_pushover_message(
            {
                "published_philippines": "2026-08-13 09:00 PHT",
                "caption": (
                    "BOHECO scheduled interruption. From 1:00 AM to 3:00 AM. "
                    "Please prepare accordingly."
                ),
            },
            classification(),
        )
        self.assertEqual(title, "Electricity update")
        self.assertEqual(
            message,
            "<b>Scheduled outage</b>\n"
            "<b>Status:</b> Power interruption reported\n"
            "<b>Where:</b> Dauis\n"
            "<b>When:</b> From 1:00 AM to 3:00 AM (post published 2026-08-13 09:00 PHT)\n\n"
            "BOHECO scheduled interruption. From 1:00 AM to 3:00 AM. Please prepare accordingly.",
        )

    def test_emergency_alert_uses_emergency_type(self) -> None:
        title, message = build_pushover_message(
            {
                "published_philippines": "2026-08-13 10:00 PHT",
                "caption": "Emergency power interruption affecting Mayacabac.",
            },
            classification(outage_type="emergency", locations=["Mayacabac"]),
        )
        self.assertEqual(title, "Electricity update")
        self.assertTrue(message.startswith("<b>Emergency outage</b>\n"))
        self.assertIn("<b>Status:</b> Power interruption reported\n", message)
        self.assertIn("<b>Where:</b> Mayacabac\n", message)
        self.assertIn("<b>When:</b> Post published 2026-08-13 10:00 PHT\n\n", message)

    def test_missing_fields_are_explicit_without_crashing(self) -> None:
        title, message = build_pushover_message({}, {})
        self.assertEqual(title, "Electricity update")
        self.assertEqual(
            message,
            "<b>Not specified outage</b>\n"
            "<b>Status:</b> Not specified\n"
            "<b>Where:</b> Not specified\n"
            "<b>When:</b> Not specified\n\n"
            "Not specified",
        )

    def test_original_facebook_text_is_preserved_after_blank_line(self) -> None:
        source_text = "BOHECO I NOTICE: Dauis interruption from 8:00 AM to 9:30 AM."
        _, message = build_pushover_message(
            {"caption": source_text}, classification()
        )
        self.assertIn("<b>When:</b> From 8:00 AM to 9:30 AM\n\n", message)
        self.assertTrue(message.endswith(source_text))
        self.assertEqual(message.count(source_text), 1)

    def test_delivery_enables_html_only_for_electricity_alert(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_send(**notification: object) -> dict[str, object]:
            calls.append(notification)
            return {"sent": True, "priority": notification["priority"]}

        post = {
            "id": "html-format-test",
            "caption": "Scheduled interruption affecting Dauis.",
        }
        classified = {
            "qualifies_for_alert": True,
            "outage_type": "scheduled",
            "pushover_priority": 0,
            "is_power_interruption": True,
            "matched_location_terms": ["Dauis"],
        }
        with tempfile.TemporaryDirectory() as directory:
            result = deliver_post_if_new(
                post,
                classified,
                Path(directory) / "state.sqlite3",
                send_function=fake_send,
            )
        self.assertTrue(result["sent"])
        self.assertEqual(calls[0]["html"], True)
        self.assertEqual(calls[0]["priority"], 0)
        self.assertIn("<b>Scheduled outage</b>", calls[0]["message"])

    def test_pushover_client_encodes_html_flag(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"status": 1, "request": "html-test"}'

        with patch(
            "pushover_client.read_credentials",
            return_value={"user_key": "u" * 30, "application_token": "t" * 30},
        ), patch("pushover_client.urlopen", return_value=FakeResponse()) as open_url:
            result = send_notification(
                title="Electricity update",
                message="<b>Scheduled outage</b>",
                priority=0,
                html=True,
            )
        payload = parse_qs(open_url.call_args.args[0].data.decode("utf-8"))
        self.assertTrue(result["sent"])
        self.assertEqual(payload["html"], ["1"])
        self.assertEqual(payload["priority"], ["0"])


if __name__ == "__main__":
    unittest.main()
