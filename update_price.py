#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市場開高低收（OHLC）更新腳本 → 更新 finmind_data.json 的 ohlc 欄

用證交所 TWSE + 櫃買 TPEX 官方 openapi，一次抓全市場最近交易日的開高低收，
免費、不用 token、幾秒完成。讀現有 finmind_data.json（財報那份），
只更新/新增 ohlc 欄後寫回 → 不用重抓 4 小時的財報。

涵蓋四個板：
  上市主板  TWSE  : https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
  上櫃主板  TPEX  : https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes
  興櫃      TPEX  : https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics
  （openapi 為最近交易日資料，盤後隔日更新；對本益比參考足夠）

防呆：
  - 任一來源失敗 → 略過該來源、保留其它，不整批中斷。
  - 抓到的總檔數低於門檻（疑似假日空檔／全面失敗）→ 不覆寫舊 ohlc，直接結束。
  - 採「合併」而非「整批取代」：本次缺的板沿用上次的值，不會把畫面洗白。
"""

import requests, json, datetime, sys

OUT = "finmind_data.json"
TWSE      = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_MAIN = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_ESB  = "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics"   # 興櫃
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# 寫檔門檻：本次抓到的總檔數低於此值，視為假日／全面失敗，不覆寫舊資料
MIN_TOTAL = 1000

def num(s):
    """把 '1,234.5' / '--' / '' 轉成 float 或 None"""
    if s is None: return None
    s = str(s).replace(",", "").strip()
    if s in ("", "--", "---", "N/A", "null", "除權", "除息", "除權息"): return None
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

def fetch(url, tag):
    try:
        rows = requests.get(url, headers=HEADERS, timeout=60).json()
        return rows if isinstance(rows, list) else []
    except Exception as e:
        print(f"   ⚠ {tag} 失敗（略過）: {e}")
        return []

def add_twse(ohlc):
    """上市主板（欄位名已知）"""
    print("① 抓 TWSE 上市全市場 OHLC ...")
    rows = fetch(TWSE, "TWSE")
    n = 0
    for d in rows:
        code = d.get("Code")
        if code and len(str(code)) == 4 and str(code).isdigit():
            ohlc[str(code)] = {"o": num(d.get("OpeningPrice")), "h": num(d.get("HighestPrice")),
                               "l": num(d.get("LowestPrice")),  "c": num(d.get("ClosingPrice"))}
            n += 1
    print(f"   → 上市 {n} 檔")
    return n

def add_tpex(url, tag, ohlc):
    """上櫃主板 / 興櫃（欄位名自動偵測；興櫃可能只有收盤，沒有開高低）"""
    print(f"{tag} ...")
    rows = fetch(url, tag)
    if not rows:
        return 0
    keys = list(rows[0].keys())
    print("   欄位範例:", keys)
    k_code  = find_key(keys, ["SecuritiesCompanyCode", "Code", "代號", "證券代號", "股票代號"])
    k_open  = find_key(keys, ["Open", "開盤", "開"])
    k_high  = find_key(keys, ["High", "最高", "高"])
    k_low   = find_key(keys, ["Low", "最低", "低"])
    # 興櫃常無開高低，收盤欄可能叫「最後成交價/均價/成交價」
    k_close = find_key(keys, ["Close", "LatestPrice", "Average", "收盤", "最後成交價", "均價"])
    print(f"   對應欄位 code={k_code} open={k_open} high={k_high} low={k_low} close={k_close}")
    n = 0
    for d in rows:
        code = d.get(k_code) if k_code else None
        if code and len(str(code)) == 4 and str(code).isdigit():
            c = num(d.get(k_close)) if k_close else None
            o = num(d.get(k_open))  if k_open  else None
            h = num(d.get(k_high))  if k_high  else None
            l = num(d.get(k_low))   if k_low   else None
            # 興櫃若只有收盤，開高低就以收盤回填，至少四欄都有值、本益比可算
            if c is not None and o is None and h is None and l is None:
                o = h = l = c
            if c is not None or o is not None:
                ohlc[str(code)] = {"o": o, "h": h, "l": l, "c": c}
                n += 1
    print(f"   → {tag} {n} 檔")
    return n

def main():
    ohlc = {}
    n_twse = add_twse(ohlc)
    n_main = add_tpex(TPEX_MAIN, "② 抓 TPEX 上櫃主板 OHLC", ohlc)
    n_esb  = add_tpex(TPEX_ESB,  "③ 抓 TPEX 興櫃 OHLC",     ohlc)
    total = len(ohlc)
    print(f"\n本次抓到合計 {total} 檔（上市{n_twse}／上櫃{n_main}／興櫃{n_esb}）")

    # 讀現有 json（含上次的 ohlc）
    try:
        with open(OUT, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    # 防呆：本次太少 → 疑似假日/全面失敗，不覆寫舊資料
    if total < MIN_TOTAL:
        old = len(data.get("ohlc", {}))
        print(f"❌ 本次僅 {total} 檔（< {MIN_TOTAL}），疑似休市或來源失敗，"
              f"保留舊 ohlc（{old} 檔）不覆寫，結束。")
        sys.exit(0)

    # 合併：以本次新值覆蓋舊值，本次沒抓到的板沿用上次（避免單一來源失敗洗白）
    merged = dict(data.get("ohlc", {}))
    merged.update(ohlc)
    data["ohlc"] = merged
    data["price_updated"] = datetime.date.today().isoformat()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n✅ 完成！本次更新 {total} 檔，合併後 ohlc 共 {len(merged)} 檔，寫入 {OUT}")

if __name__ == "__main__":
    main()
