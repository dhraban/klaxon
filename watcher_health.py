#!/usr/bin/env python3
"""Count scheduled sweeps and persist a daily health result."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from klaxon_facebook_test import (
    DEFAULT_STATE_PATH,
    KlaxonTestError,
    read_watcher_run_counter,
    record_scheduled_watcher_run,
    reset_watcher_run_counter,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"Persistent state path (default: {DEFAULT_STATE_PATH}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("watcher_health.json"),
        help="Structured audit result path (default: watcher_health.json).",
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--increment",
        action="store_true",
        help="Record one scheduled Facebook sweep.",
    )
    actions.add_argument(
        "--audit-and-reset",
        action="store_true",
        help="Audit the counter, warn if low, then reset it.",
    )
    parser.add_argument(
        "--expected-runs",
        type=int,
        default=96,
        help="Expected scheduled sweeps in the audit period (default: 96).",
    )
    parser.add_argument(
        "--minimum-fraction",
        type=float,
        default=0.90,
        help="Minimum acceptable fraction of expected runs (default: 0.90).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.expected_runs <= 0:
            raise KlaxonTestError("Expected runs must be greater than zero.")
        if not 0 < args.minimum_fraction <= 1:
            raise KlaxonTestError("Minimum fraction must be greater than 0 and at most 1.")

        if args.increment:
            record_scheduled_watcher_run(args.state_file)
            print("Recorded one scheduled Facebook watcher run.")
            return 0

        summary = read_watcher_run_counter(args.state_file)
        run_count = int(summary["run_count"])
        minimum_count = args.expected_runs * args.minimum_fraction
        warning_needed = run_count < minimum_count
        audited_at = datetime.now(timezone.utc)
        print(
            f"Watcher audit: {run_count} run(s) recorded; "
            f"expected {args.expected_runs}; "
            f"minimum acceptable {minimum_count:.1f}."
        )
        if warning_needed:
            print(
                "Watcher health is degraded; the result will be reported in the "
                "morning brief without a separate Pushover notification."
            )
        else:
            print("Watcher run count is within the acceptable range.")

        reset_watcher_run_counter(args.state_file)
        print("Watcher run counter reset for the next audit period.")
        audit_result = {
            "audit_succeeded": True,
            "audited_at_utc": audited_at.isoformat().replace("+00:00", "Z"),
            "period_started_at_utc": summary["period_started_at_utc"],
            "run_count": run_count,
            "expected_runs": args.expected_runs,
            "minimum_fraction": args.minimum_fraction,
            "minimum_count": minimum_count,
            "status": "degraded" if warning_needed else "healthy",
            "summary": (
                "Facebook watcher checks were below the acceptable threshold."
                if warning_needed
                else "Facebook watcher run count was within the acceptable range."
            ),
            "reset_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(audit_result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Saved structured health result to: {args.output}")
        return 0
    except (KlaxonTestError, OSError, ValueError) as error:
        print(f"Klaxon watcher health check stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
