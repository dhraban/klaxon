from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from phivolcs_tsunami_check import (
    build_emergency_message,
    parse_bulletin,
    process_result,
)
from pushover_client import send_notification


WARNING_BOHOL = """
<html><body>
<h1>TSUNAMI INFORMATION NO. 1</h1>
<h2>TSUNAMI WARNING</h2>
<p>Date and Time : 08 Jun 2026 - 07:37:47 AM</p>
<p>Location : 05.99°N, 125.17°E - Offshore Sarangani</p>
<p>Magnitude : 7.0</p>
<p>The people in the coastal areas of the following provinces are STRONGLY ADVISED TO IMMEDIATELY EVACUATE to higher grounds.</p>
<ul><li>Sarangani</li><li>Davao Occidental</li><li>Bohol</li></ul>
<p>Owners of boats should secure their boats.</p>
<p>Issued on: 08 Jun 2026 - 07:47:59 AM</p>
</body></html>
"""

WARNING_NO_BOHOL = """
<html><body>
<h1>TSUNAMI INFORMATION NO. 1</h1><h2>TSUNAMI WARNING</h2>
<p>The following provinces are affected: Sarangani, Sulu, and Basilan.</p>
<p>Owners of boats should secure their boats.</p>
<p>Issued on: 08 Jun 2026 - 07:47:59 AM</p>
</body></html>
"""

NO_THREAT = """
<html><body><h1>TSUNAMI INFORMATION NO. 1</h1><h2>NO TSUNAMI THREAT</h2>
<p>There is no tsunami threat to the Philippines from this earthquake.</p>
</body></html>
"""

MINOR_ADVISORY = """
<html><body><h1>TSUNAMI INFORMATION NO. 1</h1>
<h2>MINOR SEA-LEVEL DISTURBANCE</h2>
<p>The following provinces should stay away from the coast: Bohol.</p>
</body></html>
"""


class PhivolcsTsunamiTests(unittest.TestCase):
    def test_warning_with_bohol_qualifies_as_priority_two(self) -> None:
        result = parse_bulletin(WARNING_BOHOL, "https://example.test/warning.html")
        self.assertEqual(result["bulletin_status"], "TSUNAMI WARNING")
        self.assertTrue(result["bohol_affected"])
        self.assertTrue(result["qualifies_for_emergency"])
        calls: list[dict[str, object]] = []

        def fake_send(**kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {"sent": True, "receipt": "receipt-123"}

        output, state = process_result(result, {}, send=True, send_function=fake_send)
        self.assertTrue(output["notification_sent"])
        self.assertEqual(output["notification_priority"], 2)
        self.assertEqual(output["retry_seconds"], 60)
        self.assertEqual(output["expire_seconds"], 3600)
        self.assertEqual(output["receipt"], "receipt-123")
        self.assertEqual(calls[0]["priority"], 2)
        self.assertEqual(calls[0]["retry"], 60)
        self.assertEqual(calls[0]["expire"], 3600)
        self.assertEqual(state["last_warning_receipt"], "receipt-123")

    def test_warning_without_bohol_is_silent(self) -> None:
        result = parse_bulletin(WARNING_NO_BOHOL, "https://example.test/warning.html")
        self.assertFalse(result["bohol_affected"])
        output, state = process_result(result, {}, send=True, send_function=lambda **_: self.fail("sent"))
        self.assertFalse(output["notification_sent"])
        self.assertNotIn("last_warning_receipt", state)

    def test_no_threat_is_silent(self) -> None:
        result = parse_bulletin(NO_THREAT, "https://example.test/no-threat.html")
        self.assertEqual(result["bulletin_status"], "NO TSUNAMI THREAT")
        self.assertFalse(result["qualifies_for_emergency"])
        output, _ = process_result(
            result, {}, send=True, send_function=lambda **_: self.fail("sent")
        )
        self.assertFalse(output["notification_sent"])

    def test_minor_advisory_is_silent_even_if_bohol_is_named(self) -> None:
        result = parse_bulletin(MINOR_ADVISORY, "https://example.test/minor.html")
        self.assertEqual(result["bulletin_status"], "MINOR SEA-LEVEL DISTURBANCE")
        self.assertFalse(result["qualifies_for_emergency"])
        output, _ = process_result(
            result, {}, send=True, send_function=lambda **_: self.fail("sent")
        )
        self.assertFalse(output["notification_sent"])

    def test_duplicate_warning_preserves_receipt_without_resending(self) -> None:
        result = parse_bulletin(WARNING_BOHOL, "https://example.test/warning.html")
        state = {
            "last_warning_fingerprint": result["fingerprint"],
            "last_warning_receipt": "receipt-existing",
        }
        output, updated_state = process_result(
            result, state, send=True, send_function=lambda **_: self.fail("resent")
        )
        self.assertTrue(output["notification_duplicate"])
        self.assertEqual(output["receipt"], "receipt-existing")
        self.assertEqual(updated_state, state)

    def test_emergency_message_contains_source_and_no_reliability_claim(self) -> None:
        result = parse_bulletin(WARNING_BOHOL, "https://example.test/warning.html")
        title, message = build_emergency_message(result)
        self.assertIn("TSUNAMI WARNING", title)
        self.assertIn("Follow current PHIVOLCS", message)
        self.assertIn("not a guarantee of advance warning", message)

    def test_pushover_priority_two_encodes_emergency_parameters(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"status":1,"receipt":"receipt-encoded"}'

        captured: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: int) -> FakeResponse:
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

        with patch(
            "pushover_client.read_credentials",
            return_value={"user_key": "u" * 30, "application_token": "a" * 30},
        ):
            with patch("pushover_client.urlopen", side_effect=fake_urlopen):
                delivery = send_notification(
                    title="Klaxon: TSUNAMI WARNING for Bohol",
                    message="TEST-UNSENT fixture payload",
                    priority=2,
                    retry=60,
                    expire=3600,
                )

        request = captured["request"]
        fields = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(fields["priority"], ["2"])
        self.assertEqual(fields["retry"], ["60"])
        self.assertEqual(fields["expire"], ["3600"])
        self.assertEqual(delivery["receipt"], "receipt-encoded")


if __name__ == "__main__":
    unittest.main()
