"""單一公司 XBRL 財務剖析：逐季損益、資產負債與現金流，以及由此推得的比率。

評分管線只取十項因子需要的科目；做個股研究時還需要毛利、研發、業外、
合約負債、存貨等。這裡沿用同一份 MOPS 整批檔（.xbrlcache/tifrs-YYYYQn.zip），
不另外打網路，也不改評分邏輯。

期間處理與評分管線一致：
- 損益與現金流量為年初至今累計，單季 ＝ 本季累計 − 上季累計（缺上季累計則單季 N/A）。
- 資產負債表取各季期末 AsOf{yyyymmdd}。
- 同一期間出現在多個季檔時，以較新申報的（可能經追溯調整的）數字為準。
- 元素解析不到一律 N/A，不以推測值補齊。
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .sources.xbrl import FILE_RE, Facts, XbrlArchive

QUARTER_END = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}

# key: (標籤, 依序比對的元素, 期間型態 dur／inst)
PROFILE_FIELDS: dict[str, tuple[str, list[str], str]] = {
    "revenue": ("營業收入", ["ifrs-full:Revenue"], "dur"),
    "gross_profit": ("營業毛利", ["ifrs-full:GrossProfit"], "dur"),
    "rnd": ("研究發展費用", ["ifrs-full:ResearchAndDevelopmentExpense"], "dur"),
    "selling": ("推銷費用", ["ifrs-full:SellingExpense"], "dur"),
    "admin": ("管理費用", ["ifrs-full:AdministrativeExpense"], "dur"),
    "operating_income": ("營業利益", ["ifrs-full:ProfitLossFromOperatingActivities"], "dur"),
    "pretax_income": ("稅前淨利", ["ifrs-full:ProfitLossBeforeTax"], "dur"),
    "income_tax": ("所得稅費用", ["ifrs-full:IncomeTaxExpenseContinuingOperations"], "dur"),
    "net_income": ("本期淨利", ["ifrs-full:ProfitLoss"], "dur"),
    "net_income_parent": ("歸屬母公司淨利", ["ifrs-full:ProfitLossAttributableToOwnersOfParent"], "dur"),
    "eps": ("基本 EPS", ["ifrs-full:BasicEarningsLossPerShare"], "dur"),
    "operating_cash_flow": ("營業活動現金流",
                            ["ifrs-full:CashFlowsFromUsedInOperatingActivities"], "dur"),
    "capex": ("取得不動產廠房設備",
              ["ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], "dur"),
    "cash": ("現金及約當現金", ["ifrs-full:CashAndCashEquivalents"], "inst"),
    "receivables": ("應收帳款", ["ifrs-full:CurrentTradeReceivables",
                              "ifrs-full:TradeAndOtherCurrentReceivables"], "inst"),
    "inventory": ("存貨", ["ifrs-full:Inventories"], "inst"),
    "contract_liabilities": ("合約負債－流動", ["ifrs-full:CurrentContractLiabilities"], "inst"),
    "total_assets": ("資產總額", ["ifrs-full:Assets"], "inst"),
    "total_equity": ("權益總額", ["ifrs-full:Equity"], "inst"),
}

ARCHIVE_RE = re.compile(r"tifrs-(\d{4})Q([1-4])\.zip$")


@dataclass
class QuarterRow:
    year: int
    quarter: int
    values: dict[str, float | None] = field(default_factory=dict)     # 單季（dur）或期末（inst）
    ytd: dict[str, float | None] = field(default_factory=dict)        # 年初至今累計（dur）

    @property
    def label(self) -> str:
        return f"{self.year}Q{self.quarter}"


def find_archives(cache_dir: str | Path, from_year: int | None = None) -> list[XbrlArchive]:
    """快取目錄內所有季檔，依年度季別排序（舊→新）。"""
    found = []
    for p in Path(cache_dir).glob("tifrs-*.zip"):
        m = ARCHIVE_RE.search(p.name)
        if m and (from_year is None or int(m.group(1)) >= from_year):
            found.append((int(m.group(1)), int(m.group(2)), p))
    return [XbrlArchive(p) for _, _, p in sorted(found)]


def slim_archives(cache_dir: str | Path, stock_ids: list[str], out_dir: str | Path,
                  from_year: int | None = None) -> list[tuple[Path, int]]:
    """從整批檔只抽出指定公司的申報，另存為同名的小 zip（檔名與內部路徑不變，可直接當 --cache-dir 用）。

    合併與個體報表都保留，讓 XbrlArchive 照原規則挑選。回傳（輸出檔, 收錄份數）。
    """
    wanted = set(stock_ids)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for archive in find_archives(cache_dir, from_year):
        with zipfile.ZipFile(archive.path) as src:
            names = [n for n in src.namelist() if (m := FILE_RE.search(n)) and m["sid"] in wanted]
            if not names:
                continue
            dest = out_dir / archive.path.name
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as dst:
                for n in names:
                    dst.writestr(n, src.read(n))
        written.append((dest, len(names)))
    return written


def merge_facts(archives: list[XbrlArchive], stock_id: str) -> tuple[Facts, list[str]]:
    """依季檔舊→新覆蓋，同一期間以較新申報的數字為準。"""
    merged: Facts = {}
    used = []
    for archive in archives:
        facts = archive.facts(stock_id)
        if facts is None:
            continue
        used.append(archive.label)
        for tag, by_ctx in facts.items():
            merged.setdefault(tag, {}).update(by_ctx)
    return merged, used


def _lookup(facts: Facts, tags: list[str], ctx: str) -> float | None:
    for tag in tags:
        v = facts.get(tag, {}).get(ctx)
        if v is not None:
            return v
    return None


def _ytd_ctx(year: int, quarter: int) -> str:
    return f"From{year}0101To{year}{QUARTER_END[quarter]}"


def _inst_ctx(year: int, quarter: int) -> str:
    return f"AsOf{year}{QUARTER_END[quarter]}"


def build_quarters(facts: Facts) -> list[QuarterRow]:
    """有營收或權益任一期末值的季度才列出，依時間排序。"""
    periods = set()
    for by_ctx in facts.values():
        for ctx in by_ctx:
            m = re.fullmatch(r"From(\d{4})0101To\1(\d{4})|AsOf(\d{4})(\d{4})", ctx)
            if not m:
                continue
            year, mmdd = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            for q, end in QUARTER_END.items():
                if end == mmdd:
                    periods.add((int(year), q))
    rows = []
    for year, q in sorted(periods):
        row = QuarterRow(year, q)
        for key, (_, tags, kind) in PROFILE_FIELDS.items():
            if kind == "inst":
                row.values[key] = _lookup(facts, tags, _inst_ctx(year, q))
                continue
            cur = _lookup(facts, tags, _ytd_ctx(year, q))
            row.ytd[key] = cur
            if q == 1:
                row.values[key] = cur
            else:
                prev = _lookup(facts, tags, _ytd_ctx(year, q - 1))
                row.values[key] = None if cur is None or prev is None else cur - prev
        if row.values.get("revenue") is not None or row.values.get("total_equity") is not None:
            rows.append(row)
    return rows


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def derived(v: dict[str, float | None]) -> dict[str, float | None]:
    """同一期間內可算出的比率與衍生值。"""
    op, pretax = v.get("operating_income"), v.get("pretax_income")
    ocf, capex = v.get("operating_cash_flow"), v.get("capex")
    non_op = None if op is None or pretax is None else pretax - op
    fcf = None if ocf is None or capex is None else ocf - abs(capex)
    return {
        "gross_margin": _ratio(v.get("gross_profit"), v.get("revenue")),
        "operating_margin": _ratio(op, v.get("revenue")),
        "rnd_ratio": _ratio(v.get("rnd"), v.get("revenue")),
        "net_margin": _ratio(v.get("net_income"), v.get("revenue")),
        "non_operating": non_op,
        "non_operating_share": _ratio(non_op, pretax),
        "effective_tax": _ratio(v.get("income_tax"), pretax),
        "free_cash_flow": fcf,
        "fcf_conversion": _ratio(fcf, v.get("net_income")),
    }


def annual_rows(facts: Facts) -> list[tuple[int, dict[str, float | None]]]:
    """完整年度：損益與現金流取 From{Y}0101To{Y}1231，資產負債取 AsOf{Y}1231。"""
    years = sorted({int(m.group(1)) for by_ctx in facts.values() for ctx in by_ctx
                    if (m := re.fullmatch(r"From(?P<y>\d{4})0101To(?P=y)1231", ctx))})
    out = []
    for y in years:
        v = {}
        for key, (_, tags, kind) in PROFILE_FIELDS.items():
            ctx = _inst_ctx(y, 4) if kind == "inst" else _ytd_ctx(y, 4)
            v[key] = _lookup(facts, tags, ctx)
        if v.get("revenue") is not None:
            out.append((y, v))
    return out


# -- 輸出 ---------------------------------------------------------------------

DERIVED_LABELS = {
    "gross_margin": "毛利率", "operating_margin": "營益率", "rnd_ratio": "研發費用率",
    "net_margin": "淨利率", "non_operating": "業外損益（稅前−營業利益）",
    "non_operating_share": "業外占稅前比重", "effective_tax": "有效稅率",
    "free_cash_flow": "自由現金流", "fcf_conversion": "FCF／淨利",
}
PCT_KEYS = {"gross_margin", "operating_margin", "rnd_ratio", "net_margin",
            "non_operating_share", "effective_tax", "fcf_conversion"}


def _fmt(key: str, v: float | None) -> str:
    if v is None:
        return "N/A"
    if key in PCT_KEYS:
        return f"{v * 100:.1f}%"
    if key == "eps":
        return f"{v:.2f}"
    return f"{v / 1e8:,.2f}"                   # 元 → 億元


def _table(headers: list[str], series: list[dict[str, float | None]], keys: list[str],
           labels: dict[str, str]) -> list[str]:
    lines = ["| 項目 | " + " | ".join(headers) + " |",
             "|---|" + "---:|" * len(headers)]
    for k in keys:
        lines.append(f"| {labels[k]} | " + " | ".join(_fmt(k, s.get(k)) for s in series) + " |")
    return lines


def render_markdown(stock_id: str, facts: Facts, used: list[str], last_quarters: int = 8) -> str:
    labels = {k: v[0] for k, v in PROFILE_FIELDS.items()} | DERIVED_LABELS
    dur_keys = [k for k, v in PROFILE_FIELDS.items() if v[2] == "dur"]
    inst_keys = [k for k, v in PROFILE_FIELDS.items() if v[2] == "inst"]
    out = [f"# {stock_id} XBRL 財務剖析", "",
           f"來源：MOPS XBRL 整批檔（t203sb02），使用季檔：{'、'.join(used) or '無'}。",
           "金額單位：億元；EPS 單位：元。N/A 表示該元素未申報或缺上季累計數，不以推測值補齊。", ""]

    years = annual_rows(facts)
    if years:
        out += ["## 年度", ""]
        heads = [str(y) for y, _ in years]
        series = [v | derived(v) for _, v in years]
        out += _table(heads, series, dur_keys + list(DERIVED_LABELS) + inst_keys, labels) + [""]

    quarters = build_quarters(facts)[-last_quarters:]
    if quarters:
        out += ["## 單季", "", "損益與現金流為單季（本季累計 − 上季累計）；資產負債為季末。", ""]
        heads = [r.label for r in quarters]
        series = [r.values | derived(r.values) for r in quarters]
        out += _table(heads, series, dur_keys + list(DERIVED_LABELS) + inst_keys, labels) + [""]
    if not years and not quarters:
        out += [f"⚠ 快取的季檔中找不到 {stock_id} 的申報。", ""]
    return "\n".join(out)
