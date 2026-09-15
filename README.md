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
| FinMind 資料層 | ⚠ 已實作，**欄位對照待 PoC 實測確認**（見下） |
| 非獨立董監持股／質押（PRD §8.10） | ❌ FinMind 不提供，需另接來源，目前標 N/A |
| 五年含息總報酬（PRD §9） | ⚠ 介面已留，計算式待 PoC 與 TradingView 交叉驗證 |

## 安裝與執行

```bash
pip install -r requirements.txt

# 離線驗證管線（合成資料，不需網路）
PYTHONPATH=src python -m twfactor run --top 50 --source fixture

# 正式：以 FinMind 取市值前 50 檔評分
export FINMIND_TOKEN=<your token>
PYTHONPATH=src python -m twfactor run --top 50 --source finmind

# PoC：探測 FinMind 財報 dataset 的實際欄位名稱
PYTHONPATH=src python -m twfactor probe-schema --stocks 2330,2891,8299,1256,4195
```

輸出於 `output/`：`scores_*.csv`、`scores_*.xlsx`（一般產業／金融業／得分理由三分頁）、
`snapshot_*.json`（含 provenance 與缺漏原因，PRD §16 資料保存）。

## 專案結構

```
config/scoring_params.yaml   # PRD §12 所有門檻、滿分、權重（程式不得寫死）
config/finmind_fields.yaml   # FinMind 欄位對照，PoC 產出物
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

## FinMind 作為 StockBoss 替代來源：已知落差

執行 `probe-schema` 回填 `config/finmind_fields.yaml` 的 `confirmed` 欄位前，
財報欄位一律以 `candidates` 逐一嘗試，解析不到就標 N/A —— **不以推測值補齊**（PRD §19.6）。

已確認的落差：

1. **董監持股／質押（PRD §8.10，1 分）**：FinMind 公開 dataset 未提供，且無法區分獨立董事。
   需另接 MOPS 或 Goodinfo，介面為 `sources/base.py` 的 `DirectorHoldingProvider`。
   未接上時一般產業實際可評滿分為 15，金融業為 8。
2. **Interest Coverage（PRD §8.7，2 分）**：PRD 第一版規定「直接沿用 StockBoss 值」，
   FinMind 無此欄位，本專案改以 `TTM 營業利益 ÷ |TTM 利息支出|` 重建。
   **此為與 PRD 的實質偏離，需求方需確認**，否則分數無法與人工評分表對齊。
3. **ROIC（PRD §8.9，1 分）**：PRD 未定義計算式，目前一律標 N/A，待需求方確認
   NOPAT 與投入資本的定義後實作。
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

`tests/test_factors.py` 逐項覆蓋 PRD §15 點名的邊界值：EPS 負值與衰退恢復、
股息下降剛好 5%、Payout 0%／50%／100%、ROE 剛好 12%／15%、股東權益 ≤0、
質押剛好 20%、無利息負擔、金融業部分評分、未滿五年、單項缺漏。

## 資料聲明

`fixtures/synthetic_universe.json` 為**合成測試資料**（代碼 SYN001–SYN050），
不對應任何真實上市櫃公司，僅供驗證管線行為，不得用於投資判斷。
