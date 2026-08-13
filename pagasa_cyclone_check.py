#!/usr/bin/env python3
"""Read PAGASA's current tropical-cyclone bulletin.

The result is deliberately JSON so a later morning-summary job can consume it
without needing to understand HTML or PDF parsing.  GitHub Actions wakes this
checker hourly, but the checker itself decides whether a fresh fetch is due:
daily when quiet, every three hours for a cyclone in PAR, and hourly when
Bohol is named in an official Tropical Cyclone Wind Signal area.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


SOURCE_URL = "https://www.pagasa.dost.gov.ph/tropical-cyclone/severe-weather-bulletin"
CAP_FEED_URL = "https://publicalert.pagasa.dost.gov.ph/feeds/"
BOHOL_TIMEZONE = "Asia/Manila"
USER_AGENT = "Klaxon/1.0 (+https://github.com/dhraban/klaxon)"


class PagasaError(RuntimeError):
    """Raised when PAGASA data cannot be read or interpreted."""


class LinkParser(HTMLParser):
    """Collect visible text and links without adding an HTML dependency."""

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._link_text)))
            self._href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.text_parts.append(data)
        if self._href is not None:
            self._link_text.append(data)


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_html(source: str) -> tuple[str, list[tuple[str, str]]]:
    parser = LinkParser()
    parser.feed(source)
    return normalized_text(" ".join(parser.text_parts)), parser.links


def first_match(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    return normalized_text(match.group(1)) if match else None


def extract_storm_name(text: str) -> str | None:
    match = re.search(
        r"\b(?:Super Typhoon|Severe Tropical Storm|Tropical Depression|"
        r"Tropical Storm|Typhoon)\s+[\"'“”‘’]?([A-Z][A-Z0-9-]*)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).upper() if match else None


def extract_issue_time(text: str) -> str | None:
    return first_match(
        r"\bIssued at\s+(.+?)(?=\s+Valid for broadcast|\s+Prepared by|$)",
        text,
    )


def extract_par_status(text: str, active: bool) -> str:
    if re.search(
        r"\b(?:outside|out of)\s+(?:the\s+)?(?:philippine area of responsibility|PAR)\b",
        text,
        re.IGNORECASE,
    ):
        return "outside"
    if re.search(
        r"\b(?:inside|within)\s+(?:the\s+)?(?:philippine area of responsibility|PAR)\b",
        text,
        re.IGNORECASE,
    ):
        return "inside"
    # PAGASA's active bulletin page is itself the active-in-PAR product.  Use
    # that as a conservative fallback only when the bulletin does not spell it
    # out in the visible text.
    return "inside" if active else "unknown"


FORECAST_PATTERN = re.compile(
    r"(?P<hours>\d{1,3}-Hour Forecast)\s+"
    r"(?P<valid>\d{1,2}:\d{2}\s+(?:AM|PM)\s+\d{1,2}\s+[A-Za-z]+\s+\d{4})\s+"
    r"(?P<latitude>\d{1,2}(?:\.\d+)?)\s+"
    r"(?P<longitude>\d{2,3}(?:\.\d+)?)\s+"
    r"(?P<details>.*?)(?=\s+\d{1,3}-Hour Forecast\b|\s+TROPICAL CYCLONE WIND SIGNALS?\b|$)",
    re.IGNORECASE,
)


def extract_forecast_positions(text: str) -> list[dict[str, object]]:
    positions: list[dict[str, object]] = []
    for match in FORECAST_PATTERN.finditer(text):
        positions.append(
            {
                "forecast_window": match.group("hours"),
                "valid_at": normalized_text(match.group("valid")),
                "latitude": float(match.group("latitude")),
                "longitude": float(match.group("longitude")),
                "details": normalized_text(match.group("details")),
            }
        )
    return positions


def extract_bohol_signal(text: str) -> tuple[bool, int | None, str | None]:
    signal_section_match = re.search(
        r"(?:TROPICAL CYCLONE WIND SIGNALS?|TCWS)[\s:]*(.*?)(?="
        r"\s+OTHER HAZARDS|\s+HAZARDS AFFECTING|\s+The next tropical cyclone bulletin|$)",
        text,
        re.IGNORECASE,
    )
    signal_section = signal_section_match.group(1) if signal_section_match else ""
    bohol_match = re.search(
        r"(?:TCWS\s*(?:No\.?\s*)?|Signal\s*(?:No\.?\s*)?)([1-5])[^.]{0,120}\bBohol\b|"
        r"\bBohol\b[^.]{0,120}(?:TCWS|Signal\s*(?:No\.?\s*)?([1-5]))",
        signal_section,
        re.IGNORECASE,
    )
    if not bohol_match:
        return False, None, None
    signal_number = int(bohol_match.group(1) or bohol_match.group(2))
    return True, signal_number, signal_section.strip()


def choose_bulletin_pdf(links: list[tuple[str, str]]) -> str | None:
    candidates: list[str] = []
    for href, label in links:
        combined = f"{href} {label}".lower()
        if ".pdf" in href.lower() and (
            "tcb" in combined or "bulletin" in combined or "tropical cyclone" in combined
        ):
            candidates.append(urljoin(SOURCE_URL, href))
    return candidates[-1] if candidates else None


def fetch_url(url: str, timeout: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as error:  # pragma: no cover - network-specific details
        raise PagasaError(f"PAGASA request failed for {url}: {error}") from error


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover - exercised in deployment
        raise PagasaError(
            "The active bulletin is a PDF, but pypdf is not installed."
        ) from error
    try:
        reader = PdfReader(__import__("io").BytesIO(pdf_bytes))
        return normalized_text(" ".join(page.extract_text() or "" for page in reader.pages))
    except Exception as error:  # pragma: no cover - malformed remote PDF
        raise PagasaError(f"PAGASA bulletin PDF could not be read: {error}") from error


def parse_cap_feed(cap_bytes: bytes) -> list[dict[str, str]]:
    """Return active CAP entries when PAGASA's official feed is available."""
    try:
        root = ET.fromstring(cap_bytes)
    except ET.ParseError as error:
        raise PagasaError(f"PAGASA CAP feed was not valid Atom/XML: {error}") from error
    entries: list[dict[str, str]] = []
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1] != "entry":
            continue
        values: dict[str, str] = {}
        for child in entry.iter():
            name = child.tag.rsplit("}", 1)[-1]
            if child.text and name in {"title", "summary", "updated", "id"}:
                values[name] = normalized_text(child.text)
        entries.append(values)
    return entries


def build_result(
    page_text: str,
    *,
    source_url: str = SOURCE_URL,
    pdf_text: str | None = None,
    bulletin_url: str | None = None,
    cap_feed_available: bool = False,
    checked_at: str | None = None,
) -> dict[str, object]:
    checked = checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    active = not bool(
        re.search(
            r"No Active Tropical Cyclone within the Philippine Area of Responsibility",
            page_text,
            re.IGNORECASE,
        )
    )
    if not active:
        return {
            "source": source_url,
            "checked_at_utc": checked,
            "active_cyclone": False,
            "storm_name": None,
            "par_status": "none_active",
            "issued_at": None,
            "forecast_positions": [],
            "bohol_under_wind_signal": False,
            "bohol_wind_signal": None,
            "cadence": "daily",
            "cap_feed_available": cap_feed_available,
            "bulletin_url": bulletin_url,
            "summary": "No active tropical cyclone within PAR.",
        }

    combined_text = normalized_text(f"{page_text} {pdf_text or ''}")
    storm_name = extract_storm_name(combined_text)
    par_status = extract_par_status(combined_text, bool(storm_name))
    bohol_affected, signal_number, _ = extract_bohol_signal(combined_text)
    in_par = par_status == "inside"
    if bohol_affected:
        cadence = "hourly"
    elif in_par:
        cadence = "every_3_hours"
    else:
        cadence = "daily"
    return {
        "source": source_url,
        "checked_at_utc": checked,
        "active_cyclone": bool(storm_name),
        "storm_name": storm_name,
        "par_status": par_status,
        "issued_at": extract_issue_time(combined_text),
        "forecast_positions": extract_forecast_positions(combined_text),
        "bohol_under_wind_signal": bohol_affected,
        "bohol_wind_signal": signal_number,
        "cadence": cadence,
        "cap_feed_available": cap_feed_available,
        "bulletin_url": bulletin_url,
        "summary": (
            f"{storm_name or 'Active cyclone'}: PAR status {par_status}; "
            f"Bohol wind signal {'yes' if bohol_affected else 'no'}; cadence {cadence}."
        ),
    }


def next_due_at(now: datetime, cadence: str) -> str:
    intervals = {
        "daily": timedelta(days=1),
        "every_3_hours": timedelta(hours=3),
        "hourly": timedelta(hours=1),
    }
    if cadence not in intervals:
        raise PagasaError(f"Unknown cadence: {cadence}")
    return (now + intervals[cadence]).isoformat().replace("+00:00", "Z")


def state_from_result(result: dict[str, object], now: datetime) -> dict[str, object]:
    """Convert a detector/monitor result into the persisted monitor state."""
    monitoring_enabled = bool(
        result.get("active_cyclone") and result.get("par_status") == "inside"
    )
    cadence = str(result.get("cadence", "daily"))
    return {
        "updated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "monitoring_enabled": monitoring_enabled,
        "active_cyclone": bool(result.get("active_cyclone")),
        "storm_name": result.get("storm_name"),
        "par_status": result.get("par_status"),
        "bohol_under_wind_signal": bool(result.get("bohol_under_wind_signal", False)),
        "bohol_wind_signal": result.get("bohol_wind_signal"),
        "cadence": cadence if monitoring_enabled else "daily",
        "next_monitor_check_at_utc": (
            next_due_at(now, cadence) if monitoring_enabled else None
        ),
    }


def read_state(state_path: Path) -> dict[str, object]:
    if not state_path.exists():
        return {}
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PagasaError(f"PAGASA state file could not be read: {error}") from error
    if not isinstance(value, dict):
        raise PagasaError("PAGASA state file must contain a JSON object.")
    return value


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fetch_live_result() -> dict[str, object]:
    page_bytes = fetch_url(SOURCE_URL)
    page_html = page_bytes.decode("utf-8", errors="replace")
    page_text, links = parse_html(page_html)
    page_has_no_active_cyclone = bool(
        re.search(
            r"No Active Tropical Cyclone within the Philippine Area of Responsibility",
            page_text,
            re.IGNORECASE,
        )
    )
    # The page keeps archive PDFs visible even when no cyclone is active. Do
    # not download or parse an old archive bulletin in that quiet state.
    bulletin_url = None if page_has_no_active_cyclone else choose_bulletin_pdf(links)
    pdf_text = extract_pdf_text(fetch_url(bulletin_url)) if bulletin_url else None
    cap_available = False
    try:
        parse_cap_feed(fetch_url(CAP_FEED_URL))
        cap_available = True
    except PagasaError:
        # The bulletin is the operational fallback; an unavailable CAP feed
        # must not prevent the morning result from being produced.
        pass
    return build_result(
        page_text,
        pdf_text=pdf_text,
        bulletin_url=bulletin_url,
        cap_feed_available=cap_available,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, default=Path("pagasa_state.json"))
    parser.add_argument("--output", type=Path, default=Path("pagasa_cyclone_latest.json"))
    parser.add_argument(
        "--mode",
        choices=("daily-detector", "monitor"),
        default="daily-detector",
        help="Run the daily detector or the separate elevated monitor.",
    )
    parser.add_argument("--html-file", type=Path, help="Parse a saved bulletin page for tests.")
    parser.add_argument("--pdf-file", type=Path, help="Add extracted text from a saved bulletin PDF.")
    parser.add_argument("--force", action="store_true", help="Fetch now even when the next check is not due.")
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        now = datetime.now(timezone.utc)
        state = read_state(args.state_file)
        if args.mode == "monitor" and not args.force:
            if not state.get("monitoring_enabled", False):
                result = {
                    "source": SOURCE_URL,
                    "checked_at_utc": now.isoformat().replace("+00:00", "Z"),
                    "checked": False,
                    "monitoring_enabled": False,
                    "active_cyclone": state.get("active_cyclone", False),
                    "storm_name": state.get("storm_name"),
                    "par_status": state.get("par_status", "unknown"),
                    "cadence": "daily",
                    "next_monitor_check_at_utc": None,
                    "summary": "Elevated PAGASA monitor is disabled; no PAGASA fetch was made.",
                }
                write_json(args.output, result)
                print(json.dumps(result, indent=2 if args.pretty else None))
                return 0
            due_at = state.get("next_monitor_check_at_utc")
            if isinstance(due_at, str):
                due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                if now < due:
                    result = {
                        "source": SOURCE_URL,
                        "checked_at_utc": now.isoformat().replace("+00:00", "Z"),
                        "checked": False,
                        "monitoring_enabled": True,
                        "active_cyclone": state.get("active_cyclone", True),
                        "storm_name": state.get("storm_name"),
                        "par_status": state.get("par_status", "inside"),
                        "cadence": state.get("cadence", "every_3_hours"),
                        "next_monitor_check_at_utc": due_at,
                        "summary": "Elevated monitor woke, but its next PAGASA fetch is not due yet.",
                    }
                    write_json(args.output, result)
                    print(json.dumps(result, indent=2 if args.pretty else None))
                    return 0

        if args.html_file:
            page_html = args.html_file.read_text(encoding="utf-8")
            page_text, links = parse_html(page_html)
            pdf_text = args.pdf_file.read_text(encoding="utf-8") if args.pdf_file else None
            result = build_result(
                page_text,
                pdf_text=pdf_text,
                bulletin_url=choose_bulletin_pdf(links),
            )
        else:
            result = fetch_live_result()
        result["checked"] = True
        result["monitoring_enabled"] = bool(
            result.get("active_cyclone") and result.get("par_status") == "inside"
        )
        result["next_monitor_check_at_utc"] = (
            next_due_at(now, str(result["cadence"]))
            if result["monitoring_enabled"]
            else None
        )
        result["monitor_mode"] = args.mode
        write_json(args.state_file, state_from_result(result, now))
        write_json(args.output, result)
        print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
        return 0
    except (OSError, PagasaError, ValueError) as error:
        print(f"Klaxon PAGASA check stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
