"""FinMind 資料來源（取代 StockBoss 的候選來源，PRD §19.3）。

⚠ 重要：FinMind 的財報 dataset 以長表 (date, stock_id, type, origin_name, value)
   回傳，`type` 的實際字串隨會計科目而異，且金融業科目與一般產業不同。
   本模組不寫死任何 type 字串 —— 一律透過 config/finmind_fields.yaml 的
   confirmed / candidates 解析，並以 `probe_schema()` 產出 PoC 實測清單。
   未經 PoC 確認前，解析不到的欄位一律標 N/A，不得以推測值補齊（PRD §19.6）。
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime, timezone

import requests

from ..models import CompanyFacts, Provenance
from .base import NullDirectorHoldingProvider

API_URL = "https://api.finmindtrade.com/api/v4/data"


class FinMindError(RuntimeError):
    pass


class FinMindSource:
    name = "FinMind"

    def __init__(self, field_map: dict, token: str = "", years: int = 5,
                 as_of: date | None = None, director_provider=None,
                 session: requests.Session | None = None, sleep: float = 0.35):
        self.fm = field_map
        self.token = token
        self.years = years
        self.as_of = as_of or date.today()
        self.director_provider = director_provider or NullDirectorHoldingProvider()
        self.session = session or requests.Session()
        self.sleep = sleep
        self._fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # -- HTTP -------------------------------------------------------------
    def _get(self, dataset: str, **params) -> list[dict]:
        payload = {"dataset": dataset, **params}
        if self.token:
            payload["token"] = self.token
        resp = self.session.get(API_URL, params=payload, timeout=60)
        if resp.status_code == 402:
            raise FinMindError("FinMind 額度不足或需要 token（HTTP 402）")
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") not in (200, None):
            raise FinMindError(f"{dataset}: {body.get('msg')}")
        time.sleep(self.sleep)
        return body.get("data", [])

    # -- 期間（PRD §8「近五個完整年度」需要一個明確時點錨）-----------------
    @property
    def latest_complete_fy(self) -> int:
        """以年報公告時點推定最新完整年度。

        台股年度合併財報申報期限為次年 3/31，故 4 月起才視為上一年度可用。
        此規則可調整，但必須明確且可重現（PRD §16、§17 避免前視偏誤）。
        """
        return self.as_of.year - 1 if self.as_of.month >= 4 else self.as_of.year - 2

    @property
    def target_years(self) -> list[int]:
        end = self.latest_complete_fy
        return list(range(end - self.years + 1, end + 1))

    # -- Schema 探測（PoC 產出物，PRD §19.6）-------------------------------
    def probe_schema(self, stock_ids: list[str]) -> dict[str, dict[str, list[str]]]:
        """回傳 {dataset: {stock_id: [實際出現的 type ...]}}，供回填 confirmed。"""
        out: dict[str, dict[str, list[str]]] = {}
        start = f"{self.target_years[0]}-01-01"
        for key in ("income_statement", "balance_sheet", "cash_flow"):
            ds = self.fm["datasets"][key]
            out[ds] = {}
            for sid in stock_ids:
                rows = self._get(ds, data_id=sid, start_date=start)
                out[ds][sid] = sorted({r.get("type", "") for r in rows if r.get("type")})
        return out

    # -- 欄位解析 ---------------------------------------------------------
    def _resolve(self, field: str, rows_by_type: dict[str, dict[str, float]]) -> tuple[dict[str, float], str]:
        """依 confirmed → candidates 順序解析欄位，回傳 ({period: value}, 實際使用的 type)。"""
        spec = self.fm["fields"][field]
        names = ([spec["confirmed"]] if spec.get("confirmed") else []) + list(spec.get("candidates", []))
        if spec.get("aggregate") == "sum":
            hit = [n for n in names if n in rows_by_type]
            if not hit:
                return {}, ""
            merged: dict[str, float] = defaultdict(float)
            for n in hit:
                for period, v in rows_by_type[n].items():
                    merged[period] += v
            return dict(merged), "+".join(hit)
        for n in names:
            if n in rows_by_type:
                return rows_by_type[n], n
        return {}, ""

    @staticmethod
    def _pivot(rows: list[dict]) -> dict[str, dict[str, float]]:
        """長表 → {type: {date: value}}。"""
        out: dict[str, dict[str, float]] = defaultdict(dict)
        for r in rows:
            t, d, v = r.get("type"), r.get("date"), r.get("value")
            if t and d and v is not None:
                out[t][d] = float(v)
        return dict(out)

    @staticmethod
    def _annual(series: dict[str, float], years: list[int], quarterly_sum: bool) -> list[float] | None:
        """把季度序列彙總成年度序列（舊→新）。缺任一年回傳 None。"""
        by_year: dict[int, list[tuple[str, float]]] = defaultdict(list)
        for d, v in series.items():
            by_year[int(d[:4])].append((d, v))
        result = []
        for y in years:
            rows = sorted(by_year.get(y, []))
            if not rows:
                return None
            if quarterly_sum:
                if len(rows) < 4:          # 年度不完整就不推估（PRD §19.6）
                    return None
                result.append(sum(v for _, v in rows))
            else:
                result.append(rows[-1][1])  # 存量科目取年底值
        return result

    # -- 市值母體（PRD §3 + 使用者指定的「市值前 N」）-----------------------
    def top_by_market_cap(self, n: int) -> list[dict]:
        info = self._get(self.fm["datasets"]["info"])
        universe = {}
        for r in info:
            sid, typ = r.get("stock_id", ""), r.get("type", "")
            # 只留 4 碼普通股；排除 ETF/ETN/權證/REITs（PRD §3）
            if not (len(sid) == 4 and sid.isdigit()):
                continue
            if typ not in ("twse", "tpex"):
                continue
            universe[sid] = {
                "stock_id": sid,
                "stock_name": r.get("stock_name", ""),
                "market": typ,
                "industry_finmind": r.get("industry_category"),
            }

        shares = {}
        end = self.as_of.isoformat()
        start = (self.as_of.replace(year=self.as_of.year - 1)).isoformat()
        for r in self._get(self.fm["datasets"]["shareholding"], start_date=start, end_date=end):
            sid = r.get("stock_id")
            v = r.get(self.fm["fields"]["shares_issued"]["confirmed"])
            if sid in universe and v:
                shares[sid] = float(v)

        prices = {}
        for r in self._get(self.fm["datasets"]["price"], start_date=start, end_date=end):
            sid = r.get("stock_id")
            if sid in universe and r.get("close"):
                prices[sid] = float(r["close"])

        for sid, rec in universe.items():
            if sid in shares and sid in prices:
                rec["market_cap"] = shares[sid] * prices[sid]

        ranked = [r for r in universe.values() if r.get("market_cap")]
        ranked.sort(key=lambda r: r["market_cap"], reverse=True)
        return ranked[:n]

    # -- 標準化為 CompanyFacts -------------------------------------------
    def fetch_facts(self, companies: list[dict]) -> list[CompanyFacts]:
        years = self.target_years
        fin_cats = set(self.fm.get("financial_industry_categories", []))
        out = []
        for c in companies:
            sid = c["stock_id"]
            facts = CompanyFacts(
                stock_id=sid, stock_name=c.get("stock_name", ""), market=c.get("market", ""),
                industry_finmind=c.get("industry_finmind"),
                is_financial=(c.get("industry_finmind") or "") in fin_cats,
                is_ky=sid.startswith("91") or "KY" in (c.get("stock_name") or ""),
                market_cap=c.get("market_cap"), fiscal_years=years,
            )
            self._fill_financials(facts, years)
            self._fill_dividends(facts, years)
            self._fill_governance(facts)
            out.append(facts)
        return out

    def _fill_financials(self, facts: CompanyFacts, years: list[int]) -> None:
        start = f"{years[0]}-01-01"
        ds = self.fm["datasets"]
        income = self._pivot(self._get(ds["income_statement"], data_id=facts.stock_id, start_date=start))
        balance = self._pivot(self._get(ds["balance_sheet"], data_id=facts.stock_id, start_date=start))
        cashflow = self._pivot(self._get(ds["cash_flow"], data_id=facts.stock_id, start_date=start))

        pools = {"income_statement": income, "balance_sheet": balance, "cash_flow": cashflow}

        def series(field: str) -> tuple[dict[str, float], str]:
            return self._resolve(field, pools[self.fm["fields"][field]["dataset"]])

        def prov(field: str, used: str, flow: bool) -> None:
            if used:
                facts.provenance[field] = Provenance(
                    source=f"FinMind:{ds[self.fm['fields'][field]['dataset']]}",
                    field_name=used, period=f"{years[0]}–{years[-1]}", fetched_at=self._fetched_at,
                )
            else:
                facts.missing_reasons[field] = "FinMind 未回傳可辨識之科目（待 PoC 確認欄位名）"

        eps_s, eps_used = series("eps")
        facts.eps_annual = self._annual(eps_s, years, quarterly_sum=True) if eps_s else None
        prov("eps", eps_used, True)

        ocf_s, ocf_used = series("operating_cash_flow")
        capex_s, capex_used = series("capex")
        ocf_a = self._annual(ocf_s, years, True) if ocf_s else None
        capex_a = self._annual(capex_s, years, True) if capex_s else None
        if ocf_a and capex_a:
            # FinMind 現金流出多以負值表示；以絕對值扣除以避免符號歧義
            facts.fcf_annual = [o - abs(c) for o, c in zip(ocf_a, capex_a)]
            prov("operating_cash_flow", ocf_used, True)
            prov("capex", capex_used, True)
        else:
            facts.missing_reasons["free_cash_flow"] = "缺少營業現金流或資本支出科目"

        rev_s, rev_used = series("revenue")
        ni_s, ni_used = series("net_income")
        rev_a = self._annual(rev_s, years, True) if rev_s else None
        ni_a = self._annual(ni_s, years, True) if ni_s else None
        if rev_a and ni_a and all(r != 0 for r in rev_a):
            facts.net_margin_annual = [n / r * 100 for n, r in zip(ni_a, rev_a)]
            prov("revenue", rev_used, True)
            prov("net_income", ni_used, True)
        else:
            facts.missing_reasons["net_margin"] = "缺少營收或稅後淨利科目"

        eq_s, eq_used = series("total_equity")
        eq_a = self._annual(eq_s, years, False) if eq_s else None
        if ni_a and eq_a and all(e > 0 for e in eq_a):
            facts.roe_annual = [n / e * 100 for n, e in zip(ni_a, eq_a)]
            prov("total_equity", eq_used, False)
        else:
            facts.missing_reasons["roe"] = "缺少股東權益或稅後淨利，或股東權益 ≤0"

        ltd_s, ltd_used = series("long_term_debt")
        ltd_a = self._annual(ltd_s, years, False) if ltd_s else None
        if eq_a:
            facts.total_equity = eq_a[-1]
        if ltd_a:
            facts.long_term_debt = ltd_a[-1]
            prov("long_term_debt", ltd_used, False)
        else:
            facts.missing_reasons["lt_debt_equity"] = "缺少長期負債科目"

        # ROIC 需 NOPAT 與投入資本；PRD 未定義公式，PoC 確認科目前一律 N/A
        facts.missing_reasons.setdefault("roic", "PRD 未定義 ROIC 計算式；待需求方確認後實作")

        op_s, op_used = series("operating_income")
        ie_s, ie_used = series("interest_expense")
        op_ttm = self._ttm(op_s)
        ie_ttm = self._ttm(ie_s)
        facts.ttm_operating_income = op_ttm
        facts.interest_expense = ie_ttm
        if op_ttm is not None and ie_ttm:
            facts.interest_coverage = op_ttm / abs(ie_ttm)
            prov("operating_income", op_used, True)
            prov("interest_expense", ie_used, True)
        elif op_ttm is None:
            facts.missing_reasons["interest_coverage"] = "缺少營業利益科目"

        eps_ttm = self._ttm(eps_s)
        facts.ttm_eps = eps_ttm

    @staticmethod
    def _ttm(series: dict[str, float]) -> float | None:
        """最近四季合計。不足四季回傳 None（不推估）。"""
        if not series:
            return None
        rows = sorted(series.items())[-4:]
        return sum(v for _, v in rows) if len(rows) == 4 else None

    def _fill_dividends(self, facts: CompanyFacts, years: list[int]) -> None:
        spec = self.fm["fields"]["cash_dividend"]
        names = ([spec["confirmed"]] if spec.get("confirmed") else []) + list(spec.get("candidates", []))
        rows = self._get(self.fm["datasets"]["dividend"], data_id=facts.stock_id,
                         start_date=f"{years[0]}-01-01")
        by_year: dict[int, float] = defaultdict(float)
        for r in rows:
            d = r.get("CashExDividendTradingDate") or r.get("date") or ""
            if len(d) < 4:
                continue
            by_year[int(d[:4])] += sum(float(r.get(n) or 0) for n in names if r.get(n) is not None)

        if all(y in by_year for y in years):
            facts.dividend_annual = [by_year[y] for y in years]
            facts.provenance["cash_dividend"] = Provenance(
                source=f"FinMind:{self.fm['datasets']['dividend']}", field_name="+".join(names),
                period=f"{years[0]}–{years[-1]}", fetched_at=self._fetched_at,
            )
        else:
            facts.dividend_annual = [by_year.get(y, 0.0) for y in years] if by_year else None
            if not by_year:
                facts.missing_reasons["dividend"] = "FinMind 無配息紀錄"

        if facts.dividend_annual and facts.ttm_eps:
            facts.ttm_payout_ratio = facts.dividend_annual[-1] / facts.ttm_eps * 100 if facts.ttm_eps > 0 else 0.0
        else:
            facts.missing_reasons["payout_ratio"] = "缺少股利或 TTM EPS"

    def _fill_governance(self, facts: CompanyFacts) -> None:
        hold, pledge = self.director_provider.get(facts.stock_id)
        facts.director_holding_pct = hold
        facts.director_pledge_pct = pledge
        if hold is None:
            facts.missing_reasons["director_holding"] = (
                f"董監持股來源未設定（FinMind 不提供；目前 provider={self.director_provider.name}）"
            )
        else:
            facts.provenance["director_holding"] = Provenance(
                source=self.director_provider.name, field_name="非獨立董監持股/質押",
                period="最新可得", fetched_at=self._fetched_at,
            )
