from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from klaxon_facebook_test import (
    classify_post,
    initialize_state_database,
    load_filter_config,
    record_scheduled_outage,
)
from morning_brief import (
    build_health_section,
    build_morning_brief,
    build_power_section,
    format_weather_line,
    parse_weather_html,
    send_morning_brief,
)


CONFIG = load_filter_config(Path(__file__).with_name("location_config.json"))


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
    def scheduled_post(
        self,
        post_id: str,
        caption: str,
        *,
        published: str = "2026-08-13T09:00:00+08:00",
    ) -> dict[str, object]:
        post = {
            "id": post_id,
            "url": f"https://facebook.test/{post_id}",
            "published_philippines": published,
            "caption": caption,
        }
        post["classification"] = classify_post(post, CONFIG)
        return post

    def save_posts(self, posts: list[dict[str, object]]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "klaxon_state.sqlite3"
        initialize_state_database(path)
        for post in posts:
            classification = post["classification"]
            assert isinstance(classification, dict)
            record_scheduled_outage(path, post, classification)
        return path

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

    def test_health_section_reports_all_expected_checks(self) -> None:
        self.assertEqual(
            build_health_section(
                {
                    "audit_succeeded": True,
                    "run_count": 96,
                    "expected_runs": 96,
                    "minimum_fraction": 0.90,
                    "status": "healthy",
                }
            ),
            "All expected Facebook checks completed (96 of 96).",
        )

    def test_health_section_reports_degraded_audit(self) -> None:
        self.assertEqual(
            build_health_section(
                {
                    "audit_succeeded": True,
                    "run_count": 80,
                    "expected_runs": 96,
                    "minimum_fraction": 0.90,
                    "status": "degraded",
                }
            ),
            "Warning: Facebook watcher recorded only 80 of 96 expected checks; "
            "missed checks may have occurred.",
        )

    def test_health_section_does_not_claim_health_when_audit_unavailable(self) -> None:
        self.assertEqual(
            build_health_section({}),
            "System health unavailable; the latest watcher audit did not complete successfully.",
        )

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

    def test_early_announcement_is_retained_for_outage_today(self) -> None:
        post = self.scheduled_post(
            "early",
            "Scheduled interruption in Dauis on August 16, 2026 from 8:00 AM to 10:00 AM.",
        )
        state = self.save_posts([post])
        section = build_power_section(
            {}, state_path=state, today=datetime(2026, 8, 16).date()
        )
        self.assertIn("2026-08-16 from 8:00 AM to 10:00 AM", section)

    def test_future_and_past_outages_are_excluded(self) -> None:
        posts = [
            self.scheduled_post("future", "Scheduled interruption in Dauis on August 18, 2026 from 8:00 AM to 10:00 AM."),
            self.scheduled_post("past", "Scheduled interruption in Dauis on August 10, 2026 from 8:00 AM to 10:00 AM."),
        ]
        state = self.save_posts(posts)
        self.assertEqual(
            build_power_section({}, state_path=state, today=datetime(2026, 8, 16).date()),
            "No scheduled outage affecting your area.",
        )

    def test_overlapping_same_day_outages_are_both_listed(self) -> None:
        posts = [
            self.scheduled_post("one", "Scheduled interruption in Dauis on August 16, 2026 from 8:00 AM to 10:00 AM."),
            self.scheduled_post("two", "Scheduled interruption in Dauis on August 16, 2026 from 9:00 AM to 11:00 AM."),
        ]
        state = self.save_posts(posts)
        section = build_power_section({}, state_path=state, today=datetime(2026, 8, 16).date())
        self.assertEqual(section.count("Scheduled outage"), 2)
        self.assertIn("8:00 AM to 10:00 AM", section)
        self.assertIn("9:00 AM to 11:00 AM", section)

    def test_area_filter_excludes_other_locations(self) -> None:
        post = self.scheduled_post(
            "other-area",
            "Scheduled interruption in Tagbilaran on August 16, 2026 from 8:00 AM to 10:00 AM.",
        )
        state = self.save_posts([post])
        self.assertEqual(
            build_power_section({}, state_path=state, today=datetime(2026, 8, 16).date()),
            "No scheduled outage affecting your area.",
        )

    def test_reposted_same_outage_is_deduplicated(self) -> None:
        posts = [
            self.scheduled_post("original", "Scheduled interruption in Dauis on August 16, 2026 from 8:00 AM to 10:00 AM."),
            self.scheduled_post("repost", "UPDATED NOTICE: planned power interruption for Dauis on August 16, 2026 from 8:00 AM to 10:00 AM."),
        ]
        state = self.save_posts(posts)
        section = build_power_section({}, state_path=state, today=datetime(2026, 8, 16).date())
        self.assertEqual(section.count("Scheduled outage"), 1)

    def test_missing_date_is_explicitly_uncertain(self) -> None:
        post = self.scheduled_post(
            "uncertain",
            "Scheduled interruption in Dauis from 8:00 AM to 10:00 AM.",
        )
        state = self.save_posts([post])
        section = build_power_section({}, state_path=state, today=datetime(2026, 8, 13).date())
        self.assertIn("date/time uncertain", section)

    def test_brief_order_header_and_cyclone_status(self) -> None:
        title, message = build_morning_brief(
            datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc),
            power_result(),
            {"active_cyclone": False, "par_status": "none_active"},
            parse_weather_html(WEATHER_FIXTURE),
        )
        self.assertEqual(title, "Morning brief")
        self.assertTrue(message.startswith("<b>Thursday, August 13, 2026</b>\n\n"))
        self.assertLess(message.index("<b>Power Today</b>"), message.index("<b>Cyclone Status</b>"))
        self.assertLess(message.index("<b>Cyclone Status</b>"), message.index("<b>Weather</b>"))
        self.assertLess(message.index("<b>Weather</b>"), message.index("<b>System health</b>"))
        self.assertIn("No active tropical cyclone in PAR.", message)
        self.assertIn("Rain showers and thunderstorms; 73–82°F (23–28°C).", message)

    def test_brief_includes_completed_health_result_after_weather(self) -> None:
        _, message = build_morning_brief(
            datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc),
            {"latest_posts": []},
            {"active_cyclone": False},
            {"summary": "Partly cloudy; 75–91°F (24–33°C)."},
            health_result={
                "audit_succeeded": True,
                "run_count": 96,
                "expected_runs": 96,
                "minimum_fraction": 0.90,
            },
        )
        self.assertIn(
            "<b>System health</b>\nAll expected Facebook checks completed (96 of 96).",
            message,
        )

    def test_brief_bolds_only_requested_date_and_section_headings(self) -> None:
        _, message = build_morning_brief(
            datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc),
            {"latest_posts": []},
            {"active_cyclone": False},
            {"summary": "Partly cloudy; 75–91°F (24–33°C)."},
        )
        self.assertEqual(
            message.split("\n\n"),
            [
                "<b>Thursday, August 13, 2026</b>",
                "<b>Power Today</b>\nNo scheduled outage affecting your area.",
                "<b>Cyclone Status</b>\nNo active tropical cyclone in PAR.",
                "<b>Weather</b>\nPartly cloudy; 75–91°F (24–33°C).",
                "<b>System health</b>\nSystem health unavailable; the latest watcher audit did not complete successfully.",
            ],
        )

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
        self.assertEqual(
            calls,
            [{"title": "Morning brief", "message": "brief body", "priority": 0, "html": True}],
        )


if __name__ == "__main__":
    unittest.main()
