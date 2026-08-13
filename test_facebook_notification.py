from __future__ import annotations

import unittest

from klaxon_facebook_test import build_pushover_message


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
    def test_scheduled_alert_has_requested_plain_text_order(self) -> None:
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
            "Type: Scheduled\n"
            "Status: Power interruption reported\n"
            "Where: Dauis\n"
            "When: From 1:00 AM to 3:00 AM (post published 2026-08-13 09:00 PHT)\n\n"
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
        self.assertTrue(message.startswith("Type: Emergency\n"))
        self.assertIn("Status: Power interruption reported\n", message)
        self.assertIn("Where: Mayacabac\n", message)
        self.assertIn("When: Post published 2026-08-13 10:00 PHT\n\n", message)

    def test_missing_fields_are_explicit_without_crashing(self) -> None:
        title, message = build_pushover_message({}, {})
        self.assertEqual(title, "Electricity update")
        self.assertEqual(
            message,
            "Type: Not specified\n"
            "Status: Not specified\n"
            "Where: Not specified\n"
            "When: Not specified\n\n"
            "Not specified",
        )

    def test_original_facebook_text_is_preserved_after_blank_line(self) -> None:
        source_text = "BOHECO I NOTICE: Dauis interruption from 8:00 AM to 9:30 AM."
        _, message = build_pushover_message(
            {"caption": source_text}, classification()
        )
        self.assertIn("When: From 8:00 AM to 9:30 AM\n\n", message)
        self.assertTrue(message.endswith(source_text))
        self.assertEqual(message.count(source_text), 1)


if __name__ == "__main__":
    unittest.main()
