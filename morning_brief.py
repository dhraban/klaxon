#!/usr/bin/env python3
"""Build and optionally send Klaxon's plain-text morning Pushover brief."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from pushover_client import PushoverError, send_notification


BOHOL_TIMEZONE = "Asia/Manila"
WEATHER_SOURCE_URL = (
    "https://www.pagasa.dost.gov.ph/weather/"
    "weather-outlook-selected-tourist-areas"
)
USER_AGENT = "Klaxon/1.0 (+https://github.com/dhraban/klaxon)"


class MorningBriefError(RuntimeError):
    """Raised when a required brief operation cannot be completed."""


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MorningBriefError(f"Could not read {path}: {error}") from error
    if not isinstance(value, dict):
        raise MorningBriefError(f"Expected {path} to contain a JSON object.")
    return value


def visible_text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _temperature(cell: str, class_name: str) -> float | None:
    match = re.search(
        rf'<span\b[^>]*class=["\'][^"\']*\b{class_name}\b[^"\']*["\'][^>]*>'
        r"\s*(-?\d+(?:\.\d+)?)\s*°?C\s*</span>",
        cell,
        re.IGNORECASE,
    )
    return float(match.group(1)) if match else None


def _weather_condition(cell: str) -> str | None:
    match = re.search(
        r'<img\b[^>]*\btitle=["\']([^"\']+)["\']', cell, re.IGNORECASE
    )
    if not match:
        return None
    return visible_text(match.group(1))


def celsius_to_fahrenheit(value: float) -> int:
    return round(value * 9 / 5 + 32)


def format_celsius(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def format_weather_line(weather: dict[str, object]) -> str:
    condition = weather.get("condition")
    minimum = weather.get("min_c")
    maximum = weather.get("max_c")
    if not isinstance(condition, str) or not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)):
        return "Forecast unavailable from PAGASA."
    minimum_f = celsius_to_fahrenheit(float(minimum))
    maximum_f = celsius_to_fahrenheit(float(maximum))
    condition_text = condition.rstrip(" .")
    return (
        f"{condition_text}; {minimum_f}\u2013{maximum_f}\u00b0F "
        f"({format_celsius(float(minimum))}\u2013{format_celsius(float(maximum))}\u00b0C)."
    )


def unavailable_weather(reason: str) -> dict[str, object]:
    return {
        "available": False,
        "source": WEATHER_SOURCE_URL,
        "reason": reason,
        "summary": "Forecast unavailable from PAGASA.",
    }


def parse_weather_html(source: str) -> dict[str, object]:
    """Parse today's Bohol row from PAGASA's Selected Tourist Areas page."""
    issued_match = re.search(
        r"Issued\s+at\s*:\s*([^<]+)", source, re.IGNORECASE
    )
    issued_at = visible_text(issued_match.group(1)) if issued_match else None
    table_match = re.search(
        r"<table\b[^>]*>.*?<h4\b[^>]*>\s*Bohol\s*</h4>.*?</table>",
        source,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        return unavailable_weather("The Bohol forecast table was not found.")

    table = table_match.group(0)
    body_match = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", table, re.IGNORECASE | re.DOTALL)
    if not body_match:
        return unavailable_weather("The Bohol forecast row was not found.")
    row_match = re.search(r"<tr\b[^>]*>(.*?)</tr>", body_match.group(1), re.IGNORECASE | re.DOTALL)
    if not row_match:
        return unavailable_weather("The Bohol forecast row was not found.")
    cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row_match.group(1), re.IGNORECASE | re.DOTALL)
    if not cells:
        return unavailable_weather("The Bohol forecast values were not found.")

    first_cell = cells[0]
    condition = _weather_condition(first_cell)
    minimum = _temperature(first_cell, "min")
    maximum = _temperature(first_cell, "max")
    if condition is None or minimum is None or maximum is None:
        return unavailable_weather("The current Bohol weather values were incomplete.")

    return {
        "available": True,
        "source": WEATHER_SOURCE_URL,
        "issued_at": issued_at,
        "condition": condition,
        "min_c": minimum,
        "max_c": maximum,
        "summary": format_weather_line(
            {"condition": condition, "min_c": minimum, "max_c": maximum}
        ),
    }


def fetch_weather() -> dict[str, object]:
    request = Request(WEATHER_SOURCE_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            source = response.read().decode("utf-8", errors="replace")
    except Exception as error:  # pragma: no cover - network-specific
        return unavailable_weather(f"PAGASA request failed: {error}")
    return parse_weather_html(source)


def power_posts(power_result: dict[str, object]) -> list[dict[str, object]]:
    candidates = power_result.get("latest_posts", power_result.get("posts", []))
    if not isinstance(candidates, list):
        return []
    return [post for post in candidates if isinstance(post, dict)]


def load_retained_scheduled_outages(
    state_path: Path, today: object
) -> list[dict[str, object]]:
    """Read only schedule records that overlap today or remain uncertain."""
    if not state_path.exists() or not hasattr(today, "isoformat"):
        return []
    today_text = today.isoformat()
    try:
        with sqlite3.connect(state_path) as connection:
            rows = connection.execute(
                """
                SELECT schedule_key, outage_date_start, outage_date_end,
                       start_time, end_time, location_text, source_url,
                       source_text, date_status, announced_date
                FROM scheduled_outages
                WHERE (
                    date_status = 'known'
                    AND outage_date_start <= ?
                    AND outage_date_end >= ?
                )
                OR (
                    date_status != 'known'
                    AND (announced_date IS NULL OR announced_date >= date(?, '-7 day'))
                )
                ORDER BY outage_date_start IS NULL, outage_date_start,
                         start_time IS NULL, start_time, schedule_key
                """,
                (today_text, today_text, today_text),
            ).fetchall()
    except sqlite3.Error as error:
        raise MorningBriefError(f"Could not read retained scheduled outages: {error}") from error
    fields = (
        "schedule_key", "date_start", "date_end", "start_time", "end_time",
        "location_text", "source_url", "source_text", "date_status", "announced_date",
    )
    return [dict(zip(fields, row)) for row in rows]


def power_when(post: dict[str, object]) -> str:
    caption = post.get("caption")
    if not isinstance(caption, str):
        return "Not specified"
    time_window = re.search(
        r"\bfrom\s+([^.;\n]{1,100}?)\s+to\s+([^.;\n]{1,100})",
        caption,
        re.IGNORECASE,
    )
    if time_window:
        return f"From {time_window.group(1).strip()} to {time_window.group(2).strip()}"
    published = post.get("published_philippines")
    return str(published) if isinstance(published, str) and published else "Not specified"


def retained_outage_line(outage: dict[str, object]) -> str:
    location = str(outage.get("location_text") or "Not specified")
    if outage.get("date_status") != "known":
        return f"Scheduled outage — {location} — date/time uncertain; see the utility notice."
    date_start = str(outage.get("date_start") or "date unknown")
    date_end = str(outage.get("date_end") or date_start)
    date_text = date_start if date_start == date_end else f"{date_start} to {date_end}"
    start_time = outage.get("start_time")
    end_time = outage.get("end_time")
    time_text = f" from {start_time} to {end_time}" if start_time and end_time else "; time uncertain"
    return f"Scheduled outage — {location} — {date_text}{time_text}."


def build_power_section(
    power_result: dict[str, object],
    *,
    state_path: Path | None = None,
    today: object | None = None,
) -> str:
    if state_path is not None and today is not None:
        retained = load_retained_scheduled_outages(state_path, today)
        if retained:
            return "\n".join(retained_outage_line(outage) for outage in retained)
        # A known empty retained view is authoritative: the fallback below is
        # only for older/manual JSON fixtures that predate the durable table.
        if state_path.exists():
            try:
                with sqlite3.connect(state_path) as connection:
                    has_table = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scheduled_outages'"
                    ).fetchone()
            except sqlite3.Error as error:
                raise MorningBriefError(f"Could not inspect outage state: {error}") from error
            if has_table:
                return "No scheduled outage affecting your area."

    retained_from_json = power_result.get("scheduled_outages")
    if isinstance(retained_from_json, list):
        records = [item for item in retained_from_json if isinstance(item, dict)]
        if records:
            return "\n".join(retained_outage_line(item) for item in records)

    scheduled: list[str] = []
    for post in power_posts(power_result):
        classification = post.get("classification")
        if not isinstance(classification, dict):
            continue
        if classification.get("qualifies_for_alert") is not True:
            continue
        if classification.get("outage_type") != "scheduled":
            continue
        locations = classification.get("matched_location_terms")
        location_text = (
            ", ".join(str(item) for item in locations if isinstance(item, str))
            if isinstance(locations, list)
            else "Not specified"
        ) or "Not specified"
        scheduled.append(f"Scheduled outage — {location_text} — {power_when(post)}.")
    if not scheduled:
        return "No scheduled outage affecting your area."
    return "\n".join(dict.fromkeys(scheduled))


def build_cyclone_section(cyclone_result: dict[str, object]) -> str:
    if not cyclone_result:
        return "Cyclone status unavailable."
    if cyclone_result.get("active_cyclone") is not True:
        return "No active tropical cyclone in PAR."
    storm_name = cyclone_result.get("storm_name") or "An active tropical cyclone"
    par_status = cyclone_result.get("par_status")
    if par_status == "inside":
        signal = cyclone_result.get("bohol_wind_signal")
        if signal:
            return f"{storm_name} is active in PAR; Bohol is under Tropical Cyclone Wind Signal {signal}."
        return f"{storm_name} is active in PAR; Bohol has no official wind signal."
    if par_status == "outside":
        return f"{storm_name} is active but outside PAR."
    return str(cyclone_result.get("summary") or "Cyclone status unavailable.")


def format_date_line(now: datetime) -> str:
    local = now.astimezone(ZoneInfo(BOHOL_TIMEZONE))
    return local.strftime("%A, %B %d, %Y").replace(" 0", " ")


def build_morning_brief(
    now: datetime,
    power_result: dict[str, object],
    cyclone_result: dict[str, object],
    weather_result: dict[str, object],
    *,
    state_path: Path | None = None,
) -> tuple[str, str]:
    local_today = now.astimezone(ZoneInfo(BOHOL_TIMEZONE)).date()
    power_text = build_power_section(
        power_result, state_path=state_path, today=local_today
    )
    cyclone_text = build_cyclone_section(cyclone_result)
    weather_text = str(weather_result.get("summary") or "Forecast unavailable from PAGASA.")
    message = "\n\n".join(
        [
            f"<b>{html.escape(format_date_line(now), quote=False)}</b>",
            f"<b>Power Today</b>\n{html.escape(power_text, quote=False)}",
            f"<b>Cyclone Status</b>\n{html.escape(cyclone_text, quote=False)}",
            f"<b>Weather</b>\n{html.escape(weather_text, quote=False)}",
        ]
    )
    return "Morning brief", message


def send_morning_brief(
    message: str, send_function=send_notification
) -> dict[str, object]:
    """Deliver the brief at the fixed normal Pushover priority."""
    delivery = send_function(
        title="Morning brief", message=message, priority=0, html=True
    )
    return {**delivery, "priority": 0}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--power-file", type=Path, default=Path("latest_post.json"))
    parser.add_argument("--cyclone-file", type=Path, default=Path("pagasa_daily_detector.json"))
    parser.add_argument("--state-file", type=Path, default=Path("klaxon_state.sqlite3"))
    parser.add_argument("--weather-html", type=Path, help="Use saved PAGASA HTML instead of fetching it.")
    parser.add_argument("--output", type=Path, default=Path("morning_brief.json"))
    parser.add_argument("--checked-at", help="UTC ISO timestamp for deterministic tests.")
    parser.add_argument("--send-pushover", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        now = (
            datetime.fromisoformat(args.checked_at.replace("Z", "+00:00"))
            if args.checked_at
            else datetime.now(timezone.utc)
        )
        power_result = read_json(args.power_file)
        cyclone_result = read_json(args.cyclone_file)
        weather_result = (
            parse_weather_html(args.weather_html.read_text(encoding="utf-8"))
            if args.weather_html
            else fetch_weather()
        )
        title, message = build_morning_brief(
            now, power_result, cyclone_result, weather_result, state_path=args.state_file
        )
        if args.send_pushover:
            delivery = send_morning_brief(message)
        else:
            delivery = {"sent": False, "reason": "Pushover sending was not requested."}
        output = {
            "title": title,
            "message": message,
            "weather": weather_result,
            "pushover": delivery,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
        return 0
    except (OSError, MorningBriefError, PushoverError, ValueError) as error:
        print(f"Klaxon morning brief stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
