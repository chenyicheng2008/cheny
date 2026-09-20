# 臺股財務因子自動評分系統 — 專案脈絡

PRD v1.1 第一階段。以 FinMind 取代 StockBoss 作為財報來源，依 PRD §8 的十項因子
自動評分、排名、匯出。完整說明見 `README.md`；本檔是給 AI 助理的操作脈絡。

## 執行

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m pytest tests/ -q          # 161 項，應全綠
PYTHONPATH=src python -m twfactor run --top 5 --source fixture   # 離線冒煙測試
```

Windows PowerShell 需改用 `$env:PYTHONPATH="src"` 並設 `$env:PYTHONUTF8=1`
（終端輸出含 `⚠ ≥ ≤ −`，不在 cp950 內，重導向到檔案會 UnicodeEncodeError）。

正式取數需 `FINMIND_TOKEN`。**token 只放環境變數，絕不寫入任何檔案或 commit。**

## 不可違反的專案原則

1. **N/A 與 0 分嚴格區分**（PRD §10）。解析不到的欄位標 N/A，不以推測值補齊、
   不按比例放大、不補分。N/A 會降低該公司的「實際可評滿分」，0 分不會。
2. **未經實測的解析邏輯不出貨**（PRD §19.6）。欄位對照一律先用 `probe-schema`
   實測，回填 `config/finmind_fields.yaml` 的 `confirmed`；`candidates` 只是猜測清單。
3. **門檻與權重不得寫死在程式裡**（PRD §12）。全部在 `config/scoring_params.yaml`，
   `params.py` 於載入時自檢十項滿分合計為 16、金融業五項為 9。
4. **可追溯性**：每個分數都要能回推到命中規則與原始值，寫入 snapshot 的
   `factors[].rule` / `.value`；每個 N/A 都要有 `missing` 原因。
5. 金融業依 §8.12 部分評分（9 分制），與一般產業分池排名，不混排。

## 資料來源的已知陷阱（都是實測踩過的，別重蹈）

- **三張報表期間語意不同**：損益表是單季值、現金流量表是年初至今累計、
  資產負債表是期末存量。彙總方式寫在 `finmind_fields.yaml` 的 `period_semantics`，
  用錯會讓現金流高估約 2.5 倍。
- **FinMind 不回傳餘額為零的會計科目**，故「確實無長期借款」與「資料缺漏」
  長得一樣。已確認的判定：該年度有期末資產負債表卻無任何借款科目 → 認定為 0。
- **董監持股用 open data 而非 FinMind**（`--director-openapi`）。那份資料是
  「內部人」全表（含總經理、大股東），須以職稱白名單挑出董監再排除獨立董事；
  且法人董事佔多席時同一法人持股會重複列示，須去重（887 家有 380 家中招）。
- **FinMind 免費與註冊層級都不得做全市場查詢**，需贊助層級。無 token 時以
  `--stocks-file` 指定候選母體，排名僅在池內成立。
- **證交所對機房 IP 封鎖**（`openapi.twse.com.tw`、`mops.twse.com.tw`）。
  這是 IP 政策不是權限問題，從一般網路環境執行即可；不得以輪換 IP 或代理規避。

## 延伸文件（需要理由或細節時再讀，不必預載）

- `docs/decisions.md` —— 決策紀錄。PRD 未規範或實作中另行判斷的八項事項，
  每則含背景、實證數據與結論。**要改動既有行為前先讀這份**，多數「看起來很怪」
  的作法背後都有實測依據。
- `docs/eps-factor-redesign.md` —— EPS 因子改版提案的完整分析：現行規則的三個
  結構性問題、為何標準差／CV 方案不可行、候選公式的推導與穩健性實測。

## 待需求方決定（未定案前不要自行實作）

- **EPS 因子改版**：`scripts/eps_score.py` 是候選公式
  （`Score = 2 × G(g) × Q(R²) × P(D)`，對 ln EPS 迴歸），尚未取代 `factors.py`
  的現行明線規則。未決兩點：金融業是否用較低的成長門檻、分數採連續或分箱。
  完整分析見 `docs/eps-factor-redesign.md`。
- **XBRL 來源接入**：使用者本機有 XBRL 原始資料，待提供樣本後撰寫萃取器。
  台股 XBRL 的 EPS 須注意元素名稱隨分類標準版本而異、同檔多 context 要挑對期間、
  金融／金控／保險業元素集不同 —— 未見樣本前不要臆測欄位。

## 結構

```
config/scoring_params.yaml      PRD §12 所有門檻、滿分、權重
config/finmind_fields.yaml      FinMind 欄位對照與期間語意（PoC 產出物）
config/universe_candidates.txt  免費層級用的候選母體（非全市場掃描）
src/twfactor/scoring/           十項因子純函式、N/A 處理、排名
src/twfactor/sources/           FinMind 取數、董監持股 provider
scripts/eps_score.py            EPS 因子候選公式，吃本機 CSV，不連網
docs/                           決策紀錄與改版提案（理由與實證）
poc/                            PoC 實測結果（schema 探測、前 50 檔評分）
output/                         執行產出，已 gitignore
```
