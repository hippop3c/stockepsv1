# 台股 EPS 篩選器（GitHub Pages + Actions 自動更新版）

全市場逐季 EPS 篩選工具，含官方市場 OHLC 與公開外資目標價；資料由 GitHub Actions 增量更新。

## 架構

```
GitHub Actions（EPS 每週、股價與目標價每日，雲端跑 Python）
      │  抓 FinMind（後端環境，無 CORS）
      ▼
  finmind_data.json  ──commit回 repo──┐
                                      ▼
GitHub Pages 的 index.html ──同源讀取（無跨域、無 CORS）──> 打開即最新資料
```

token 藏在 GitHub Secrets，不會出現在程式碼或 JSON 裡 → 即使 public repo 也安全。

## 檔案

| 檔案 | 用途 |
|------|------|
| `index.html` | 篩選器網頁（內建 698 檔候選池當基底，開啟時自動載入同源 `finmind_data.json`） |
| `finmind_fetch.py` | 抓取腳本，token 從環境變數 `FINMIND_TOKEN` 讀 |
| `.github/workflows/update-data.yml` | 每週增量回補 EPS 並 commit JSON |
| `update_price.py` | 抓 TWSE / TPEx / 興櫃最新交易日 OHLC，保留 30 天近期歷史 |
| `update_target_price.py` | 輪詢公開外資評等頁，保留目標價的日期、機構與來源連結 |
| `.github/workflows/update-price.yml` | 每個交易日更新官方 OHLC |
| `.github/workflows/update-target-price.yml` | 每日輪詢部分目標價並逐步覆蓋市場 |
| `validate_data.py` / `tests/` | 提交前阻擋殘缺、錯誤或來源版型失效的資料 |
| `finmind_data.json` | Actions 產生、GitHub Pages 同源讀取的資料快照 |

## 部署步驟

### 1. 建 repo 並放入檔案
把本專案檔案（含 `.github/workflows/` 資料夾結構）放進一個新的 GitHub repo。
public / private 皆可（public 才能免費用 GitHub Pages）。

### 2. 設定 Token Secret
repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
- Name: `FINMIND_TOKEN`
- Secret: 貼上你的 FinMind token

### 3. 開啟 GitHub Pages
repo → **Settings** → **Pages** → Source 選 **Deploy from a branch** → 選 `main` / `(root)` → Save。
幾分鐘後網址會是 `https://你的帳號.github.io/repo名稱/`

### 4. 第一次手動觸發抓資料
repo → **Actions** → 左側「更新 FinMind 資料」→ **Run workflow**。
首次全市場逐檔抓取約需 4–5 小時；完成後會 commit `finmind_data.json`。
之後 EPS 每週回補、OHLC 每個交易日盤後更新、目標價每日輪詢。

### 5. 打開網頁
進 `https://你的帳號.github.io/repo名稱/`，會自動載入最新資料，
每檔標出 ✅ 12 季全正 / ⚠ 接近 / ✗ 不符，可用各種條件篩選。

## 調整

- **改抓幾年財報**：workflow 裡 `YEARS: '10'` 改成你要的年數。
- **改更新頻率**：修改各 workflow 的 `schedule.cron`（GitHub Actions 使用 UTC）。
- **本機測試抓取**：`export FINMIND_TOKEN=你的token && python finmind_fetch.py`

## 篩選功能

- **連續全正**：近 N 季每季 EPS > 0
- **季數計數**：近 N 季中，>0 / <0 / =0 的季數 ≥ / ≤ / = M
- EPS 顯示 FinMind 財報資料集的原始單季值
- 類股下拉、本益比範圍、搜尋、點欄排序、CSV 匯出、全部 / 只看通過切換

## JSON schema v2

- `active_stock_ids`: 目前 TWSE / TPEx / 興櫃交易中的四碼公司股票；排除 ETF、ETN、權證、TDR 與已下市代號。
- `financials[code][]`: `{date, single, cum}`。FinMind 回傳的 EPS 已是該季數值，`single` 與相容舊前端的 `cum` 都保存同一原始季 EPS；不再錯誤相減。
- `ohlc[code]`: `{date, o, h, l, c}`，`date` 是官方 API 的實際交易日。興櫃官方來源沒有開盤欄時 `o` 為 `null`，不製造假價格。
- `ohlc_history[code][]`: 最近 30 個日曆日的日 OHLC；同交易日覆寫、不會無限膨脹。
- `target_price[code]`: `{price, institution, source, date, url, ...}`；`foreign_target_price` 是相同資料的明確別名。
- `foreign_target_price_history[code][]`: 目標價原始觀測紀錄，成功輪詢時只保留一年；超過 180 天標示舊資料，超過一年不顯示。

## 更新與資料品質

- EPS 每週逐檔回補最近兩年並依季度日期合併，可吃到更正申報；單檔失敗會保留舊資料。FinMind token 僅從 `FINMIND_TOKEN` GitHub Secret 讀取。
- 股價每個交易日盤後抓 TWSE / TPEx 官方 OpenAPI。`price_updated` 與 `price_source_dates` 使用來源內的民國日期轉西元，不能用 workflow 執行日冒充。
- 交易所沒有「外資目標價」官方資料源。目標價採 Cnyes 公開「外資評等」頁每秒至多一筆低頻輪詢；每筆都帶來源與日期，遇 403/429 立即停止且不刪除快取。目標價只是預測值，不是交易所資料。
