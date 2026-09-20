---
name: rescore
description: 重跑臺股財務因子評分管線（FinMind 取數 → 十項因子評分 → 排名 → 匯出 CSV/Excel/snapshot），並解讀結果。只要使用者提到重跑評分、更新評分、跑一次評分、重新計算分數、更新排名、產生評分表、rerun scoring、refresh scores，或問「這次跟上次差在哪」「為什麼某檔分數變了」「某檔的某項為什麼是 N/A」，即使沒有明說 skill 名稱也要使用本 skill。它封裝了 FinMind 層級判斷、董監持股來源、配額中斷續跑、以及三種已知失敗模式的處理 —— 不用它就得每次重新推導一遍，而且容易把「該標 N/A」誤當成 bug。
---

# 重跑評分

這條管線有幾個決策點不是一眼看得出來的：母體怎麼取決於 FinMind 帳號層級、
董監持股得另外接 open data、免費層級會中途撞配額。這份 skill 把這些收斂成固定流程，
讓每次重跑都走同一條路、結果可比對。

背景與理由見 `CLAUDE.md` 與 `docs/decisions.md`；這裡只講「怎麼跑」。

## 開跑前先確認三件事

```bash
PYTHONPATH=src python -m pytest tests/ -q                        # 應 161 passed
PYTHONPATH=src python -m twfactor run --top 5 --source fixture   # 離線冒煙測試
echo ${FINMIND_TOKEN:+已設定}                                     # 有沒有 token
```

冒煙測試用合成資料（SYN001–SYN050），不連網。它過了才代表環境沒問題 ——
先跑它能把「環境壞了」和「取數失敗」區分開，省掉很多繞路。

Windows PowerShell 改用 `$env:PYTHONPATH="src"`，並設 `$env:PYTHONUTF8=1`
（輸出含 `⚠ ≥ ≤ −`，不在 cp950 內，一重導向就 UnicodeEncodeError）。

## 選路徑：看 FinMind 帳號層級

全市場市值排名需要**贊助（Sponsor）層級**。免費與註冊層級都會被擋，
訊息分別是 `Your level is free.` 與 `Your level is register.` ——
註冊 token 只提高每小時配額，不解鎖全市場查詢。

**A. 有 Sponsor token** —— 符合 PRD §3 原義

```bash
PYTHONPATH=src python -m twfactor run --top 50 --source finmind \
    --director-openapi --cache-dir .fincache
```

**B. 其他情況** —— 在候選母體內排名

```bash
PYTHONPATH=src python -m twfactor run --top 50 --source finmind \
    --stocks-file config/universe_candidates.txt \
    --director-openapi --cache-dir .fincache \
    --quota-wait 600 --quota-retries 18
```

不帶 `--stocks-file` 又沒有 Sponsor 層級時，程式會以 `FinMindLevelError` 明確中止，
而不是回傳殘缺母體 —— 「市值前 N」若不是在完整母體上排名，排名本身沒有意義。

**C. 只看少數幾檔**（驗證單一公司、或回答「某檔為什麼是這個分數」）

```bash
PYTHONPATH=src python -m twfactor run --top 3 --source finmind \
    --stocks 2330,2454,2891 --director-openapi --cache-dir .fincache
```

### 兩個一定要帶的參數

`--cache-dir .fincache` —— 快取 API 回應。**每次都帶**。配額中斷後重跑不重抓；
改門檻重算是 0 次請求（實測：50 檔即時取數 48.7 秒，全快取重跑 14 秒）。

`--director-openapi` —— 從 TWSE／TPEx 公開 open data 取董監持股，免金鑰。
不帶的話 PRD §8.10 那 1 分全部 N/A，一般產業可評滿分從 16 降為 15。

## 三種已知失敗模式

遇到這些**不是 bug**，照下面處理就好，不要去改程式：

**`FinMindLevelError: 帳號層級為 register`**
全市場查詢需 Sponsor。改走路徑 B，或請使用者升級 token。

**`FinMindQuotaError`（HTTP 402）**
每小時配額用盡。確認有帶 `--cache-dir`，加上 `--quota-wait 600 --quota-retries 18`
後重跑 —— 已取得的不會重抓，它會等配額重置自己續跑。

**`⚠ 董監持股來源「上市」取得失敗`**
證交所對機房／雲端 IP 封鎖（回 800 bytes 的「因為安全性考量」HTML）。
這是 IP 政策不是權限問題，實測換 User-Agent 或補 Referer 都沒用。
在一般家用／公司網路執行即可；程式會讓該市場標 N/A 並繼續，不中斷評分。
**不要嘗試用輪換 IP、代理或偽裝指紋規避。**

## 讀結果

輸出在 `output/`，三個帶時間戳的檔案：

| 檔案 | 用途 |
|---|---|
| `scores_*.csv` | 結果表，一列一檔 |
| `scores_*.xlsx` | 分「一般產業／金融業／得分理由」三個分頁 |
| `snapshot_*.json` | 每欄的 provenance 與每個 N/A 的原因 |

回報給使用者時，這幾件事比分數本身更值得講：

- **達門檻家數**與前段名單（終端輸出最後會印）
- **因子缺漏統計**（N/A 家數）—— 這是資料覆蓋率的溫度計，
  突然變多通常代表來源出事，而不是公司變差
- **與上次執行的差異** —— 用下面的比對腳本

要追某一項分數為什麼是那個值，看 snapshot 的 `cards[].factors[].rule`（命中規則）
與 `.value`（判斷用的原始值）；N/A 的原因在 `missing`。例如 2330 的 EPS：

```
rule:  一次 >12% 大幅衰退（39.2→32.34），次年恢復至衰退前水準以上（45.26 ≥ 39.2）→ 穩定性 0.5
value: 23.02, 39.2, 32.34, 45.26, 66.26
```

## 比對兩次執行

分數變動要能說清楚是「資料更新」還是「規則改動」造成的：

```bash
python .claude/skills/rescore/scripts/compare_runs.py 舊的.csv 新的.csv
```

列出總分變動、各因子分數變動、新增／消失的個股，以及 N/A 家數的增減。

## N/A 不是 bug

最容易誤判的地方：看到大量 N/A 就想「修好它」。

PRD §10 要求 N/A 與 0 分嚴格區分 —— 解析不到就標 N/A，不以推測值補齊、
不按比例放大。N/A 會降低該公司的「實際可評滿分」（例如 16 → 14），0 分不會。
每個 N/A 在 snapshot 的 `missing` 都有具體原因，先讀原因再判斷是否真的是問題。

目前預期內的 N/A：董監持股（未帶 `--director-openapi`，或上市資料被 IP 擋）、
ROIC（五年內有稅前淨利 ≤0，無法導出有效稅率）、金融業不適用的因子（§8.12）。
