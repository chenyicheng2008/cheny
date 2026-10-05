"""單一個股 XBRL 財報與現金流量分析 PDF 報告。

    PYTHONPATH=src python scripts/xbrl_financial_report.py 7763 --name 崇舜
    PYTHONPATH=src python scripts/xbrl_financial_report.py 7763 --name 崇舜 \\
        --detail reports/7763/7763_detail_facts.json --notes reports/7763/notes.md \\
        --price 178 --price-date 2026-10-02 --outdir reports/7763

資料：
  - data/xbrl_facts.csv.gz（精簡事實檔，全部公司、2021Q4 起各季檔）提供年度與 TTM 的核心科目：
    營收、營業利益、淨利、EPS、稅前、所得稅、利息、權益、短期借款、營業現金流、資本支出。
  - --detail（可多次）：個別公司的完整 inline XBRL（MOPS t164sb01 的 .html），或本程式以
    --save-detail 匯出的 .json。補上毛利、營業費用、營運資金變動、股利、現金、存貨、應收應付等明細。
  - --notes：Markdown 質化評論（產業、題材、風險…），原樣排進報告「分析評論」一節。

所有衍生數字（單季、下半年、TTM）都由年初至今累計相減而來，解析不到一律顯示「—」，不推估。
輸出 <outdir>/<代號>_financial_report_<日期>.html／.pdf，另輸出 .json 指標檔方便後續撰寫評論。
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from twfactor.report import ReportError, html_to_pdf  # noqa: E402
from twfactor.sources.xbrl import load_snapshot, parse_facts  # noqa: E402

e = html.escape

# 欄位 → XBRL 元素。sum=True 時把有出現的元素相加，否則取第一個出現的。
FIELDS: dict[str, tuple[list[str], bool]] = {
    # 損益
    "revenue": (["ifrs-full:Revenue"], False),
    "cogs": (["ifrs-full:CostOfSales", "tifrs-bsci-ci:OperatingCosts"], False),
    "gross_profit": (["ifrs-full:GrossProfit", "tifrs-bsci-ci:GrossProfitLossFromOperations"], False),
    "opex": (["ifrs-full:OperatingExpense"], False),
    "rnd": (["ifrs-full:ResearchAndDevelopmentExpense"], False),
    "op_income": (["ifrs-full:ProfitLossFromOperatingActivities"], False),
    "nonop": (["tifrs-bsci-ci:NonoperatingIncomeAndExpenses"], False),
    "fx_gain": (["tifrs-notes:ForeignExchangeGainsLosses_n"], False),
    "pretax": (["ifrs-full:ProfitLossBeforeTax"], False),
    "tax": (["ifrs-full:IncomeTaxExpenseContinuingOperations"], False),
    "net_income": (["ifrs-full:ProfitLoss"], False),
    "eps": (["ifrs-full:BasicEarningsLossPerShare"], False),
    "interest": (["ifrs-full:AdjustmentsForInterestExpense", "ifrs-full:FinanceCosts"], False),
    # 現金流量
    "ocf": (["ifrs-full:CashFlowsFromUsedInOperatingActivities"], False),
    "cfi": (["tifrs-SCF:NetCashFlowsFromUsedInInvestingActivities",
             "ifrs-full:CashFlowsFromUsedInInvestingActivities"], False),
    "cff": (["tifrs-SCF:CashFlowsFromUsedInFinancingActivities",
             "ifrs-full:CashFlowsFromUsedInFinancingActivities"], False),
    "fx_cash": (["ifrs-full:EffectOfExchangeRateChangesOnCashAndCashEquivalents"], False),
    "capex": (["ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], True),
    "dep": (["ifrs-full:AdjustmentsForDepreciationExpense"], False),
    "amort": (["ifrs-full:AdjustmentsForAmortisationExpense"], False),
    "wc_change": (["tifrs-SCF:ChangesInOperatingAssetsAndLiabilities"], False),
    "d_inv": (["ifrs-full:AdjustmentsForDecreaseIncreaseInInventories"], False),
    "d_recv": (["tifrs-SCF:DecreaseIncreaseInAccountsReceivable",
                "tifrs-SCF:DecreaseIncreaseInNotesReceivable"], True),
    "d_pay": (["tifrs-SCF:IncreaseDecreaseInAccountsPayable",
               "tifrs-SCF:IncreaseDecreaseInNotesPayable"], True),
    "tax_paid": (["ifrs-full:IncomeTaxesPaidRefundClassifiedAsOperatingActivities"], False),
    "div_paid": (["ifrs-full:DividendsPaidClassifiedAsFinancingActivities"], False),
    # 資產負債
    "cash": (["ifrs-full:CashAndCashEquivalents"], False),
    "time_dep": (["ifrs-full:CurrentFinancialAssetsAtAmortisedCost"], False),
    "receivables": (["tifrs-bsci-ci:AccountsReceivableNet", "tifrs-bsci-ci:NotesReceivableNet"], True),
    "inventory": (["ifrs-full:Inventories"], False),
    "payables": (["ifrs-full:TradeAndOtherCurrentPayablesToTradeSuppliers", "tifrs-bsci-ci:NotesPayable"], True),
    "current_assets": (["ifrs-full:CurrentAssets"], False),
    "current_liab": (["ifrs-full:CurrentLiabilities"], False),
    "assets": (["ifrs-full:Assets"], False),
    "liabilities": (["ifrs-full:Liabilities"], False),
    "equity": (["ifrs-full:Equity"], False),
    "ppe": (["ifrs-full:PropertyPlantAndEquipment"], False),
    "retained": (["ifrs-full:RetainedEarnings"], False),
    "st_debt": (["ifrs-full:ShorttermBorrowings"], True),
    "lt_debt": (["ifrs-full:LongtermBorrowings", "ifrs-full:NoncurrentPortionOfNoncurrentBondsIssued"], True),
    "capital": (["ifrs-full:IssuedCapital", "tifrs-bsci-ci:OrdinaryShare"], False),
    "china_inv": (["tifrs-notes:BookValueAtTheEndOfThePeriod"], False),
}
ALL_TAGS = {t for tags, _ in FIELDS.values() for t in tags}
# 有期末資產負債表、卻沒有借款元素 → 借款視為 0（與評分系統相同規則）
ZERO_FILL = {"st_debt", "lt_debt"}

Q_END = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}
Q_START = {1: "0101", 2: "0401", 3: "0701", 4: "1001"}
YTD_LABEL = {"0331": "Q1", "0630": "H1", "0930": "1–3Q"}
DAYS = {"0331": 90, "0630": 181, "0930": 273, "1231": 365}


# -- 資料 -----------------------------------------------------------------------
class Facts:
    """合併後的 {tag: {context: value}}，新的季檔覆蓋舊的（可能經追溯調整）。"""

    def __init__(self) -> None:
        self.raw: dict[str, dict[str, float]] = {}
        self.sources: list[str] = []

    def merge(self, facts: dict[str, dict[str, float]], source: str) -> None:
        for tag, ctxs in facts.items():
            if tag in ALL_TAGS:
                self.raw.setdefault(tag, {}).update(ctxs)
        self.sources.append(source)

    def get(self, key: str, ctx: str) -> float | None:
        tags, agg = FIELDS[key]
        vals = [self.raw[t][ctx] for t in tags if ctx in self.raw.get(t, {})]
        if vals:
            return sum(vals) if agg else vals[0]
        if key in ZERO_FILL and ctx.startswith("AsOf") and self.get("equity", ctx) is not None:
            return 0.0
        return None

    def contexts(self, key: str) -> set[str]:
        return {c for t in FIELDS[key][0] for c in self.raw.get(t, {})}


def load_detail(path: Path) -> tuple[dict[str, dict[str, float]], str]:
    """回傳 (facts, 來源說明)。JSON 的 _meta.from 記錄當初解析的原始申報。"""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data.pop("_meta", {})
        origin = "、".join(meta.get("from", []))
        return data, f"{path.name}（{origin}）" if origin else path.name
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("big5", "ignore")
    return parse_facts(text), path.name


# -- 期間 -----------------------------------------------------------------------
def ytd(y: int, md: str) -> str:
    return f"From{y}0101To{y}{md}"


def sub(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def div(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else a / b


class Periods:
    def __init__(self, f: Facts):
        self.f = f
        flow = f.contexts("revenue") | f.contexts("net_income")
        self.years = sorted({int(c[4:8]) for c in flow if re.fullmatch(r"From\d{4}0101To\d{4}1231", c)})
        interim = [c for c in flow if re.fullmatch(r"From\d{4}0101To\d{4}(0331|0630|0930)", c)
                   and self.years and int(c[4:8]) > self.years[-1]]
        last = max(interim, key=lambda c: c[-8:]) if interim else None
        self.ytd_year = int(last[4:8]) if last else None
        self.ytd_md = last[-4:] if last else None

    # 流量：年度、年初至今、TTM、單季
    def annual(self, key: str, y: int) -> float | None:
        return self.f.get(key, ytd(y, "1231"))

    def ytd(self, key: str, prior: bool = False) -> float | None:
        if not self.ytd_year:
            return None
        return self.f.get(key, ytd(self.ytd_year - (1 if prior else 0), self.ytd_md))

    def ttm(self, key: str) -> float | None:
        if not self.ytd_year:
            return self.annual(key, self.years[-1]) if self.years else None
        a, b, c = self.ytd(key), self.annual(key, self.ytd_year - 1), self.ytd(key, prior=True)
        return None if None in (a, b, c) else a + b - c

    def quarter(self, key: str, y: int, q: int) -> float | None:
        direct = self.f.get(key, f"From{y}{Q_START[q]}To{y}{Q_END[q]}")
        if direct is not None:
            return direct
        if q == 1:                              # 只有半年報時：Q1＝H1－Q2
            q1 = self.f.get(key, ytd(y, "0331"))
            return q1 if q1 is not None else sub(self.f.get(key, ytd(y, "0630")),
                                                 self.f.get(key, f"From{y}0401To{y}0630"))
        return sub(self.f.get(key, ytd(y, Q_END[q])), self.f.get(key, ytd(y, Q_END[q - 1])))

    def slices(self) -> list[tuple[str, callable]]:
        """可取得的單季（或下半年）期間，舊到新。"""
        out = []
        last_y = self.ytd_year or (self.years[-1] if self.years else None)
        if last_y is None:
            return out
        for y in range(last_y - 2, last_y + 1):
            got = {q: self.quarter("revenue", y, q) is not None for q in range(1, 5)}
            for q in range(1, 5):
                if got[q]:
                    out.append((f"{y} Q{q}", lambda k, y=y, q=q: self.quarter(k, y, q)))
            if not got[3] and not got[4] and self.annual("revenue", y) is not None \
                    and self.f.get("revenue", ytd(y, "0630")) is not None:
                out.append((f"{y} H2", lambda k, y=y: sub(self.annual(k, y), self.f.get(k, ytd(y, "0630")))))
        return out[-8:]

    @property
    def ytd_label(self) -> str:
        return f"{self.ytd_year} {YTD_LABEL[self.ytd_md]}" if self.ytd_year else ""


# -- 指標 -----------------------------------------------------------------------
def shares(f: Facts, p: Periods) -> tuple[float | None, str]:
    for ctx in sorted(f.contexts("capital"), reverse=True):
        cap = f.get("capital", ctx)
        if cap:
            return cap / 10, f"股本 {ctx[4:]} ÷ 面額 10 元"
    for y in reversed(p.years):
        ni, eps = p.annual("net_income", y), p.annual("eps", y)
        if ni and eps:
            return ni / eps, (f"{y} 年淨利 ÷ EPS 推估（淨利含非控制權益，股數可能偏高；"
                              "可用 --shares 指定）")
    return None, "無"


def flow_block(get, n_shares: float | None) -> dict:
    """一個期間的損益與現金流量指標。get(key) 回傳該期間的值。"""
    v = {k: get(k) for k in ("revenue", "cogs", "gross_profit", "opex", "rnd", "op_income", "nonop", "fx_gain",
                             "pretax", "tax", "net_income", "eps", "interest", "ocf", "cfi", "cff", "fx_cash",
                             "capex", "dep", "amort", "wc_change", "d_inv", "d_recv", "d_pay", "tax_paid",
                             "div_paid")}
    if v["gross_profit"] is None and v["revenue"] is not None and v["cogs"] is not None:
        v["gross_profit"] = v["revenue"] - v["cogs"]
    capex = v["capex"]
    v["fcf"] = None if v["ocf"] is None or capex is None else v["ocf"] + capex
    v["gm"] = div(v["gross_profit"], v["revenue"])
    v["opm"] = div(v["op_income"], v["revenue"])
    v["npm"] = div(v["net_income"], v["revenue"])
    v["ocf_ni"] = div(v["ocf"], v["net_income"])
    v["da"] = None if v["dep"] is None else v["dep"] + (v["amort"] or 0)
    v["capex_rev"] = div(-capex if capex is not None else None, v["revenue"])
    v["capex_da"] = div(-capex if capex is not None else None, v["da"])
    v["fcf_ps"] = div(v["fcf"], n_shares)
    v["fcf_after_div"] = None if v["fcf"] is None or v["div_paid"] is None else v["fcf"] + v["div_paid"]
    v["tax_rate"] = div(v["tax"], v["pretax"])
    v["int_cover"] = div(None if v["pretax"] is None or v["interest"] is None else v["pretax"] + v["interest"],
                         v["interest"])
    return v


def balance_block(f: Facts, ctx: str, n_shares: float | None, cogs_annual: float | None,
                  rev_annual: float | None) -> dict:
    b = {k: f.get(k, ctx) for k in ("cash", "time_dep", "receivables", "inventory", "payables", "current_assets",
                                    "current_liab", "assets", "liabilities", "equity", "ppe", "retained",
                                    "st_debt", "lt_debt", "china_inv")}
    liquid = None if b["cash"] is None else b["cash"] + (b["time_dep"] or 0)
    debt = None if b["st_debt"] is None else b["st_debt"] + (b["lt_debt"] or 0)
    b["net_cash"] = None if liquid is None or debt is None else liquid - debt
    b["net_cash_ps"] = div(b["net_cash"], n_shares)
    b["debt_ratio"] = div(b["liabilities"], b["assets"])
    b["current_ratio"] = div(b["current_assets"], b["current_liab"])
    b["bvps"] = div(b["equity"], n_shares)
    b["dso"] = None if b["receivables"] is None or not rev_annual else b["receivables"] / rev_annual * 365
    b["dio"] = None if b["inventory"] is None or not cogs_annual else b["inventory"] / cogs_annual * 365
    b["dpo"] = None if b["payables"] is None or not cogs_annual else b["payables"] / cogs_annual * 365
    b["china_share"] = div(b["china_inv"], b["equity"])
    return b


def analyse(f: Facts, stock: str, shares_override: tuple[float, str] | None = None) -> dict:
    p = Periods(f)
    if not p.years:
        raise SystemExit(f"✖ 找不到 {stock} 的年度財報（Revenue／ProfitLoss）")
    n, n_src = shares_override or shares(f, p)
    years = p.years[-5:]
    cols: list[tuple[str, dict]] = [(str(y), flow_block(lambda k, y=y: p.annual(k, y), n)) for y in years]
    if p.ytd_year:
        cols.append((f"{p.ytd_year - 1} {YTD_LABEL[p.ytd_md]}", flow_block(lambda k: p.ytd(k, True), n)))
        cols.append((p.ytd_label, flow_block(lambda k: p.ytd(k), n)))
        ttm = flow_block(p.ttm, n)
        # TTM EPS 以淨利／股數重算會受配股影響，改用 EPS 本身相加減
        cols.append(("TTM", ttm))
    flows = dict(cols)
    for y in years:
        prev = flows.get(str(y - 1)) or {"equity_end": None}
        flows[str(y)]["roe"] = div(flows[str(y)]["net_income"],
                                   _avg(f.get("equity", f"AsOf{y}1231"), f.get("equity", f"AsOf{y - 1}1231")))
    if p.ytd_year:
        end = f"AsOf{p.ytd_year}{p.ytd_md}"
        start = f"AsOf{p.ytd_year - 1}{p.ytd_md}"
        flows["TTM"]["roe"] = div(flows["TTM"]["net_income"], _avg(f.get("equity", end), f.get("equity", start)))

    quarters = [(lbl, flow_block(g, n)) for lbl, g in p.slices()]

    # 資產負債：各年底＋最新期末＋去年同期
    bal_ctx = [f"AsOf{y}1231" for y in years]
    if p.ytd_year:
        bal_ctx += [f"AsOf{p.ytd_year - 1}{p.ytd_md}", f"AsOf{p.ytd_year}{p.ytd_md}"]
    bal_ctx = sorted({c for c in bal_ctx if f.get("equity", c) is not None})
    balances = []
    for c in bal_ctx:
        y, md = int(c[4:8]), c[-4:]
        if md == "1231":
            rev, cogs = p.annual("revenue", y), flows.get(str(y), {}).get("cogs")
            gp = flows.get(str(y), {}).get("gross_profit")
            cogs = cogs if cogs is not None else sub(rev, gp)
        else:                                   # 期中：以年初至今年化
            scale = 365 / DAYS[md]
            blk = flow_block(lambda k: f.get(k, ytd(y, md)), n)
            rev = blk["revenue"] * scale if blk["revenue"] is not None else None
            cogs_raw = blk["cogs"] if blk["cogs"] is not None else sub(blk["revenue"], blk["gross_profit"])
            cogs = cogs_raw * scale if cogs_raw is not None else None
        balances.append((f"{y}/{md[:2]}/{md[2:]}", balance_block(f, c, n, cogs, rev)))

    return {"stock": stock, "periods": p, "shares": n, "shares_source": n_src, "annual_years": years,
            "flows": flows, "flow_order": [c for c, _ in cols], "quarters": quarters, "balances": balances}


def _avg(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else (a + b) / 2


# -- 評價：本益比 vs. 企業價值／營業利益 ---------------------------------------------
def valuation(r: dict, price: float) -> dict:
    """同一股價下，比較本益比（P/E）與企業價值倍數（EV/EBIT、EV/NOPAT），並拆解兩者差異。

    P/E ＝（市值／EV）×（EV／稅後營業利益）×（稅後營業利益／淨利）
          淨現金因子       本業倍數             盈餘組成因子（<1 表示淨利含業外收益）
    """
    fl, p, n = r["flows"], r["periods"], r["shares"]
    bal = r["balances"][-1][1] if r["balances"] else {}
    mcap = price * n if n else None
    debt = None if bal.get("st_debt") is None else bal["st_debt"] + (bal.get("lt_debt") or 0)
    if mcap is not None and bal.get("net_cash") is not None:
        ev, ev_note, ev_upper = mcap - bal["net_cash"], "市值－淨現金", False
    elif mcap is not None and debt is not None:
        ev, ev_note, ev_upper = mcap + debt, "市值＋有息負債（資料無現金科目，未扣現金，EV 倍數為上限）", True
    else:
        ev, ev_note, ev_upper = None, "無資產負債資料", False
    yrs = [str(y) for y in r["annual_years"]]
    bases = [(f"{yrs[-1]} 年", fl[yrs[-1]], 1.0)]
    if "TTM" in fl and p.ytd_year:
        bases.append(("TTM", fl["TTM"], 1.0))
        months = {"0331": 3, "0630": 6, "0930": 9}[p.ytd_md]
        bases.append((f"{p.ytd_label} 年化", fl[p.ytd_label], 12 / months))
    rows = []
    for label, b, k in bases:
        eps = None if b["eps"] is None else b["eps"] * k
        oi = None if b["op_income"] is None else b["op_income"] * k
        nopat = None if oi is None or b["tax_rate"] is None else oi * (1 - b["tax_rate"])
        core_eps = div(nopat, n)
        ni = None if eps is None or n is None else eps * n          # 歸屬母公司淨利（EPS × 股數）
        rows.append({"basis": label, "eps": eps, "pe": div(price, eps), "core_eps": core_eps,
                     "core_pe": div(price, core_eps), "ev_ebit": div(ev, oi), "ev_nopat": div(ev, nopat),
                     "nonop_share": div(sub(b["pretax"], b["op_income"]), b["pretax"]),
                     "f_cash": div(mcap, ev), "f_mix": div(nopat, ni)})
    return {"mcap": mcap, "ev": ev, "ev_note": ev_note, "ev_upper": ev_upper, "debt": debt,
            "net_cash": bal.get("net_cash"), "rows": rows}


def valuation_reading(v: dict) -> list[str]:
    """把 P/E 與 EV 倍數的落差翻成文字。"""
    out = []
    for row in v["rows"]:
        if row["pe"] is None or row["ev_nopat"] is None or row["f_cash"] is None or row["f_mix"] is None:
            continue
        out.append(f"{row['basis']}：本益比 {row['pe']:.1f}× ＝ 淨現金因子 {row['f_cash']:.2f} × "
                   f"EV／稅後營業利益 {row['ev_nopat']:.1f}× × 盈餘組成因子 {row['f_mix']:.2f}。")
    ttm = next((x for x in v["rows"] if x["basis"] == "TTM"), v["rows"][0])
    if ttm["f_mix"] is not None:
        if ttm["f_mix"] < 0.85:
            out.append(f"盈餘組成因子 {ttm['f_mix']:.2f} < 1：淨利含業外收益，帳面本益比低估了本業的真實倍數；"
                       f"以稅後營業利益計，本業本益比約 {ttm['core_pe']:.1f}×，EV／營業利益 {ttm['ev_ebit']:.1f}×。")
        elif ttm["f_mix"] > 1.15:
            out.append(f"盈餘組成因子 {ttm['f_mix']:.2f} > 1：業外為損失（如匯損、利息），帳面本益比高估本業倍數。")
        else:
            out.append("盈餘組成因子接近 1：淨利幾乎全部來自本業，本益比與 EV 倍數可直接互相印證。")
    if ttm["f_cash"] is not None:
        if v["ev_upper"]:
            out.append("資料缺現金餘額，EV 未扣現金；實際 EV 倍數會比表列略低。")
        elif ttm["f_cash"] > 1.05:
            out.append(f"淨現金占市值約 {1 - 1 / ttm['f_cash']:.0%}，扣除後本業被市場賦予的倍數比本益比更低。")
        elif ttm["f_cash"] < 0.95:
            out.append(f"公司有淨負債，EV 大於市值；只看本益比會低估負債風險。")
    out.append("EV／營業利益是「本業、未稅、不受現金與負債影響」的倍數；"
               "除以 (1－稅率) 換成 EV／稅後營業利益（EV／NOPAT）後，才和本益比同為稅後基準。")
    return out


# -- 自動判讀 -------------------------------------------------------------------
def observations(r: dict, price: float | None) -> list[tuple[str, str]]:
    """(等級, 文字)；等級：good／watch／risk。只陳述數字推得出的事實。"""
    out: list[tuple[str, str]] = []
    fl, yrs, p = r["flows"], [str(y) for y in r["annual_years"]], r["periods"]
    ratios = [(y, fl[y]["ocf_ni"]) for y in yrs if fl[y]["ocf_ni"] is not None]
    if ratios:
        lo = min(v for _, v in ratios)
        txt = "、".join(f"{y} {v:.2f}" for y, v in ratios)
        if lo >= 1:
            out.append(("good", f"營業現金流／淨利逐年 ≥1（{txt}），獲利有現金支撐。"))
        elif lo < 0.8:
            out.append(("risk", f"營業現金流／淨利曾低於 0.8（{txt}），留意應收或存貨積壓。"))
        else:
            out.append(("watch", f"營業現金流／淨利介於 0.8–1（{txt}）。"))
    fcfs = [(y, fl[y]["fcf"]) for y in yrs if fl[y]["fcf"] is not None]
    if fcfs:
        neg = [y for y, v in fcfs if v < 0]
        out.append(("risk" if neg else "good",
                    f"自由現金流{'於 ' + '、'.join(neg) + ' 為負' if neg else '近 %d 年皆為正' % len(fcfs)}"
                    f"（最近一年 {_m(fcfs[-1][1])}）。"))
    if p.ytd_year:
        cur, prev = fl[p.ytd_label], fl[f"{p.ytd_year - 1} {YTD_LABEL[p.ytd_md]}"]
        if cur["eps"] is not None and prev["eps"]:
            chg = cur["eps"] / prev["eps"] - 1
            out.append(("good" if chg >= 0 else "risk",
                        f"{p.ytd_label} EPS {cur['eps']:.2f} 元，較去年同期 {prev['eps']:.2f} 元 {chg:+.0%}。"))
        if cur["op_income"] is not None and prev["op_income"]:
            chg = cur["op_income"] / prev["op_income"] - 1
            lvl = "watch" if abs(chg) < 0.03 else "good" if chg > 0 else "risk"
            out.append((lvl, f"{p.ytd_label} 營業利益 {_m(cur['op_income'])}，較去年同期"
                             + ("持平" if abs(chg) < 0.03 else f" {chg:+.0%}") + "。"))
        if cur["fx_gain"] is not None and cur["pretax"] and abs(cur["fx_gain"]) / abs(cur["pretax"]) >= 0.08:
            swing = (cur["fx_gain"] - (prev["fx_gain"] or 0))
            out.append(("watch", f"{p.ytd_label} 匯兌損益 {_m(cur['fx_gain'])}，占稅前 {cur['fx_gain'] / cur['pretax']:+.0%}；"
                                  f"較去年同期擺盪 {_m(swing)}，獲利對匯率敏感。"))
        if cur["d_inv"] is not None and cur["ocf"] and cur["d_inv"] > 0 and cur["d_inv"] / cur["ocf"] >= 0.2:
            out.append(("watch", f"{p.ytd_label} 存貨減少貢獻營業現金流 {_m(cur['d_inv'])}"
                                  f"（{cur['d_inv'] / cur['ocf']:.0%}），屬去化效果，不宜外推。"))
        base = [-fl[y]["capex"] for y in yrs[-3:] if fl[y]["capex"] is not None]
        if cur["capex"] is not None and base:
            ann = -cur["capex"] * 365 / DAYS[p.ytd_md]
            avg = sum(base) / len(base)
            if avg > 0 and ann / avg >= 2.5:
                out.append(("watch", f"{p.ytd_label} 資本支出 {_m(-cur['capex'])}，年化為近三年平均的 {ann / avg:.1f} 倍，"
                                      "進入擴產期，自由現金流將下降。"))
    for col in [yrs[-1]] + ([p.ytd_label] if p.ytd_year else []):
        b = fl[col]
        nonop = sub(b["pretax"], b["op_income"])
        if nonop is not None and b["pretax"] and nonop / b["pretax"] >= 0.25:
            out.append(("watch", f"{col} 業外（稅前－營業利益）{_m(nonop)}，占稅前 {nonop / b['pretax']:.0%}，"
                                  "EPS 含非本業貢獻，需確認是否一次性。"))
    last = fl[yrs[-1]]
    if last["div_paid"] is not None and last["fcf"] is not None and last["div_paid"] < 0:
        cover = last["fcf"] / -last["div_paid"]
        out.append(("good" if cover >= 1.2 else "watch", f"{yrs[-1]} 自由現金流為發放現金股利的 {cover:.1f} 倍。"))
    opm = [(y, fl[y]["opm"]) for y in yrs if fl[y]["opm"] is not None]
    if len(opm) >= 3:
        d = opm[-1][1] - opm[0][1]
        out.append(("good" if d > 0.02 else "watch" if d > -0.02 else "risk",
                    f"營益率由 {opm[0][0]} 年 {opm[0][1]:.1%} 變為 {opm[-1][0]} 年 {opm[-1][1]:.1%}。"))
    if r["balances"]:
        lbl, b = r["balances"][-1]
        if b["net_cash"] is not None:
            out.append(("good" if b["net_cash"] > 0 else "watch",
                        f"{lbl} {'淨現金' if b['net_cash'] > 0 else '淨負債'} {_m(abs(b['net_cash']))}"
                        + (f"（每股 {abs(b['net_cash_ps']):.2f} 元）" if b["net_cash_ps"] is not None else "") + "。"))
        if b["china_share"] is not None and b["china_share"] >= 0.3:
            out.append(("watch", f"大陸投資帳面價值占權益 {b['china_share']:.0%}，資金調度受匯出入規範影響。"))
    return out


# -- 格式 -----------------------------------------------------------------------
def _m(v: float | None, d: int = 1) -> str:
    """元 → 百萬元。"""
    return "—" if v is None else f"{v / 1e6:,.{d}f}"


def _pct(v: float | None, d: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{d}f}%"


def _x(v: float | None, d: int = 2) -> str:
    return "—" if v is None else f"{v:,.{d}f}"


def _times(v: float | None, d: int = 1) -> str:
    return "—" if v is None else f"{v:,.{d}f}×"


def _days(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f}"


def table(head: list[str], rows: list[list[str]], first_col: str = "項目", cls: str = "") -> str:
    th = "".join(f"<th>{e(h)}</th>" for h in [first_col] + head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def bar_chart(cats: list[str], series: list[tuple[str, str, list[float | None]]],
              title: str, unit: str = "百萬元", w: int = 640, h: int = 210) -> str:
    """分組直條圖（單一 y 軸，零基線；負值往下）。series：(名稱, CSS 變數, 值)。"""
    vals = [v for _, _, vs in series for v in vs if v is not None]
    if not vals:
        return ""
    hi, lo = max(max(vals), 0), min(min(vals), 0)
    span = (hi - lo) or 1
    step = _nice(span / 4)
    top, bot = step * -(-hi // step), step * (lo // step)
    pad_l, pad_r, pad_t, pad_b = 46, 8, 10, 22
    ph, pw = h - pad_t - pad_b, w - pad_l - pad_r
    y = lambda v: pad_t + (top - v) / (top - bot) * ph   # noqa: E731
    grid = []
    t = bot
    while t <= top + 1e-9:
        grid.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="{"zl" if t == 0 else "gl"}"/>'
                    f'<text x="{pad_l - 6}" y="{y(t) + 3:.1f}" class="ax" text-anchor="end">{t / 1e6:,.0f}</text>')
        t += step
    gw = pw / len(cats)
    bw = min(22, (gw * 0.7 - 2 * (len(series) - 1)) / len(series))
    bars, labels = [], []
    for i, c in enumerate(cats):
        x0 = pad_l + i * gw + (gw - (bw * len(series) + 2 * (len(series) - 1))) / 2
        for j, (name, color, vs) in enumerate(series):
            v = vs[i]
            if v is None:
                continue
            x = x0 + j * (bw + 2)
            y0, y1 = y(0), y(v)
            bars.append(f'<path d="{_bar_path(x, y0, y1, bw)}" style="fill:var({color})"><title>{e(name)} {e(c)}：{_m(v)}</title></path>')
        labels.append(f'<text x="{pad_l + i * gw + gw / 2:.1f}" y="{h - 6}" class="ax" text-anchor="middle">{e(c)}</text>')
    legend = "".join(f'<span class="lg"><i style="background:var({c})"></i>{e(n)}</span>' for n, c, _ in series) \
        if len(series) > 1 else ""
    return (f'<figure class="chart"><figcaption><b>{e(title)}</b><span class="unit">單位：{unit}</span>{legend}</figcaption>'
            f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{e(title)}">'
            f'{"".join(grid)}{"".join(bars)}{"".join(labels)}</svg></figure>')


def _bar_path(x: float, y0: float, y1: float, bw: float, r: float = 3) -> str:
    """資料端圓角、基線端直角。"""
    hgt = abs(y1 - y0)
    r = min(r, hgt / 2, bw / 2)
    if y1 <= y0:   # 正值向上
        return (f"M{x:.1f},{y0:.1f}V{y1 + r:.1f}Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f}H{x + bw - r:.1f}"
                f"Q{x + bw:.1f},{y1:.1f} {x + bw:.1f},{y1 + r:.1f}V{y0:.1f}Z")
    return (f"M{x:.1f},{y0:.1f}V{y1 - r:.1f}Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f}H{x + bw - r:.1f}"
            f"Q{x + bw:.1f},{y1:.1f} {x + bw:.1f},{y1 - r:.1f}V{y0:.1f}Z")


def _nice(x: float) -> float:
    import math
    p = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if x <= m * p:
            return m * p
    return 10 * p


def md_to_html(md: str) -> str:
    """評論用的極簡 Markdown：##／### 標題、- 與 1. 清單、| 表格、**粗體**、段落。"""
    out, lst, tbl = [], None, []

    def inline(s: str) -> str:
        s = e(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        return re.sub(r"`(.+?)`", r"<code>\1</code>", s)

    def flush_list():
        nonlocal lst
        if lst:
            out.append(f"<{lst[0]}>" + "".join(f"<li>{inline(i)}</li>" for i in lst[1]) + f"</{lst[0]}>")
        lst = None

    def flush_table():
        nonlocal tbl
        if tbl:
            rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in tbl
                    if not re.fullmatch(r"\|?[\s:\-|]+\|?", r.strip())]
            head, body = rows[0], rows[1:]
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table>")
        tbl = []

    for line in md.splitlines():
        s = line.rstrip()
        if s.lstrip().startswith("|"):
            flush_list()
            tbl.append(s)
            continue
        flush_table()
        m = re.match(r"^(#{1,4})\s+(.*)", s)
        b = re.match(r"^\s*[-*]\s+(.*)", s)
        o = re.match(r"^\s*\d+\.\s+(.*)", s)
        if m:
            flush_list()
            lvl = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
        elif b or o:
            kind = "ul" if b else "ol"
            if not lst or lst[0] != kind:
                flush_list()
                lst = (kind, [])
            lst[1].append((b or o).group(1))
        elif not s.strip():
            flush_list()
        else:
            flush_list()
            out.append(f"<p>{inline(s)}</p>")
    flush_list()
    flush_table()
    return "\n".join(out)


# -- 版面 -----------------------------------------------------------------------
CSS = """
@page { size: A4; margin: 14mm 13mm 14mm 13mm; }
:root { --surface-1:#fcfcfb; --text-primary:#0b0b0b; --text-secondary:#52514e; --muted:#8a8984;
  --rule:#e4e3df; --zebra:#f4f3ef; --series-1:#2a78d6; --series-2:#eb6834; --series-3:#1baf7a;
  --good:#008300; --watch:#b36b00; --risk:#c62828; }
* { box-sizing: border-box; }
body { margin:0; background:var(--surface-1); color:var(--text-primary);
  font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC","WenQuanYi Zen Hei",sans-serif;
  font-size:9.6pt; line-height:1.5; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
h1 { font-size:19pt; margin:0 0 2px; letter-spacing:.5px; }
h2 { font-size:13pt; margin:18px 0 6px; padding-bottom:3px; border-bottom:2px solid var(--text-primary); break-after:avoid; }
h3 { font-size:11pt; margin:12px 0 4px; break-after:avoid; }
h4 { font-size:10pt; margin:10px 0 3px; color:var(--text-secondary); break-after:avoid; }
p { margin:4px 0; }
.sub { color:var(--text-secondary); font-size:9pt; }
.kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:6px; margin:10px 0 4px; }
.kpi { border:1px solid var(--rule); border-radius:6px; padding:6px 8px; }
.kpi .v { font-size:15pt; font-weight:700; font-variant-numeric:tabular-nums; }
.kpi .l { color:var(--text-secondary); font-size:8.4pt; }
table { border-collapse:collapse; width:100%; margin:4px 0 8px; font-variant-numeric:tabular-nums; break-inside:auto; }
th, td { padding:2.5px 6px; border-bottom:1px solid var(--rule); text-align:right; white-space:nowrap; }
th { font-weight:700; color:var(--text-secondary); font-size:8.6pt; border-bottom:1.5px solid var(--text-secondary); }
td:first-child, th:first-child { text-align:left; }
tbody tr:nth-child(even) td { background:var(--zebra); }
tr.sec td { font-weight:700; background:none !important; padding-top:6px; color:var(--text-secondary); }
.notes table td, .notes table th { white-space:normal; text-align:left; }
.obs { list-style:none; padding:0; margin:4px 0; }
.obs li { padding:3px 0 3px 64px; position:relative; border-bottom:1px dotted var(--rule); }
.obs li span { position:absolute; left:0; top:3px; font-size:8pt; font-weight:700; border-radius:3px;
  padding:0 5px; border:1px solid; }
.obs .good span { color:var(--good); } .obs .watch span { color:var(--watch); } .obs .risk span { color:var(--risk); }
.chart { margin:6px 0 10px; break-inside:avoid; }
.chart figcaption { display:flex; gap:12px; align-items:baseline; font-size:9pt; margin-bottom:2px; flex-wrap:wrap; }
.chart .unit { color:var(--muted); font-size:8pt; }
.lg { color:var(--text-secondary); font-size:8.4pt; } .lg i { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:4px; vertical-align:-1px; }
.gl { stroke:var(--rule); stroke-width:1; } .zl { stroke:var(--text-secondary); stroke-width:1; }
.ax { fill:var(--muted); font-size:9px; }
.two { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.foot { color:var(--muted); font-size:8pt; margin-top:14px; border-top:1px solid var(--rule); padding-top:6px; }
.pb { break-before:page; }
.vr { margin:4px 0; padding-left:18px; } .vr li { margin:2px 0; }
code { font-size:8.4pt; background:var(--zebra); padding:0 3px; border-radius:3px; }
"""


def render(r: dict, name: str, notes_html: str, obs: list[tuple[str, str]], price: float | None,
           price_date: str | None, sources: list[str], today: str) -> str:
    fl, order, p = r["flows"], r["flow_order"], r["periods"]
    stock = r["stock"]
    ttm = fl.get("TTM", fl[order[-1]])
    last_bal = r["balances"][-1][1] if r["balances"] else {}

    # KPI
    kp = [(_x(ttm["eps"]), "TTM EPS（元）"), (_x(ttm["fcf_ps"]), "TTM 每股自由現金流（元）"),
          (_x(ttm["ocf_ni"]), "TTM 營業現金流／淨利"), (_pct(ttm.get("roe")), "TTM ROE"),
          (_pct(ttm["opm"]), "TTM 營益率"), (_x(last_bal.get("net_cash_ps")), "每股淨現金（元）"),
          (_pct(last_bal.get("debt_ratio")), "負債比"), (_x(last_bal.get("bvps")), "每股淨值（元）")]
    if price:
        kp[-1:] = [(_times(div(price, ttm["eps"])), f"本益比（{price:g} 元／TTM EPS）")]
    kpis = "".join(f'<div class="kpi"><div class="v">{v}</div><div class="l">{e(l)}</div></div>' for v, l in kp)

    obs_html = "".join(f'<li class="{lvl}"><span>{ {"good": "正面", "watch": "留意", "risk": "風險"}[lvl] }</span>{e(t)}</li>'
                       for lvl, t in obs)

    def rows(spec, cols):
        out = []
        for label, key, fmt in spec:
            if key is None:
                out.append([f"<b>{e(label)}</b>"] + [""] * len(cols))
                continue
            vals = [fl[c].get(key) for c in cols]
            if all(v is None for v in vals):
                continue
            out.append([e(label)] + [fmt(v) for v in vals])
        return out

    is_spec = [("營業收入", "revenue", _m), ("營業毛利", "gross_profit", _m), ("毛利率", "gm", _pct),
               ("營業費用", "opex", _m), ("研發費用", "rnd", _m), ("營業利益", "op_income", _m), ("營益率", "opm", _pct),
               ("業外收支", "nonop", _m), ("　其中匯兌損益", "fx_gain", _m), ("稅前淨利", "pretax", _m),
               ("有效稅率", "tax_rate", _pct), ("本期淨利", "net_income", _m), ("淨利率", "npm", _pct),
               ("EPS（元）", "eps", _x), ("ROE", "roe", _pct), ("利息保障倍數", "int_cover", lambda v: _x(v, 0))]
    cf_spec = [("營業活動現金流", "ocf", _m), ("　本期淨利", "net_income", _m), ("　折舊攤銷", "da", _m),
               ("　營運資金變動", "wc_change", _m), ("　　存貨減少（增加）", "d_inv", _m),
               ("　　應收款減少（增加）", "d_recv", _m), ("　　應付款增加（減少）", "d_pay", _m),
               ("　支付所得稅", "tax_paid", _m), ("營業現金流／淨利", "ocf_ni", _x),
               ("投資活動現金流", "cfi", _m), ("　資本支出", "capex", _m), ("資本支出／營收", "capex_rev", _pct),
               ("資本支出／折舊攤銷", "capex_da", _times),
               ("自由現金流（營業現金流＋資本支出）", "fcf", _m), ("每股自由現金流（元）", "fcf_ps", _x),
               ("籌資活動現金流", "cff", _m), ("　發放現金股利", "div_paid", _m), ("股利後自由現金流", "fcf_after_div", _m),
               ("匯率影響數", "fx_cash", _m)]

    q = r["quarters"]
    q_rows = []
    for label, key, fmt in [("營業收入", "revenue", _m), ("毛利率", "gm", _pct), ("營業利益", "op_income", _m),
                            ("營益率", "opm", _pct), ("本期淨利", "net_income", _m), ("EPS（元）", "eps", _x)]:
        vals = [b.get(key) for _, b in q]
        if any(v is not None for v in vals):
            q_rows.append([e(label)] + [fmt(v) for v in vals])

    bal = r["balances"]
    b_rows = []
    for label, key, fmt in [("現金及約當現金", "cash", _m), ("三個月以上定存", "time_dep", _m),
                            ("應收帳款及票據", "receivables", _m), ("存貨", "inventory", _m),
                            ("不動產廠房設備", "ppe", _m), ("資產總額", "assets", _m), ("應付帳款及票據", "payables", _m),
                            ("短期借款", "st_debt", _m), ("長期借款及公司債", "lt_debt", _m), ("負債總額", "liabilities", _m),
                            ("權益總額", "equity", _m), ("淨現金（負債）", "net_cash", _m), ("每股淨現金（元）", "net_cash_ps", _x),
                            ("負債比", "debt_ratio", _pct), ("流動比率", "current_ratio", _pct), ("每股淨值（元）", "bvps", _x),
                            ("應收週轉天數", "dso", _days), ("存貨週轉天數", "dio", _days), ("應付週轉天數", "dpo", _days),
                            ("大陸投資帳面價值", "china_inv", _m), ("大陸投資／權益", "china_share", _pct)]:
        vals = [b.get(key) for _, b in bal]
        if any(v is not None for v in vals):
            b_rows.append([e(label)] + [fmt(v) for v in vals])

    yrs = [str(y) for y in r["annual_years"]]
    cf_cols = yrs + (["TTM"] if "TTM" in fl else [])
    chart_cf = bar_chart(cf_cols, [("營業現金流", "--series-1", [fl[c]["ocf"] for c in cf_cols]),
                                   ("資本支出", "--series-2", [fl[c]["capex"] for c in cf_cols]),
                                   ("自由現金流", "--series-3", [fl[c]["fcf"] for c in cf_cols])],
                         "營業現金流、資本支出與自由現金流")
    chart_rev = bar_chart(cf_cols, [("營業利益", "--series-1", [fl[c]["op_income"] for c in cf_cols]),
                                    ("本期淨利", "--series-2", [fl[c]["net_income"] for c in cf_cols]),
                                    ("營業現金流", "--series-3", [fl[c]["ocf"] for c in cf_cols])],
                          "營業利益、淨利與營業現金流")
    chart_q = bar_chart([l for l, _ in q], [("營業收入", "--series-1", [b["revenue"] for _, b in q])],
                        "單季（或半年）營業收入") if q else ""

    val_html = ""
    if price:
        n = r["shares"]
        v = valuation(r, price)
        last_y = fl[yrs[-1]]
        div_ps = div(-last_y["div_paid"], n) if last_y.get("div_paid") else None
        vrows = [["市值", _m(v["mcap"]) + " 百萬元"],
                 [f"企業價值 EV（{v['ev_note']}）", _m(v["ev"]) + " 百萬元"],
                 ["股價淨值比", _times(div(price, last_bal.get("bvps")))],
                 ["自由現金流殖利率（TTM）", _pct(div(ttm["fcf_ps"], price))],
                 [f"自由現金流殖利率（{yrs[-1]} 年）", _pct(div(last_y["fcf_ps"], price))]]
        if div_ps:
            vrows.append([f"{yrs[-1]} 年實際發放股利／股價", _pct(div(div_ps, price))])
        cmp_rows = [[e(x["basis"]), _x(x["eps"]), _times(x["pe"]), _x(x["core_eps"]), _times(x["core_pe"]),
                     _times(x["ev_ebit"]), _times(x["ev_nopat"]), _pct(x["nonop_share"], 0)] for x in v["rows"]]
        dec_rows = [[e(x["basis"]), _times(x["pe"]), _x(x["f_cash"]), _times(x["ev_nopat"]), _x(x["f_mix"])]
                    for x in v["rows"]]
        reading = "".join(f"<li>{e(t)}</li>" for t in valuation_reading(v))
        val_html = (f"<h2>五、評價（股價 {price:g} 元，{e(price_date or '')}）</h2>"
                    + table(["數值"], [[e(a), b] for a, b in vrows])
                    + "<h3>本益比 vs. 企業價值／營業利益</h3>"
                    + '<p class="sub">本業 EPS＝營業利益×(1－有效稅率)÷股數；EV／EBIT＝EV÷營業利益（未稅）；'
                      "EV／NOPAT＝EV÷稅後營業利益，與本益比同為稅後基準。期中年化＝年初至今×12÷月數。</p>"
                    + table(["EPS（元）", "本益比", "本業 EPS（元）", "本業本益比", "EV／EBIT", "EV／NOPAT", "業外占稅前"],
                            cmp_rows, first_col="基準")
                    + "<h4>拆解：本益比＝淨現金因子 × EV／NOPAT × 盈餘組成因子</h4>"
                    + table(["本益比", "淨現金因子（市值／EV）", "EV／NOPAT", "盈餘組成因子（NOPAT／淨利）"],
                            dec_rows, first_col="基準")
                    + f'<ul class="vr">{reading}</ul>')

    src_items = "".join(f"<li><code>{e(s)}</code></li>" for s in sources)
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>{e(name)}（{e(stock)}）財報與現金流量分析</title><style>{CSS}</style></head><body>
<h1>{e(name)}（{e(stock)}）財報與現金流量分析</h1>
<div class="sub">XBRL 合併財報｜最新期別 {e(p.ytd_label or yrs[-1])}｜金額單位：新臺幣百萬元（另註明者除外）｜產出日 {today}</div>
<div class="kpis">{kpis}</div>
<h2>重點判讀（依數字自動產生）</h2><ul class="obs">{obs_html}</ul>
{f'<div class="notes">{notes_html}</div>' if notes_html else ''}
<h2>一、損益表摘要</h2>
{table(order, rows(is_spec, order))}
{chart_rev}
<h2>二、單季營運</h2>
{table([l for l, _ in q], q_rows) if q else '<p class="sub">無單季資料</p>'}
{chart_q}
<h2>三、現金流量分析</h2>
<p class="sub">自由現金流＝營業活動現金流＋取得不動產廠房設備（負值）。「—」表示該期資料未涵蓋此科目（精簡事實檔只含核心科目，明細來自完整 XBRL）。</p>
{table(order, rows(cf_spec, order))}
{chart_cf}
<h2>四、資產負債與營運資金</h2>
<p class="sub">週轉天數以期末餘額 ÷ 年度（期中為年化）營收或營業成本計算。</p>
{table([l for l, _ in bal], b_rows)}
{val_html}
<h2>資料來源與方法</h2>
<ul>{src_items}</ul>
<p class="sub">年度值取 From{{Y}}0101To{{Y}}1231；TTM＝今年累計＋去年全年－去年同期累計；單季與下半年由累計相減。
同一期間出現在多個季檔時以較新申報者為準。股數：{e(r['shares_source'])}（{_x(div(r['shares'], 1e3), 0)} 千股）。</p>
<div class="foot">本報告為客觀財務數據整理，不構成投資建議。</div>
</body></html>"""


# -- 主程式 ---------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="單一個股 XBRL 財報與現金流量分析 PDF")
    ap.add_argument("stock")
    ap.add_argument("--name", default="")
    ap.add_argument("--snapshot", default=str(ROOT / "data" / "xbrl_facts.csv.gz"))
    ap.add_argument("--detail", action="append", default=[], help="完整 inline XBRL .html 或匯出的 .json，可多次")
    ap.add_argument("--save-detail", help="把 --detail 解析出的相關科目存成 JSON（可 commit，下次免下載）")
    ap.add_argument("--detail-label", action="append",
                    help="--save-detail 時記錄的原始申報說明（例：MOPS t164sb01 2026Q2 合併），可多次")
    ap.add_argument("--notes", help="Markdown 質化評論")
    ap.add_argument("--shares", type=float, help="流通在外股數（千股）；未指定時取股本÷10 或淨利÷EPS 推估")
    ap.add_argument("--shares-note", default="使用者指定", help="--shares 的來源說明")
    ap.add_argument("--price", type=float)
    ap.add_argument("--price-date")
    ap.add_argument("--outdir", default=str(ROOT / "output"))
    ap.add_argument("--no-pdf", action="store_true", help="只輸出 HTML 與 JSON")
    a = ap.parse_args(argv)

    f = Facts()
    snap = Path(a.snapshot)
    if snap.exists():
        archives = load_snapshot(snap)
        for label in sorted(archives):
            facts = archives[label].facts(a.stock)
            if facts:
                f.merge(facts, f"{snap.name}:{label}")
    detail_merged: dict[str, dict[str, float]] = {}
    origins: list[str] = []
    for d in a.detail:
        facts, label = load_detail(Path(d))
        f.merge(facts, label)
        origins.append(label)
        for tag, ctxs in facts.items():
            if tag in ALL_TAGS:
                detail_merged.setdefault(tag, {}).update(ctxs)
    if not f.raw:
        print(f"✖ {a.stock} 在精簡事實檔與明細檔中都沒有資料", file=sys.stderr)
        return 1
    if a.save_detail:
        Path(a.save_detail).parent.mkdir(parents=True, exist_ok=True)
        detail_merged["_meta"] = {"from": a.detail_label or origins, "saved": date.today().isoformat()}
        Path(a.save_detail).write_text(json.dumps(detail_merged, ensure_ascii=False, indent=0, sort_keys=True),
                                       encoding="utf-8")
        print(f"明細科目已存：{a.save_detail}")

    r = analyse(f, a.stock, (a.shares * 1e3, a.shares_note) if a.shares else None)
    obs = observations(r, a.price)
    notes = md_to_html(Path(a.notes).read_text(encoding="utf-8")) if a.notes else ""
    today = date.today().isoformat()
    page = render(r, a.name or a.stock, notes, obs, a.price, a.price_date, f.sources, today)

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"{a.stock}_financial_report_{today}"
    stem.with_suffix(".html").write_text(page, encoding="utf-8")
    metrics = {"stock": a.stock, "shares": r["shares"], "flows": r["flows"],
               "valuation": valuation(r, a.price) if a.price else None,
               "quarters": dict(r["quarters"]), "balances": dict(r["balances"]), "observations": obs}
    stem.with_suffix(".json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"HTML：{stem.with_suffix('.html')}\nJSON：{stem.with_suffix('.json')}")
    for lvl, t in obs:
        print(f"  [{lvl}] {t}")
    if not a.no_pdf:
        try:
            pdf = html_to_pdf(stem.with_suffix(".html"), stem.with_suffix(".pdf"))
            print(f"PDF：{pdf}")
        except ReportError as exc:
            print(f"⚠ {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
