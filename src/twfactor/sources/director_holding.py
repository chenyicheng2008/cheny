"""非獨立董監持股與質押（PRD §8.10、§19.4）。

FinMind 不提供此資料，需另接 MOPS 公開資訊觀測站的
「董事、監察人持股餘額明細資料」（t16sn02）。

⚠ 本檔只實作「讀檔」路徑，沒有實作線上抓取。原因是 PoC 執行環境無法連到 MOPS
  （mops.twse.com.tw 對本機 IP 回「因為安全性考量，您所執行的頁面無法呈現」），
  因此線上解析程式碼無從驗證。依 PRD §19.6 的原則，未經實測的解析邏輯不應出貨，
  故改為由使用者提供 MOPS 匯出檔，解析與欄位對應都可被測試涵蓋。

PRD §19.4 要求 PoC 確認兩件事，兩者都靠欄位是否存在來判定，不做推估：
  1. 來源能否排除獨立董事 —— 需要有「職稱」欄可辨識獨立董事；
     沒有這欄時本 provider 回 (None, None)，該因子標 N/A，而不是把獨立董事算進去。
  2. 來源是否提供質押比例 —— 沒有質押欄時質押回 None，
     評分引擎會因此無法套用「質押 > 20% 取消得分」而標 N/A。
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

# MOPS t16sn02 的欄名在不同匯出管道（網頁另存、API、第三方轉檔）略有差異，
# 因此以「同義字清單」比對，全部比不到就視為該欄不存在。
STOCK_ID_COLUMNS = ("公司代號", "股票代號", "證券代號", "stock_id")
TITLE_COLUMNS = ("職稱", "身分別", "職務", "title")
HOLDING_SHARES_COLUMNS = ("目前持股", "持股張數", "選任時持股", "目前持有股數", "holding_shares")
HOLDING_PCT_COLUMNS = ("目前持股比例", "持股比例", "holding_pct")
PLEDGE_SHARES_COLUMNS = ("設質股數", "質押股數", "pledged_shares")
PLEDGE_PCT_COLUMNS = ("設質比例", "質押比例", "pledge_pct")
SHARES_OUTSTANDING_COLUMNS = ("發行股數", "已發行股份總數", "shares_issued")

# PRD §8.10 只算「非獨立」董監，故職稱含以下字樣者一律排除。
INDEPENDENT_MARKERS = ("獨立董事", "獨董", "independent")


def _pick(row: dict, names: tuple[str, ...]) -> str | None:
    for n in names:
        if n in row and str(row[n]).strip():
            return str(row[n]).strip()
    return None


def _num(text: str | None) -> float | None:
    if text is None:
        return None
    cleaned = text.replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


class CsvDirectorHoldingProvider:
    """由 MOPS t16sn02 匯出檔（CSV）提供非獨立董監持股與質押。

    持股比例優先採檔案自帶的比例欄；沒有比例欄但同時有持股股數與發行股數時才自行計算。
    兩者都湊不齊就回 None —— 不以任何方式推估（PRD §19.6）。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.name = f"MOPS t16sn02 匯出檔（{self.path.name}）"
        self._by_stock: dict[str, tuple[float | None, float | None]] = {}
        self.has_title_column = False
        self.has_pledge_column = False
        self._load()

    def _load(self) -> None:
        with open(self.path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            return
        header = rows[0].keys()
        self.has_title_column = any(c in header for c in TITLE_COLUMNS)
        self.has_pledge_column = any(c in header for c in PLEDGE_PCT_COLUMNS + PLEDGE_SHARES_COLUMNS)

        holdings: dict[str, float] = defaultdict(float)
        pledges: dict[str, float] = defaultdict(float)
        pct_direct: dict[str, float] = defaultdict(float)
        outstanding: dict[str, float] = {}

        for row in rows:
            sid = _pick(row, STOCK_ID_COLUMNS)
            if not sid:
                continue
            sid = sid.strip()
            title = _pick(row, TITLE_COLUMNS) or ""
            if any(m in title.lower() or m in title for m in INDEPENDENT_MARKERS):
                continue                       # 排除獨立董事（PRD §8.10）
            shares = _num(_pick(row, HOLDING_SHARES_COLUMNS))
            if shares:
                holdings[sid] += shares
            pct = _num(_pick(row, HOLDING_PCT_COLUMNS))
            if pct:
                pct_direct[sid] += pct
            pledged = _num(_pick(row, PLEDGE_SHARES_COLUMNS))
            if pledged:
                pledges[sid] += pledged
            total = _num(_pick(row, SHARES_OUTSTANDING_COLUMNS))
            if total:
                outstanding[sid] = total

        for sid in set(holdings) | set(pct_direct):
            if pct_direct.get(sid):
                hold_pct = pct_direct[sid]
            elif holdings.get(sid) and outstanding.get(sid):
                hold_pct = holdings[sid] / outstanding[sid] * 100
            else:
                hold_pct = None

            if not self.has_pledge_column or not holdings.get(sid):
                pledge_pct = None
            else:
                pledge_pct = pledges.get(sid, 0.0) / holdings[sid] * 100

            self._by_stock[sid] = (hold_pct, pledge_pct)

    def get(self, stock_id: str) -> tuple[float | None, float | None]:
        if not self.has_title_column:
            # 無法辨識並排除獨立董事，寧可標 N/A 也不送出含獨立董事的持股（PRD §8.10）
            return None, None
        return self._by_stock.get(stock_id, (None, None))
