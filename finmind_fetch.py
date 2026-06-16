#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FinMind 抓取腳本（GitHub Actions 版）→ 輸出 finmind_data.json

由 GitHub Actions 定時執行：抓 FinMind 資料、累計 EPS 換算單季、輸出 JSON 後
commit 回 repo。GitHub Pages 上的 index.html 會自動讀同源的 finmind_data.json。
→ 完全沒有 CORS 問題、token 藏在 GitHub Secrets 不外流、全自動更新。

本機測試也可跑：先 export FINMIND_TOKEN=你的token 再 python finmind_fetch.py

抓取內容：
  1. 類股（TaiwanStockInfo）                          —— 1 req
  2. 最近交易日全市場收盤價（TaiwanStockPrice）        —— 1~7 req
  3. 近 N 年全市場財報 EPS（FinancialStatements，一季一抓）—— 約 4*N req
     並自動把「累計 EPS」換算成「單季 EPS」
"""

import requests, json, time, datetime, os, sys

# ====== 設定 ======
TOKEN = os.environ.get("FINMIND_TOKEN", "")   # 從環境變數 / GitHub Secrets 讀取
YEARS = int(os.environ.get("YEARS", "3"))     # 抓近幾年財報（3 年 = 12 季）
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

def quarter_end_dates(years):
    today = datetime.date.today()
    y = today.year
    ends = ["03-31", "06-30", "09-30", "12-31"]
    out = []
    for yy in range(y - years, y + 1):
        for e in ends:
            d = f"{yy}-{e}"
            if d <= today.isoformat():
                out.append(d)
    return out[-years * 4:]

def cum_to_single(records):
    """累計 EPS → 單季。records 按日期 舊→新。
       Q1=Q1；其餘 = 本期累計 − 同年度前一期累計；跨年度重新起算。"""
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
        print("本機測試：export FINMIND_TOKEN=你的token")
        print("GitHub：到 repo Settings → Secrets → Actions 新增 FINMIND_TOKEN")
        sys.exit(1)

    result = {"updated": datetime.date.today().isoformat(),
              "industry": {}, "price": {}, "financials": {}}

    print("① 類股 TaiwanStockInfo ...")
    for d in fm("TaiwanStockInfo"):
        if d.get("stock_id") and d.get("industry_category"):
            result["industry"][d["stock_id"]] = d["industry_category"]
    print(f"   → {len(result['industry'])} 檔產業別")

    print("② 最近交易日全市場收盤價 ...")
    for i in range(7):
        ds = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
        rows = fm("TaiwanStockPrice", start_date=ds, end_date=ds)
        if len(rows) > 50:
            for d in rows:
                result["price"][d["stock_id"]] = d.get("close")
            print(f"   → {ds} 取得 {len(rows)} 檔收盤價")
            break
        time.sleep(0.3)

    dates = quarter_end_dates(YEARS)
    print(f"③ {YEARS} 年（{len(dates)} 季）全市場財報 EPS ...")
    fin = {}
    for d in dates:
        rows = fm("TaiwanStockFinancialStatements", start_date=d, end_date=d)
        cnt = 0
        for row in rows:
            if row.get("type") == "EPS":
                fin.setdefault(row["stock_id"], []).append(
                    {"date": row["date"], "cum": float(row["value"])})
                cnt += 1
        print(f"   → {d} 取得 {cnt} 檔 EPS")
        time.sleep(0.5)

    for code in fin:
        fin[code].sort(key=lambda x: x["date"])
        fin[code] = cum_to_single(fin[code])
    result["financials"] = fin
    print(f"   → 共 {len(fin)} 檔有財報資料")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n✅ 完成！輸出 {OUT}：類股 {len(result['industry'])}、"
          f"股價 {len(result['price'])}、財報 {len(fin)} 檔")

if __name__ == "__main__":
    main()
