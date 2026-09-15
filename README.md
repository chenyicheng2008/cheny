# 臺股財務因子自動評分系統（PRD v1.1 第一階段）

以 **FinMind** 取代 StockBoss 作為財報資料來源，依 PRD v1.1 第 8 章的十項因子
規則自動評分、排名並匯出。

## 現況

| 模組 | 狀態 |
|---|---|
| 評分引擎（PRD §8 十項因子、§10 缺漏處理、§11 總分與排名） | ✅ 完成，82 項測試通過 |
| 參數化（PRD §12 門檻／權重全部外部化） | ✅ 完成 |
| 可追溯性（原始值 → 命中規則 → 得分） | ✅ 完成 |
| 匯出（CSV / Excel 三分頁 / JSON 快照） | ✅ 完成 |
| FinMind 資料層 | ✅ 欄位對照已 PoC 實測回填（2026-09-15） |
| 市值前 N 母體（PRD §3） | ❌ FinMind 免費層級不得做全市場查詢，需贊助層級 token 或外部指定候選母體 |
| 非獨立董監持股／質押（PRD §8.10） | ✅ 改由 TWSE／TPEx 公開 open data 取得（免金鑰） |
| ROIC（PRD §8.9） | ✅ 依需求方 2026-09-15 指定計算式實作 |
| 五年含息總報酬（PRD §9） | ⚠ 介面已留，計算式待 PoC 與 TradingView 交叉驗證 |

## 安裝與執行

```bash
git clone <repo> && cd cheny
pip install -r requirements.txt
```

需求：Python 3.11+。所有指令都從專案根目錄執行，且都要帶 `PYTHONPATH=src`。

### 步驟 1：先跑離線冒煙測試（不需網路、不需 token）

```bash
PYTHONPATH=src python -m twfactor run --top 5 --source fixture
```

用 `fixtures/synthetic_universe.json` 的合成資料（代碼 SYN001–SYN050，非真實個股）
跑完整條管線。看到排名表與三個輸出檔就表示安裝正確。

### 步驟 2：正式評分

先設 token（沒有也能跑，但配額低很多，逐檔取數約 200～300 次就會收到 HTTP 402）：

```bash
export FINMIND_TOKEN=<你的 FinMind token>
```

然後依你的 FinMind 層級擇一：

**A. 有贊助（Sponsor）層級 token —— 符合 PRD §3 的全市場市值前 50**

```bash
PYTHONPATH=src python -m twfactor run --top 50 --source finmind \
    --director-openapi --cache-dir .fincache
```

**B. 免費或註冊層級 —— 在指定候選母體內排名（目前的 PoC 作法）**

```bash
PYTHONPATH=src python -m twfactor run --top 50 --source finmind \
    --stocks-file config/universe_candidates.txt \
    --director-openapi --cache-dir .fincache \
    --quota-wait 600 --quota-retries 18
```

不帶 `--stocks-file` 又沒有贊助層級時，程式會以 `FinMindLevelError` 明確中止，
而不是回傳一份殘缺的母體。排名只在池內成立，執行時會印出警告。

**C. 只想看幾檔**

```bash
PYTHONPATH=src python -m twfactor run --top 3 --source finmind \
    --stocks 2330,2454,2891 --director-openapi --cache-dir .fincache
```

### 常用選項

| 選項 | 用途 |
|---|---|
| `--cache-dir .fincache` | 快取 API 回應。**強烈建議一律加上** —— 配額中斷後重跑不會重抓，改參數重算也不花配額 |
| `--director-openapi` | 從 TWSE／TPEx 公開 open data 取董監持股（免金鑰）。不加則該因子全部 N/A |
| `--director-holdings <csv>` | 改用自行下載的 MOPS 匯出檔 |
| `--quota-wait 600 --quota-retries 18` | 遇 HTTP 402 時等配額重置再續跑，需搭配 `--cache-dir` |
| `--as-of 2026-09-15` | 指定資料基準日，用於推定最新完整年度（避免前視偏誤） |
| `--top N` | 取前 N 檔 |
| `--outdir <dir>` | 輸出目錄，預設 `output/` |
| `--params` / `--fields` | 改用其他參數檔或欄位對照檔 |

### 輸出

每次執行在 `--outdir`（預設 `output/`）產生三個帶時間戳的檔案：

| 檔案 | 內容 |
|---|---|
| `scores_*.csv` | PRD §13.1 結果表，一列一檔，含十項因子分數、總分、排名、標籤、缺漏說明 |
| `scores_*.xlsx` | 同上，分「一般產業」「金融業」「得分理由」三個分頁 |
| `snapshot_*.json` | 完整快照：每個欄位的 provenance（來源／原始欄位名／期間／抓取時間）與每個 N/A 的缺漏原因（PRD §16） |

要查某一項分數為什麼是那個數字，看 `snapshot_*.json` 的 `cards[].factors[].rule`
（命中的規則）與 `value`（判斷所用的原始值）；要查某一項為什麼是 N/A，看 `missing`。

### 跑不動時

| 現象 | 原因與處理 |
|---|---|
| `FinMindLevelError: 帳號層級為 register` | 全市場查詢需贊助層級。改用 `--stocks-file` 或升級 token |
| `FinMindQuotaError`（HTTP 402） | 每小時配額用盡。加 `--cache-dir` 與 `--quota-wait 600 --quota-retries 18` 後重跑，已取得的不會重抓 |
| `⚠ 董監持股來源「上市」取得失敗` | 取不到證交所資料，見下節。取不到的市場會標 N/A，不中斷評分 |
| 某因子大量 N/A | 正常且刻意 —— 解析不到就標 N/A，不以推測值補齊（PRD §19.6）。原因寫在 snapshot 的 `missing` |

### 取不到證交所（TWSE／MOPS）資料

上市的董監持股來自證交所，症狀是收到一份 800 bytes 的 HTML
（「因為安全性考量，您所執行的頁面無法呈現／FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED」）
而不是 JSON。**這其實是兩種不同的阻擋，處理方式不同，先分清楚是哪一種：**

**（一）執行環境自己的對外白名單** —— 連線根本沒出去。

容器／CI／公司網路常有 egress 白名單。徵狀是 TLS 隧道就被拒絕，看到的是
`CONNECT tunnel failed, response 403` 或 `ProxyError`，而不是證交所的擋頁。
實測受影響的網域有 `www.twse.com.tw`、`mopsov.twse.com.tw`、`data.gov.tw`。

處理：把這些網域加進該環境的允許清單即可。Claude Code 的雲端執行環境是在環境的
網路設定調整（見 <https://code.claude.com/docs/en/claude-code-on-the-web>）。

**（二）證交所端的來源 IP 過濾** —— 連得到，但被它的邊界設備擋下。

`openapi.twse.com.tw` 與 `mops.twse.com.tw` 屬於這類。實測結果：

| 試法 | 結果 |
|---|---|
| 四種 User-Agent（curl／Chrome／python-requests／空值） | 全部回同一張 800B 擋頁 |
| 補 `Referer`、`Accept-Language` 模擬瀏覽器 | 一樣被擋 |
| 回應標頭 | 無 `Server`、無 CDN 標記，`Connection: close` |

換句話說**不是認 User-Agent 也不是認標頭，是認來源 IP** —— 機房／雲端 IP 段封鎖，
用來擋自動化爬取。同一個 IP 打櫃買中心（`www.tpex.org.tw`）完全正常，
所以不是「政府網站全擋」，是證交所自己的政策。

處理方式有三條，都不需要規避它的存取控制：

1. **改在一般網路環境執行**（家用／公司網路）。IP 不在封鎖段，
   `--director-openapi` 就會把上市那部分一併補齊。這是最省事的作法。
2. **手動下載後餵檔**：瀏覽器開
   <https://openapi.twse.com.tw/v1/opendata/t187ap11_L> 另存成 CSV，
   改用 `--director-holdings <檔案>.csv`。兩條路徑共用同一套解析
   （職稱白名單、法人重複列去重都一樣），結果相同。
3. **改走政府資料開放平台** `data.gov.tw` 的鏡像（不同網域、不同 IP 政策），
   但它常同時被上述（一）擋住，需先開通白名單。

⚠ 不要用輪換 IP、住宅代理或偽裝指紋去繞過（二）。那是規避對方刻意設置的存取控制，
違反使用條款，且一旦被認定為濫用，影響範圍會大於單一台機器。
本專案不提供、也不應加入這類手段。

## 專案結構

```
config/scoring_params.yaml     # PRD §12 所有門檻、滿分、權重（程式不得寫死）
config/finmind_fields.yaml     # FinMind 欄位對照與期間語意，PoC 產出物
config/universe_candidates.txt # 免費層級用的候選母體（非全市場掃描，見下）
src/twfactor/
  models.py                  # CompanyFacts / FactorScore / ScoreCard
  params.py                  # 設定載入與一致性驗證（16 分、9 分自檢）
  scoring/factors.py         # 十項因子純函式，逐項保留命中規則
  scoring/engine.py          # N/A 處理、總分、金融業矩陣、標籤
  scoring/ranking.py         # 競賽排名跳號，一般／金融不混排
  sources/finmind.py         # FinMind 取數與標準化
  sources/fixtures.py        # 離線合成資料（SYN*，非真實個股）
  export.py                  # PRD §13.1 結果表與匯出
  cli.py
tests/                       # 82 項測試，含 PRD §15 全部邊界案例
```

## 為何門檻都在 YAML

PRD §12 要求所有分數、門檻與期間以設定檔管理，以支撐第二階段的因子權重最佳化。
`params.py` 在載入時會自檢十項 `max_score` 合計是否等於 16、金融業五項是否等於 9，
設定寫錯會在啟動時就報錯，而不是在評分中途產生錯誤分數。

權重第一版全為 1.0，`raw_total` 與 `weighted_total` 分開保存（PRD §12 明訂不可覆蓋原始結果）。

## PoC 實測結果（2026-09-15）

以 `probe-schema --stocks 2330,2891,8299,1256,4195` 實測，原始清單在
`poc/poc_schema_20260915.json`，已回填 `config/finmind_fields.yaml` 的 `confirmed`。
解析不到的欄位一律標 N/A —— **不以推測值補齊**（PRD §19.6）。

`probe-schema` 會一併印出各 dataset 的期間語意推斷（`period_semantics_observed`），
但那只是輔助判讀：它靠「同年度四季絕對值是否逐季遞增」猜測，年內變號的科目略過不計，
資料稀疏的小型股仍會猜不準（例如 4195 只有三個季度，現金流量表被猜成 quarterly）。
**權威值是 `config/finmind_fields.yaml` 的 `period_semantics`**，由人工比對公告數確認。

### 一、三張報表的期間語意不同（會直接影響數值正確性）

| dataset | 期間語意 | 年度值取法 |
|---|---|---|
| TaiwanStockFinancialStatements | 單季值 | 四季相加 |
| TaiwanStockCashFlowsStatement | 年初至今累計 | 取該年度 12-31 當期值 |
| TaiwanStockBalanceSheet | 期末存量 | 取該年度 12-31 當期值 |

以 2330 FY2024 驗證：EPS 8.70+9.56+12.55+14.45 = 45.26（公告 45.25）；
營業活動現金流 Q1 436.3B → Q4 1,826.2B，Q4 即全年（公告 1,826.2B）。
**舊版程式把現金流量表當成單季值四季相加，會把營運現金流與資本支出高估約 2.5 倍**，
本次已依 `period_semantics` 分開處理，並在 `tests/test_finmind_periods.py` 以上述實際數列固定行為。

### 二、確認的欄位名稱

`Revenue`／`OperatingIncome`／`EPS`／`Equity`／`CashFlowsFromOperatingActivities`／
`PropertyAndPlantAndEquipment`／`CashEarningsDistribution`／`NumberOfSharesIssued`
皆如預期存在。與原 `candidates` 猜測不同之處：

- **淨利**：一般產業為 `IncomeAfterTaxes`，金融業（2891）拼法為 `IncomeAfterTax`。
  原 candidates 首位的 `TotalConsolidatedProfitForThePeriod` 實為「本期綜合損益總額」
  （2330 FY2024Q4 為 412.4B，而非淨利 374.5B），語意錯誤，已移除。
- **利息費用**：損益表沒有這個科目，實際出現在**現金流量表**的收益費損調整項
  （`InterestExpense`，正值）。欄位對照的 dataset 已改為 `cash_flow`。
- **長期借款**：實際拼法是 `LongtermBorrowings`（小寫 t），原設定拼成 `LongTermBorrowings`
  永遠解析不到。現以 `LongtermBorrowings + BondsPayable` 合計。
- FinMind 資產負債表**沒有**「租賃負債－非流動」科目，故長期負債不含長期融資租賃（低估）。
- FinMind **不回傳餘額為零的會計科目**，因此「確實無長期借款」與「資料缺漏」在長表上
  長得一樣。需求方 2026-09-15 確認：該年度期末資產負債表本身有回傳、卻完全沒有任何
  借款科目時，認定長期借款為 0 並照常評分（影響大立光、信驊、創意等）；
  整年沒有期末資產負債表才標 N/A。此判定會寫進 snapshot 的 provenance note。
- 現金流量表**沒有**取得無形資產等科目，資本支出僅含不動產廠房設備（低估）。
- 金融業（2891）無 `OperatingIncome`、無 `PropertyAndPlantAndEquipment`、
  無長期借款科目 → 對應因子標 N/A，符合 PRD §8.12 部分評分。

### 三、免費層級不得做全市場查詢（影響 PRD §3 母體）

不帶 `data_id` 的全市場查詢一律回 HTTP 400。實測仍開放全市場查詢的只有
`TaiwanStockInfo`、`TaiwanStockInfoWithWarrant`、`TaiwanStockTotalMarginPurchaseShortSale`；
`TaiwanStockPrice`、`TaiwanStockShareholding`、`TaiwanStockMarketValue`、`TaiwanStockPER` 全被擋。

**註冊帳號的 token 不解決這件事。** 實測三種層級：

| 層級 | 全市場查詢 | 錯誤訊息 |
|---|---|---|
| 匿名（無 token） | ❌ | `Your level is free.` |
| 註冊帳號 token | ❌ | `Your level is register.` |
| 贊助（Sponsor） | 未測（需付費） | — |

註冊 token 的作用是**提高每小時請求配額**（匿名層級跑約 200～300 次就會收到 HTTP 402），
逐檔查詢因此順暢很多，但全市場排名仍然打不開。

因此**「市值前 N」在免費層級無法成立**：程式無法對全市場 4 碼普通股排名。
處理方式是兩條路徑，且兩者都不會產生推測出來的市值：

1. 有贊助（Sponsor）層級 `FINMIND_TOKEN` → 不帶 `--stocks-file`，走全市場排名（PRD §3 原義）。
2. 無 Sponsor token → 以 `--stocks-file` 指定候選母體，程式仍逐檔以
   `收盤價 × 發行股數` 實測市值並排名，但**排名只在池內成立**，池外是否有更大的公司無法驗證。
   程式會在執行時印出這個警告，`config/universe_candidates.txt` 也在檔頭寫明。

本次 PoC 走路徑 2。候選池由 103 檔擴充為 234 檔後，**新增的 131 檔裡只有 3 檔擠進前 50**
（致茂 2360 市值第 22 名、環球晶 6488 第 43 名、景碩 3189 第 48 名），
前 50 的市值下緣由 0.374 兆升到 0.386 兆。

這組數字說明兩件事：擴充池確實抓回原本漏掉的公司（致茂 0.885 兆排到第 22 名，
原本整個漏掉），而且擴充的邊際效益正在收斂。但**收斂不等於完備** ——
只要不是全市場掃描，就無法證明池外沒有更大的公司。要真正符合 PRD §3，
仍然需要贊助層級 token。

## FinMind 作為 StockBoss 替代來源：其餘已知落差

1. **董監持股／質押（PRD §8.10，1 分）**：FinMind 不提供，但**證交所／櫃買中心的
   公開 open data 就有，不需申請金鑰**：

   | 市場 | 端點 |
   |---|---|
   | 上市 | `https://openapi.twse.com.tw/v1/opendata/t187ap11_L` |
   | 上櫃 | `https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap11_O` |

   ```bash
   PYTHONPATH=src python -m twfactor run --top 50 --source finmind \
       --stocks-file config/universe_candidates.txt --director-openapi
   ```

   也可改吃自行下載的 CSV：`--director-holdings <檔案>.csv`，兩者共用同一套解析。

   **這份資料有兩個會直接算錯的坑，PoC 實測後都已處理**：

   - **它是「內部人」全表，不是董監表。** 職稱涵蓋總經理、副總經理、協理、經理、
     會計／財務部門主管、大股東、其他。只排除獨立董事並不夠 ——
     必須以「職稱含董事或監察人」白名單挑選，再排除獨立董事。
   - **法人董事佔多席時，同一法人的持股會每席重複列示。** 環球晶（6488）的
     中美矽晶 223,007,864 股佔兩席、列了兩次，直接加總會超過其發行股數。
     實測 887 家上櫃公司中 **380 家（43%）有此情形**，故以
     （姓名, 目前持股, 設質股數）去重。這不是邊緣案例。

   這份資料沒有發行股數，持股比例的分母取自 FinMind 的 `NumberOfSharesIssued`
   （取得母體時已連同市值一併取回）。缺職稱欄時整項回 N/A，缺質押欄時質押回 N/A
   （PRD §19.4 的兩個必測點）。

   ⚠ **本 PoC 環境只跑得到上櫃**：`openapi.twse.com.tw` 與 `mops.twse.com.tw`
   對本機 IP 回「因為安全性考量，您所執行的頁面無法呈現」，屬 IP 阻擋而非權限問題，
   從一般網路環境執行即可取得上市資料。取不到的來源會印出警告、該市場標 N/A，
   不會中斷評分。本次前 50 檔中 3 檔上櫃（信驊、環球晶、群聯）已評分，
   47 檔上市仍為 N/A。

   驗證：信驊持股 20.90%／質押 4.95%、環球晶 47.17%／0%、群聯 13.36%／7.79%，
   與原始資料手算逐檔吻合。
2. **Interest Coverage（PRD §8.7，2 分）**：PRD 第一版規定「直接沿用 StockBoss 值」，
   FinMind 無此欄位，本專案改以 `TTM 營業利益 ÷ |TTM 利息支出|` 重建
   （營業利益取自損益表單季值、利息費用取自現金流量表累計值，各自依期間語意還原 TTM）。
   此為與 PRD 的實質偏離，**需求方已於 2026-09-15 確認接受重建值**；
   仍須留意分數無法與人工 StockBoss 評分表逐項對齊。
3. **ROIC（PRD §8.9，1 分）**：PRD 未定義計算式，需求方 2026-09-15 指定為

   ```
   ROIC        = 稅後營業利益 ÷（股東權益 ＋ 有息負債）    皆取期末值
   稅後營業利益 = 年度營業利益 × (1 − 有效稅率)
   有效稅率     = 年度所得稅費用 ÷ 年度稅前淨利            同一年度
   有息負債     = 期末 短期借款 ＋ 長期借款 ＋ 應付公司債
   ```

   實作時另外做了兩個判斷，都記在 `config/finmind_fields.yaml` 的 `roic` 區塊：

   - **稅前淨利 ≤0 的年度沒有可導出的有效稅率**，該公司整項標 N/A，
     不以推估稅率補齊（PRD §19.6）。前 50 檔中有 6 檔因此為 N/A（如南亞科）。
   - **有息負債不含一年內到期長期負債與租賃負債**，FinMind 無此科目，故分母可能低估、
     ROIC 略為高估。這與 §8.6 長期負債的落差同源。

   驗證：2330 五年 ROIC 為 20.2／26.2／17.9／20.9／25.6%，2454 為 19.6／24.8／17.0／22.6／21.4%。
4. **股息支付率的分子分母期間不一致**（PRD §8.4）：需求方 2026-09-15 確認採

   ```
   TTM Payout Ratio = 最新完整年度現金股利 ÷ TTM EPS
   ```

   分子是年度值、分母是最近四季滾動值，兩者期間不對齊，這是刻意接受的：
   PRD §8.4 本來就把這項定義成 TTM 指標，而股利是一年一議的年度決策，
   沒有對應的 TTM 分子。

   **已知偏誤**：EPS 快速成長的公司分母跑在分子前面，支付率會被系統性低估，
   因而偏向落在 `<50%` 的滿分區間。以 2330 為例，19.0 ÷ 86.28 = 22.0% → 2 分。
   若之後要與人工評分表對齊而發現落差，這是第一個該檢查的地方。

   邊界行為（`tests/test_factors.py` 已涵蓋）：TTM EPS < 0 → 0 分（不是 N/A）；
   TTM EPS = 0 → N/A（比率無定義，不視為 0%）。

5. **淨利率／ROE**：PRD 未定義是否用歸母淨利、期末或平均權益；目前採
   稅後淨利 ÷ 營收、稅後淨利 ÷ 期末權益，需與人工評分表比對後定案。
6. **金融業判定**：PRD §4 要求 StockBoss／Goodinfo 雙分類並存互不覆蓋，但未指定
   哪一欄觸發 §8.12 的部分評分。目前暫以 FinMind 分類判定，待需求方指定主判定欄位。
7. **「近五個完整年度」時點錨**：PRD 未定義。本專案以年報申報期限 3/31 推定
   （4 月起採前一年度），規則寫在 `FinMindSource.latest_complete_fy`，可調整但可重現。

## 測試

```bash
python -m pytest tests/ -q
```

`tests/test_finmind_periods.py` 以 2330 FY2024 實際數列固定三張報表的年度／TTM 彙總行為。
`tests/test_factors.py` 逐項覆蓋 PRD §15 點名的邊界值：EPS 負值與衰退恢復、
股息下降剛好 5%、Payout 0%／50%／100%、ROE 剛好 12%／15%、股東權益 ≤0、
質押剛好 20%、無利息負擔、金融業部分評分、未滿五年、單項缺漏。

## 資料聲明

`fixtures/synthetic_universe.json` 為**合成測試資料**（代碼 SYN001–SYN050），
不對應任何真實上市櫃公司，僅供驗證管線行為，不得用於投資判斷。

`config/universe_candidates.txt` 為人工提供的候選母體，**不是全市場掃描的結果**，
不得視為「市值前 N」的權威定義；其存在只是為了讓免費層級能跑完整條管線。

`output/` 內的評分結果由真實 FinMind 資料產生，但受上述資料落差影響
（董監持股與 ROIC 全數 N/A、長期負債不含融資租賃、利息保障倍數為重建值），
不得直接用於投資判斷。
