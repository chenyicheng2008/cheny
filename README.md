# 臺股財務因子自動評分系統（PRD v1.1 第一階段）

依 PRD v1.1 第 8 章的十項因子規則，對全市場市值前 N 檔自動評分、排名並匯出。

**資料來源全部是公開資料、免金鑰、無每小時配額**（2026-09-15 起捨棄 FinMind）：

| 用途 | 來源 |
|---|---|
| 母體、市值、產業別、發行股數 | 證交所／櫃買中心 OpenAPI |
| 財報（損益、資產負債、現金流量） | 公開資訊觀測站 XBRL 整批檔（t203sb02） |
| 現金股利 | 證交所 TWT49U、櫃買中心 exDailyQ 除權息結果表 |
| 非獨立董監持股／質押 | 證交所 t187ap11_L、櫃買中心 mopsfin_t187ap11_O |

## 現況

| 模組 | 狀態 |
|---|---|
| 評分引擎（PRD §8 十項因子、§10 缺漏處理、§11 總分與排名） | ✅ |
| 參數化（PRD §12 門檻／權重全部外部化） | ✅ |
| 可追溯性（原始值 → 命中規則 → 得分） | ✅ |
| 匯出（CSV / Excel 三分頁 / JSON 快照） | ✅ |
| 全市場市值前 N 母體（PRD §3） | ✅ OpenAPI 一次取得全市場，不再需要付費層級或候選清單 |
| 財報資料層 | ✅ XBRL 整批檔，已與公告數及原 FinMind 資料交叉驗證（見下） |
| 非獨立董監持股／質押（PRD §8.10） | ✅ 上市、上櫃都取得到（需在一般網路環境執行，見下） |
| ROIC（PRD §8.9） | ✅ 依需求方 2026-09-15 指定計算式 |
| 五年含息總報酬（PRD §9） | ⚠ 介面已留，計算式待與 TradingView 交叉驗證 |

## 安裝與執行

```bash
git clone <repo> && cd cheny
pip install -r requirements.txt
```

需求：Python 3.11+。所有指令都從專案根目錄執行，且都要讓 Python 找得到 `src/`。
本文範例以 macOS／Linux 的 bash 寫法呈現，**Windows 的寫法不同**：

| | 執行 |
|---|---|
| macOS / Linux | `PYTHONPATH=src python -m twfactor ...` |
| Windows PowerShell | `$env:PYTHONPATH="src"; $env:PYTHONUTF8=1; python -m twfactor ...` |
| Windows cmd | `set PYTHONPATH=src`、`set PYTHONUTF8=1` 後另起一行 `python -m twfactor ...` |

**Windows 要設 `PYTHONUTF8=1`**：終端輸出用到 `⚠ ≥ ≤ −`，不在繁中 Windows 預設的 cp950 裡，
輸出導向檔案或管線時會 `UnicodeEncodeError`。匯出的 CSV 帶 UTF-8 BOM，Excel 直接開不會亂碼。

### 步驟 1：離線冒煙測試（不需網路）

```bash
PYTHONPATH=src python -m twfactor run --top 5 --source fixture
```

用 `fixtures/synthetic_universe.json` 的合成資料（SYN001–SYN050，非真實個股）跑完整條管線。

### 步驟 2：正式評分

第一次執行加 `--download-xbrl`，會從公開資訊觀測站下載需要的 XBRL 整批檔到 `.xbrlcache/`：

```bash
PYTHONPATH=src python -m twfactor run --top 50 --director-openapi --download-xbrl
```

之後同一時點重跑就不必再下載：

```bash
PYTHONPATH=src python -m twfactor run --top 50 --director-openapi
```

PowerShell 版：

```powershell
$env:PYTHONPATH="src"; $env:PYTHONUTF8=1
python -m twfactor run --top 50 --director-openapi --download-xbrl
```

**需要下載哪些檔**：五個完整年度各一個 Q4 年報檔，外加一個已過申報期限的期中檔（算 TTM）。
以 2026-09-15 執行為例是 `tifrs-2021Q4` ～ `tifrs-2025Q4` 共五檔，再加 `tifrs-2026Q2`。
每檔約 105～125 MB，下載一檔約 1～6 分鐘。伺服器不提供檔案大小，程式設了單檔 500 MB 上限，
下載先寫 `.part`，確認是完整 zip 才改名，中斷不會留下殘檔。

為什麼五年都要：年報雖然附前一年比較數，但比較數會因配股、減資、面額變更而追溯調整。
要把 EPS 換算到同一股本基準，需要每一年的原始數，也需要次年的追溯數（見「EPS 股本基準」）。

**只想看幾檔**：

```bash
PYTHONPATH=src python -m twfactor run --top 3 --stocks 2330,2454,2891 --director-openapi
```

`--stocks`／`--stocks-file` 會把排名限定在指定清單內（執行時會印出警告）。
不指定時就是全市場排名。

**以比例取母體並產生 PDF 報告**：

```bash
PYTHONPATH=src python -m twfactor run --top-fraction 1/3 --focus 50 --director-openapi --pdf
```

`--top-fraction 1/3` 取可排名普通股中市值前三分之一（約 630 檔）；`--pdf` 另產生
`output/report_*.pdf`（A4 橫式），內容依序為摘要與總分分布、**得分定義**（門檻取自
`scoring_params.yaml`）、得分前 `--focus` 名排名與關鍵數字、前 N 名的產業與市值分層組成、
金融業排名、全體排名附錄、資料缺漏與資料來源。

PDF 由本機的 Edge 或 Chrome 以 headless 模式列印，找不到時會保留 `report_*.html` 並印出提示；
瀏覽器裝在非預設位置時設定 `TWFACTOR_BROWSER` 指向執行檔。母體約 630 檔時，
證交所「權息」明細需逐筆查詢（每筆間隔 2 秒避免被暫時封鎖），第一次執行約需 5～10 分鐘，之後走快取。

**全體台股 EPS 因子評分**（只算 §8.1 EPS 一項，母體為全市場普通股）：

```bash
PYTHONPATH=src python scripts/eps_score.py --as-of 2026-09-18
```

輸出 `output/eps_score_<基準日>.csv`（每檔五年 EPS〔最新股本基準〕、TTM EPS、五年年複合成長、
EPS 分數與命中規則），終端機印出分數分布、各產業平均分與「滿分且成長最快」的領先者。
評分直接呼叫 `scoring.factors.score_eps`，與十項因子評分同一套規則與門檻。

### 常用選項

| 選項 | 用途 |
|---|---|
| `--download-xbrl` | 缺少的 XBRL 整批檔自動下載。沒加又缺檔時會列出缺哪些檔後中止 |
| `--director-openapi` | 從證交所／櫃買中心 open data 取董監持股。不加則該因子全部 N/A |
| `--director-holdings <csv>` | 改用自行下載的 MOPS 董監持股匯出檔 |
| `--cache-dir <dir>` | XBRL 整批檔與公開資料快取，預設 `.xbrlcache` |
| `--as-of 2026-09-15` | 資料基準日，用於推定最新完整年度與 TTM 季別（避免前視偏誤） |
| `--top N` | 取前 N 檔 |
| `--top-fraction 1/3` | 改以比例取市值前段（優先於 `--top`） |
| `--pdf` | 另產生 A4 橫式 PDF 報告（需 Edge 或 Chrome，或設定 `TWFACTOR_BROWSER`） |
| `--focus N` | PDF 重點分析的得分前 N 名，預設 50；母體很大時終端機也只印前 N 名 |
| `--stocks` / `--stocks-file` | 限定候選母體 |
| `--outdir <dir>` | 輸出目錄，預設 `output/` |
| `--params` / `--fields` | 改用其他參數檔或科目對照檔 |

快取規則：XBRL 整批檔下載一次沿用；OpenAPI 的母體、股價、董監持股以**執行當日**分檔快取
（同一天重跑不重抓，隔天自動更新）；已結束年度的除權息表快取後沿用。

### 輸出

每次執行在 `--outdir` 產生三個帶時間戳的檔案：

| 檔案 | 內容 |
|---|---|
| `scores_*.csv` | PRD §13.1 結果表，一列一檔，含十項因子分數、總分、排名、標籤、缺漏說明 |
| `scores_*.xlsx` | 同上，分「一般產業」「金融業」「得分理由」三個分頁 |
| `snapshot_*.json` | 完整快照：每個欄位的 provenance（來源季檔／XBRL 元素名／期間／抓取時間／備註）與每個 N/A 的原因 |

要查某一項分數為什麼是那個數字，看 `snapshot_*.json` 的 `cards[].factors[].rule`
與 `value`；要查為什麼是 N/A，看 `missing`；要查數字從哪個 XBRL 元素、哪一季檔來，看 `provenance`。

### 跑不動時

| 現象 | 原因與處理 |
|---|---|
| `✖ 缺少 XBRL 整批檔：…` | 加 `--download-xbrl`，或手動從 t203sb02 頁面下載後放進 `.xbrlcache/` |
| `… 不是有效的 zip` | 下載不完整或被擋，刪掉該檔重下 |
| `⚠ 董監持股來源「上市」取得失敗` | 證交所擋了執行環境的 IP，見下節。取不到的市場標 N/A，不中斷評分 |
| `OpenDataError: … 取得失敗` | 證交所／櫃買中心端點暫時不通。已重試 4 次，稍後再跑 |
| 某因子大量 N/A | 正常且刻意 —— 解析不到就標 N/A，不以推測值補齊（PRD §19.6）。原因寫在 snapshot 的 `missing` |

### 取不到證交所（TWSE／MOPS）資料

證交所的 `openapi.twse.com.tw`、`www.twse.com.tw`、`mopsov.twse.com.tw` 會擋機房／雲端 IP，
症狀是收到一份 800 bytes 的 HTML（「因為安全性考量，您所執行的頁面無法呈現」）而不是 JSON 或 zip。
實測換 User-Agent、補 Referer 都一樣被擋，是認來源 IP 而不是認標頭。
另一種情況是容器／CI 自己的對外白名單，徵狀是 `CONNECT tunnel failed, response 403` 或 `ProxyError`。

處理方式：

1. **在一般網路環境執行**（家用／公司網路）。2026-09-15 從家用網路實測全部端點皆正常。
2. 容器／CI 的白名單問題：把上述網域加進允許清單。
3. 董監持股可改為瀏覽器手動下載 <https://openapi.twse.com.tw/v1/opendata/t187ap11_L>
   另存 CSV，改用 `--director-holdings <檔案>.csv`；XBRL 整批檔也可手動下載後放進 `.xbrlcache/`。

⚠ 不要用輪換 IP、住宅代理或偽裝指紋去繞過證交所的 IP 過濾。那是規避對方刻意設置的存取控制，
違反使用條款。本專案不提供、也不應加入這類手段。

## 資料來源細節

### 母體與市值（PRD §3）

| 市場 | 公司基本資料 | 收盤價 |
|---|---|---|
| 上市 | `openapi.twse.com.tw/v1/opendata/t187ap03_L` | `/v1/exchangeReport/STOCK_DAY_ALL` |
| 上櫃 | `www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O` | `/openapi/v1/tpex_mainboard_quotes` |

市值 ＝ 最新收盤價 × 已發行普通股數。只取 4 碼、非 0 開頭的代號（排除 ETF／ETN），
當日無成交或無股數者不列入排名（不推估）。OpenAPI 只提供最新一日，
所以 `--as-of` 不會回溯歷史市值，市值基準日會印在執行畫面上。

金融業判定（PRD §8.12）用交易所「產業別」代碼 `17`（金融保險業），上市櫃共用同一套代碼，
設定在 `config/xbrl_fields.yaml` 的 `financial_industry_codes`。

### 財報：XBRL 整批檔

公開資訊觀測站「XBRL 資訊平台－案例文件整批下載」（`https://mopsov.twse.com.tw/mops/web/t203sb02`），
每季一個 zip，內含當季所有公發公司申報的 inline XBRL 財報，一家一個 html。
2025Q4 檔實測有 2,722 份申報、2,694 家公司。

- 檔名 `tifrs-fr1-m1-ci-cr-2330-2025Q4.html`：`ci`／`basi`／`fh`／`ins`／`bd`／`mim` 是產業 taxonomy，
  `cr` 合併、`ir` 個體。同一家公司有合併與個體報表時以合併為準。
- 數值一律為 `ix:nonFraction`，千分位逗號、`scale="3"`（千元）、負值以 `sign="-"` 標示。
- 只取主報表 context：`AsOf{yyyymmdd}`（期末存量）與 `From{yyyymmdd}To{yyyymmdd}`（期間）。
  帶底線後綴的是權益變動表等明細維度，不取。
- 損益與現金流量為**年初至今累計**：年度值取 `From{Y}0101To{Y}1231`，
  TTM ＝ 今年累計 ＋ 去年全年 − 去年同期累計（去年同期取自期中檔的比較數）。
- 同一期間出現在多個季檔時，以較新申報的（可能經追溯調整的）數字為準。

科目對照在 `config/xbrl_fields.yaml`：

| 欄位 | XBRL 元素 |
|---|---|
| 營收 | `ifrs-full:Revenue` |
| 營業利益 | `ifrs-full:ProfitLossFromOperatingActivities` |
| 本期淨利 | `ifrs-full:ProfitLoss` |
| EPS | `ifrs-full:BasicEarningsLossPerShare` |
| 稅前淨利／所得稅 | `ifrs-full:ProfitLossBeforeTax`／`IncomeTaxExpenseContinuingOperations` |
| 利息費用 | `ifrs-full:AdjustmentsForInterestExpense`（金控：`InterestExpense`） |
| 權益總額 | `ifrs-full:Equity` |
| 短期借款 | `ifrs-full:ShorttermBorrowings` |
| 長期借款／應付公司債 | `ifrs-full:LongtermBorrowings`／`NoncurrentPortionOfNoncurrentBondsIssued` |
| 租賃負債－非流動 | `ifrs-full:NoncurrentFinanceLeaseLiabilities` |
| 營業活動現金流 | `ifrs-full:CashFlowsFromUsedInOperatingActivities` |
| 取得不動產廠房設備 | `ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities` |

⚠ 2330 同時列示 `LongtermBorrowings` 與 `NoncurrentPortionOfNoncurrentLoansReceived`，兩者同值，
不可重複相加，對照表只取前者。

### 精簡事實檔（repo 內，免下載整批檔）

整批 zip 每檔 100 MB 以上，超過 GitHub 單檔上限，無法放進 repo。因此 repo 附一份
`data/xbrl_facts.csv.gz`：只保留上表（`config/xbrl_fields.yaml`）用到的元素，
涵蓋各季檔的**全部公司**，欄位為 `archive, stock_id, taxonomy, tag, context, value`（金額單位：元）。

`run` 時每一季優先用 `.xbrlcache/` 裡的 zip；沒有 zip 的季別改讀精簡事實檔；兩者都沒有才需要
`--download-xbrl`。所以 clone 下來即可評分，只剩母體、股價、股利、董監持股要連線取得。
執行畫面會標出哪幾季來自精簡事實檔。

更新方式（在有 zip 的電腦上）：

```bash
PYTHONPATH=src python -m twfactor build-snapshot --cache-dir .xbrlcache --as-of 2026-09-15
```

⚠ 在 `xbrl_fields.yaml` 新增元素後要重建精簡事實檔，否則新元素在沒有 zip 的環境一律解析不到（標 N/A）。
⚠ 季別會隨時間前進（例如 11/14 後 TTM 改用 Q3 檔），精簡事實檔沒有該季時，程式會要求下載。

### 現金股利

| 市場 | 端點 | 備註 |
|---|---|---|
| 上市 | `www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate=…&endDate=…&response=json` | 「權息」同除的列只給權值＋息值合計，再查 `TWT49UDetail` 拆出現金股利 |
| 上櫃 | `www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate=…&endDate=…&response=json` | 直接有「現金股利」欄 |

依除權息交易日所屬年度歸戶。除權息結果表是全市場完整名單，**某年查無紀錄即認定該年未配現金股利（0）**；
「權息」列查不到明細時該年度標 N/A，不以合計值頂替。

## EPS 股本基準（需求方 2026-09-15 決定：追溯調整至最新股本）

配股、減資、面額變更時，次年年報會追溯調整前一年的 EPS。五年序列若混用原始數與追溯數，
股本變動就會被誤判成 EPS 衰退。實測例子：富邦金 2022 原始 3.54 → 追溯 3.37；
玉山金 1.10 → 1.06；國巨面額變更後 2024 由 42.12 → 9.53。

作法：第 k 年的比例 r_k ＝ k 年在 k+1 年年報的追溯數 ÷ k 年在當年年報的原始數，
各年 EPS 乘上其後各年比例的連乘積，五年全部換算到最新一年的股本基準。
任一比例無法計算（原始數缺漏、為 0 或正負號改變）時 EPS 標 N/A，不假設股本未變動。
調整比例寫在 snapshot 的 `provenance.eps.note`。

**未調整的部分**：每股現金股利仍是當時的每股金額；TTM EPS 的「去年全年」一項若在今年發生股本變動
也未調整。兩者影響只在有配股／面額變更的公司，數字需要時可再對照 provenance。

## 資料定義與已知落差

1. **LT-Debt/Equity（PRD §8.6，1 分）**：長期負債 ＝ 長期借款 ＋ 應付公司債 ＋ 租賃負債－非流動。
   PRD 原文「含長期融資租賃」，FinMind 沒有租賃負債科目而長期低估，XBRL 有，依 PRD 原義納入。
   不含一年內到期部分（PRD：到期日一年以上）。
2. **零餘額科目**：XBRL 不列示餘額為零的科目。該年度期末資產負債表存在（有權益總額）、
   卻沒有任何借款元素時，借款認定為 0（需求方 2026-09-15 確認的規則沿用）；
   整年沒有期末資產負債表才標 N/A。同一邏輯套用在利息費用：現金流量表存在卻沒有
   利息費用調整項時認定為 0，交由 §8.7「無利息負擔」規則評分。認定為 0 的會寫進 provenance note。
3. **Interest Coverage（PRD §8.7，2 分）**：PRD 第一版規定「直接沿用 StockBoss 值」，
   本專案以 `TTM 營業利益 ÷ |TTM 利息費用|` 重建，**需求方已於 2026-09-15 確認接受**；
   分數無法與人工 StockBoss 評分表逐項對齊。
4. **ROIC（PRD §8.9，1 分）**：需求方 2026-09-15 指定

   ```
   ROIC        = 稅後營業利益 ÷（股東權益 ＋ 有息負債）    皆取期末值
   稅後營業利益 = 年度營業利益 × (1 − 有效稅率)
   有效稅率     = 年度所得稅費用 ÷ 年度稅前淨利            同一年度
   有息負債     = 期末 短期借款 ＋ 長期借款 ＋ 應付公司債
   ```

   稅前淨利 ≤0 的年度沒有可導出的有效稅率，整項標 N/A。有息負債依定義不含租賃負債與一年內到期部分。
5. **股息支付率**（PRD §8.4）：需求方 2026-09-15 確認採
   `最新完整年度現金股利 ÷ TTM EPS`。分子為年度值、分母為滾動值，期間刻意不對齊；
   EPS 快速成長時支付率會被系統性低估。TTM EPS < 0 → 0 分；TTM EPS = 0 → N/A。
6. **自由現金流**：營業活動現金流 − |取得不動產、廠房及設備|。XBRL 另有取得無形資產，
   為與原定義一致暫不納入；要納入時加進 `xbrl_fields.yaml` 的 `capex.tags` 即可。
7. **淨利率／ROE**：採本期淨利（含非控制權益）÷ 營收、÷ 期末權益總額，需與人工評分表比對後定案。
8. **金融業判定**：PRD §4 要求 StockBoss／Goodinfo 雙分類並存，但未指定主判定欄位，
   目前以交易所產業別代碼判定，待需求方指定。
9. **「近五個完整年度」時點錨**：以年報申報期限 3/31 推定（4 月起採前一年度）；
   TTM 季別以期中報告申報期限（5/15、8/14、11/14）推定，設定在 `xbrl_fields.yaml`。

## 驗證（2026-09-15）

- **對公告數**：2330 FY2024 營收 2,894,307,699 千元、EPS 45.25、營業現金流 1,826,177,068 千元、
  取得不動產廠房設備 956,006,536 千元、應付公司債 926,604,506 千元，與公告數逐項吻合；
  五年 EPS 23.01／39.20／32.34／45.25／66.26。
- **對原 FinMind 資料**：以先前 `.fincache` 內 37 家公司的 FinMind 回應逐年比對（僅作驗證，管線已不使用）：

  | 項目 | 吻合 |
  |---|---|
  | 現金股利 | 181／181 |
  | 權益總額 | 184／185 |
  | 營業活動現金流 | 182／185 |
  | EPS | 173／185 |

  EPS 不一致者全部是股本變動造成的追溯調整（上節），已改為換算至同一股本基準；
  現金流與權益的少數差異是國巨、華邦電、廣達的前期重編。營收只比對一般產業
  （金控沒有 `ifrs-full:Revenue`，§8.12 也不評淨利率）。

## 專案結構

```
config/scoring_params.yaml   # PRD §12 所有門檻、滿分、權重（程式不得寫死）
config/xbrl_fields.yaml      # XBRL 元素對照、零餘額規則、期中申報期限、金融業代碼
src/twfactor/
  models.py                  # CompanyFacts / FactorScore / ScoreCard
  params.py                  # 設定載入與一致性驗證（16 分、9 分自檢）
  scoring/factors.py         # 十項因子純函式，逐項保留命中規則
  scoring/engine.py          # N/A 處理、總分、金融業矩陣、標籤
  scoring/ranking.py         # 競賽排名跳號，一般／金融不混排
  sources/opendata.py        # 母體、市值、股利、XBRL 事實 → CompanyFacts
  sources/xbrl.py            # XBRL 整批檔下載與 inline XBRL 解析
  sources/director_holding.py# 非獨立董監持股／質押
  sources/fixtures.py        # 離線合成資料（SYN*，非真實個股）
  export.py                  # PRD §13.1 結果表與匯出
  cli.py
tests/                       # 含 PRD §15 全部邊界案例
legacy/finmind/              # 已停用的 FinMind 資料層，僅供參考
```

## 為何門檻都在 YAML

PRD §12 要求所有分數、門檻與期間以設定檔管理，以支撐第二階段的因子權重最佳化。
`params.py` 在載入時會自檢十項 `max_score` 合計是否等於 16、金融業五項是否等於 9，
設定寫錯會在啟動時就報錯，而不是在評分中途產生錯誤分數。

權重第一版全為 1.0，`raw_total` 與 `weighted_total` 分開保存（PRD §12 明訂不可覆蓋原始結果）。

## 董監持股（PRD §8.10）的兩個坑

- **它是「內部人」全表，不是董監表。** 職稱涵蓋總經理、協理、經理、大股東等。
  必須以「職稱含董事或監察人」白名單挑選，再排除獨立董事。
- **法人董事佔多席時，同一法人的持股會每席重複列示。** 實測 887 家上櫃公司中 380 家（43%）有此情形，
  故以（姓名, 目前持股, 設質股數）去重。

持股比例的分母取自交易所公司基本資料的已發行普通股數。缺職稱欄時整項 N/A，缺質押欄時質押 N/A。

## 測試

```bash
python -m pytest tests/ -q
```

- `tests/test_xbrl.py`：inline XBRL 解析（scale、正負號、明細維度排除）、合併報表優先、需要哪些季檔。
- `tests/test_opendata.py`：母體與市值、除權息表解析、XBRL 事實 → CompanyFacts、零餘額規則、TTM。
- `tests/test_eps_basis.py`：EPS 追溯調整（配股、多年連乘、面額變更、無法計算時 N/A）。
- `tests/test_roic.py`、`tests/test_payout_ratio.py`：需求方指定的 ROIC 與支付率算式。
- `tests/test_factors.py`：PRD §15 點名的邊界值（EPS 負值、股息下降剛好 5%、Payout 0%／50%／100%、
  ROE 剛好 12%／15%、股東權益 ≤0、質押剛好 20%、無利息負擔、金融業部分評分、未滿五年）。

## 資料聲明

`fixtures/synthetic_universe.json` 為**合成測試資料**（SYN001–SYN050），不對應任何真實公司，
僅供驗證管線行為。`output/` 內的評分結果由真實公開資料產生，但受上述定義與落差影響
（利息保障倍數為重建值、淨利率／ROE 定義待定案），不得直接用於投資判斷。
