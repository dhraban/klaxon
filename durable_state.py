#!/usr/bin/env python3
"""Import/export Klaxon's non-secret durable state as canonical JSON.

The public ``klaxon-state`` branch is the durable source for alert and schedule
state. GitHub Actions cache remains useful for the high-frequency watcher health
counter, but is not relied upon for duplicate prevention or the morning outage
schedule.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from klaxon_facebook_test import KlaxonTestError, initialize_state_database


STATE_VERSION = 1
PROCESSED_FIELDS = (
    "post_id",
    "first_seen_at_utc",
    "classification",
    "pushover_priority",
    "delivery_status",
    "delivered_at_utc",
)
SCHEDULED_FIELDS = (
    "schedule_key",
    "source_post_id",
    "announced_date",
    "outage_date_start",
    "outage_date_end",
    "start_time",
    "end_time",
    "location_text",
    "location_terms_json",
    "source_url",
    "source_text",
    "date_status",
    "first_seen_at_utc",
)


class DurableStateError(RuntimeError):
    """Raised when durable state cannot safely be imported or exported."""


def rows_as_dicts(connection: sqlite3.Connection, query: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    return [dict(zip(fields, row)) for row in connection.execute(query).fetchall()]


def export_state(state_file: Path, output_file: Path) -> bool:
    """Write canonical business state, returning whether the output changed."""
    initialize_state_database(state_file)
    with sqlite3.connect(state_file) as connection:
        processed_posts = rows_as_dicts(
            connection,
            """
            SELECT post_id, first_seen_at_utc, classification, pushover_priority,
                   delivery_status, delivered_at_utc
            FROM processed_posts
            ORDER BY first_seen_at_utc, post_id
            """,
            PROCESSED_FIELDS,
        )
        scheduled_outages = rows_as_dicts(
            connection,
            """
            SELECT schedule_key, source_post_id, announced_date, outage_date_start,
                   outage_date_end, start_time, end_time, location_text,
                   location_terms_json, source_url, source_text, date_status,
                   first_seen_at_utc
            FROM scheduled_outages
            ORDER BY schedule_key
            """,
            SCHEDULED_FIELDS,
        )
    payload = {
        "version": STATE_VERSION,
        "processed_posts": processed_posts,
        "scheduled_outages": scheduled_outages,
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output_file.exists() and output_file.read_text(encoding="utf-8") == serialized:
        return False
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(serialized, encoding="utf-8")
    return True


def read_state_json(input_file: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        payload = json.loads(input_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DurableStateError(f"Could not read durable state {input_file}: {error}") from error
    if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
        raise DurableStateError("Durable state has an unknown format version.")
    result: dict[str, list[dict[str, Any]]] = {}
    for key, fields in (("processed_posts", PROCESSED_FIELDS), ("scheduled_outages", SCHEDULED_FIELDS)):
        records = payload.get(key)
        if not isinstance(records, list) or not all(
            isinstance(record, dict) and set(record) == set(fields) for record in records
        ):
            raise DurableStateError(f"Durable state field {key!r} is malformed.")
        result[key] = records
    return result


def import_state(input_file: Path, state_file: Path) -> bool:
    """Replace durable tables while retaining the local health-run counter."""
    if not input_file.exists():
        return False
    payload = read_state_json(input_file)
    initialize_state_database(state_file)
    with sqlite3.connect(state_file) as connection:
        connection.execute("DELETE FROM processed_posts")
        connection.execute("DELETE FROM scheduled_outages")
        connection.executemany(
            """
            INSERT INTO processed_posts (
                post_id, first_seen_at_utc, classification, pushover_priority,
                delivery_status, delivered_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [tuple(record[field] for field in PROCESSED_FIELDS) for record in payload["processed_posts"]],
        )
        connection.executemany(
            """
            INSERT INTO scheduled_outages (
                schedule_key, source_post_id, announced_date, outage_date_start,
                outage_date_end, start_time, end_time, location_text,
                location_terms_json, source_url, source_text, date_status,
                first_seen_at_utc, last_seen_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                tuple(record[field] for field in SCHEDULED_FIELDS)
                + (record["first_seen_at_utc"],)
                for record in payload["scheduled_outages"]
            ],
        )
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("import", "export"))
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--json-file", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        changed = (
            import_state(args.json_file, args.state_file)
            if args.action == "import"
            else export_state(args.state_file, args.json_file)
        )
        print(f"Durable state {args.action} {'changed' if changed else 'was unchanged'}.")
        return 0
    except (DurableStateError, KlaxonTestError, OSError, sqlite3.Error, ValueError) as error:
        print(f"Durable state {args.action} stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
