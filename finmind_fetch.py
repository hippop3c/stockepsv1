#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incrementally update quarterly EPS for every currently traded Taiwan stock.

FinMind's ``TaiwanStockFinancialStatements`` EPS rows are already one-quarter
values. Older versions incorrectly subtracted adjacent quarters. This updater
stores the reported value unchanged in ``single`` and keeps ``cum`` only as a
backwards-compatible alias.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import requests

print = functools.partial(print, flush=True)

TAIPEI = dt.timezone(dt.timedelta(hours=8))

TOKEN = os.environ.get("FINMIND_TOKEN", "")
YEARS = int(os.environ.get("YEARS", "10"))
OVERLAP_YEARS = int(os.environ.get("EPS_OVERLAP_YEARS", "2"))
SLEEP = float(os.environ.get("SLEEP", "6.5"))
OUT = Path(os.environ.get("DATA_FILE", "finmind_data.json"))
API = "https://api.finmindtrade.com/api/v4/data"

TWSE = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_MAIN = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_ESB = "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics"
HEADERS = {"User-Agent": "stockepsv1-data-updater/2.0", "Accept": "application/json"}
MIN_UNIVERSE = int(os.environ.get("MIN_STOCK_UNIVERSE", "1000"))
MIN_MARKET_COUNTS = {
    "上市": int(os.environ.get("MIN_TWSE_STOCKS", "800")),
    "上櫃": int(os.environ.get("MIN_TPEX_STOCKS", "650")),
    "興櫃": int(os.environ.get("MIN_ESB_STOCKS", "150")),
}


def fm(dataset: str, **params: str) -> list[dict[str, Any]]:
    params["dataset"] = dataset
    if TOKEN:
        params["token"] = TOKEN
    response = requests.get(API, params=params, headers=HEADERS, timeout=60)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") not in (200, None):
        raise RuntimeError(payload.get("msg", "FinMind API error"))
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        raise RuntimeError("FinMind API returned a non-list data field")
    return rows


def is_common_stock(code: object) -> bool:
    value = str(code or "")
    # 00xx are funds/ETNs; 91xx are Taiwan depositary receipts rather than
    # domestic common shares and do not have comparable FinMind EPS records.
    return len(value) == 4 and value.isdigit() and not value.startswith(("00", "91"))


def load_existing() -> dict[str, Any]:
    try:
        with OUT.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def atomic_dump(payload: dict[str, Any]) -> None:
    temp = OUT.with_suffix(OUT.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
        handle.write("\n")
    os.replace(temp, OUT)


def current_universe(info: list[dict[str, Any]], existing: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Use current official quotes to exclude historical/delisted symbols.

    Validate every market independently.  A single TWSE response is large
    enough to pass a total-count check by itself, so accepting a partial fetch
    could otherwise erase every TPEx/ESB symbol from the next snapshot.
    """
    metadata: dict[str, dict[str, str]] = {}
    metadata_dates: dict[str, str] = {}
    for row in info:
        code = str(row.get("stock_id", ""))
        snapshot_date = str(row.get("date", ""))
        if is_common_stock(code) and snapshot_date >= metadata_dates.get(code, ""):
            metadata[code] = {
                "name": str(row.get("stock_name", "") or ""),
                "industry": str(row.get("industry_category", "") or ""),
            }
            metadata_dates[code] = snapshot_date

    sources = (
        (TWSE, "Code", "Name", "上市"),
        (TPEX_MAIN, "SecuritiesCompanyCode", "CompanyName", "上櫃"),
        (TPEX_ESB, "SecuritiesCompanyCode", "CompanyName", "興櫃"),
    )
    old_market = existing.get("market", {})
    old_names = existing.get("name", {})
    old_industries = existing.get("industry", {})
    if not isinstance(old_market, dict):
        old_market = {}
    if not isinstance(old_names, dict):
        old_names = {}
    if not isinstance(old_industries, dict):
        old_industries = {}
    old_active = existing.get("active_stock_ids")
    if isinstance(old_active, list):
        cached_codes = {str(code) for code in old_active if is_common_stock(code)}
    else:
        cached_codes = {str(code) for code in old_market if is_common_stock(code)}

    active: dict[str, dict[str, str]] = {}
    for url, code_key, name_key, market in sources:
        fresh: dict[str, dict[str, str]] = {}
        cached = {
            code: {
                "name": str(metadata.get(code, {}).get("name", old_names.get(code, ""))),
                "industry": str(metadata.get(code, {}).get("industry", old_industries.get(code, ""))),
                "market": market,
            }
            for code in cached_codes
            if old_market.get(code) == market
        }
        try:
            response = requests.get(url, headers=HEADERS, timeout=60)
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("response is not a list")
            for row in rows:
                code = str(row.get(code_key, ""))
                if is_common_stock(code):
                    fresh[code] = {
                        "name": str(row.get(name_key, "") or metadata.get(code, {}).get("name", "")),
                        "industry": metadata.get(code, {}).get("industry", ""),
                        "market": market,
                    }
            minimum = MIN_MARKET_COUNTS[market]
            # Also guard against a syntactically valid but truncated response.
            # The 80% comparison lets normal listings/delistings through while
            # rejecting catastrophic shrinkage relative to the last snapshot.
            required = max(minimum, int(len(cached) * 0.8))
            if len(fresh) < required:
                raise ValueError(f"only {len(fresh)} common stocks (need at least {required})")
            active.update(fresh)
        except Exception as exc:
            minimum = MIN_MARKET_COUNTS[market]
            if len(cached) >= minimum:
                print(
                    f"   warning: current-universe source {market} failed: {exc}; "
                    f"using {len(cached)} cached active stocks for this market"
                )
                active.update(cached)
            else:
                raise RuntimeError(
                    f"current-universe source {market} failed and has no safe cache "
                    f"({len(cached)} < {minimum}): {exc}"
                ) from exc

    if len(active) >= MIN_UNIVERSE:
        return active
    raise RuntimeError(f"current stock universe is implausibly small ({len(active)} < {MIN_UNIVERSE})")


def eps_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize FinMind EPS rows; one unique record per ISO quarter date."""
    by_date: dict[str, float] = {}
    for row in rows:
        if row.get("type") != "EPS":
            continue
        date = str(row.get("date", ""))[:10]
        if len(date) != 10:
            continue
        try:
            value = round(float(row["value"]), 2)
        except (KeyError, TypeError, ValueError):
            continue
        by_date[date] = value
    return [{"date": date, "single": value, "cum": value} for date, value in sorted(by_date.items())]


def normalize_old_rows(rows: object) -> list[dict[str, Any]]:
    """Repair the old subtraction bug from the retained raw ``cum`` value."""
    if not isinstance(rows, list):
        return []
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        raw = row.get("cum", row.get("single"))
        try:
            value = round(float(raw), 2)
        except (TypeError, ValueError):
            continue
        normalized.append({"date": str(row["date"])[:10], "single": value, "cum": value})
    return sorted({row["date"]: row for row in normalized}.values(), key=lambda row: row["date"])


def merge_eps(old: object, fresh: list[dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    merged = {row["date"]: row for row in normalize_old_rows(old) if row["date"] >= cutoff}
    merged.update({row["date"]: row for row in fresh if row["date"] >= cutoff})
    return [merged[key] for key in sorted(merged)]


def refresh_start(old: object, full_start: dt.date) -> str:
    rows = normalize_old_rows(old)
    if not rows:
        return full_start.isoformat()
    latest_year = int(rows[-1]["date"][:4])
    return max(full_start, dt.date(latest_year - OVERLAP_YEARS, 1, 1)).isoformat()


def main() -> int:
    if not TOKEN:
        print("error: FINMIND_TOKEN is required for a full-market refresh")
        return 2

    # The weekly workflow starts on Sunday UTC but Monday in Taiwan.  Stamp the
    # data with the Taiwan calendar date shown to users, not the runner's UTC
    # date.
    today = dt.datetime.now(TAIPEI).date()
    full_start = dt.date(today.year - YEARS, 1, 1)
    existing = load_existing()

    print("1/2 Fetching stock metadata and current official market universe ...")
    universe = current_universe(fm("TaiwanStockInfo"), existing)
    codes = sorted(universe)
    print(f"   current common stocks: {len(codes)}")

    old_financials = existing.get("financials", {})
    financials = {
        code: old_financials.get(code, [])
        for code in codes
        if isinstance(old_financials, dict) and old_financials.get(code)
    }
    old_checks = existing.get("eps_last_checked", {})
    checks = {
        code: old_checks[code]
        for code in codes
        if isinstance(old_checks, dict) and code in old_checks
    }
    errors: list[str] = []
    changed = 0
    print(f"2/2 Refreshing EPS with a {OVERLAP_YEARS}-year overlap ({len(codes)} requests) ...")
    for index, code in enumerate(codes, 1):
        old = financials.get(code, [])
        try:
            start = refresh_start(old, full_start)
            fresh = eps_rows(fm("TaiwanStockFinancialStatements", data_id=code, start_date=start))
            merged = merge_eps(old, fresh, full_start.isoformat())
            if merged:
                if merged != old:
                    changed += 1
                financials[code] = merged
            checks[code] = today.isoformat()
        except Exception as exc:
            errors.append(code)
            if len(errors) <= 10:
                print(f"   warning: {code}: {exc}")
        if index % 50 == 0 or index == len(codes):
            covered = sum(bool(financials.get(stock)) for stock in codes)
            print(f"   progress {index}/{len(codes)}; EPS={covered}, changed={changed}, errors={len(errors)}")
        if index < len(codes):
            time.sleep(SLEEP)

    names = {code: universe[code]["name"] for code in codes}
    industries = {code: universe[code]["industry"] for code in codes}
    markets = {code: universe[code]["market"] for code in codes}
    active_set = set(codes)
    # Keep all code-keyed caches aligned with the authoritative active
    # universe so delisted securities cannot linger in later snapshots.
    for key in (
        "ohlc", "ohlc_history", "target_price", "foreign_target_price",
        "foreign_target_price_history", "foreign_target_price_last_checked",
        "foreign_target_price_last_attempted",
    ):
        values = existing.get(key)
        if isinstance(values, dict):
            existing[key] = {code: value for code, value in values.items() if code in active_set}

    existing.update({
        "schema_version": 2,
        "updated": today.isoformat(),
        "eps_updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "eps_source": "FinMind TaiwanStockFinancialStatements (reported quarterly EPS)",
        "active_stock_ids": codes,
        "industry": industries,
        "name": names,
        "market": markets,
        "financials": financials,
        "eps_last_checked": checks,
    })
    atomic_dump(existing)
    print(f"done: {len(codes)} active stocks, {changed} changed, {len(errors)} request errors (old data retained)")
    if len(errors) > max(50, len(codes) // 4):
        print("error: more than 25% of EPS requests failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
