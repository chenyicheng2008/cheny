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
| 非獨立董監持股／質押（PRD §8.10） | ⚠ FinMind 不提供；已實作 MOPS 匯出檔 provider，待餵入資料 |
| ROIC（PRD §8.9） | ✅ 依需求方 2026-09-15 指定計算式實作 |
| 五年含息總報酬（PRD §9） | ⚠ 介面已留，計算式待 PoC 與 TradingView 交叉驗證 |

## 安裝與執行

```bash
pip install -r requirements.txt

# 離線驗證管線（合成資料，不需網路）
PYTHONPATH=src python -m twfactor run --top 50 --source fixture

# 正式：以 FinMind 取全市場市值前 50 檔評分（需贊助層級 token，見下節）
export FINMIND_TOKEN=<your sponsor token>
PYTHONPATH=src python -m twfactor run --top 50 --source finmind

# 免費／匿名層級：在指定候選母體內排名，並快取回應以便配額中斷後續跑
PYTHONPATH=src python -m twfactor run --top 50 --source finmind \
    --stocks-file config/universe_candidates.txt \
    --cache-dir .fincache --quota-wait 600 --quota-retries 18

# PoC：探測 FinMind 財報 dataset 的實際欄位名稱與期間語意
PYTHONPATH=src python -m twfactor probe-schema --stocks 2330,2891,8299,1256,4195
```

輸出於 `output/`：`scores_*.csv`、`scores_*.xlsx`（一般產業／金融業／得分理由三分頁）、
`snapshot_*.json`（含 provenance 與缺漏原因，PRD §16 資料保存）。

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

不帶 `data_id` 的全市場查詢一律回 HTTP 400
（`Your level is free. Please update your user level.`）。實測仍開放全市場查詢的只有
`TaiwanStockInfo`、`TaiwanStockInfoWithWarrant`、`TaiwanStockTotalMarginPurchaseShortSale`；
`TaiwanStockPrice`、`TaiwanStockShareholding`、`TaiwanStockMarketValue`、`TaiwanStockPER` 全被擋。
另外匿名層級每小時請求數有上限，超過回 HTTP 402。

因此**「市值前 N」在免費層級無法成立**：程式無法對全市場 4 碼普通股排名。
處理方式是兩條路徑，且兩者都不會產生推測出來的市值：

1. 有贊助（Sponsor）層級 `FINMIND_TOKEN` → 不帶 `--stocks-file`，走全市場排名（PRD §3 原義）。
2. 無 token → 以 `--stocks-file` 指定候選母體，程式仍逐檔以
   `收盤價 × 發行股數` 實測市值並排名，但**排名只在池內成立**，池外是否有更大的公司無法驗證。
   程式會在執行時印出這個警告，`config/universe_candidates.txt` 也在檔頭寫明。

本次 PoC 走路徑 2（環境未設 `FINMIND_TOKEN`）。

## FinMind 作為 StockBoss 替代來源：其餘已知落差

1. **董監持股／質押（PRD §8.10，1 分）**：PoC 已確認 FinMind 公開 dataset 未提供，且無法區分獨立董事。
   已實作 `sources/director_holding.py` 的 `CsvDirectorHoldingProvider`，
   吃 MOPS「董事、監察人持股餘額明細資料」（t16sn02）的 CSV 匯出檔：

   ```bash
   PYTHONPATH=src python -m twfactor run --top 50 --source finmind \
       --stocks-file config/universe_candidates.txt \
       --director-holdings <你的 t16sn02 匯出檔>.csv
   ```

   解析時排除職稱含「獨立董事／獨董」者（PRD §8.10）。依 PRD §19.4 的兩個必測點，
   檔案缺「職稱」欄時整項回 N/A（不送出混入獨立董事的持股），缺質押欄時質押回 N/A。

   ⚠ **沒有實作線上抓取**：PoC 環境連不到 MOPS
   （`mops.twse.com.tw` 對本機 IP 回「因為安全性考量，您所執行的頁面無法呈現」），
   線上解析邏輯無從驗證，依 §19.6 不出貨未經實測的解析程式碼。
   未餵入檔案時此因子仍為 N/A，一般產業實際可評滿分為 15，金融業為 8。
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
4. **淨利率／ROE**：PRD 未定義是否用歸母淨利、期末或平均權益；目前採
   稅後淨利 ÷ 營收、稅後淨利 ÷ 期末權益，需與人工評分表比對後定案。
5. **金融業判定**：PRD §4 要求 StockBoss／Goodinfo 雙分類並存互不覆蓋，但未指定
   哪一欄觸發 §8.12 的部分評分。目前暫以 FinMind 分類判定，待需求方指定主判定欄位。
6. **「近五個完整年度」時點錨**：PRD 未定義。本專案以年報申報期限 3/31 推定
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
