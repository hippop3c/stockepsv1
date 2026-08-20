#!/usr/bin/env python3
"""Fail CI before committing a structurally incomplete/corrupt data snapshot."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path
import sys

DATA = Path(os.environ.get("DATA_FILE", "finmind_data.json"))
MIN_STOCKS = int(os.environ.get("MIN_STOCK_UNIVERSE", "1000"))
TAIPEI = dt.timezone(dt.timedelta(hours=8))


def fail(message: str) -> None:
    raise ValueError(message)


def iso_date(value: object, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{label}: invalid ISO date {value!r}") from exc


def finite_number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        fail(f"{label}: boolean is not a numeric value")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid number {value!r}") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        fail(f"{label}: invalid number {value!r}")
    return number


def validate_quote(code: str, row: object, latest_date: dt.date, label: str) -> None:
    if not isinstance(row, dict) or not row.get("date"):
        fail(f"{code}: {label} lacks its trading date")
    row_date = iso_date(row["date"], f"{code} {label}")
    if row_date > latest_date:
        fail(f"{code}: {label} date is later than price_updated")
    values: dict[str, float | None] = {}
    for key in ("o", "h", "l", "c"):
        raw = row.get(key)
        values[key] = None if raw is None else finite_number(raw, f"{code} {label} {key}", positive=True)
    if all(value is None for value in values.values()):
        fail(f"{code}: empty {label}")
    high, low = values["h"], values["l"]
    if high is not None and low is not None and high < low:
        fail(f"{code}: {label} high is below low")
    for key in ("o", "c"):
        value = values[key]
        if value is not None and high is not None and value > high:
            fail(f"{code}: {label} {key} is above high")
        if value is not None and low is not None and value < low:
            fail(f"{code}: {label} {key} is below low")


def main() -> int:
    with DATA.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if int(data.get("schema_version", 0)) < 2:
        fail("schema_version must be >= 2")

    today = dt.datetime.now(TAIPEI).date()
    active = data.get("active_stock_ids", [])
    if not isinstance(active, list) or len(active) < MIN_STOCKS or len(active) != len(set(active)):
        fail("active_stock_ids is missing, too small, or contains duplicates")
    if any(not isinstance(code, str) or len(code) != 4 or not code.isdigit() or code.startswith(("00", "91")) for code in active):
        fail("active_stock_ids contains a non-company security")
    active_set = set(active)
    for key in ("name", "industry", "market"):
        values = data.get(key)
        if not isinstance(values, dict) or set(values) != active_set:
            fail(f"{key} keys do not exactly match active_stock_ids")

    financials = data.get("financials", {})
    if not isinstance(financials, dict) or any(code not in active_set for code in financials):
        fail("financials contains an inactive security or is not an object")
    covered = 0
    for code in active:
        rows = financials.get(code, [])
        if not rows:
            continue
        covered += 1
        if not isinstance(rows, list):
            fail(f"{code}: EPS records are not a list")
        dates = [row.get("date") if isinstance(row, dict) else None for row in rows]
        if dates != sorted(set(dates)):
            fail(f"{code}: EPS dates are duplicate or unsorted")
        for row in rows:
            if not isinstance(row, dict):
                fail(f"{code}: EPS record is not an object")
            report_date = iso_date(row.get("date"), f"{code} EPS")
            if report_date > today or (report_date.month, report_date.day) not in ((3, 31), (6, 30), (9, 30), (12, 31)):
                fail(f"{code} {report_date}: EPS date is not a valid completed quarter")
            if row.get("single") != row.get("cum"):
                fail(f"{code} {row.get('date')}: single EPS differs from reported EPS alias")
            finite_number(row.get("single"), f"{code} {row.get('date')} EPS")
    if covered < len(active) * 0.90:
        fail(f"EPS coverage too low: {covered}/{len(active)}")

    ohlc = data.get("ohlc", {})
    if not isinstance(ohlc, dict) or len(ohlc) < MIN_STOCKS:
        fail("latest OHLC universe is implausibly small")
    if any(code not in active_set for code in ohlc):
        fail("latest OHLC contains an inactive or non-company security")
    if len(ohlc) < len(active) * 0.90:
        fail(f"OHLC coverage too low: {len(ohlc)}/{len(active)}")
    price_updated = iso_date(data.get("price_updated"), "price_updated")
    if price_updated > today:
        fail("price_updated is in the future")
    for code, row in ohlc.items():
        validate_quote(code, row, price_updated, "latest OHLC")
    source_dates = data.get("price_source_dates")
    if not isinstance(source_dates, dict) or set(source_dates) != {"TWSE", "TPEX", "ESB"}:
        fail("price_source_dates must contain TWSE, TPEX, and ESB")
    for source, value in source_dates.items():
        if iso_date(value, f"price_source_dates.{source}") != price_updated:
            fail(f"price_source_dates.{source} does not match price_updated")

    history = data.get("ohlc_history", {})
    if not isinstance(history, dict) or any(code not in active_set for code in history):
        fail("ohlc_history contains an inactive security or is not an object")
    for code, rows in history.items():
        if not isinstance(rows, list):
            fail(f"{code}: OHLC history is not a list")
        dates = [row.get("date") if isinstance(row, dict) else None for row in rows]
        if dates != sorted(set(dates)):
            fail(f"{code}: OHLC history dates are duplicate or unsorted")
        for row in rows:
            validate_quote(code, row, price_updated, "OHLC history")

    targets = data.get("target_price", {})
    if not isinstance(targets, dict):
        fail("target_price must be an object")
    if targets != data.get("foreign_target_price", {}):
        fail("foreign_target_price alias differs from target_price")
    if any(code not in active_set for code in targets):
        fail("target_price contains an inactive security")
    for code, row in targets.items():
        if not isinstance(row, dict):
            fail(f"{code}: target price is not an object")
        if not all(row.get(key) not in (None, "") for key in ("price", "institution", "source", "date", "url")):
            fail(f"{code}: target price is missing provenance")
        finite_number(row["price"], f"{code} target price", positive=True)
        target_date = iso_date(row["date"], f"{code} target price")
        if target_date > today:
            fail(f"{code}: target price date is in the future")
        if not str(row["url"]).startswith(("https://", "http://")):
            fail(f"{code}: target price URL is invalid")

    target_history = data.get("foreign_target_price_history", {})
    if not isinstance(target_history, dict) or any(code not in active_set for code in target_history):
        fail("foreign_target_price_history contains an inactive security or is not an object")
    for code, rows in target_history.items():
        if not isinstance(rows, list):
            fail(f"{code}: target price history is not a list")
        for row in rows:
            if not isinstance(row, dict) or not all(row.get(key) not in (None, "") for key in ("date", "target", "broker", "source", "source_url")):
                fail(f"{code}: target price history lacks value or provenance")
            finite_number(row["target"], f"{code} target price history", positive=True)
            history_date = iso_date(row["date"], f"{code} target price history")
            if history_date > today:
                fail(f"{code}: target price history date is in the future")

    if data.get("target_price_updated"):
        if iso_date(data["target_price_updated"], "target_price_updated") > today:
            fail("target_price_updated is in the future")
        if data.get("foreign_target_price_updated") != data["target_price_updated"]:
            fail("foreign target update-date alias differs")

    print(f"valid schema v2: active={len(active)}, EPS={covered}, OHLC={len(ohlc)}, targets={len(targets)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
