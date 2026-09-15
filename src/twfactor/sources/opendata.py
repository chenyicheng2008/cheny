"""公開資料來源（取代 FinMind，2026-09-15 需求方決定捨棄 FinMind）。

三個來源都免金鑰、無每小時配額：

1. 母體與市值 —— TWSE／TPEx OpenAPI。一次請求就是全市場，
   「市值前 N」可以在完整母體上排名（PRD §3），不再需要 FinMind 贊助層級。
       上市  t187ap03_L（公司基本資料：產業別、已發行普通股數）＋ STOCK_DAY_ALL（收盤價）
       上櫃  mopsfin_t187ap03_O（IssueShares）＋ tpex_mainboard_quotes（Close）
2. 財報 —— MOPS XBRL 整批檔（見 xbrl.py）。五個年度各取一個 Q4 年報檔，
   另加一個期中檔還原 TTM。
3. 現金股利 —— 證交所 TWT49U、櫃買中心 exDailyQ 除權息結果表，依除權息交易日所屬年度歸戶。
   證交所「權息」列只給權值＋息值合計，需再查 TWT49UDetail 拆出現金股利。

OpenAPI 只提供最新一日，因此市值一律以執行當日最新收盤價計算（價格日期寫進 price_date），
--as-of 只影響財報年度的時點錨，不會回溯歷史市值。
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from ..models import CompanyFacts, Provenance
from .base import NullDirectorHoldingProvider
from .xbrl import Facts, XbrlArchive, XbrlMissingError, archive_name, download_archive

TWSE_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TWSE_PRICE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_INFO_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TPEX_PRICE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
TWSE_EXRIGHT_URL = ("https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
                    "?startDate={y}0101&endDate={y}1231&response=json")
TWSE_EXRIGHT_DETAIL_URL = ("https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail"
                           "?STK_NO={sid}&T1={day}&response=json")
TPEX_EXRIGHT_URL = ("https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"
                    "?startDate={y}/01/01&endDate={y}/12/31&response=json")

QUARTER_END = {1: "0331", 2: "0630", 3: "0930"}
CONTEXT_END_RE = re.compile(r"(?:AsOf|To)(\d{4})\d{4}$")


class OpenDataError(RuntimeError):
    pass


# -- 純函式（可單獨測試）------------------------------------------------------
def _num(text) -> float | None:
    if text is None:
        return None
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def is_common_stock(stock_id: str) -> bool:
    """4 碼數字、非 0 開頭（排除 ETF／ETN，PRD §3）。"""
    return len(stock_id) == 4 and stock_id.isdigit() and not stock_id.startswith("0")


def roc_to_iso(text: str) -> str:
    """民國日期 → ISO。支援 1150914、114年06月04日、114/01/08 三種寫法。"""
    parts = re.findall(r"\d+", text or "")
    if len(parts) == 1 and len(parts[0]) == 7:
        parts = [parts[0][:3], parts[0][3:5], parts[0][5:]]
    if len(parts) != 3:
        raise ValueError(f"無法解析民國日期：{text!r}")
    y, m, d = (int(p) for p in parts)
    return f"{y + 1911:04d}-{m:02d}-{d:02d}"


def restate_to_latest_basis(restated: list[float],
                            as_filed: list[float | None]) -> tuple[list[float] | None, dict[int, float]]:
    """把每股數值的年度序列（舊→新）換算到最新一年的股本基準。

    restated[i]：i 年度在「次一年年報」的比較數（已追溯調整一次）；最後一年為當年年報數。
    as_filed[i]：i 年度在「當年年報」的原始數。
    第 k 年的比例 r_k ＝ restated[k] ÷ as_filed[k]，反映 k+1 年的配股／減資／面額變更；
    第 i 年的調整值 ＝ restated[i] × r_{i+1} × … × r_{n-2}。

    回傳（調整後序列, {序列索引: 比例}，只列 ≠1 者）。任一比例無法計算（原始數缺漏、為 0
    或正負號不同）時回傳 None —— 不假設股本未變動（PRD §19.6）。
    """
    n = len(restated)
    ratios: dict[int, float] = {}
    for k in range(1, n - 1):
        a, r = as_filed[k], restated[k]
        if a is None:
            return None, {}
        if a == r:
            continue
        if a == 0 or (a > 0) != (r > 0):
            return None, {}
        ratios[k] = r / a
    out = []
    for i in range(n):
        factor = 1.0
        for k in range(i + 1, n - 1):
            factor *= ratios.get(k, 1.0)
        out.append(restated[i] * factor)
    return out, ratios


def _index(fields: list[str], *names: str) -> dict[str, int]:
    idx = {f.strip(): i for i, f in enumerate(fields)}
    missing = [n for n in names if n not in idx]
    if missing:
        raise OpenDataError(f"除權息資料缺少欄位 {missing}（來源格式可能已變更）")
    return idx


def twse_exright_rows(payload: dict) -> list[dict]:
    """TWT49U → [{stock_id, date, cash, detail}]。cash 為 None 表示需查明細（權息同除）。"""
    if not payload.get("data"):
        return []
    idx = _index(payload.get("fields") or [], "資料日期", "股票代號", "權值+息值", "權/息")
    out = []
    for row in payload["data"]:
        kind = row[idx["權/息"]].strip()
        value = _num(row[idx["權值+息值"]])
        cash = value if kind == "息" else 0.0 if kind == "權" else None
        out.append({"stock_id": row[idx["股票代號"]].strip(), "date": roc_to_iso(row[idx["資料日期"]]),
                    "cash": cash, "detail": row[idx["詳細資料"]] if "詳細資料" in idx else ""})
    return out


def twse_detail_cash(payload: dict) -> float | None:
    """TWT49UDetail 的「(每股配發現金股利)除息」欄，例如「0.5 元／股」。"""
    fields, data = payload.get("fields") or [], payload.get("data") or []
    col = next((i for i, f in enumerate(fields) if "現金股利" in f), None)
    if col is None or not data:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", data[0][col] or "")
    return float(m.group(0).replace(",", "")) if m else None


def tpex_exright_rows(payload: dict) -> list[dict]:
    """exDailyQ → [{stock_id, date, cash}]。櫃買中心的表直接有現金股利欄，不需查明細。"""
    tables = payload.get("tables") or []
    if not tables or not tables[0].get("data"):
        return []
    idx = _index(tables[0].get("fields") or [], "除權息日期", "代號", "現金股利")
    return [{"stock_id": row[idx["代號"]].strip(), "date": roc_to_iso(row[idx["除權息日期"]]),
             "cash": _num(row[idx["現金股利"]]) or 0.0}
            for row in tables[0]["data"]]


# -- 資料來源 -----------------------------------------------------------------
class OpenDataSource:
    name = "TWSE/TPEx OpenAPI＋MOPS XBRL"

    def __init__(self, field_map: dict, years: int = 5, as_of: date | None = None,
                 director_provider=None, cache_dir: str | Path = ".xbrlcache",
                 allow_download: bool = False, session: requests.Session | None = None,
                 request_pause: float = 1.0, log: Callable[[str], None] | None = None):
        self.fm = field_map
        self.years = years
        self.as_of = as_of or date.today()
        self.director_provider = director_provider or NullDirectorHoldingProvider()
        self.cache_dir = Path(cache_dir)
        self.allow_download = allow_download
        self.session = session or requests.Session()
        self.request_pause = request_pause
        self.log = log or (lambda msg: print(msg, file=sys.stderr))
        self._archives: list[XbrlArchive] | None = None
        self._fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # -- 期間（PRD §8「近五個完整年度」的時點錨）---------------------------
    @property
    def latest_complete_fy(self) -> int:
        """年報申報期限為次年 3/31，故 4 月起才視為上一年度可用（PRD §16、§17 避免前視偏誤）。"""
        return self.as_of.year - 1 if self.as_of.month >= 4 else self.as_of.year - 2

    @property
    def target_years(self) -> list[int]:
        end = self.latest_complete_fy
        return list(range(end - self.years + 1, end + 1))

    def latest_interim(self) -> tuple[int, int] | None:
        """最新完整年度之後、在基準日前已過申報期限的最後一季，用於 TTM。"""
        year = self.latest_complete_fy + 1
        best = None
        for q, (m, d) in sorted(self.fm.get("interim_deadlines", {}).items()):
            if self.as_of > date(year, m, d):
                best = (year, int(q))
        return best

    def required_archives(self) -> list[tuple[int, int]]:
        """每個年度各一個 Q4 年報檔，外加期中檔。

        年報雖然含前一年比較數，但比較數會因配股、減資、面額變更而追溯調整；
        要把 EPS 換算到同一股本基準，需要每一年的原始數與次年的追溯數，所以五年都要下載。
        """
        need = {(y, 4) for y in self.target_years}
        interim = self.latest_interim()
        if interim:
            need.add(interim)
        return sorted(need)

    # -- XBRL ---------------------------------------------------------------
    def load_archives(self) -> list[XbrlArchive]:
        if self._archives is None:
            need = self.required_archives()
            missing = [(y, q) for y, q in need if not (self.cache_dir / archive_name(y, q)).exists()]
            if missing and not self.allow_download:
                names = "、".join(archive_name(y, q) for y, q in missing)
                raise XbrlMissingError(
                    f"缺少 XBRL 整批檔：{names}。加上 --download-xbrl 自動下載"
                    f"（公開資訊觀測站 t203sb02，單檔約 100 MB 以上），或手動下載後放到 {self.cache_dir}/")
            for y, q in missing:
                download_archive(y, q, self.cache_dir, session=self.session, log=self.log)
            self._archives = [XbrlArchive(self.cache_dir / archive_name(y, q)) for y, q in need]
        return self._archives

    def company_facts(self, stock_id: str) -> tuple[Facts, list[str], Facts]:
        """回傳（合併事實, 用到的季檔, 原始申報數）。

        合併事實依季檔舊→新覆蓋，同一期間以較新申報的（可能經追溯調整的）比較數為準。
        原始申報數只保留「該期間屬於申報檔當期」的值，例如 FY2023 取自 2023Q4 檔，
        用來和次年的追溯數相除，求出股本變動比例。
        """
        merged: Facts = {}
        as_filed: Facts = {}
        used = []
        for archive in self.load_archives():
            facts = archive.facts(stock_id)
            if facts is None:
                continue
            used.append(archive.label)
            for tag, by_ctx in facts.items():
                merged.setdefault(tag, {}).update(by_ctx)
                for ctx, v in by_ctx.items():
                    m = CONTEXT_END_RE.search(ctx)
                    if m and int(m.group(1)) == archive.year:
                        as_filed.setdefault(tag, {})[ctx] = v
        return merged, used, as_filed

    # -- HTTP -----------------------------------------------------------------
    def _get_json(self, key: str, url: str):
        path = self.cache_dir / "opendata" / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        last = ""
        for attempt in range(4):
            try:
                # 櫃買中心在壓縮傳輸時偶爾中途斷線，要求不壓縮較穩定
                resp = self.session.get(url, timeout=90, headers={
                    "User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"})
                resp.raise_for_status()
                data = resp.json()
                break
            except (requests.RequestException, ValueError) as exc:
                last = f"{type(exc).__name__}: {exc}"[:200]
                time.sleep(2 * (attempt + 1))
        else:
            raise OpenDataError(f"{key} 取得失敗：{last}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        time.sleep(self.request_pause)
        return data

    # -- 母體與市值（PRD §3）-------------------------------------------------
    def list_universe(self) -> dict[str, dict]:
        today = date.today().isoformat()
        universe: dict[str, dict] = {}
        for r in self._get_json(f"twse_t187ap03_L_{today}", TWSE_INFO_URL):
            sid = str(r.get("公司代號", "")).strip()
            if is_common_stock(sid):
                universe[sid] = {"stock_id": sid, "stock_name": str(r.get("公司簡稱", "")).strip(),
                                 "market": "twse", "industry_code": str(r.get("產業別", "")).strip(),
                                 "shares_issued": _num(r.get("已發行普通股數或TDR原股發行股數"))}
        for r in self._get_json(f"tpex_t187ap03_O_{today}", TPEX_INFO_URL):
            sid = str(r.get("SecuritiesCompanyCode", "")).strip()
            if is_common_stock(sid):
                universe[sid] = {"stock_id": sid, "stock_name": str(r.get("CompanyAbbreviation", "")).strip(),
                                 "market": "tpex",
                                 "industry_code": str(r.get("SecuritiesIndustryCode", "")).strip(),
                                 "shares_issued": _num(r.get("IssueShares"))}
        return universe

    def latest_prices(self) -> dict[str, tuple[float, str]]:
        today = date.today().isoformat()
        prices: dict[str, tuple[float, str]] = {}
        for r in self._get_json(f"twse_STOCK_DAY_ALL_{today}", TWSE_PRICE_URL):
            close = _num(r.get("ClosingPrice"))
            if close:
                prices[str(r.get("Code", "")).strip()] = (close, roc_to_iso(str(r.get("Date", ""))))
        for r in self._get_json(f"tpex_mainboard_quotes_{today}", TPEX_PRICE_URL):
            close = _num(r.get("Close"))
            if close:
                prices[str(r.get("SecuritiesCompanyCode", "")).strip()] = (
                    close, roc_to_iso(str(r.get("Date", ""))))
        return prices

    def top_by_market_cap(self, n: int, candidates: list[str] | None = None) -> list[dict]:
        """市值 ＝ 最新收盤價 × 已發行普通股數。無成交或無股數者不列入排名（不推估）。"""
        universe = self.list_universe()
        if candidates is not None:
            unknown = [s for s in candidates if s not in universe]
            if unknown:
                raise OpenDataError(f"候選母體含上市櫃普通股查無之代碼：{unknown}")
        prices = self.latest_prices()
        ranked = []
        for sid in (candidates if candidates is not None else universe):
            company, quote = universe[sid], prices.get(sid)
            if not quote or not company.get("shares_issued"):
                continue
            close, price_date = quote
            ranked.append({**company, "close": close, "price_date": price_date,
                           "market_cap": close * company["shares_issued"]})
        ranked.sort(key=lambda r: r["market_cap"], reverse=True)
        return ranked[:n]

    # -- 現金股利（PRD §8.3、§8.4）--------------------------------------------
    def dividend_history(self, stock_ids: list[str]) -> dict[str, dict]:
        """→ {stock_id: {"by_year": {年: 每股現金股利}, "unresolved": [年]}}。

        除權息結果表是全市場完整名單，因此某年沒有紀錄即代表該年未配現金股利。
        「權息」列查不到明細時，該年度記為 unresolved，不以權值＋息值合計頂替。
        """
        wanted = set(stock_ids)
        by_year: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        unresolved: dict[str, set[int]] = defaultdict(set)
        this_year = date.today().year
        for y in self.target_years:
            suffix = "" if y < this_year else f"_{date.today().isoformat()}"
            for r in twse_exright_rows(self._get_json(f"twse_TWT49U_{y}{suffix}",
                                                      TWSE_EXRIGHT_URL.format(y=y))):
                if r["stock_id"] not in wanted:
                    continue
                cash = r["cash"]
                if cash is None:
                    cash = self._twse_detail_cash(r["detail"])
                    if cash is None:
                        unresolved[r["stock_id"]].add(y)
                        continue
                by_year[r["stock_id"]][y] += cash
            for r in tpex_exright_rows(self._get_json(f"tpex_exDailyQ_{y}{suffix}",
                                                      TPEX_EXRIGHT_URL.format(y=y))):
                if r["stock_id"] in wanted:
                    by_year[r["stock_id"]][y] += r["cash"]
        return {sid: {"by_year": dict(by_year.get(sid, {})), "unresolved": sorted(unresolved.get(sid, ()))}
                for sid in stock_ids}

    def _twse_detail_cash(self, detail: str) -> float | None:
        try:
            sid, day = (s.strip() for s in detail.split(","))
        except ValueError:
            return None
        try:
            payload = self._get_json(f"twse_TWT49UDetail_{sid}_{day}",
                                     TWSE_EXRIGHT_DETAIL_URL.format(sid=sid, day=day))
        except OpenDataError:
            return None
        return twse_detail_cash(payload)

    # -- 標準化為 CompanyFacts ---------------------------------------------
    def fetch_facts(self, companies: list[dict]) -> list[CompanyFacts]:
        years = self.target_years
        archives = self.load_archives()
        if archives:
            self.log("      XBRL 整批檔：" + "、".join(f"{a.label}（{len(a)} 家）" for a in archives))
        dividends = self.dividend_history([c["stock_id"] for c in companies])
        fin_codes = {str(c) for c in self.fm.get("financial_industry_codes", [])}
        out = []
        for c in companies:
            sid = c["stock_id"]
            facts = CompanyFacts(
                stock_id=sid, stock_name=c.get("stock_name", ""), market=c.get("market", ""),
                industry_code=c.get("industry_code"),
                is_financial=str(c.get("industry_code") or "") in fin_codes,
                is_ky=sid.startswith("91") or "KY" in (c.get("stock_name") or ""),
                market_cap=c.get("market_cap"), fiscal_years=years,
            )
            pool, used, as_filed = self.company_facts(sid)
            if pool:
                self._fill_financials(facts, years, pool, used, as_filed)
            else:
                facts.missing_reasons["financials"] = "XBRL 整批檔中查無本公司申報"
            self._fill_dividends(facts, years, dividends.get(sid, {}))
            self._fill_governance(facts)
            out.append(facts)
        return out

    def _fill_financials(self, facts: CompanyFacts, years: list[int], pool: Facts,
                         used: list[str], as_filed: Facts) -> None:
        fields = self.fm["fields"]
        zero_fill = set(self.fm.get("zero_fill_fields", []))
        source = "MOPS XBRL " + "、".join(used)
        period = f"{years[0]}–{years[-1]}"
        last = years[-1]

        def dur(y: int) -> str:
            return f"From{y}0101To{y}1231"

        def inst(y: int) -> str:
            return f"AsOf{y}1231"

        def value(field: str, ctx: str, facts_: Facts = pool) -> tuple[float | None, str]:
            spec = fields[field]
            hits = [t for t in spec["tags"] if ctx in facts_.get(t, {})]
            if not hits:
                return None, ""
            if spec.get("aggregate") == "sum":
                return sum(facts_[t][ctx] for t in hits), "+".join(hits)
            return facts_[hits[0]][ctx], hits[0]

        def has_balance_sheet(y: int) -> bool:
            return value("total_equity", inst(y))[0] is not None

        def annual(field: str) -> tuple[list[float] | None, str]:
            vals, tags = [], []
            for y in years:
                v, t = value(field, dur(y))
                if v is None:
                    return None, ""
                vals.append(v)
                tags += [x for x in t.split("+") if x not in tags]
            return vals, "+".join(tags)

        def instant(field: str, y: int) -> tuple[float | None, str, bool]:
            """期末值；借款類科目在有期末資產負債表但無該元素時認定為 0（第三個值標記是否填補）。"""
            v, t = value(field, inst(y))
            if v is None and field in zero_fill and has_balance_sheet(y):
                return 0.0, "", True
            return v, t, False

        def instant_series(field: str) -> list[float] | None:
            out = []
            for y in years:
                v, _, _ = instant(field, y)
                if v is None:
                    return None
                out.append(v)
            return out

        interim = self.latest_interim()

        def ttm(field: str) -> float | None:
            """今年累計 ＋ 去年全年 − 去年同期累計；尚無期中報告時即為最新年度值。"""
            fy, _ = value(field, dur(last))
            if fy is None or interim is None:
                return fy
            yy, q = interim
            ytd, _ = value(field, f"From{yy}0101To{yy}{QUARTER_END[q]}")
            prev, _ = value(field, f"From{yy - 1}0101To{yy - 1}{QUARTER_END[q]}")
            if ytd is None or prev is None:
                return None
            return ytd + fy - prev

        def prov(key: str, tags: str, note: str = "", per: str = period) -> None:
            facts.provenance[key] = Provenance(source=source, field_name=tags, period=per,
                                               fetched_at=self._fetched_at, note=note)

        # §8.1 EPS：追溯調整至最新股本基準（需求方 2026-09-15 決定），
        # 否則配股、減資、面額變更會被誤判為 EPS 衰退（例如國巨面額變更、金控配股）
        eps_restated, eps_tags = annual("eps")
        if eps_restated:
            filed = [value("eps", dur(y), as_filed)[0] for y in years]
            facts.eps_annual, ratios = restate_to_latest_basis(eps_restated, filed)
            if facts.eps_annual is None:
                facts.missing_reasons["eps"] = "無法換算至同一股本基準（當年原始 EPS 缺漏、為 0 或正負號改變）"
            else:
                note = (f"已追溯調整至 {last} 年股本基準；調整比例 "
                        + "、".join(f"{years[k]}:{r:.4f}" for k, r in ratios.items())
                        if ratios else f"{last} 年股本基準（五年間無追溯調整）")
                prov("eps", eps_tags, note=note)
        else:
            facts.missing_reasons["eps"] = f"XBRL 缺少 {period} 任一年度之 EPS"

        # §8.2 自由現金流 ＝ 營業現金流 − |取得不動產廠房設備|
        ocf_a, ocf_tags = annual("operating_cash_flow")
        capex_a, capex_tags = annual("capex")
        if ocf_a and capex_a:
            facts.fcf_annual = [o - abs(c) for o, c in zip(ocf_a, capex_a)]
            prov("operating_cash_flow", ocf_tags)
            prov("capex", capex_tags)
        else:
            facts.missing_reasons["free_cash_flow"] = "缺少營業現金流或資本支出科目"

        # §8.5 淨利率、§8.8 ROE
        rev_a, rev_tags = annual("revenue")
        ni_a, ni_tags = annual("net_income")
        if rev_a and ni_a and all(r != 0 for r in rev_a):
            facts.net_margin_annual = [n / r * 100 for n, r in zip(ni_a, rev_a)]
            prov("revenue", rev_tags)
            prov("net_income", ni_tags)
        else:
            facts.missing_reasons["net_margin"] = "缺少營收或稅後淨利科目"

        eq_a = instant_series("total_equity")
        if ni_a and eq_a and all(e > 0 for e in eq_a):
            facts.roe_annual = [n / e * 100 for n, e in zip(ni_a, eq_a)]
            prov("total_equity", fields["total_equity"]["tags"][0])
        else:
            facts.missing_reasons["roe"] = "缺少股東權益或稅後淨利，或股東權益 ≤0"

        # §8.6 LT-Debt/Equity（最新完整年度期末）
        facts.total_equity, _, _ = instant("total_equity", last)
        ltd, ltd_tags, filled = instant("long_term_debt", last)
        facts.long_term_debt = ltd
        if ltd is None:
            facts.missing_reasons["lt_debt_equity"] = f"{last} 年底無資產負債表資料，無法判斷長期負債"
        elif filled:
            prov("long_term_debt", "（無長期借款／公司債／租賃負債元素，認定為 0）", per=str(last),
                 note="該年度期末資產負債表存在但無任何長期負債元素，依需求方確認之零餘額規則認定為 0")
        else:
            prov("long_term_debt", ltd_tags, per=str(last),
                 note="長期借款＋應付公司債＋租賃負債－非流動（PRD §8.6 含長期融資租賃）")

        self._fill_roic(facts, annual, instant_series, prov)

        # §8.7 利息保障倍數 ＝ TTM 營業利益 ÷ |TTM 利息費用|
        op_ttm = ttm("operating_income")
        ie_ttm = ttm("interest_expense")
        ie_note = ""
        if ie_ttm is None and op_ttm is not None and ttm("operating_cash_flow") is not None:
            # 現金流量表存在卻沒有利息費用調整項，視同無利息負擔（與借款零餘額規則同一邏輯）
            ie_ttm, ie_note = 0.0, "現金流量表無利息費用調整項，認定為 0"
        facts.ttm_operating_income = op_ttm
        facts.interest_expense = ie_ttm
        ttm_period = f"TTM 至 {interim[0]}Q{interim[1]}" if interim else f"FY{last}"
        if op_ttm is None:
            facts.missing_reasons["interest_coverage"] = f"缺少 {ttm_period} 營業利益"
        else:
            prov("interest_expense", "+".join(fields["interest_expense"]["tags"]), note=ie_note,
                 per=ttm_period)
            if ie_ttm:
                facts.interest_coverage = op_ttm / abs(ie_ttm)

        facts.ttm_eps = ttm("eps")
        if facts.ttm_eps is None:
            facts.missing_reasons["ttm_eps"] = f"缺少 {ttm_period} EPS（期中報告未涵蓋本公司）"

    def _fill_roic(self, facts: CompanyFacts, annual, instant_series, prov) -> None:
        """PRD §8.9 ROIC（需求方 2026-09-15 指定計算式）。

            ROIC = 稅後營業利益 ÷（股東權益 ＋ 有息負債），皆取期末值
            稅後營業利益 = 年度營業利益 × (1 − 有效稅率)
            有效稅率     = 年度所得稅費用 ÷ 年度稅前淨利
            有息負債     = 期末 短期借款 ＋ 長期借款 ＋ 應付公司債（依定義不含租賃負債）

        稅前淨利 ≤0 的年度沒有可導出的有效稅率，整項標 N/A，不以推估稅率補齊（PRD §19.6）。
        """
        op_a, op_tags = annual("operating_income")
        pretax_a, _ = annual("pretax_income")
        tax_a, _ = annual("income_tax")
        eq_a = instant_series("total_equity")
        std_a = instant_series("short_term_debt")
        ltb_a = instant_series("long_term_borrowings")

        if not all([op_a, pretax_a, tax_a, eq_a, std_a, ltb_a]):
            facts.missing_reasons["roic"] = (
                "缺少 ROIC 所需科目（營業利益／稅前淨利／所得稅費用／股東權益／"
                "短期借款／長期借款）之完整五年序列")
            return
        if any(v <= 0 for v in pretax_a):
            facts.missing_reasons["roic"] = "五年內有稅前淨利 ≤0 之年度，無法導出有效稅率（不以推估稅率補齊）"
            return
        invested = [e + s + l for e, s, l in zip(eq_a, std_a, ltb_a)]
        if any(c <= 0 for c in invested):
            facts.missing_reasons["roic"] = "投入資本（股東權益＋有息負債）≤0，ROIC 無意義"
            return

        facts.roic_annual = [op * (1 - t / pt) / cap * 100
                             for op, pt, t, cap in zip(op_a, pretax_a, tax_a, invested)]
        prov("operating_income", op_tags)
        prov("roic", "稅後營業利益 ÷（股東權益＋有息負債），期末值",
             note="有效稅率＝所得稅費用÷稅前淨利（同年度）；"
                  "有息負債＝短期借款＋長期借款＋應付公司債，依需求方定義不含一年內到期長期負債與租賃負債")

    def _fill_dividends(self, facts: CompanyFacts, years: list[int], record: dict) -> None:
        by_year = record.get("by_year", {})
        unresolved = record.get("unresolved", [])
        if unresolved:
            facts.dividend_annual = None
            facts.missing_reasons["dividend"] = (
                f"{'、'.join(map(str, unresolved))} 年有權息同除紀錄，查不到明細無法拆出現金股利")
        else:
            facts.dividend_annual = [by_year.get(y, 0.0) for y in years]
            facts.provenance["cash_dividend"] = Provenance(
                source="TWSE TWT49U／TPEx exDailyQ 除權息結果表", field_name="每股現金股利",
                period=f"{years[0]}–{years[-1]}", fetched_at=self._fetched_at,
                note="依除權息交易日所屬年度歸戶；該年度無除息紀錄即認定未配現金股利（0）；"
                     "每股金額未依股本變動追溯調整")

        # PRD §8.4 股息支付率 ＝ 最新完整年度現金股利 ÷ TTM EPS（需求方 2026-09-15 確認）。
        # 分子為年度值、分母為最近四季滾動值，期間刻意不對齊；EPS 快速成長時支付率會被低估。
        # TTM EPS 為 0 時比率無定義標 N/A；為負時比率設 0，由評分規則「TTM EPS<0」給 0 分。
        if facts.dividend_annual is not None and facts.ttm_eps:
            facts.ttm_payout_ratio = (facts.dividend_annual[-1] / facts.ttm_eps * 100
                                      if facts.ttm_eps > 0 else 0.0)
        else:
            facts.missing_reasons["payout_ratio"] = "缺少股利或 TTM EPS（TTM EPS=0 時比率無定義）"

    def _fill_governance(self, facts: CompanyFacts) -> None:
        hold, pledge = self.director_provider.get(facts.stock_id)
        facts.director_holding_pct = hold
        facts.director_pledge_pct = pledge
        if hold is None:
            provider = self.director_provider
            facts.missing_reasons["director_holding"] = (
                "董監持股來源未設定（加 --director-openapi）"
                if getattr(provider, "name", "none") == "none" else
                f"董監持股來源未涵蓋本檔（provider={provider.name}）")
        else:
            facts.provenance["director_holding"] = Provenance(
                source=self.director_provider.name, field_name="非獨立董監持股/質押",
                period="最新可得", fetched_at=self._fetched_at)
