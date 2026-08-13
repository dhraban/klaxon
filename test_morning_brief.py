from __future__ import annotations

from datetime import datetime, timezone
import unittest

from morning_brief import (
    build_morning_brief,
    build_power_section,
    format_weather_line,
    parse_weather_html,
    send_morning_brief,
)


WEATHER_FIXTURE = """
<html><body>
<div class="validity"><b>Issued at: 6:00 PM today, 13 August 2026</b></div>
<table class="table mobile">
  <thead>
    <tr><th colspan="5"><h4>Bohol</h4><h5>(The Chocolate Hills)</h5></th></tr>
    <tr><th>Thursday </br> August 13</th><th>Friday </br> August 14</th></tr>
  </thead>
  <tbody><tr>
    <td><div class="weather-values">
      <img title="Rain showers and thunderstorms" />
      <div class="ol-temperature"><span class="min">23°C</span><span class="max">28°C</span></div>
    </div></td>
    <td><img title="Sunny" /><span class="min">24°C</span><span class="max">30°C</span></td>
  </tr></tbody>
</table>
</body></html>
"""


def power_result() -> dict[str, object]:
    return {
        "latest_posts": [
            {
                "caption": "BOHECO scheduled outage from 1:00 AM to 3:00 AM.",
                "published_philippines": "2026-08-13T09:00:00+08:00",
                "classification": {
                    "qualifies_for_alert": True,
                    "outage_type": "scheduled",
                    "matched_location_terms": ["Dauis"],
                },
            }
        ],
        "posts": [],
    }


class MorningBriefTests(unittest.TestCase):
    def test_weather_parser_formats_official_bohol_values_compactly(self) -> None:
        weather = parse_weather_html(WEATHER_FIXTURE)
        self.assertTrue(weather["available"])
        self.assertEqual(weather["condition"], "Rain showers and thunderstorms")
        self.assertEqual(weather["summary"], "Rain showers and thunderstorms; 73–82°F (23–28°C).")
        self.assertEqual(weather["issued_at"], "6:00 PM today, 13 August 2026")

    def test_weather_parser_reports_missing_forecast_without_fabricating(self) -> None:
        weather = parse_weather_html("<html><body><h4>Cebu</h4></body></html>")
        self.assertFalse(weather["available"])
        self.assertEqual(weather["summary"], "Forecast unavailable from PAGASA.")

    def test_weather_line_retains_celsius_and_adds_fahrenheit(self) -> None:
        self.assertEqual(
            format_weather_line(
                {"condition": "Partly cloudy", "min_c": 24, "max_c": 33}
            ),
            "Partly cloudy; 75–91°F (24–33°C).",
        )

    def test_power_section_uses_latest_posts_when_current_sweep_has_no_new_posts(self) -> None:
        self.assertEqual(
            build_power_section(power_result()),
            "Scheduled outage — Dauis — From 1:00 AM to 3:00 AM.",
        )

    def test_brief_order_header_and_cyclone_status(self) -> None:
        title, message = build_morning_brief(
            datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc),
            power_result(),
            {"active_cyclone": False, "par_status": "none_active"},
            parse_weather_html(WEATHER_FIXTURE),
        )
        self.assertEqual(title, "Morning brief")
        self.assertTrue(message.startswith("Thursday, August 13, 2026\n\n"))
        self.assertLess(message.index("Power today"), message.index("Cyclone status"))
        self.assertLess(message.index("Cyclone status"), message.index("Weather"))
        self.assertIn("No active tropical cyclone in PAR.", message)
        self.assertIn("Rain showers and thunderstorms; 73–82°F (23–28°C).", message)

    def test_quiet_power_section_uses_requested_fallback(self) -> None:
        self.assertEqual(
            build_power_section({"latest_posts": []}),
            "No scheduled outage affecting your area.",
        )

    def test_delivery_is_always_normal_priority(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_send(**kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {"sent": True, "request_id": "brief-test"}

        delivery = send_morning_brief("brief body", send_function=fake_send)
        self.assertEqual(delivery["priority"], 0)
        self.assertEqual(calls, [{"title": "Morning brief", "message": "brief body", "priority": 0}])


if __name__ == "__main__":
    unittest.main()
