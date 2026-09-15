"""非獨立董監持股與質押（PRD §8.10、§19.4）。

資料來源是 MOPS「董監事持股餘額明細資料」（t187ap11 / t16sn02），
公開 open data 即可取得，不需申請金鑰：

    上市（_L）  https://openapi.twse.com.tw/v1/opendata/t187ap11_L
    上櫃（_O）  https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap11_O

PoC（2026-09-15）從這份資料實測出兩件會直接算錯的事，兩者都在 _aggregate 處理：

1. **它是「內部人」全表，不是董監表。** 職稱包含總經理、協理、副總經理、經理、
   會計／財務部門主管、大股東、其他。PRD §8.10 只要非獨立董監，
   因此以「職稱含董事或監察人」白名單挑選，再排除獨立董事 ——
   只排除獨立董事而不設白名單會把總經理、大股東一起算進去。
2. **法人董事佔多席時，同一法人的持股會每席重複列一次。** 例如環球晶（6488）的
   中美矽晶 223,007,864 股出現兩列，直接加總會得到 446M 股、超過其發行股數。
   實測 887 家上櫃公司中有 380 家（43%）有此情形，故以
   （姓名, 目前持股, 設質股數）去重。這不是邊緣案例，不能不處理。

這份資料沒有發行股數，持股比例需由外部提供發行股數（本專案取自 TWSE／TPEx
公司基本資料的已發行普通股數），湊不齊就回 None，不推估。
"""

from __future__ import annotations

import csv
import json
import time
from collections import defaultdict
from pathlib import Path

import requests

TWSE_LISTED_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap11_L"
TPEX_OTC_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap11_O"

# 同一份資料經不同管道匯出時欄名略有差異，故以同義字清單比對；全部比不到即視為該欄不存在。
STOCK_ID_COLUMNS = ("公司代號", "股票代號", "證券代號", "stock_id")
NAME_COLUMNS = ("姓名", "內部人姓名", "name")
TITLE_COLUMNS = ("職稱", "身分別", "職務", "title")
HOLDING_SHARES_COLUMNS = ("目前持股", "持股張數", "目前持有股數", "holding_shares")
HOLDING_PCT_COLUMNS = ("目前持股比例", "持股比例", "holding_pct")
PLEDGE_SHARES_COLUMNS = ("設質股數", "質押股數", "pledged_shares")
PLEDGE_PCT_COLUMNS = ("設質比例", "質押比例", "pledge_pct")
SHARES_OUTSTANDING_COLUMNS = ("發行股數", "已發行股份總數", "shares_issued")

# PRD §8.10 的「非獨立董監」：職稱須含董事或監察人（涵蓋董事長、副董事長、常務董事、
# 法人代表人），且不得為獨立董事。
DIRECTOR_MARKERS = ("董事", "監察人")
INDEPENDENT_MARKERS = ("獨立", "獨董", "independent")


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


def is_non_independent_director(title: str) -> bool:
    """職稱是否為非獨立董監。白名單優先於黑名單，兩者都要過。"""
    low = title.lower()
    if any(m in title or m in low for m in INDEPENDENT_MARKERS):
        return False
    return any(m in title for m in DIRECTOR_MARKERS)


def _aggregate(rows: list[dict], shares_outstanding: dict[str, float] | None = None):
    """長表 → {stock_id: (持股%, 質押%)}，並回報職稱／質押欄是否存在。"""
    if not rows:
        return {}, False, False
    header = rows[0].keys()
    has_title = any(c in header for c in TITLE_COLUMNS)
    has_pledge = any(c in header for c in PLEDGE_PCT_COLUMNS + PLEDGE_SHARES_COLUMNS)
    has_name = any(c in header for c in NAME_COLUMNS)

    holdings: dict[str, float] = defaultdict(float)
    pledges: dict[str, float] = defaultdict(float)
    pct_direct: dict[str, float] = defaultdict(float)
    outstanding: dict[str, float] = dict(shares_outstanding or {})
    seen: set[tuple] = set()

    for row in rows:
        sid = _pick(row, STOCK_ID_COLUMNS)
        if not sid:
            continue
        sid = sid.strip()
        if not is_non_independent_director(_pick(row, TITLE_COLUMNS) or ""):
            continue

        shares = _num(_pick(row, HOLDING_SHARES_COLUMNS))
        pledged = _num(_pick(row, PLEDGE_SHARES_COLUMNS))
        if has_name:
            # 法人董事佔多席會重複列示同一法人的持股，去重後才加總
            key = (sid, _pick(row, NAME_COLUMNS), shares, pledged)
            if key in seen:
                continue
            seen.add(key)

        if shares:
            holdings[sid] += shares
        if pledged:
            pledges[sid] += pledged
        pct = _num(_pick(row, HOLDING_PCT_COLUMNS))
        if pct:
            pct_direct[sid] += pct
        total = _num(_pick(row, SHARES_OUTSTANDING_COLUMNS))
        if total:
            outstanding[sid] = total

    by_stock: dict[str, tuple[float | None, float | None]] = {}
    for sid in set(holdings) | set(pct_direct):
        if pct_direct.get(sid):
            hold_pct = pct_direct[sid]
        elif holdings.get(sid) and outstanding.get(sid):
            hold_pct = holdings[sid] / outstanding[sid] * 100
        else:
            hold_pct = None
        pledge_pct = (pledges.get(sid, 0.0) / holdings[sid] * 100
                      if has_pledge and holdings.get(sid) else None)
        by_stock[sid] = (hold_pct, pledge_pct)
    return by_stock, has_title, has_pledge


class _BaseProvider:
    name = "director-holding"

    def __init__(self):
        self._by_stock: dict[str, tuple[float | None, float | None]] = {}
        self.has_title_column = False
        self.has_pledge_column = False

    def get(self, stock_id: str) -> tuple[float | None, float | None]:
        if not self.has_title_column:
            # 無職稱欄就無法挑出非獨立董監 —— 標 N/A，不送出混入經理人或獨董的持股
            return None, None
        return self._by_stock.get(stock_id, (None, None))


class CsvDirectorHoldingProvider(_BaseProvider):
    """由 MOPS t187ap11／t16sn02 的 CSV 匯出檔提供。"""

    def __init__(self, path: str | Path, shares_outstanding: dict[str, float] | None = None):
        super().__init__()
        self.path = Path(path)
        self.name = f"MOPS 董監持股匯出檔（{self.path.name}）"
        with open(self.path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self._by_stock, self.has_title_column, self.has_pledge_column = _aggregate(
            rows, shares_outstanding)


class OpenApiDirectorHoldingProvider(_BaseProvider):
    """直接取 TWSE／TPEx 的公開 open data（免金鑰）。

    任一來源取不到就跳過並記在 failed_sources，不讓單一來源失敗導致整項無資料；
    未涵蓋的公司照樣回 (None, None) 由評分引擎標 N/A。
    """

    def __init__(self, shares_outstanding: dict[str, float] | None = None,
                 urls: dict[str, str] | None = None, timeout: float = 180,
                 retries: int = 3, session: requests.Session | None = None,
                 cache_dir: str | Path | None = None):
        super().__init__()
        self.urls = urls if urls is not None else {"上市": TWSE_LISTED_URL, "上櫃": TPEX_OTC_URL}
        self.timeout, self.retries = timeout, retries
        self.session = session or requests.Session()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.failed_sources: dict[str, str] = {}
        self.loaded_sources: list[str] = []

        rows: list[dict] = []
        for label, url in self.urls.items():
            got = self._fetch(label, url)
            if got:
                rows.extend(got)
                self.loaded_sources.append(f"{label}({len(got)} 列)")
        self.name = ("MOPS 董監持股 open data："
                     + ("、".join(self.loaded_sources) if self.loaded_sources else "全部取得失敗"))
        self._by_stock, self.has_title_column, self.has_pledge_column = _aggregate(
            rows, shares_outstanding)

    def _fetch(self, label: str, url: str) -> list[dict]:
        cache = self.cache_dir / f"{label}.json" if self.cache_dir else None
        if cache and cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        last = ""
        for attempt in range(self.retries):
            try:
                # 這兩個端點回傳數 MB 且偶爾中途斷線，故重試而非一次失敗就放棄
                resp = self.session.get(url, timeout=self.timeout, headers={
                    "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list):
                    raise ValueError("回應不是列表")
                if cache:
                    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            except Exception as exc:                      # noqa: BLE001 - 來源失敗不應中斷評分
                last = f"{type(exc).__name__}: {exc}"[:160]
                time.sleep(1.5 * (attempt + 1))
        self.failed_sources[label] = last
        return []

