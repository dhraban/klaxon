#!/usr/bin/env python3
"""Run deterministic Phase 1 qualifying and nonqualifying filter tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

from klaxon_facebook_test import (
    classify_post,
    deliver_post_if_new,
    load_filter_config,
    load_processed_post_ids,
    record_processed_post_id,
)


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "location_config.json"
OUTPUT_PATH = SCRIPT_DIR / "filter_test_results.json"


def sample_post(caption: str, image_text: str) -> dict[str, object]:
    return {
        "caption": caption,
        "attachments": [{"facebook_image_text": image_text}],
    }


def main() -> int:
    config = load_filter_config(CONFIG_PATH)
    cases = [
        {
            "name": "qualifying_dauis_wide_outage",
            "expected": True,
            "expected_type": "scheduled",
            "expected_priority": 0,
            "post": sample_post(
                "BOHECO I SCHEDULED POWER INTERRUPTION ADVISORY",
                "Affected municipality: Dauis. Power interruption from 1:00 AM to 3:00 AM.",
            ),
        },
        {
            "name": "qualifying_emergency_mayacabac_outage",
            "expected": True,
            "expected_type": "emergency",
            "expected_priority": 1,
            "post": sample_post(
                "BOHECO I EMERGENCY POWER INTERRUPTION ADVISORY",
                "Affected barangay: Mayacabac. Power interruption from 1:00 AM to 3:00 AM.",
            ),
        },
        {
            "name": "nonqualifying_other_municipalities",
            "expected": False,
            "expected_type": "emergency",
            "expected_priority": None,
            "post": sample_post(
                "NGCP EMERGENCY POWER INTERRUPTION ADVISORY",
                "Affected areas: Loon, Maribojoc, Balilihan, Cortes, Corella, and Panglao.",
            ),
        },
    ]

    results: list[dict[str, object]] = []
    all_passed = True
    for case in cases:
        classification = classify_post(case["post"], config)
        actual = classification["qualifies_for_alert"]
        passed = (
            actual == case["expected"]
            and classification["outage_type"] == case["expected_type"]
            and classification["pushover_priority"] == case["expected_priority"]
        )
        all_passed = all_passed and passed
        results.append(
            {
                "name": case["name"],
                "expected_qualifies": case["expected"],
                "actual_qualifies": actual,
                "expected_type": case["expected_type"],
                "actual_type": classification["outage_type"],
                "expected_priority": case["expected_priority"],
                "actual_priority": classification["pushover_priority"],
                "passed": passed,
                "classification": classification,
            }
        )

    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        decision = "ALERT" if result["actual_qualifies"] else "NO ALERT"
        print(f"{status}: {result['name']} -> {decision}")
        classification = result["classification"]
        print(f"  Location matches: {classification['matched_location_terms']}")
        print(f"  Outage matches: {classification['matched_outage_terms']}")
        print(
            "  Type / priority: "
            f"{classification['outage_type']} / {classification['pushover_priority']}"
        )

    sent_notifications: list[dict[str, object]] = []

    def fake_send_notification(**notification: object) -> dict[str, object]:
        sent_notifications.append(notification)
        return {
            "sent": True,
            "priority": notification["priority"],
            "request_id": "offline-test",
        }

    duplicate_test_post = {
        "id": "duplicate-test-post",
        "url": "https://www.facebook.com/BOHECO1officialpage/posts/test",
        "published_philippines": "2026-08-13T09:00:00+08:00",
        "caption": "Scheduled power interruption affecting Dauis.",
        "attachments": [],
    }
    duplicate_classification = classify_post(duplicate_test_post, config)
    with tempfile.TemporaryDirectory(prefix="klaxon-duplicate-test-") as temp_dir:
        state_path = Path(temp_dir) / "klaxon_state.sqlite3"
        first_delivery = deliver_post_if_new(
            duplicate_test_post,
            duplicate_classification,
            state_path,
            send_function=fake_send_notification,
        )
        second_delivery = deliver_post_if_new(
            duplicate_test_post,
            duplicate_classification,
            state_path,
            send_function=fake_send_notification,
        )

    duplicate_test_passed = (
        first_delivery["sent"] is True
        and second_delivery["sent"] is False
        and second_delivery["duplicate"] is True
        and len(sent_notifications) == 1
    )
    all_passed = all_passed and duplicate_test_passed
    results.append(
        {
            "name": "same_post_is_not_sent_twice",
            "passed": duplicate_test_passed,
            "first_delivery": first_delivery,
            "second_delivery": second_delivery,
            "send_call_count": len(sent_notifications),
        }
    )
    print(
        ("PASS" if duplicate_test_passed else "FAIL")
        + ": same_post_is_not_sent_twice"
    )

    with tempfile.TemporaryDirectory(prefix="klaxon-retention-test-") as temp_dir:
        retention_state_path = Path(temp_dir) / "klaxon_state.sqlite3"
        for post_number in range(25):
            record_processed_post_id(
                retention_state_path,
                f"retention-post-{post_number:02d}",
                classification="test",
                delivery_status="ignored",
            )
        retained_ids = load_processed_post_ids(retention_state_path)

    expected_retained_ids = {
        f"retention-post-{post_number:02d}" for post_number in range(5, 25)
    }
    retention_test_passed = retained_ids == expected_retained_ids
    all_passed = all_passed and retention_test_passed
    results.append(
        {
            "name": "retain_only_20_newest_post_ids",
            "passed": retention_test_passed,
            "retained_count": len(retained_ids),
            "oldest_expected_id": "retention-post-05",
            "newest_expected_id": "retention-post-24",
        }
    )
    print(
        ("PASS" if retention_test_passed else "FAIL")
        + ": retain_only_20_newest_post_ids"
    )

    output = {
        "all_tests_passed": all_passed,
        "config_used": str(CONFIG_PATH),
        "results": results,
    }
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Saved test details to: {OUTPUT_PATH}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
