#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FinMind 抓取腳本（GitHub Actions 版 · 全市場逐檔 · 近 N 年）→ finmind_data.json

【為什麼逐檔】FinMind 免費版「不帶 data_id 查全市場」會被擋（需付費版）。
免費版只能帶 data_id 逐檔抓，因此本腳本：
  1. 先抓 TaiwanStockInfo 取得全市場個股清單（代號 + 名稱 + 類股）
  2. 逐檔查近 N 年財報 EPS，累計換算單季
自動限速（每筆 SLEEP 秒）壓在 600 requests/hr 以下。
全市場約 1800 檔 × 6.5 秒 ≈ 3.3 小時。

本機測試：export FINMIND_TOKEN=你的token && python finmind_fetch.py
GitHub：token 放 repo Secrets（FINMIND_TOKEN）。
"""

import requests, json, time, datetime, os, sys, functools
print = functools.partial(print, flush=True)   # 讓進度即時輸出（Actions log 才看得到）

# ====== 設定 ======
TOKEN = os.environ.get("FINMIND_TOKEN", "")
YEARS = int(os.environ.get("YEARS", "10"))       # 近幾年（10 = 40 季）
SLEEP = float(os.environ.get("SLEEP", "6.5"))    # 每筆間隔秒（6.5 → 約 554 req/hr）
OUT   = "finmind_data.json"
API   = "https://api.finmindtrade.com/api/v4/data"
# ==================

def fm(dataset, **params):
    params["dataset"] = dataset
    params["token"]   = TOKEN
    r = requests.get(API, params=params, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("status") not in (200, None) and not j.get("data"):
        raise RuntimeError(j.get("msg", "API error"))
    return j.get("data", [])

def cum_to_single(records):
    """累計 EPS → 單季。records 按日期 舊→新。"""
    out = []
    for i, r in enumerate(records):
        yr, mm = r["date"][:4], r["date"][5:7]
        if mm == "03":
            single = r["cum"]
        else:
            prev = records[i-1] if i > 0 else None
            single = (r["cum"] - prev["cum"]) if (prev and prev["date"][:4] == yr) else r["cum"]
        out.append({"date": r["date"], "cum": round(r["cum"], 2),
                    "single": round(single, 2)})
    return out

def main():
    if not TOKEN:
        print("錯誤：找不到 FINMIND_TOKEN 環境變數。")
        sys.exit(1)

    result = {"updated": datetime.date.today().isoformat(),
              "industry": {}, "name": {}, "financials": {}}

    # ① 全市場個股清單（代號為 4 位純數字、排除 00 開頭的 ETF/權證）
    print("① 抓全市場個股清單 TaiwanStockInfo ...")
    info = fm("TaiwanStockInfo")
    stocks = {}
    for d in info:
        sid = d.get("stock_id", "")
        if len(sid) == 4 and sid.isdigit() and not sid.startswith("00"):
            if sid not in stocks:
                stocks[sid] = {"name": d.get("stock_name", ""),
                               "industry": d.get("industry_category", "")}
    for sid, v in stocks.items():
        result["industry"][sid] = v["industry"]
        result["name"][sid]     = v["name"]
    codes = sorted(stocks.keys())
    print(f"   → 全市場個股 {len(codes)} 檔")

    # ② 逐檔抓財報 EPS
    start = f"{datetime.date.today().year - YEARS}-01-01"
    total = len(codes)
    print(f"② 逐檔抓財報 EPS（{total} 檔，近 {YEARS} 年，start_date={start}，每筆 {SLEEP}s）")
    print(f"   預估耗時約 {total*SLEEP/60:.0f} 分鐘（{total*SLEEP/3600:.1f} 小時）")
    fin, errs = {}, []
    for i, code in enumerate(codes):
        try:
            rows = fm("TaiwanStockFinancialStatements", data_id=code, start_date=start)
            recs = sorted(
                [{"date": r["date"], "cum": float(r["value"])}
                 for r in rows if r.get("type") == "EPS"],
                key=lambda x: x["date"])
            if recs:
                fin[code] = cum_to_single(recs)
        except Exception as e:
            errs.append(code)
        if (i + 1) % 50 == 0:
            print(f"   進度 {i+1}/{total}，有財報 {len(fin)}，失敗 {len(errs)}")
        time.sleep(SLEEP)

    result["financials"] = fin
    print(f"   → 完成：{len(fin)} 檔有財報，{len(errs)} 檔失敗/無資料")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n✅ 完成！輸出 {OUT}：個股 {len(codes)}、有財報 {len(fin)} 檔、近 {YEARS} 年")

if __name__ == "__main__":
    main()
