"""FinMind 資料來源（取代 StockBoss 的候選來源，PRD §19.3）。

本模組不寫死任何 type 字串 —— 一律透過 config/finmind_fields.yaml 的
confirmed / candidates 解析，並以 `probe_schema()` 產出 PoC 實測清單。
解析不到的欄位一律標 N/A，不得以推測值補齊（PRD §19.6）。

PoC（2026-09-15）實測出兩件會直接影響數值正確性的事，詳見 finmind_fields.yaml：

1. 三張報表的期間語意不同，彙總成年度值的方式必須分開處理：
   income_statement 為單季值（年度＝四季相加）、cash_flow 為年初至今累計值
   （年度＝Q4 當期值，四季相加會高估約 2.5 倍）、balance_sheet 為期末存量。
2. 免費／匿名層級不得做「不帶 data_id 的全市場查詢」，因此全市場市值排名
   需贊助層級 token，否則須由外部指定候選母體（見 top_by_market_cap）。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from ..models import CompanyFacts, Provenance
from .base import NullDirectorHoldingProvider

API_URL = "https://api.finmindtrade.com/api/v4/data"


class FinMindError(RuntimeError):
    pass


class FinMindLevelError(FinMindError):
    """FinMind 帳號層級不足（免費層級不得做全市場查詢）。"""


class FinMindQuotaError(FinMindError):
    """FinMind 請求配額用盡（HTTP 402）。"""


class FinMindSource:
    name = "FinMind"

    def __init__(self, field_map: dict, token: str = "", years: int = 5,
                 as_of: date | None = None, director_provider=None,
                 session: requests.Session | None = None, sleep: float = 0.35,
                 cache_dir: str | Path | None = None,
                 quota_wait: float = 0.0, max_quota_retries: int = 0):
        self.fm = field_map
        self.token = token
        self.years = years
        self.as_of = as_of or date.today()
        self.director_provider = director_provider or NullDirectorHoldingProvider()
        self.session = session or requests.Session()
        self.sleep = sleep
        # 免費層級額度有限（實測約每小時數百次），快取讓中斷的取數可以續跑而不重抓。
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 免費層級有每小時請求上限，超過回 HTTP 402。quota_wait > 0 時等待配額重置後續跑，
        # 搭配 cache_dir 才有意義（已取得的回應不會重抓）。
        self.quota_wait = quota_wait
        self.max_quota_retries = max_quota_retries
        self.request_count = 0
        self._fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # -- HTTP -------------------------------------------------------------
    def _cache_path(self, payload: dict) -> Path | None:
        if not self.cache_dir:
            return None
        key = json.dumps({k: v for k, v in payload.items() if k != "token"}, sort_keys=True)
        digest = hashlib.sha256(key.encode()).hexdigest()[:20]
        return self.cache_dir / f"{payload['dataset']}_{digest}.json"

    def _get(self, dataset: str, **params) -> list[dict]:
        payload = {"dataset": dataset, **params}
        cache = self._cache_path(payload)
        if cache and cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))

        if self.token:
            payload["token"] = self.token

        resp = None
        for attempt in range(self.max_quota_retries + 1):
            resp = self.session.get(API_URL, params=payload, timeout=60)
            self.request_count += 1
            if resp.status_code != 402:
                break
            if attempt == self.max_quota_retries or self.quota_wait <= 0:
                raise FinMindQuotaError(
                    f"FinMind 額度不足或需要 token（HTTP 402），已送出 {self.request_count} 次請求。"
                    "請設定 FINMIND_TOKEN，或搭配 --cache-dir 於配額重置後續跑。"
                )
            print(f"  [FinMind] 額度用盡，等待 {self.quota_wait:.0f} 秒後重試 "
                  f"（{attempt + 1}/{self.max_quota_retries}）…", flush=True)
            time.sleep(self.quota_wait)

        if resp.status_code == 400 and "level is free" in resp.text:
            raise FinMindLevelError(
                f"{dataset}: FinMind 目前層級不允許此查詢"
                f"（{'未帶 data_id 的全市場查詢' if 'data_id' not in params else '此 dataset'}）。"
                "需贊助（Sponsor）層級 token；請設定 FINMIND_TOKEN 或改用 --stocks 指定母體。"
            )
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") not in (200, None):
            raise FinMindError(f"{dataset}: {body.get('msg')}")
        data = body.get("data", [])
        if cache:
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        time.sleep(self.sleep)
        return data

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
    def probe_schema(self, stock_ids: list[str]) -> dict[str, dict]:
        """回傳每個 dataset 的實際 type 清單與期間語意判讀，供回填 confirmed。

        period_semantics 由實測推斷：同一年度內若各季值嚴格單調遞增（且首季非零），
        判為年初至今累計（cumulative_ytd），否則判為單季值（quarterly）。
        資產負債表為存量表，固定為 instant。
        """
        out: dict[str, dict] = {}
        start = f"{self.target_years[0]}-01-01"
        for key in ("income_statement", "balance_sheet", "cash_flow"):
            ds = self.fm["datasets"][key]
            per_stock: dict[str, list[str]] = {}
            guessed: dict[str, str] = {}
            for sid in stock_ids:
                rows = self._get(ds, data_id=sid, start_date=start)
                per_stock[sid] = sorted({r.get("type", "") for r in rows if r.get("type")})
                guessed[sid] = ("instant" if key == "balance_sheet"
                                else self._guess_semantics(self._pivot(rows)))
            out[ds] = {"types": per_stock, "period_semantics_observed": guessed,
                       "period_semantics_configured": self.fm.get("period_semantics", {}).get(key)}
        return out

    @staticmethod
    def _guess_semantics(pools: dict[str, dict[str, float]]) -> str:
        """以「同年度四季是否嚴格遞增」判斷累計 vs 單季。"""
        cumulative = quarterly = 0
        for series in pools.values():
            by_year: dict[int, list[tuple[str, float]]] = defaultdict(list)
            for d, v in series.items():
                by_year[int(d[:4])].append((d, v))
            for rows in by_year.values():
                if len(rows) < 4:
                    continue
                vals = [v for _, v in sorted(rows)]
                if all(v > 0 for v in vals) and all(b > a for a, b in zip(vals, vals[1:])):
                    cumulative += 1
                elif all(v < 0 for v in vals) and all(b < a for a, b in zip(vals, vals[1:])):
                    cumulative += 1
                else:
                    quarterly += 1
        if cumulative + quarterly == 0:
            return "unknown"
        return "cumulative_ytd" if cumulative > quarterly else "quarterly"

    # -- 欄位解析 ---------------------------------------------------------
    def _resolve(self, field: str, rows_by_type: dict[str, dict[str, float]]) -> tuple[dict[str, float], str]:
        """依 confirmed → candidates 順序解析欄位，回傳 ({period: value}, 實際使用的 type)。"""
        spec = self.fm["fields"][field]
        names = self._field_names(spec)
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
    def _has_year_end(pools: dict[str, dict[str, float]], year: int) -> bool:
        """該年度是否有期末（12 月）資料。用來區分「科目餘額為零」與「整年無資料」。"""
        prefix = f"{year}-12"
        return any(d.startswith(prefix) for series in pools.values() for d in series)

    @staticmethod
    def _field_names(spec: dict) -> list[str]:
        """confirmed（字串或字串陣列）優先，其後才是未經實測的 candidates。"""
        confirmed = spec.get("confirmed")
        names = [] if confirmed is None else (list(confirmed) if isinstance(confirmed, list) else [confirmed])
        for c in spec.get("candidates", []):
            if c not in names:
                names.append(c)
        return names

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
    def _by_year(series: dict[str, float]) -> dict[int, list[tuple[str, float]]]:
        out: dict[int, list[tuple[str, float]]] = defaultdict(list)
        for d, v in series.items():
            out[int(d[:4])].append((d, v))
        return out

    @classmethod
    def _annual(cls, series: dict[str, float], years: list[int], mode: str) -> list[float] | None:
        """依期間語意把季度序列彙總成年度序列（舊→新）。缺任一年回傳 None。

        quarterly       單季值 → 四季相加，不足四季不推估（PRD §19.6）
        cumulative_ytd  年初至今累計 → 取該年度第四季（12-31）當期值即為全年
        instant         期末存量 → 取 12-31 當期值
        """
        if mode not in ("quarterly", "cumulative_ytd", "instant"):
            raise FinMindError(f"未知的期間語意：{mode}")
        by_year = cls._by_year(series)
        result = []
        for y in years:
            rows = sorted(by_year.get(y, []))
            if not rows:
                return None
            if mode == "quarterly":
                if len(rows) < 4:
                    return None
                result.append(sum(v for _, v in rows))
            else:
                # 累計值與存量值都必須有年底那一期，否則該年度不完整，不以其他季推估
                year_end = [v for d, v in rows if d[5:7] == "12"]
                if not year_end:
                    return None
                result.append(year_end[-1])
        return result

    # -- 市值母體（PRD §3 + 使用者指定的「市值前 N」）-----------------------
    def list_universe(self) -> dict[str, dict]:
        """TaiwanStockInfo 全市場清單（免費層級可用），只留 4 碼普通股。"""
        universe = {}
        for r in self._get(self.fm["datasets"]["info"]):
            sid, typ = r.get("stock_id", ""), r.get("type", "")
            # 只留 4 碼普通股；排除 ETF/ETN/權證/REITs（PRD §3）
            if not (len(sid) == 4 and sid.isdigit()) or typ not in ("twse", "tpex"):
                continue
            universe[sid] = {
                "stock_id": sid,
                "stock_name": r.get("stock_name", ""),
                "market": typ,
                "industry_finmind": r.get("industry_category"),
            }
        return universe

    def market_caps(self, stock_ids: list[str], lookback_days: int = 30) -> dict[str, dict]:
        """逐檔以 收盤價 × 發行股數 實測市值。每檔 2 次請求，免費層級可用。

        取樣窗內最後一筆收盤價與最後一筆發行股數；任一缺漏即不列入排名（不推估）。
        """
        end = self.as_of
        start = date.fromordinal(end.toordinal() - lookback_days)
        shares_field = self._field_names(self.fm["fields"]["shares_issued"])
        out: dict[str, dict] = {}
        for sid in stock_ids:
            price = shares = None
            price_date = shares_date = ""
            for r in sorted(self._get(self.fm["datasets"]["price"], data_id=sid,
                                      start_date=start.isoformat(), end_date=end.isoformat()),
                            key=lambda r: r.get("date", "")):
                if r.get("close"):
                    price, price_date = float(r["close"]), r.get("date", "")
            for r in sorted(self._get(self.fm["datasets"]["shareholding"], data_id=sid,
                                      start_date=start.isoformat(), end_date=end.isoformat()),
                            key=lambda r: r.get("date", "")):
                v = next((r.get(n) for n in shares_field if r.get(n)), None)
                if v:
                    shares, shares_date = float(v), r.get("date", "")
            if price is None or shares is None:
                continue
            out[sid] = {"market_cap": price * shares, "close": price, "shares_issued": shares,
                        "price_date": price_date, "shares_date": shares_date}
        return out

    def top_by_market_cap(self, n: int, candidates: list[str] | None = None) -> list[dict]:
        """市值前 n 名。

        candidates 為 None 時嘗試全市場掃描；FinMind 免費層級不允許不帶 data_id 的
        全市場查詢（PoC 實測），此時丟出 FinMindLevelError 而不是回傳殘缺母體 ——
        「市值前 N」若不是在完整母體上排名，排名本身就沒有意義（PRD §3）。
        """
        universe = self.list_universe()
        if candidates is not None:
            unknown = [s for s in candidates if s not in universe]
            if unknown:
                raise FinMindError(f"候選母體含 TaiwanStockInfo 查無之代碼：{unknown}")
            caps = self.market_caps(candidates)
        else:
            caps = self._whole_market_caps(universe)

        for sid, cap in caps.items():
            universe[sid].update(cap)
        ranked = [r for sid, r in universe.items() if sid in caps]
        ranked.sort(key=lambda r: r["market_cap"], reverse=True)
        return ranked[:n]

    def _whole_market_caps(self, universe: dict[str, dict]) -> dict[str, dict]:
        """全市場市值（需贊助層級 token）。"""
        end = self.as_of
        start = date.fromordinal(end.toordinal() - 30)
        shares_field = self._field_names(self.fm["fields"]["shares_issued"])
        shares: dict[str, float] = {}
        for r in self._get(self.fm["datasets"]["shareholding"],
                           start_date=start.isoformat(), end_date=end.isoformat()):
            sid = r.get("stock_id")
            v = next((r.get(n) for n in shares_field if r.get(n)), None)
            if sid in universe and v:
                shares[sid] = float(v)
        prices: dict[str, float] = {}
        for r in self._get(self.fm["datasets"]["price"],
                           start_date=start.isoformat(), end_date=end.isoformat()):
            sid = r.get("stock_id")
            if sid in universe and r.get("close"):
                prices[sid] = float(r["close"])
        return {sid: {"market_cap": shares[sid] * prices[sid], "close": prices[sid],
                      "shares_issued": shares[sid]}
                for sid in shares if sid in prices}

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
        semantics = self.fm["period_semantics"]

        def series(field: str) -> tuple[dict[str, float], str]:
            return self._resolve(field, pools[self.fm["fields"][field]["dataset"]])

        def mode(field: str) -> str:
            return semantics[self.fm["fields"][field]["dataset"]]

        def annual(field: str, s: dict[str, float]) -> list[float] | None:
            return self._annual(s, years, mode(field)) if s else None

        def latest(field: str, s: dict[str, float]) -> float | None:
            """只取最新完整年度的值。存量科目的因子（§8.6）不需要五年序列，
            要求五年齊全會把「某一年該科目餘額為零而未列示」誤判成缺漏。"""
            got = self._annual(s, [years[-1]], mode(field)) if s else None
            return got[0] if got else None

        def prov(field: str, used: str, flow: bool) -> None:
            if used:
                facts.provenance[field] = Provenance(
                    source=f"FinMind:{ds[self.fm['fields'][field]['dataset']]}",
                    field_name=used, period=f"{years[0]}–{years[-1]}", fetched_at=self._fetched_at,
                )
            else:
                facts.missing_reasons[field] = "FinMind 未回傳可辨識之科目（待 PoC 確認欄位名）"

        eps_s, eps_used = series("eps")
        facts.eps_annual = annual("eps", eps_s)
        prov("eps", eps_used, True)

        ocf_s, ocf_used = series("operating_cash_flow")
        capex_s, capex_used = series("capex")
        ocf_a = annual("operating_cash_flow", ocf_s)
        capex_a = annual("capex", capex_s)
        if ocf_a and capex_a:
            # FinMind 現金流出多以負值表示；以絕對值扣除以避免符號歧義
            facts.fcf_annual = [o - abs(c) for o, c in zip(ocf_a, capex_a)]
            prov("operating_cash_flow", ocf_used, True)
            prov("capex", capex_used, True)
        else:
            facts.missing_reasons["free_cash_flow"] = "缺少營業現金流或資本支出科目"

        rev_s, rev_used = series("revenue")
        ni_s, ni_used = series("net_income")
        rev_a = annual("revenue", rev_s)
        ni_a = annual("net_income", ni_s)
        if rev_a and ni_a and all(r != 0 for r in rev_a):
            facts.net_margin_annual = [n / r * 100 for n, r in zip(ni_a, rev_a)]
            prov("revenue", rev_used, True)
            prov("net_income", ni_used, True)
        else:
            facts.missing_reasons["net_margin"] = "缺少營收或稅後淨利科目"

        eq_s, eq_used = series("total_equity")
        eq_a = annual("total_equity", eq_s)
        if ni_a and eq_a and all(e > 0 for e in eq_a):
            facts.roe_annual = [n / e * 100 for n, e in zip(ni_a, eq_a)]
            prov("total_equity", eq_used, False)
        else:
            facts.missing_reasons["roe"] = "缺少股東權益或稅後淨利，或股東權益 ≤0"

        ltd_s, ltd_used = series("long_term_debt")
        facts.total_equity = latest("total_equity", eq_s)
        facts.long_term_debt = latest("long_term_debt", ltd_s)
        if facts.long_term_debt is not None:
            prov("long_term_debt", ltd_used, False)
        elif self._has_year_end(balance, years[-1]):
            # FinMind 不回傳餘額為零的會計科目，因此「確實無長期借款」與「資料缺漏」
            # 在長表上長得一樣。需求方 2026-09-15 確認：只要該年度期末資產負債表本身
            # 有回傳，卻完全沒有任何借款科目，即認定長期借款為 0（而非缺漏）。
            facts.long_term_debt = 0.0
            facts.provenance["long_term_debt"] = Provenance(
                source=f"FinMind:{ds['balance_sheet']}", field_name="（無借款科目，認定為 0）",
                period=str(years[-1]), fetched_at=self._fetched_at,
                note="該年度期末資產負債表有回傳但無長期借款／應付公司債科目；"
                     "FinMind 不列示零餘額科目，依需求方確認認定為 0",
            )
        else:
            facts.missing_reasons["lt_debt_equity"] = (
                f"{years[-1]} 年底無資產負債表資料，無法判斷長期負債"
            )

        # ROIC 需 NOPAT 與投入資本；PRD 未定義公式，PoC 確認科目前一律 N/A
        facts.missing_reasons.setdefault("roic", "PRD 未定義 ROIC 計算式；待需求方確認後實作")

        op_s, op_used = series("operating_income")
        ie_s, ie_used = series("interest_expense")
        op_ttm = self._ttm(op_s, mode("operating_income"))
        ie_ttm = self._ttm(ie_s, mode("interest_expense"))
        facts.ttm_operating_income = op_ttm
        facts.interest_expense = ie_ttm
        if op_ttm is not None and ie_ttm:
            facts.interest_coverage = op_ttm / abs(ie_ttm)
            prov("operating_income", op_used, True)
            prov("interest_expense", ie_used, True)
        elif op_ttm is None:
            facts.missing_reasons["interest_coverage"] = "缺少營業利益科目"

        eps_ttm = self._ttm(eps_s, mode("eps"))
        facts.ttm_eps = eps_ttm

    @classmethod
    def _ttm(cls, series: dict[str, float], mode: str) -> float | None:
        """最近十二個月合計。資料不足以還原 TTM 時回傳 None（不推估）。

        quarterly       最近四季單季值直接相加
        cumulative_ytd  最近一期累計 + 去年全年 - 去年同期累計；
                        若最近一期正好是 Q4，其本身即為全年。
        """
        if not series:
            return None
        if mode == "quarterly":
            rows = sorted(series.items())[-4:]
            return sum(v for _, v in rows) if len(rows) == 4 else None
        if mode != "cumulative_ytd":
            raise FinMindError(f"TTM 不支援的期間語意：{mode}")

        latest_date, latest = sorted(series.items())[-1]
        year, month = int(latest_date[:4]), latest_date[5:7]
        if month == "12":
            return latest
        prev = cls._by_year(series).get(year - 1, [])
        prev_full = [v for d, v in sorted(prev) if d[5:7] == "12"]
        prev_same = [v for d, v in sorted(prev) if d[5:7] == month]
        if not prev_full or not prev_same:
            return None
        return latest + prev_full[-1] - prev_same[-1]

    def _fill_dividends(self, facts: CompanyFacts, years: list[int]) -> None:
        spec = self.fm["fields"]["cash_dividend"]
        names = self._field_names(spec)
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
