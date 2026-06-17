#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市場開高低收（OHLC）更新腳本 → 更新 finmind_data.json 的 ohlc 欄

用證交所 TWSE + 櫃買 TPEX 官方 openapi，一次抓全市場最近交易日的開高低收，
免費、不用 token、幾秒完成。讀現有 finmind_data.json（財報那份），
只更新/新增 ohlc 欄後寫回 → 不用重抓 4 小時的財報。

資料來源：
  TWSE 上市：https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
  TPEX 上櫃：https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes
（openapi 為最近交易日資料，盤後隔日更新；對本益比參考足夠）
"""

import requests, json, datetime, sys

OUT = "finmind_data.json"
TWSE = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def num(s):
    """把 '1,234.5' / '--' / '' 轉成 float 或 None"""
    if s is None: return None
    s = str(s).replace(",", "").strip()
    if s in ("", "--", "---", "N/A", "null"): return None
    try: return float(s)
    except: return None

def find_key(keys, candidates):
    for c in candidates:
        for k in keys:
            if c.lower() == k.lower(): return k      # 先找完全相同
    for c in candidates:
        for k in keys:
            if c.lower() in k.lower(): return k      # 再找包含
    return None

def main():
    ohlc = {}

    # ① TWSE 上市（欄位名已知：Code/OpeningPrice/HighestPrice/LowestPrice/ClosingPrice）
    print("① 抓 TWSE 上市全市場 OHLC ...")
    try:
        rows = requests.get(TWSE, headers=HEADERS, timeout=60).json()
        n = 0
        for d in rows:
            code = d.get("Code")
            if code and len(str(code)) == 4 and str(code).isdigit():
                ohlc[code] = {"o": num(d.get("OpeningPrice")), "h": num(d.get("HighestPrice")),
                              "l": num(d.get("LowestPrice")),  "c": num(d.get("ClosingPrice"))}
                n += 1
        print(f"   → 上市 {n} 檔")
    except Exception as e:
        print(f"   ⚠ TWSE 失敗（略過）: {e}")

    # ② TPEX 上櫃（欄位名不確定，自動偵測 + 印出供核對）
    print("② 抓 TPEX 上櫃全市場 OHLC ...")
    try:
        rows = requests.get(TPEX, headers=HEADERS, timeout=60).json()
        if rows:
            keys = list(rows[0].keys())
            print("   TPEX 欄位範例:", keys)
            k_code  = find_key(keys, ["SecuritiesCompanyCode", "Code", "代號", "證券代號", "股票代號"])
            k_open  = find_key(keys, ["Open", "開盤", "開"])
            k_high  = find_key(keys, ["High", "最高", "高"])
            k_low   = find_key(keys, ["Low", "最低", "低"])
            k_close = find_key(keys, ["Close", "收盤", "收"])
            print(f"   對應欄位 code={k_code} open={k_open} high={k_high} low={k_low} close={k_close}")
            n = 0
            for d in rows:
                code = d.get(k_code) if k_code else None
                if code and len(str(code)) == 4 and str(code).isdigit():
                    ohlc[code] = {"o": num(d.get(k_open)), "h": num(d.get(k_high)),
                                  "l": num(d.get(k_low)),  "c": num(d.get(k_close))}
                    n += 1
            print(f"   → 上櫃 {n} 檔")
    except Exception as e:
        print(f"   ⚠ TPEX 失敗（略過）: {e}")

    if not ohlc:
        print("❌ 完全沒抓到 OHLC，不寫檔。")
        sys.exit(1)

    # ③ 讀現有 finmind_data.json，更新 ohlc 欄後寫回
    try:
        with open(OUT, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}   # 財報 JSON 還不存在就建一份只有 ohlc 的
    data["ohlc"] = ohlc
    data["price_updated"] = datetime.date.today().isoformat()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n✅ 完成！更新 {len(ohlc)} 檔開高低收進 {OUT}")

if __name__ == "__main__":
    main()
