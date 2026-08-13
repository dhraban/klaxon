#!/usr/bin/env python3
"""Minimal Pushover delivery using credentials stored in macOS Keychain."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
CREDENTIALS_PATH = Path(__file__).resolve().with_name("pushover_credentials.json")


class PushoverError(RuntimeError):
    pass


def read_credentials() -> dict[str, str]:
    environment_user_key = os.environ.get("PUSHOVER_USER_KEY")
    environment_application_token = os.environ.get(
        "PUSHOVER_APPLICATION_TOKEN"
    )
    if environment_user_key or environment_application_token:
        credentials = {
            "user_key": environment_user_key,
            "application_token": environment_application_token,
        }
    else:
        try:
            credentials = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PushoverError(
                "Klaxon's Pushover credentials are missing or unreadable."
            ) from error

    user_key = credentials.get("user_key")
    application_token = credentials.get("application_token")
    if not isinstance(user_key, str) or not isinstance(application_token, str):
        raise PushoverError("Klaxon's Pushover credentials are incomplete.")
    if len(user_key) != 30 or len(application_token) != 30:
        raise PushoverError("Klaxon's stored Pushover credentials do not look valid.")
    return {
        "user_key": user_key,
        "application_token": application_token,
    }


def send_notification(
    *,
    title: str,
    message: str,
    priority: int,
    url: str | None = None,
    html: bool = False,
) -> dict[str, object]:
    if priority not in (0, 1):
        raise PushoverError("Klaxon currently permits only Pushover priority 0 or 1.")

    credentials = read_credentials()
    parameters = {
        "token": credentials["application_token"],
        "user": credentials["user_key"],
        "title": title,
        "message": message,
        "priority": str(priority),
    }
    if url:
        parameters["url"] = url
        parameters["url_title"] = "Open BOHECO post"
    if html:
        parameters["html"] = "1"

    request = Request(
        PUSHOVER_API_URL,
        data=urlencode(parameters).encode("utf-8"),
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise PushoverError(
            f"Pushover rejected the request (HTTP {error.code}). "
            "Check the two stored credentials and try again."
        ) from error
    except URLError as error:
        raise PushoverError(
            "Klaxon could not reach Pushover. Check the internet connection and try again."
        ) from error

    if response_data.get("status") != 1:
        raise PushoverError("Pushover did not confirm that the notification was sent.")

    return {
        "sent": True,
        "priority": priority,
        "request_id": response_data.get("request"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--priority",
        type=int,
        choices=(0, 1),
        default=0,
        help="Pushover priority for the connection test (default: 0).",
    )
    args = parser.parse_args()

    try:
        result = send_notification(
            title="Klaxon connection test",
            message=(
                "Klaxon successfully used its own Pushover application credentials. "
                f"This is a priority {args.priority} test."
            ),
            priority=args.priority,
        )
        print(
            "Pushover connection test sent successfully at priority "
            f"{result['priority']}."
        )
        return 0
    except PushoverError as error:
        print(f"Pushover connection test stopped: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
