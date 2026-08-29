#!/usr/bin/env python3
"""Manual Phase 1 test: extract one recent BOHECO Facebook post as JSON."""

from __future__ import annotations

import argparse
import datetime as dt
import html as html_module
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from pushover_client import PushoverError, send_notification


PAGE_URL = "https://www.facebook.com/BOHECO1officialpage"
SCRIPT_DIR = Path(__file__).resolve().parent
CODEX_RUNTIME_ROOT = (
    Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies"
)


def resolve_executable(
    environment_name: str, candidates: list[Path], command_names: list[str]
) -> Path:
    configured_path = os.environ.get(environment_name)
    if configured_path:
        return Path(configured_path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for command_name in command_names:
        discovered_path = shutil.which(command_name)
        if discovered_path:
            return Path(discovered_path)
    return candidates[0]


CHROME_PATH = resolve_executable(
    "KLAXON_CHROME_PATH",
    [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/chromium"),
    ],
    ["google-chrome", "chromium", "chromium-browser"],
)
CODEX_NODE_PATH = resolve_executable(
    "KLAXON_NODE_PATH",
    [CODEX_RUNTIME_ROOT / "node/bin/node", Path("/usr/bin/node")],
    ["node"],
)
PLAYWRIGHT_PATH = Path(
    os.environ.get(
        "KLAXON_PLAYWRIGHT_PATH",
        next(
            (
                str(path)
                for path in (
                    SCRIPT_DIR / "node_modules/playwright-core",
                    CODEX_RUNTIME_ROOT / "node/node_modules/playwright",
                )
                if path.exists()
            ),
            str(SCRIPT_DIR / "node_modules/playwright-core"),
        ),
    )
)
FEED_FETCHER_PATH = Path(__file__).resolve().with_name("fetch_facebook_feed.mjs")
FETCH_TIMEOUT_SECONDS = 30
DEFAULT_STATE_PATH = Path(__file__).resolve().with_name("klaxon_state.sqlite3")
LEGACY_STATE_PATH = Path(__file__).resolve().with_name("processed_posts.json")
SEED_STATE_PATH = Path(__file__).resolve().with_name("seed_processed_posts.json")
MAX_PROCESSED_POST_IDS = 20
MAX_POSTS_PER_SWEEP = 25
WATCHER_COUNTER_NAME = "facebook_sweeps"

POST_META_PATTERN = re.compile(
    r'"post_id":"(?P<post_id>\d+)","creation_time":(?P<created>\d+)'
)
MESSAGE_PATTERN = re.compile(
    r'"message":\{"__typename":"TextWithEntities","text":"'
    r'(?P<value>(?:\\.|[^"\\])*)"'
)
POST_URL_PATTERN = re.compile(
    r'https:\\/\\/www\.facebook\.com\\/BOHECO1officialpage\\/posts\\/'
    r'(?P<slug>pfbid[^"?&<\\ ]+)'
)
ACCESSIBILITY_PATTERN = re.compile(
    r'"accessibility_caption":"(?P<value>(?:\\.|[^"\\])*)"'
)
MONTH_NAMES = (
    "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    "Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    "Nov(?:ember)?|Dec(?:ember)?"
)
TIME_VALUE_PATTERN = r"\d{1,2}(?::\d{2})?\s*(?:A\.?\s*M\.?|P\.?\s*M\.?)"


class KlaxonTestError(RuntimeError):
    pass


def decode_json_string(value: str) -> str:
    """Decode Facebook's JSON escapes, then decode any HTML entities."""
    decoded = json.loads(f'"{value}"')
    return html_module.unescape(decoded)


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def published_philippines_date(post: dict[str, object]) -> dt.date | None:
    value = post.get("published_philippines")
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value).date()
    except ValueError:
        return None


def parse_clock_time(value: str) -> dt.datetime | None:
    normalized = value.upper().replace(" ", "").replace(".", "")
    for format_string in ("%I:%M%p", "%I%p"):
        try:
            return dt.datetime.strptime(normalized, format_string)
        except ValueError:
            continue
    return None


def extract_scheduled_outage_details(
    searchable_text: str, post: dict[str, object]
) -> dict[str, object]:
    """Extract the announced outage date/window without guessing a date."""
    announced_date = published_philippines_date(post)
    date_start: dt.date | None = None
    date_end: dt.date | None = None

    relative_match = re.search(r"\b(today|tomorrow)\b", searchable_text, re.IGNORECASE)
    if relative_match and announced_date:
        date_start = announced_date + dt.timedelta(
            days=1 if relative_match.group(1).casefold() == "tomorrow" else 0
        )
        date_end = date_start

    iso_match = re.search(
        r"\b(?P<year>\d{4})[-/](?P<month>0?[1-9]|1[0-2])[-/](?P<day>0?[1-9]|[12]\d|3[01])\b",
        searchable_text,
    )
    month_match = None
    if iso_match:
        try:
            date_start = dt.date(
                int(iso_match.group("year")),
                int(iso_match.group("month")),
                int(iso_match.group("day")),
            )
            date_end = date_start
        except ValueError:
            date_start = date_end = None
    else:
        month_match = re.search(
            rf"(?P<month>{MONTH_NAMES})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
            rf"(?:,?\s*(?P<year>\d{{4}}))?",
            searchable_text,
            re.IGNORECASE,
        )
        if not month_match:
            month_match = re.search(
                rf"(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_NAMES})\.?"
                rf"(?:,?\s*(?P<year>\d{{4}}))?",
                searchable_text,
                re.IGNORECASE,
            )
        if month_match:
            month_number = dt.datetime.strptime(
                month_match.group("month")[:3], "%b"
            ).month
            year = int(month_match.group("year") or (announced_date.year if announced_date else 0))
            if year:
                try:
                    date_start = dt.date(year, month_number, int(month_match.group("day")))
                    date_end = date_start
                    range_match = re.match(
                        r"\s*[-–]\s*(\d{1,2})", searchable_text[month_match.end() :]
                    )
                    if range_match:
                        date_end = dt.date(year, month_number, int(range_match.group(1)))
                except ValueError:
                    date_start = date_end = None

    time_match = re.search(
        rf"\bfrom\s+(?P<start>{TIME_VALUE_PATTERN})\s+"
        rf"to\s+(?P<end>{TIME_VALUE_PATTERN})",
        searchable_text,
        re.IGNORECASE,
    )
    start_time = time_match.group("start") if time_match else None
    end_time = time_match.group("end") if time_match else None
    if date_start and start_time and end_time:
        parsed_start = parse_clock_time(start_time)
        parsed_end = parse_clock_time(end_time)
        if parsed_start and parsed_end and parsed_end < parsed_start:
            date_end = date_start + dt.timedelta(days=1)

    return {
        "date_status": "known" if date_start else "uncertain",
        "date_start": date_start.isoformat() if date_start else None,
        "date_end": date_end.isoformat() if date_end else None,
        "start_time": start_time,
        "end_time": end_time,
        "announced_date": announced_date.isoformat() if announced_date else None,
        "when_text": (
            f"{date_start.isoformat()} from {start_time} to {end_time}"
            if date_start and start_time and end_time
            else "Date/time uncertain"
        ),
    }


def normalize_for_matching(value: str) -> str:
    """Make Facebook's decorative Unicode text comparable to plain keywords."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def load_filter_config(config_path: Path) -> dict[str, object]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    required_lists = [
        "location_terms",
        "nearby_area_terms",
        "feeder_terms",
        "alternate_spellings",
        "outage_terms",
        "scheduled_outage_terms",
        "emergency_outage_terms",
    ]
    for key in required_lists:
        value = config.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise KlaxonTestError(
                f'The setting "{key}" must be a list of non-empty text values.'
            )
    return config


def matching_terms(text: str, terms: list[str]) -> list[str]:
    normalized_text = f" {normalize_for_matching(text)} "
    matches: list[str] = []
    for term in terms:
        normalized_term = normalize_for_matching(term)
        if normalized_term and f" {normalized_term} " in normalized_text:
            matches.append(term)
    return matches


def stop_temporary_browser(process: subprocess.Popen[bytes]) -> None:
    """Best-effort shutdown of only the anonymous Chrome test process group."""
    if process.poll() is not None:
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            process.terminate()
        except (ProcessLookupError, PermissionError):
            return

    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            process.kill()
        except (ProcessLookupError, PermissionError):
            return

    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def classify_post(
    post: dict[str, object], config: dict[str, object]
) -> dict[str, object]:
    caption = post.get("caption", "")
    if not isinstance(caption, str):
        caption = ""

    image_texts: list[str] = []
    attachments = post.get("attachments", [])
    if isinstance(attachments, list):
        for attachment in attachments:
            if isinstance(attachment, dict):
                image_text = attachment.get("facebook_image_text")
                if isinstance(image_text, str):
                    image_texts.append(image_text)

    searchable_text = "\n".join([caption, *image_texts])
    location_terms = unique(
        [
            *config["location_terms"],
            *config["nearby_area_terms"],
            *config["feeder_terms"],
            *config["alternate_spellings"],
        ]
    )
    outage_terms = config["outage_terms"]
    assert isinstance(outage_terms, list)

    matched_locations = matching_terms(searchable_text, location_terms)
    matched_outage_terms = matching_terms(searchable_text, outage_terms)
    matched_scheduled_terms = matching_terms(
        searchable_text, config["scheduled_outage_terms"]
    )
    matched_emergency_terms = matching_terms(
        searchable_text, config["emergency_outage_terms"]
    )
    is_outage = bool(matched_outage_terms)
    affects_location = bool(matched_locations)
    qualifies = is_outage and affects_location

    if matched_emergency_terms:
        outage_type = "emergency"
        priority = 1
    elif matched_scheduled_terms:
        outage_type = "scheduled"
        priority = 0
    elif is_outage:
        outage_type = "unspecified"
        priority = 0
    else:
        outage_type = "not_an_outage"
        priority = None

    if qualifies:
        reason = "Outage wording and a configured location were both found."
    elif not is_outage and not affects_location:
        reason = "No outage wording or configured location was found."
    elif not is_outage:
        reason = "A configured location was found, but no outage wording was found."
    else:
        reason = "Outage wording was found, but no configured location was found."

    scheduled_details = (
        extract_scheduled_outage_details(searchable_text, post)
        if outage_type == "scheduled" and qualifies
        else None
    )

    return {
        "qualifies_for_alert": qualifies,
        "is_power_interruption": is_outage,
        "affects_configured_location": affects_location,
        "matched_outage_terms": matched_outage_terms,
        "matched_location_terms": matched_locations,
        "matched_scheduled_terms": matched_scheduled_terms,
        "matched_emergency_terms": matched_emergency_terms,
        "outage_type": outage_type,
        "pushover_priority": priority if qualifies else None,
        "scheduled_outage": scheduled_details,
        "reason": reason,
    }


def build_pushover_message(
    post: dict[str, object], classification: dict[str, object]
) -> tuple[str, str]:
    outage_type = classification.get("outage_type")
    if outage_type == "emergency":
        title_type = "Emergency outage"
    elif outage_type in {"scheduled", "unspecified"}:
        title_type = "Scheduled outage"
    else:
        title_type = "Not specified outage"
    title = "Electricity update"

    locations = classification.get("matched_location_terms", [])
    if not isinstance(locations, list):
        locations = []
    location_text = ", ".join(str(value) for value in locations) or "Not specified"

    published = post.get("published_philippines")
    published_text = str(published).strip() if published else "Not specified"
    caption = post.get("caption", "")
    if not isinstance(caption, str):
        caption = ""
    compact_caption = " ".join(caption.split())
    if len(compact_caption) > 650:
        compact_caption = compact_caption[:647] + "..."

    time_window_match = re.search(
        r"\bfrom\s+([^.;\n]{1,100}?)\s+to\s+([^.;\n]{1,100})",
        caption,
        re.IGNORECASE,
    )
    if time_window_match:
        when_text = (
            f"From {time_window_match.group(1).strip()} to "
            f"{time_window_match.group(2).strip()}"
        )
        if published_text != "Not specified":
            when_text += f" (post published {published_text})"
    else:
        when_text = (
            f"Post published {published_text}"
            if published_text != "Not specified"
            else "Not specified"
        )

    status_text = (
        "Power interruption reported"
        if classification.get("is_power_interruption") is True
        else "Not specified"
    )

    message = (
        f"<b>{html_module.escape(title_type, quote=False)}</b>\n"
        f"<b>Status:</b> {html_module.escape(status_text, quote=False)}\n"
        f"<b>Where:</b> {html_module.escape(location_text, quote=False)}\n"
        f"<b>When:</b> {html_module.escape(when_text, quote=False)}\n\n"
        f"{html_module.escape(compact_caption or 'Not specified', quote=False)}"
    )
    return title, message


def initialize_state_database(state_path: Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    database_was_missing = not state_path.exists()
    with sqlite3.connect(state_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_posts (
                post_id TEXT PRIMARY KEY,
                first_seen_at_utc TEXT NOT NULL,
                classification TEXT NOT NULL,
                pushover_priority INTEGER,
                delivery_status TEXT NOT NULL,
                delivered_at_utc TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS watcher_run_counter (
                counter_name TEXT PRIMARY KEY,
                period_started_at_utc TEXT NOT NULL,
                run_count INTEGER NOT NULL DEFAULT 0,
                last_run_at_utc TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_outages (
                schedule_key TEXT PRIMARY KEY,
                source_post_id TEXT NOT NULL,
                announced_date TEXT,
                outage_date_start TEXT,
                outage_date_end TEXT,
                start_time TEXT,
                end_time TEXT,
                location_text TEXT NOT NULL,
                location_terms_json TEXT NOT NULL,
                source_url TEXT,
                source_text TEXT NOT NULL,
                date_status TEXT NOT NULL,
                first_seen_at_utc TEXT NOT NULL,
                last_seen_at_utc TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO watcher_run_counter (
                counter_name, period_started_at_utc, run_count, last_run_at_utc
            ) VALUES (?, ?, 0, NULL)
            """,
            (WATCHER_COUNTER_NAME, dt.datetime.now(dt.timezone.utc).isoformat()),
        )

        seed_state_path = (
            LEGACY_STATE_PATH if LEGACY_STATE_PATH.exists() else SEED_STATE_PATH
        )
        if (
            database_was_missing
            and state_path.resolve() == DEFAULT_STATE_PATH.resolve()
            and seed_state_path.exists()
        ):
            try:
                legacy_state = json.loads(
                    seed_state_path.read_text(encoding="utf-8")
                )
                legacy_ids = legacy_state.get("processed_post_ids", [])
            except (OSError, json.JSONDecodeError) as error:
                raise KlaxonTestError(
                    f"The previous duplicate-state file could not be read: "
                    f"{seed_state_path}"
                ) from error
            if not isinstance(legacy_ids, list) or not all(
                isinstance(post_id, str) for post_id in legacy_ids
            ):
                raise KlaxonTestError(
                    f"The previous duplicate-state file is invalid: "
                    f"{seed_state_path}"
                )
            imported_at = dt.datetime.now(dt.timezone.utc).isoformat()
            connection.executemany(
                """
                INSERT OR IGNORE INTO processed_posts (
                    post_id, first_seen_at_utc, classification,
                    pushover_priority, delivery_status, delivered_at_utc
                ) VALUES (?, ?, 'legacy_processed', NULL, 'processed', NULL)
                """,
                [(post_id, imported_at) for post_id in legacy_ids],
            )


def record_scheduled_watcher_run(state_path: Path) -> None:
    """Count one scheduled Facebook sweep in the persistent state database."""
    initialize_state_database(state_path)
    recorded_at = dt.datetime.now(dt.timezone.utc).isoformat()
    with sqlite3.connect(state_path) as connection:
        connection.execute(
            """
            UPDATE watcher_run_counter
            SET run_count = run_count + 1, last_run_at_utc = ?
            WHERE counter_name = ?
            """,
            (recorded_at, WATCHER_COUNTER_NAME),
        )


def read_watcher_run_counter(state_path: Path) -> dict[str, object]:
    initialize_state_database(state_path)
    with sqlite3.connect(state_path) as connection:
        row = connection.execute(
            """
            SELECT period_started_at_utc, run_count, last_run_at_utc
            FROM watcher_run_counter
            WHERE counter_name = ?
            """,
            (WATCHER_COUNTER_NAME,),
        ).fetchone()
    if row is None:
        raise KlaxonTestError("The watcher run counter could not be initialized.")
    return {
        "period_started_at_utc": str(row[0]),
        "run_count": int(row[1]),
        "last_run_at_utc": str(row[2]) if row[2] is not None else None,
    }


def reset_watcher_run_counter(state_path: Path) -> None:
    initialize_state_database(state_path)
    reset_at = dt.datetime.now(dt.timezone.utc).isoformat()
    with sqlite3.connect(state_path) as connection:
        connection.execute(
            """
            UPDATE watcher_run_counter
            SET period_started_at_utc = ?, run_count = 0, last_run_at_utc = NULL
            WHERE counter_name = ?
            """,
            (reset_at, WATCHER_COUNTER_NAME),
        )


def load_processed_post_ids(state_path: Path) -> set[str]:
    initialize_state_database(state_path)
    with sqlite3.connect(state_path) as connection:
        rows = connection.execute("SELECT post_id FROM processed_posts").fetchall()
    return {str(row[0]) for row in rows}


def scheduled_outage_key(
    details: dict[str, object], classification: dict[str, object]
) -> str:
    locations = classification.get("matched_location_terms", [])
    location_key = ",".join(
        sorted(str(value).casefold() for value in locations if isinstance(value, str))
    ) if isinstance(locations, list) else ""
    return "|".join(
        [
            str(details.get("date_start") or "unknown"),
            str(details.get("date_end") or "unknown"),
            str(details.get("start_time") or "unknown").casefold(),
            str(details.get("end_time") or "unknown").casefold(),
            location_key,
        ]
    )


def record_scheduled_outage(
    state_path: Path,
    post: dict[str, object],
    classification: dict[str, object],
) -> None:
    """Upsert a recognized scheduled outage for the durable morning view."""
    details = classification.get("scheduled_outage")
    if classification.get("outage_type") != "scheduled" or not isinstance(details, dict):
        return
    post_id = post.get("id")
    if not isinstance(post_id, (str, int)):
        return
    locations = classification.get("matched_location_terms", [])
    location_terms = [str(value) for value in locations if isinstance(value, str)] if isinstance(locations, list) else []
    if not location_terms:
        return
    initialize_state_database(state_path)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    source_text = post.get("caption") if isinstance(post.get("caption"), str) else ""
    source_url = post.get("url") if isinstance(post.get("url"), str) else None
    location_text = ", ".join(location_terms)
    schedule_key = scheduled_outage_key(details, classification)
    with sqlite3.connect(state_path) as connection:
        connection.execute(
            """
            INSERT INTO scheduled_outages (
                schedule_key, source_post_id, announced_date, outage_date_start,
                outage_date_end, start_time, end_time, location_text,
                location_terms_json, source_url, source_text, date_status,
                first_seen_at_utc, last_seen_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schedule_key) DO UPDATE SET
                source_post_id = excluded.source_post_id,
                announced_date = excluded.announced_date,
                source_url = excluded.source_url,
                source_text = excluded.source_text,
                last_seen_at_utc = excluded.last_seen_at_utc
            """,
            (
                schedule_key,
                str(post_id),
                details.get("announced_date"),
                details.get("date_start"),
                details.get("date_end"),
                details.get("start_time"),
                details.get("end_time"),
                location_text,
                json.dumps(location_terms),
                source_url,
                source_text,
                details.get("date_status", "uncertain"),
                now,
                now,
            ),
        )


def record_processed_post_id(
    state_path: Path,
    post_id: str,
    *,
    classification: str = "unknown",
    priority: int | None = None,
    delivery_status: str = "processed",
) -> None:
    initialize_state_database(state_path)
    recorded_at = dt.datetime.now(dt.timezone.utc).isoformat()
    delivered_at = recorded_at if delivery_status == "sent" else None
    with sqlite3.connect(state_path) as connection:
        insert_result = connection.execute(
            """
            INSERT OR IGNORE INTO processed_posts (
                post_id, first_seen_at_utc, classification,
                pushover_priority, delivery_status, delivered_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                post_id,
                recorded_at,
                classification,
                priority,
                delivery_status,
                delivered_at,
            ),
        )
        if insert_result.rowcount == 1:
            connection.execute(
                """
                DELETE FROM processed_posts
                WHERE post_id IN (
                    SELECT post_id
                    FROM processed_posts
                    ORDER BY first_seen_at_utc DESC, rowid DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (MAX_PROCESSED_POST_IDS,),
            )


def deliver_post_if_new(
    post: dict[str, object],
    classification: dict[str, object],
    state_path: Path,
    send_function=send_notification,
) -> dict[str, object]:
    raw_post_id = post.get("id")
    if not isinstance(raw_post_id, (str, int)):
        raise KlaxonTestError("The Facebook post has no usable stable ID.")
    post_id = str(raw_post_id)

    if post_id in load_processed_post_ids(state_path):
        return {
            "sent": False,
            "duplicate": True,
            "reason": "This Facebook post was already processed.",
        }

    if not classification["qualifies_for_alert"]:
        record_processed_post_id(
            state_path,
            post_id,
            classification=str(classification["outage_type"]),
            delivery_status="ignored",
        )
        return {
            "sent": False,
            "duplicate": False,
            "reason": "The post did not qualify for an alert.",
        }

    title, message = build_pushover_message(post, classification)
    priority = classification["pushover_priority"]
    assert isinstance(priority, int)
    post_url = post.get("url")
    delivery = send_function(
        title=title,
        message=message,
        priority=priority,
        url=post_url if isinstance(post_url, str) else None,
        html=True,
    )
    record_processed_post_id(
        state_path,
        post_id,
        classification=str(classification["outage_type"]),
        priority=priority,
        delivery_status="sent",
    )
    return {**delivery, "duplicate": False}


def fetch_rendered_page(
    post_limit: int = 1, stop_post_ids: set[str] | None = None
) -> str:
    if not CHROME_PATH.is_file():
        raise KlaxonTestError(
            "Google Chrome was not found in Applications. Install Chrome, then try again."
        )

    if post_limit > 1:
        missing_paths = [
            path
            for path in (CODEX_NODE_PATH, PLAYWRIGHT_PATH, FEED_FETCHER_PATH)
            if not path.exists()
        ]
        if missing_paths:
            raise KlaxonTestError(
                "The installed browser-scrolling component could not be found."
            )

        with tempfile.TemporaryDirectory(prefix="klaxon-facebook-feed-") as temp_dir:
            rendered_path = Path(temp_dir) / "rendered-feed.html"
            try:
                result = subprocess.run(
                    [
                        str(CODEX_NODE_PATH),
                        str(FEED_FETCHER_PATH),
                        str(rendered_path),
                        str(post_limit),
                        str(PLAYWRIGHT_PATH),
                        str(CHROME_PATH),
                        f"{PAGE_URL}/posts",
                        "24",
                        ",".join(sorted(stop_post_ids or set())),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise KlaxonTestError(
                    "Facebook did not load the requested post history within 90 seconds."
                ) from error

            if result.returncode != 0 or not rendered_path.exists():
                detail = result.stderr.strip().splitlines()
                reason = detail[-1] if detail else "unknown browser error"
                raise KlaxonTestError(
                    f"The scrolling Facebook fetch stopped: {reason}"
                )
            page_data = rendered_path.read_bytes()

        if not page_data:
            raise KlaxonTestError("Facebook returned no rendered feed data.")
        return page_data.decode("utf-8", errors="replace")

    with tempfile.TemporaryDirectory(prefix="klaxon-facebook-") as profile_dir:
        rendered_path = Path(profile_dir) / "rendered.html"
        command = [
            str(CHROME_PATH),
            "--headless=new",
            "--incognito",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-default-apps",
            "--window-size=1280,30000",
            f"--user-data-dir={profile_dir}",
            "--virtual-time-budget=15000",
            "--dump-dom",
            PAGE_URL,
        ]

        with rendered_path.open("wb") as rendered_file:
            process = subprocess.Popen(
                command,
                stdout=rendered_file,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            deadline = time.monotonic() + FETCH_TIMEOUT_SECONDS
            page_data = b""
            while time.monotonic() < deadline:
                rendered_file.flush()
                if rendered_path.stat().st_size > 100_000:
                    page_data = rendered_path.read_bytes()
                    if b'"post_id":"' in page_data:
                        break
                if process.poll() is not None:
                    page_data = rendered_path.read_bytes()
                    break
                time.sleep(0.5)

            # Chrome may leave helper processes running after --dump-dom has
            # produced its output. End only this temporary process group.
            stop_temporary_browser(process)

            rendered_file.flush()
            page_data = rendered_path.read_bytes()

    if not page_data:
        raise KlaxonTestError("Facebook returned no rendered page data.")

    return page_data.decode("utf-8", errors="replace")


def extract_posts(rendered_html: str, limit: int) -> dict[str, object]:
    logged_out = "CometProfilePlusLoggedOutRoute" in rendered_html
    if not logged_out:
        raise KlaxonTestError(
            "The page was not confirmed as logged out, so the test stopped without using it."
        )

    post_matches = list(POST_META_PATTERN.finditer(rendered_html))
    if not post_matches:
        raise KlaxonTestError(
            "Facebook rendered the page, but no usable post ID and timestamp were found."
        )

    posts: list[dict[str, object]] = []
    extracted_post_ids: set[str] = set()

    for index, post_match in enumerate(post_matches):
        post_id = post_match.group("post_id")
        if post_id in extracted_post_ids:
            continue
        start = post_match.start()
        next_start = (
            post_matches[index + 1].start()
            if index + 1 < len(post_matches)
            else len(rendered_html)
        )
        window = rendered_html[start : min(next_start, start + 600_000)]
        caption_match = MESSAGE_PATTERN.search(window)
        if not caption_match:
            continue
        caption = decode_json_string(caption_match.group("value"))
        created_unix = int(post_match.group("created"))

        url_match = POST_URL_PATTERN.search(window)
        post_url = (
            f"https://www.facebook.com/BOHECO1officialpage/posts/{url_match.group('slug')}"
            if url_match
            else f"https://www.facebook.com/BOHECO1officialpage/posts/{post_id}"
        )

        image_texts = unique(
            [
                decode_json_string(match.group("value"))
                for match in ACCESSIBILITY_PATTERN.finditer(window)
            ]
        )
        attached_photo_pattern = re.compile(
            rf'fbid=(?P<photo_id>\d+)[^"<>]{{0,500}}?pcb\.{re.escape(post_id)}'
        )
        photo_ids = unique(
            [
                match.group("photo_id")
                for match in attached_photo_pattern.finditer(window)
            ]
        )

        attachments: list[dict[str, str | None]] = []
        for attachment_index in range(max(len(photo_ids), len(image_texts))):
            photo_id = (
                photo_ids[attachment_index]
                if attachment_index < len(photo_ids)
                else None
            )
            image_text = (
                image_texts[attachment_index]
                if attachment_index < len(image_texts)
                else None
            )
            attachments.append(
                {
                    "photo_id": photo_id,
                    "photo_url": (
                        f"https://www.facebook.com/photo/?fbid={photo_id}"
                        if photo_id
                        else None
                    ),
                    "facebook_image_text": image_text,
                }
            )

        created_utc = dt.datetime.fromtimestamp(created_unix, tz=dt.timezone.utc)
        created_ph = created_utc.astimezone(ZoneInfo("Asia/Manila"))
        posts.append(
            {
                "id": post_id,
                "url": post_url,
                "published_unix": created_unix,
                "published_utc": created_utc.isoformat(),
                "published_philippines": created_ph.isoformat(),
                "caption": caption,
                "attachments": attachments,
            }
        )
        extracted_post_ids.add(post_id)
        if len(posts) >= limit:
            break

    if not posts:
        raise KlaxonTestError("Posts were found, but their captions could not be extracted.")

    return {
        "source": {
            "name": "Bohol I Electric Cooperative, Inc.",
            "page_url": PAGE_URL,
            "facebook_route": "logged_out",
            "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
        "requested_post_count": limit,
        "extracted_post_count": len(posts),
        "posts": posts,
    }


def extract_post(rendered_html: str) -> dict[str, object]:
    """Backward-compatible single-post result for earlier Phase 1 callers."""
    result = extract_posts(rendered_html, 1)
    posts = result["posts"]
    assert isinstance(posts, list) and posts
    return {"source": result["source"], "post": posts[0]}


def select_new_posts_until_known(
    posts: list[dict[str, object]], processed_post_ids: set[str]
) -> list[dict[str, object]]:
    """Return the newest unseen run, ordered oldest-to-newest for delivery."""
    unseen_newest_first: list[dict[str, object]] = []
    for post in posts:
        raw_post_id = post.get("id")
        if not isinstance(raw_post_id, (str, int)):
            raise KlaxonTestError("The Facebook post has no usable stable ID.")
        if str(raw_post_id) in processed_post_ids:
            break
        unseen_newest_first.append(post)
    return list(reversed(unseen_newest_first))


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_output = script_dir / "latest_post.json"
    default_config = script_dir / "location_config.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-html",
        type=Path,
        help="Parse an existing rendered HTML file instead of contacting Facebook.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help=f"JSON output path (default: {default_output}).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config,
        help=f"Location-filter settings (default: {default_config}).",
    )
    parser.add_argument(
        "--send-pushover",
        action="store_true",
        help="Send Pushover only if the fetched post qualifies for an alert.",
    )
    parser.add_argument(
        "--post-limit",
        type=int,
        choices=range(1, MAX_POSTS_PER_SWEEP + 1),
        default=1,
        metavar=f"1-{MAX_POSTS_PER_SWEEP}",
        help=(
            "Number of recent Facebook posts to inspect "
            f"(maximum {MAX_POSTS_PER_SWEEP}; default: 1)."
        ),
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"Processed-post state path (default: {DEFAULT_STATE_PATH}).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        processed_post_ids = (
            load_processed_post_ids(args.state_file) if args.send_pushover else set()
        )
        if args.input_html:
            rendered_html = args.input_html.read_text(encoding="utf-8")
        else:
            print(
                "Opening an anonymous temporary browser for BOHECO "
                f"and adaptively checking up to {args.post_limit} post(s)..."
            )
            rendered_html = fetch_rendered_page(
                args.post_limit,
                processed_post_ids if args.send_pushover else None,
            )

        result = extract_posts(rendered_html, args.post_limit)
        config = load_filter_config(args.config)
        scanned_posts = result["posts"]
        assert isinstance(scanned_posts, list)
        posts = (
            select_new_posts_until_known(scanned_posts, processed_post_ids)
            if args.send_pushover
            else scanned_posts
        )
        result["scanned_post_count"] = len(scanned_posts)
        result["new_post_count"] = len(posts)
        result["stopped_at_processed_post"] = len(posts) < len(scanned_posts)
        result["posts"] = posts
        # Keep the complete recent scan, including already-processed posts, so
        # the morning brief can use the latest scheduled-outage result even
        # when this sweep has no new IDs to deliver.
        result["latest_posts"] = scanned_posts
        result["latest_post_count"] = len(scanned_posts)
        result["extracted_post_count"] = len(posts)
        for post_for_classification in scanned_posts:
            assert isinstance(post_for_classification, dict)
            classification = classify_post(post_for_classification, config)
            post_for_classification["classification"] = classification
            if args.send_pushover:
                record_scheduled_outage(
                    args.state_file, post_for_classification, classification
                )
        for post_for_delivery in posts:
            assert isinstance(post_for_delivery, dict)
            classification = post_for_delivery["classification"]
            assert isinstance(classification, dict)
            if args.send_pushover:
                post_for_delivery["pushover_delivery"] = deliver_post_if_new(
                    post_for_delivery,
                    classification,
                    args.state_file,
                )
            else:
                post_for_delivery["pushover_delivery"] = {
                    "sent": False,
                    "reason": "Pushover sending was not requested for this run.",
                }

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        print(
            "Klaxon Facebook test succeeded. "
            f"Scanned {result['scanned_post_count']} post(s); "
            f"found {result['new_post_count']} new post(s)."
        )
        for index, post in enumerate(posts, start=1):
            assert isinstance(post, dict)
            classification = post["classification"]
            delivery = post["pushover_delivery"]
            assert isinstance(classification, dict)
            assert isinstance(delivery, dict)
            print(f"Post {index}: {post['id']} — {post['published_philippines']}")
            print(
                "  Qualifies for alert: "
                + ("YES" if classification["qualifies_for_alert"] else "NO")
            )
            print(f"  Location matches: {classification['matched_location_terms']}")
            print(f"  Reason: {classification['reason']}")
            if classification["qualifies_for_alert"]:
                print(
                    "  Alert type / priority: "
                    f"{classification['outage_type']} / "
                    f"{classification['pushover_priority']}"
                )
            if delivery["sent"]:
                print("  Pushover notification sent successfully.")
            elif args.send_pushover:
                print(f"  Pushover notification not sent: {delivery['reason']}")
        print(f"Saved structured data to: {args.output}")
        return 0
    except (
        KlaxonTestError,
        PushoverError,
        HTTPError,
        URLError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"Klaxon Facebook test stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
