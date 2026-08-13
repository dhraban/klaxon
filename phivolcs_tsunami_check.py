#!/usr/bin/env python3
"""Monitor the current official PHIVOLCS tsunami bulletin for Bohol.

This checker is intentionally fail-closed. It sends an emergency Pushover
notification only when the current bulletin heading explicitly says
"TSUNAMI WARNING" and Bohol appears in the bulletin's affected-area section.
It does not infer tsunami danger from earthquake magnitude, location, or a
minor sea-level advisory.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from pushover_client import (
    DEFAULT_EMERGENCY_EXPIRE_SECONDS,
    DEFAULT_EMERGENCY_RETRY_SECONDS,
    PushoverError,
    send_notification,
)


SOURCE_URL = "https://tsunami.phivolcs.dost.gov.ph/"
USER_AGENT = "Klaxon/1.0 (+https://github.com/dhraban/klaxon)"
EMERGENCY_PRIORITY = 2


class PhivolcsError(RuntimeError):
    """Raised when the official PHIVOLCS page cannot be interpreted."""


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.text_parts.append(data)


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_html(source: str) -> tuple[str, list[str]]:
    parser = PageParser()
    parser.feed(source)
    return normalized_text(" ".join(parser.text_parts)), parser.links


def first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    return normalized_text(match.group(1)) if match else None


def latest_bulletin_url(home_html: str) -> str | None:
    _, links = parse_html(home_html)
    for href in links:
        absolute = urljoin(SOURCE_URL, href)
        if "/Tsunami_Information/" in absolute and absolute.lower().endswith(".html"):
            return absolute
    return None


def parse_bulletin(text: str, source_url: str) -> dict[str, object]:
    # Accept both the raw official HTML and already-extracted visible text so
    # fixture tests can exercise the parser at the same boundary as live data.
    visible = (
        parse_html(text)[0]
        if re.search(r"<\s*(?:html|body|h[1-6]|p|ul|li)\b", text, re.IGNORECASE)
        else normalized_text(text)
    )
    heading_match = re.search(
        r"TSUNAMI\s+INFORMATION\s+NO\.?\s*\d+\s+(TSUNAMI\s+WARNING|"
        r"NO\s+TSUNAMI\s+THREAT|MINOR\s+SEA-LEVEL\s+DISTURBANCE)",
        visible,
        re.IGNORECASE,
    )
    bulletin_status = (
        heading_match.group(1).upper() if heading_match else "UNKNOWN"
    )
    warning = bulletin_status == "TSUNAMI WARNING"
    affected_match = re.search(
        r"(?:following provinces|following areas|affected areas?)\b.*?"
        r"(?P<areas>.*?)(?=\s+Owners of boats|\s+Issued on:|\s+IMPORTANT|"
        r"\s+No evacuation|$)",
        visible,
        re.IGNORECASE,
    )
    affected_areas = normalized_text(affected_match.group("areas")) if affected_match else ""
    bohol_affected = bool(re.search(r"\bBohol\b", affected_areas, re.IGNORECASE))
    issued_on = first_match(r"Issued on:\s*(.*?)(?=\s+Issued by:|\s+IMPORTANT|$)", visible)
    earthquake_time = first_match(
        r"Date and Time\s*:\s*(.*?)(?=\s+Location\s*:|\s+Depth|$)", visible
    )
    location = first_match(r"Location\s*:\s*(.*?)(?=\s+Depth|\s+Magnitude|$)", visible)
    magnitude = first_match(r"Magnitude\s*:\s*([^\s]+)", visible)
    fingerprint_source = "|".join(
        [source_url, bulletin_status, issued_on or "", earthquake_time or "", affected_areas]
    )
    fingerprint = sha256(fingerprint_source.encode("utf-8")).hexdigest()
    qualifies = warning and bohol_affected
    return {
        "source": source_url,
        "bulletin_status": bulletin_status,
        "explicit_tsunami_warning": warning,
        "affected_areas": affected_areas,
        "bohol_affected": bohol_affected,
        "qualifies_for_emergency": qualifies,
        "issued_on": issued_on,
        "earthquake_time": earthquake_time,
        "location": location,
        "magnitude": magnitude,
        "fingerprint": fingerprint,
        "summary": (
            "Official TSUNAMI WARNING names Bohol."
            if qualifies
            else "No qualifying PHIVOLCS tsunami warning for Bohol."
        ),
    }


def build_emergency_message(result: dict[str, object]) -> tuple[str, str]:
    title = "Klaxon: TSUNAMI WARNING for Bohol"
    message = (
        "TSUNAMI WARNING — BOHOL\n"
        f"Issued: {result.get('issued_on') or 'Not specified'}\n"
        f"Affected areas: {result.get('affected_areas') or 'Bohol'}\n"
        f"Earthquake: {result.get('location') or 'Not specified'}\n"
        f"Magnitude: {result.get('magnitude') or 'Not specified'}\n\n"
        "Follow current PHIVOLCS and local-authority evacuation instructions immediately.\n"
        "This checker relays the current official bulletin; it is not a guarantee of advance warning.\n\n"
        f"Source: {result['source']}"
    )
    return title, message


def process_result(
    result: dict[str, object],
    state: dict[str, object],
    *,
    send: bool,
    send_function=send_notification,
) -> tuple[dict[str, object], dict[str, object]]:
    """Apply duplicate suppression and optionally send one emergency alert."""
    output = {**result, "checked_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    if not result["qualifies_for_emergency"]:
        output["notification_sent"] = False
        if state.get("last_warning_receipt"):
            output["last_warning_receipt"] = state["last_warning_receipt"]
        output["notification_reason"] = "Bulletin is not a Bohol tsunami warning."
        return output, state
    fingerprint = str(result["fingerprint"])
    if state.get("last_warning_fingerprint") == fingerprint and state.get("last_warning_receipt"):
        output["notification_sent"] = False
        output["notification_duplicate"] = True
        output["receipt"] = state.get("last_warning_receipt")
        output["notification_reason"] = "This warning was already sent; receipt preserved."
        return output, state
    output["notification_priority"] = EMERGENCY_PRIORITY
    output["retry_seconds"] = DEFAULT_EMERGENCY_RETRY_SECONDS
    output["expire_seconds"] = DEFAULT_EMERGENCY_EXPIRE_SECONDS
    if not send:
        output["notification_sent"] = False
        output["would_send"] = True
        output["notification_reason"] = "Sending was not requested."
        return output, state
    title, message = build_emergency_message(result)
    delivery = send_function(
        title=title,
        message=message,
        priority=EMERGENCY_PRIORITY,
        retry=DEFAULT_EMERGENCY_RETRY_SECONDS,
        expire=DEFAULT_EMERGENCY_EXPIRE_SECONDS,
    )
    receipt = delivery.get("receipt")
    if not isinstance(receipt, str) or not receipt:
        raise PhivolcsError("Pushover did not return an emergency receipt.")
    updated_state = {
        **state,
        "last_warning_fingerprint": fingerprint,
        "last_warning_receipt": receipt,
        "last_warning_sent_at_utc": output["checked_at_utc"],
        "last_warning_source": result["source"],
    }
    output["notification_sent"] = True
    output["receipt"] = receipt
    return output, updated_state


def fetch_url(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except Exception as error:  # pragma: no cover - network-specific
        raise PhivolcsError(f"PHIVOLCS request failed for {url}: {error}") from error


def load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PhivolcsError(f"Tsunami state could not be read: {error}") from error
    if not isinstance(value, dict):
        raise PhivolcsError("Tsunami state must contain a JSON object.")
    return value


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fetch_live_result() -> dict[str, object]:
    home_html = fetch_url(SOURCE_URL).decode("utf-8", errors="replace")
    bulletin_url = latest_bulletin_url(home_html)
    if not bulletin_url:
        raise PhivolcsError("No current PHIVOLCS tsunami bulletin link was found.")
    bulletin_html = fetch_url(bulletin_url).decode("utf-8", errors="replace")
    text, _ = parse_html(bulletin_html)
    return parse_bulletin(text, bulletin_url)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, default=Path("phivolcs_tsunami_state.json"))
    parser.add_argument("--output", type=Path, default=Path("phivolcs_tsunami_latest.json"))
    parser.add_argument("--input-html", type=Path, help="Parse a saved bulletin HTML fixture.")
    parser.add_argument("--source-url", default="fixture://phivolcs-tsunami")
    parser.add_argument("--send-pushover", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        state = load_state(args.state_file)
        if args.input_html:
            text, _ = parse_html(args.input_html.read_text(encoding="utf-8"))
            result = parse_bulletin(text, args.source_url)
        else:
            result = fetch_live_result()
        output, updated_state = process_result(
            result,
            state,
            send=args.send_pushover,
        )
        write_json(args.output, output)
        if updated_state != state:
            write_json(args.state_file, updated_state)
        print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
        return 0
    except (OSError, PhivolcsError, PushoverError, ValueError) as error:
        print(f"Klaxon PHIVOLCS tsunami check stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
