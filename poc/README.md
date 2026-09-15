# FinMind Data Source PoC 結果（2026-09-15）

PRD v1.1 §19 的 PoC 產出物。三個檔案都由真實 FinMind 資料產生，未經人工修改。

| 檔案 | 內容 |
|---|---|
| `poc_schema_20260915.json` | `probe-schema --stocks 2330,2891,8299,1256,4195` 的原始輸出：三張報表各自實際出現的 `type` 清單，以及期間語意的輔助推斷（權威值在 `config/finmind_fields.yaml`） |
| `scores_top50_20260915.csv` | 候選母體內市值前 50 檔的評分結果（PRD §13.1 結果表） |
| `snapshot_top50_20260915.json` | 同一次執行的完整快照，含每個欄位的 provenance 與每個 N/A 的缺漏原因（PRD §16） |

重現方式：

```bash
PYTHONPATH=src python -m twfactor probe-schema --stocks 2330,2891,8299,1256,4195
PYTHONPATH=src python -m twfactor run --top 50 --source finmind \
    --stocks-file config/universe_candidates.txt \
    --cache-dir .fincache --quota-wait 600 --quota-retries 18
```

## 讀這份結果前必須知道的四件事

1. **「前 50」不是全市場前 50。** FinMind 不允許不帶 `data_id` 的全市場查詢
   （匿名與註冊 token 皆被擋，需贊助層級），程式無法對全市場排名。這 50 檔是在
   `config/universe_candidates.txt` 這個人工候選池（234 檔）內，
   由程式逐檔以 `收盤價 × 發行股數` 實測市值後取前 50。
   池內市值全是實測值，但池外是否有更大的公司無法驗證。
   候選池由 103 檔擴為 234 檔時，新增的 131 檔只有 3 檔擠進前 50（致茂、環球晶、景碩），
   顯示擴充的邊際效益在收斂，但收斂不等於完備。
2. **非獨立董監持股（PRD §8.10）50 檔全部 N/A。** FinMind 不提供此資料。
   已實作 MOPS t16sn02 匯出檔的 provider（`--director-holdings`），但本次執行未餵入檔案
   （PoC 環境連不到 MOPS）。因此一般產業實際可評滿分為 15 而非 16，金融業為 8 而非 9。
3. **ROIC（PRD §8.9）已依需求方 2026-09-15 指定的計算式實作**
   （稅後營業利益 ÷（股東權益＋有息負債），期末值）。一般產業 38 檔中 32 檔可評，
   6 檔因五年內有稅前淨利 ≤0 的年度、無法導出有效稅率而標 N/A。
4. **利息保障倍數為重建值**，非 PRD 規定的 StockBoss 原值，
   計算式為 `TTM 營業利益 ÷ |TTM 利息費用|`。金融業無營業利益科目故 N/A。

另有兩項來源限制會讓分數偏保守：長期負債不含長期融資租賃（FinMind 無此科目）、
資本支出僅含不動產廠房設備。

長期負債另有一個判定：FinMind 不回傳餘額為零的科目，故該年度期末資產負債表有回傳
卻無任何借款科目時，認定長期借款為 0 並照常評分（需求方 2026-09-15 確認）；
此判定會記在 snapshot 的 provenance note。詳見專案根目錄 README 的「PoC 實測結果」一節。

**這份結果不得用於投資判斷。**
