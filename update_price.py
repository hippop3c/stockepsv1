#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update latest official-market OHLC and retain a bounded daily history."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import requests

OUT = Path(os.environ.get("DATA_FILE", "finmind_data.json"))
TWSE = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_MAIN = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_ESB = "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics"
HEADERS = {"User-Agent": "stockepsv1-data-updater/2.0", "Accept": "application/json"}
MIN_TOTAL = int(os.environ.get("MIN_PRICE_TOTAL", "1000"))
HISTORY_DAYS = int(os.environ.get("PRICE_HISTORY_DAYS", "30"))
MIN_SOURCE_COUNTS = {
    "TWSE": int(os.environ.get("MIN_TWSE_QUOTES", "800")),
    "TPEX": int(os.environ.get("MIN_TPEX_QUOTES", "650")),
    "ESB": int(os.environ.get("MIN_ESB_QUOTES", "150")),
}


def num(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in ("", "--", "---", "N/A", "null", "除權", "除息", "除權息"):
        return None
    try:
        number = float(text)
        # A zero quote is the exchange's no-trade placeholder, not a price.
        return number if number > 0 else None
    except ValueError:
        return None


def trading_date(value: object) -> str | None:
    """Convert TWSE/TPEx ROC ``1150818`` (or ISO) to ``2026-08-18``."""
    text = str(value or "").strip().replace("/", "").replace("-", "")
    try:
        if len(text) == 7 and text.isdigit():
            return dt.date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7])).isoformat()
        if len(text) == 8 and text.isdigit():
            return dt.date(int(text[:4]), int(text[4:6]), int(text[6:8])).isoformat()
    except ValueError:
        return None
    return None


def fetch(url: str, tag: str, retries: int = 3) -> list[dict[str, Any]]:
    last = ""
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=60)
            response.raise_for_status()
            rows = response.json()
            if isinstance(rows, list) and rows:
                return rows
            last = "empty/non-list response"
        except Exception as exc:
            last = str(exc)
        if attempt < retries - 1:
            print(f"   retry {tag} after failure: {last}", flush=True)
            time.sleep(5)
    print(f"   warning: {tag} failed after {retries} attempts: {last}", flush=True)
    return []


def quote(code: str, date: str, o: object, h: object, low: object, c: object) -> dict[str, Any]:
    return {"date": date, "o": num(o), "h": num(h), "l": num(low), "c": num(c)}


def pick(row: dict[str, Any], *candidates: str) -> object:
    """Case-insensitive field fallback for occasional TPEx schema renames."""
    lower = {str(key).lower(): key for key in row}
    for candidate in candidates:
        key = lower.get(candidate.lower())
        if key is not None:
            return row.get(key)
    return None


def collect() -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, str], dict[str, int]]:
    latest: dict[str, dict[str, Any]] = {}
    markets: dict[str, str] = {}
    source_dates: dict[str, str] = {}
    source_counts: dict[str, int] = {}

    specs = (
        ("TWSE", TWSE, ("Code",), ("OpeningPrice", "Open"), ("HighestPrice", "High"), ("LowestPrice", "Low"), ("ClosingPrice", "Close"), "上市"),
        ("TPEX", TPEX_MAIN, ("SecuritiesCompanyCode", "Code"), ("Open", "OpeningPrice"), ("High", "Highest"), ("Low", "Lowest"), ("Close", "LatestPrice"), "上櫃"),
        # Emerging quotes do not publish an opening-price field. It remains null;
        # high/low/latest are retained instead of fabricating an OHLC candle.
        ("ESB", TPEX_ESB, ("SecuritiesCompanyCode", "Code"), (), ("Highest", "High"), ("Lowest", "Low"), ("LatestPrice", "Close", "Average"), "興櫃"),
    )
    for tag, url, code_keys, open_keys, high_keys, low_keys, close_keys, market in specs:
        rows = fetch(url, tag)
        count = 0
        dates: list[str] = []
        for row in rows:
            code = str(pick(row, *code_keys) or "")
            # Four-digit company shares only. This intentionally excludes ETF,
            # ETN and bond codes (normally 00xx/006xx), which do not have EPS.
            if len(code) != 4 or not code.isdigit() or code.startswith(("00", "91")):
                continue
            date = trading_date(row.get("Date"))
            if not date:
                continue
            item = quote(
                code,
                date,
                pick(row, *open_keys),
                pick(row, *high_keys),
                pick(row, *low_keys),
                pick(row, *close_keys),
            )
            if all(item[key] is None for key in ("o", "h", "l", "c")):
                continue
            latest[code] = item
            markets[code] = market
            dates.append(date)
            count += 1
        if dates:
            source_dates[tag] = max(dates)
        source_counts[tag] = count
        print(f"   {tag}: {count} securities; trading date={source_dates.get(tag, 'unknown')}", flush=True)
    return latest, markets, source_dates, source_counts


def load() -> dict[str, Any]:
    try:
        with OUT.open(encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def atomic_dump(data: dict[str, Any]) -> None:
    temp = OUT.with_suffix(OUT.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    os.replace(temp, OUT)


def merge_history(
    old: object,
    latest: dict[str, dict[str, Any]],
    max_date: str,
    active: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    history = dict(old) if isinstance(old, dict) else {}
    cutoff = (dt.date.fromisoformat(max_date) - dt.timedelta(days=HISTORY_DAYS)).isoformat()
    for code, row in latest.items():
        prior = history.get(code, [])
        by_date = {
            str(item.get("date")): item
            for item in prior
            if isinstance(item, dict) and str(item.get("date", "")) >= cutoff
        } if isinstance(prior, list) else {}
        by_date[row["date"]] = row
        history[code] = [by_date[key] for key in sorted(by_date)]
    # Prune inactive symbols too, preventing unbounded JSON growth.
    for code in list(history):
        if active is not None and code not in active:
            del history[code]
            continue
        rows = history[code]
        if not isinstance(rows, list):
            del history[code]
            continue
        kept = [row for row in rows if isinstance(row, dict) and str(row.get("date", "")) >= cutoff]
        if kept:
            history[code] = kept
        else:
            del history[code]
    return history


def main() -> int:
    print("Fetching official latest OHLC ...", flush=True)
    latest, markets, source_dates, source_counts = collect()
    bad_sources = [
        tag for tag, minimum in MIN_SOURCE_COUNTS.items()
        if source_counts.get(tag, 0) < minimum or tag not in source_dates
    ]
    if len(latest) < MIN_TOTAL or bad_sources:
        print(
            f"error: incomplete quote snapshot ({len(latest)} total; bad sources={bad_sources}); "
            "refusing to overwrite cached data",
            flush=True,
        )
        return 1

    max_date = max(source_dates.values())
    old = load()
    listed = old.get("active_stock_ids", [])
    active = {
        str(code) for code in listed
        if len(str(code)) == 4 and str(code).isdigit() and not str(code).startswith(("00", "91"))
    } if isinstance(listed, list) else set()
    if not active:
        print("error: no active stock universe; refusing to merge quotes", flush=True)
        return 1
    latest = {code: row for code, row in latest.items() if code in active}
    markets = {code: market for code, market in markets.items() if code in active}
    merged_latest = {
        code: row for code, row in old.get("ohlc", {}).items()
        if code in active and isinstance(row, dict)
    }
    merged_latest.update(latest)
    merged_markets = {
        code: market for code, market in old.get("market", {}).items()
        if code in active
    }
    merged_markets.update(markets)

    old.update({
        "schema_version": 2,
        "ohlc": merged_latest,
        "ohlc_history": merge_history(old.get("ohlc_history", {}), latest, max_date, active),
        "market": merged_markets,
        # This is the actual market date, not the workflow execution date.
        "price_updated": max_date,
        "price_source_dates": source_dates,
        "price_fetched_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    })
    atomic_dump(old)
    print(f"done: updated {len(latest)} latest quotes; history retained for {HISTORY_DAYS} calendar days", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
