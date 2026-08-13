#!/usr/bin/env python3
"""Count scheduled sweeps and send a daily health warning when needed."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from klaxon_facebook_test import (
    DEFAULT_STATE_PATH,
    KlaxonTestError,
    PushoverError,
    read_watcher_run_counter,
    record_scheduled_watcher_run,
    reset_watcher_run_counter,
    send_notification,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"Persistent state path (default: {DEFAULT_STATE_PATH}).",
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
    parser.add_argument(
        "--send-pushover",
        action="store_true",
        help="Send a priority 0 warning when the count is below the threshold.",
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
        print(
            f"Watcher audit: {run_count} run(s) recorded; "
            f"expected {args.expected_runs}; "
            f"minimum acceptable {minimum_count:.1f}."
        )
        if warning_needed:
            last_run = summary["last_run_at_utc"] or "none recorded"
            message = (
                "Klaxon watcher health warning\n"
                f"Recorded runs: {run_count}\n"
                f"Expected runs: {args.expected_runs}\n"
                f"Last recorded sweep: {last_run}\n\n"
                "The scheduled Facebook watcher may have missed one or more runs."
            )
            if args.send_pushover:
                send_notification(
                    title="Klaxon watcher health warning",
                    message=message,
                    priority=0,
                )
                print("Priority 0 health warning sent through Pushover.")
            else:
                print("Health warning required; Pushover sending was not requested.")
        else:
            print("Watcher run count is within the acceptable range.")

        reset_watcher_run_counter(args.state_file)
        print("Watcher run counter reset for the next audit period.")
        return 0
    except (KlaxonTestError, PushoverError, OSError, ValueError) as error:
        print(f"Klaxon watcher health check stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
