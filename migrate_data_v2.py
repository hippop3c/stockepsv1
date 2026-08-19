#!/usr/bin/env python3
"""One-time, offline migration of an existing finmind_data.json to schema v2."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

DATA = Path(os.environ.get("DATA_FILE", "finmind_data.json"))


def is_stock(code: object) -> bool:
    value = str(code or "")
    return len(value) == 4 and value.isdigit() and not value.startswith(("00", "91"))


def main() -> None:
    with DATA.open(encoding="utf-8") as handle:
        data = json.load(handle)

    repaired = 0
    financials = data.get("financials", {})
    for code, rows in financials.items():
        if not isinstance(rows, list):
            continue
        clean = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("date"):
                continue
            raw = row.get("cum", row.get("single"))
            try:
                value = round(float(raw), 2)
            except (TypeError, ValueError):
                continue
            normalized = {"date": str(row["date"])[:10], "single": value, "cum": value}
            if normalized != row:
                repaired += 1
            clean.append(normalized)
        financials[code] = sorted({row["date"]: row for row in clean}.values(), key=lambda row: row["date"])

    price_date = str(data.get("price_updated", ""))[:10]
    latest = {}
    for code, row in data.get("ohlc", {}).items():
        if not is_stock(code) or not isinstance(row, dict):
            continue
        normalized = {}
        for key in ("o", "h", "l", "c"):
            try:
                value = float(row.get(key)) if row.get(key) is not None else None
            except (TypeError, ValueError):
                value = None
            normalized[key] = value if value is not None and value > 0 else None
        if all(value is None for value in normalized.values()):
            continue
        if price_date:
            normalized["date"] = price_date
        latest[code] = normalized

    data["schema_version"] = 2
    active = sorted(code for code in data.get("market", {}) if is_stock(code))
    data["financials"] = {code: financials[code] for code in active if financials.get(code)}
    data["ohlc"] = latest
    for key in ("name", "industry", "market", "eps_last_checked"):
        values = data.get(key)
        if isinstance(values, dict):
            data[key] = {code: values[code] for code in active if code in values}
    for key in (
        "target_price", "foreign_target_price", "foreign_target_price_history",
        "foreign_target_price_last_checked", "foreign_target_price_last_attempted",
    ):
        values = data.get(key)
        if isinstance(values, dict):
            data[key] = {code: value for code, value in values.items() if code in set(active)}
    if price_date:
        data["ohlc_history"] = {code: [row] for code, row in latest.items()}
    data["active_stock_ids"] = active

    # Optional one-time correction for snapshots produced by the old weekly
    # workflow, which stamped Sunday UTC although it was already Monday in
    # Taiwan.  The normal updater now uses Taiwan time directly.
    eps_updated_date = os.environ.get("EPS_UPDATED_DATE", "").strip()
    eps_updated_at = os.environ.get("EPS_UPDATED_AT", "").strip()
    if eps_updated_date:
        dt.date.fromisoformat(eps_updated_date)
        data["updated"] = eps_updated_date
    if eps_updated_at:
        dt.datetime.fromisoformat(eps_updated_at.replace("Z", "+00:00"))
        data["eps_updated_at"] = eps_updated_at

    temp = DATA.with_suffix(DATA.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    os.replace(temp, DATA)
    print(f"migrated schema v2: repaired {repaired} EPS rows; latest OHLC={len(latest)}")


if __name__ == "__main__":
    main()
