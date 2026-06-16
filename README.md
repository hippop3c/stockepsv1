# 台股 EPS 篩選器（GitHub Pages + Actions 自動更新版）

近年每季 EPS 全正篩選工具，含類股、本益比，資料由 GitHub Actions 每天自動抓 FinMind 更新。

## 架構

```
GitHub Actions（每天定時，雲端跑 Python）
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
| `.github/workflows/update-data.yml` | 每天定時跑抓取腳本並 commit JSON |
| `finmind_data.json` | （由 Actions 自動產生，第一次跑完才會出現） |

## 部署步驟

### 1. 建 repo 並放入檔案
把這四個檔案（含 `.github/workflows/` 資料夾結構）放進一個新的 GitHub repo。
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
跑完（約 1~2 分鐘）會 commit 出 `finmind_data.json`。
之後每天台灣時間 09:00 自動更新，你什麼都不用做。

### 5. 打開網頁
進 `https://你的帳號.github.io/repo名稱/`，會自動載入最新資料，
每檔標出 ✅ 12 季全正 / ⚠ 接近 / ✗ 不符，可用各種條件篩選。

## 調整

- **改抓幾年財報**：workflow 裡 `YEARS: '3'` 改成你要的年數。
- **改更新頻率**：workflow 裡 `cron: '0 1 * * *'`（每天）。財報一季才一次，可改成每週，例如 `'0 1 * * 1'`（每週一）。
- **本機測試抓取**：`export FINMIND_TOKEN=你的token && python finmind_fetch.py`

## 篩選功能

- **連續全正**：近 N 季每季 EPS > 0
- **季數計數**：近 N 季中，>0 / <0 / =0 的季數 ≥ / ≤ / = M
- EPS 基準可切「單季（累計換算）/ 累計（FinMind 原始）」核對
- 類股下拉、本益比範圍、搜尋、點欄排序、CSV 匯出、全部 / 只看通過切換

## 注意

- FinMind 的 EPS 是**累計值**，工具會自動換算成**單季**來判斷「每季 > 0」。
- FinMind 免費版限制 600 requests/hr；本工具一季抓一次全市場，約 4×年數 個 request，遠低於上限。
- 內建候選池 698 檔僅為基底；Actions 抓的是**全市場**財報，驗證不受候選池缺漏影響。
