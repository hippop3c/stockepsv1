#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Best-effort incremental foreign-broker target-price collector.

There is no official TWSE/TPEx target-price feed.  This script reads the public
``外資評等`` table on Cnyes, records provenance for every value, rotates through
the active universe, and never deletes good cached data when a page fails.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

import requests

OUT = Path(os.environ.get("DATA_FILE", "finmind_data.json"))
TAIPEI = dt.timezone(dt.timedelta(hours=8))
MAX_STOCKS = int(os.environ.get("TARGET_PRICE_MAX_STOCKS", "240"))
WORKERS = int(os.environ.get("TARGET_PRICE_WORKERS", "1"))
REQUEST_DELAY = float(os.environ.get("TARGET_PRICE_REQUEST_DELAY", "1.0"))
TIMEOUT = float(os.environ.get("TARGET_PRICE_TIMEOUT", "30"))
HISTORY_DAYS = int(os.environ.get("TARGET_PRICE_HISTORY_DAYS", "365"))
BASE_URL = "https://www.cnyes.com/twstock/foreignrating.aspx?code={code}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stockepsv1/2.0)", "Accept": "text/html"}


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows = []
        elif self._depth == 1 and tag == "tr":
            self._row = []
        elif self._depth == 1 and tag in ("td", "th"):
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._depth == 1 and tag in ("td", "th") and self._cell is not None:
            assert self._row is not None
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif self._depth == 1 and tag == "tr" and self._row is not None:
            if self._row and self._rows is not None:
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            if self._depth == 1 and self._rows is not None:
                self.tables.append(self._rows)
                self._rows = None
            self._depth -= 1


def parse_target_page(html: str, code: str) -> tuple[bool, list[dict[str, Any]]]:
    """Return whether the expected rating table exists plus its usable rows.

    An empty, recognized table is a valid "no target price" result.  A HTTP
    200 challenge page or future layout change is not, and must fail the run
    instead of being stamped as a successful scan.
    """
    parser = TableParser()
    parser.feed(html)
    url = BASE_URL.format(code=code)
    parsed: dict[tuple[str, str, float], dict[str, Any]] = {}
    found_table = False
    for table in parser.tables:
        if not table or "評等日期" not in table[0] or "目標價" not in table[0]:
            continue
        found_table = True
        headers = table[0]
        positions = {name: headers.index(name) for name in ("評等日期", "券商", "新評等", "目標價")}
        for cells in table[1:]:
            if len(cells) <= max(positions.values()):
                continue
            raw_date = re.sub(r"\D", "", cells[positions["評等日期"]])
            raw_target = cells[positions["目標價"]].replace(",", "").strip()
            if len(raw_date) != 8 or raw_target in ("", "--", "-"):
                continue
            try:
                date = dt.datetime.strptime(raw_date, "%Y%m%d").date().isoformat()
                target = float(raw_target)
            except ValueError:
                continue
            if target <= 0:
                continue
            broker = cells[positions["券商"]]
            row = {
                "date": date,
                "target": target,
                "broker": broker,
                "rating": cells[positions["新評等"]],
                "currency": "TWD",
                "source": "Cnyes 外資評等",
                "source_url": url,
            }
            parsed[(date, broker, target)] = row
    rows = sorted(parsed.values(), key=lambda row: (row["date"], row["broker"], row["target"]), reverse=True)
    return found_table, rows


def parse_target_rows(html: str, code: str) -> list[dict[str, Any]]:
    """Compatibility wrapper used by callers that only need parsed rows."""
    return parse_target_page(html, code)[1]


def fetch_code(code: str) -> tuple[str, list[dict[str, Any]] | None, str | None]:
    try:
        response = requests.get(BASE_URL.format(code=code), headers=HEADERS, timeout=TIMEOUT)
        if response.status_code in (403, 429):
            return code, None, f"SOURCE_BLOCKED_HTTP_{response.status_code}"
        response.raise_for_status()
        found_table, rows = parse_target_page(response.text, code)
        if not found_table:
            return code, None, "SOURCE_FORMAT_UNRECOGNIZED"
        return code, rows, None
    except Exception as exc:
        return code, None, str(exc)


def load() -> dict[str, Any]:
    try:
        with OUT.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def atomic_dump(data: dict[str, Any]) -> None:
    temp = OUT.with_suffix(OUT.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    os.replace(temp, OUT)


def active_codes(data: dict[str, Any]) -> list[str]:
    codes = data.get("active_stock_ids")
    if isinstance(codes, list) and codes:
        return sorted(
            str(code) for code in codes
            if len(str(code)) == 4 and str(code).isdigit() and not str(code).startswith(("00", "91"))
        )
    market = data.get("market", {})
    if isinstance(market, dict):
        return sorted(
            code for code in market
            if len(code) == 4 and code.isdigit() and not code.startswith(("00", "91"))
        )
    return []


def main() -> int:
    data = load()
    codes = active_codes(data)
    if not codes:
        print("error: no active stock universe in data file", flush=True)
        return 2

    active = set(codes)
    scans = {
        code: value for code, value in data.get("foreign_target_price_last_checked", {}).items()
        if code in active
    }
    attempts = {
        code: value for code, value in data.get("foreign_target_price_last_attempted", scans).items()
        if code in active
    }
    # Empty/unseen and oldest attempts go first, so failures cannot starve later codes.
    selected = sorted(codes, key=lambda code: (str(attempts.get(code, "")), code))[:MAX_STOCKS]
    today_date = dt.datetime.now(TAIPEI).date()
    today = today_date.isoformat()
    cutoff = (today_date - dt.timedelta(days=HISTORY_DAYS)).isoformat()
    raw_history = data.get("foreign_target_price_history", {})
    history: dict[str, list[dict[str, Any]]] = {}
    if isinstance(raw_history, dict):
        for code, prior in raw_history.items():
            if code not in active or not isinstance(prior, list):
                continue
            kept = [
                row for row in prior
                if isinstance(row, dict) and str(row.get("date", "")) >= cutoff
            ]
            if kept:
                history[code] = kept
    raw_latest = data.get("target_price", data.get("foreign_target_price", {}))
    latest = {
        code: row for code, row in raw_latest.items()
        if code in active and isinstance(row, dict) and str(row.get("date", "")) >= cutoff
    } if isinstance(raw_latest, dict) else {}
    errors: list[tuple[str, str]] = []
    found = 0
    valid_pages = 0

    print(f"Scanning {len(selected)}/{len(codes)} stocks for public foreign-rating target prices ...", flush=True)
    source_blocked = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, WORKERS)) as pool:
        for offset in range(0, len(selected), max(1, WORKERS)):
            batch = selected[offset:offset + max(1, WORKERS)]
            batch_format_errors = 0
            for code, rows, error in pool.map(fetch_code, batch):
                attempts[code] = today
                if error is not None:
                    errors.append((code, error))
                    source_blocked = source_blocked or error.startswith("SOURCE_BLOCKED_")
                    batch_format_errors += error == "SOURCE_FORMAT_UNRECOGNIZED"
                    continue
                valid_pages += 1
                scans[code] = today
                prior = history.get(code, [])
                combined: dict[tuple[str, str, float], dict[str, Any]] = {}
                if isinstance(prior, list):
                    for row in prior:
                        if isinstance(row, dict) and str(row.get("date", "")) >= cutoff:
                            try:
                                combined[(str(row["date"]), str(row.get("broker", "")), float(row["target"]))] = row
                            except (KeyError, TypeError, ValueError):
                                pass
                for row in rows or []:
                    if row["date"] >= cutoff:
                        combined[(row["date"], row["broker"], row["target"])] = row
                merged = sorted(combined.values(), key=lambda row: (row["date"], row.get("broker", ""), row["target"]), reverse=True)
                if merged:
                    history[code] = merged
                    best = merged[0]
                    latest[code] = {
                        **best,
                        "value": best["target"],
                        "price": best["target"],
                        "institution": best["broker"],
                        "url": best["source_url"],
                    }
                    found += 1
                else:
                    history.pop(code, None)
                    latest.pop(code, None)
            if source_blocked or (batch and batch_format_errors == len(batch)):
                reason = "403/429" if source_blocked else "an unrecognized page format"
                print(f"source returned {reason}; stopping this run and retaining cached values", flush=True)
                break
            if offset + len(batch) < len(selected) and REQUEST_DELAY > 0:
                # Public fallback source: keep the scheduled collector polite.
                time.sleep(REQUEST_DELAY)

    if valid_pages == 0:
        print("error: no Cnyes rating page passed structural validation; update date was not changed", flush=True)
        return 1

    data.update({
        "schema_version": 2,
        "target_price": latest,
        # Explicit alias retained for data consumers that prefer the longer name.
        "foreign_target_price": latest,
        "foreign_target_price_history": history,
        "foreign_target_price_last_checked": scans,
        "foreign_target_price_last_attempted": attempts,
        "foreign_target_price_updated": today,
        "target_price_updated": today,
        "foreign_target_price_last_successful_scan_count": valid_pages,
        "foreign_target_price_source_note": "Best-effort public Cnyes 外資評等 data; values are forecasts, not exchange data.",
    })
    atomic_dump(data)
    print(f"done: {found} selected stocks have target prices; errors={len(errors)}; total cached={len(latest)}", flush=True)
    for code, error in errors[:10]:
        print(f"   warning: {code}: {error}", flush=True)
    # A few transient page failures are expected, but surface a blocked source.
    return 1 if len(errors) > max(20, len(selected) // 2) else 0


if __name__ == "__main__":
    sys.exit(main())
