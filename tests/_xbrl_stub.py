"""測試用：以假造的 XBRL 事實取代整批檔與網路，其餘流程（科目解析、期間、零餘額規則）照跑。"""

from datetime import date

from twfactor.params import load_field_map
from twfactor.sources.opendata import OpenDataSource

YEARS = [2021, 2022, 2023, 2024, 2025]
COMPANY = {"stock_id": "9999", "stock_name": "測試", "market": "twse", "industry_code": "24"}

REVENUE = "ifrs-full:Revenue"
NET_INCOME = "ifrs-full:ProfitLoss"
EPS = "ifrs-full:BasicEarningsLossPerShare"
OP = "ifrs-full:ProfitLossFromOperatingActivities"
PRETAX = "ifrs-full:ProfitLossBeforeTax"
TAX = "ifrs-full:IncomeTaxExpenseContinuingOperations"
INTEREST = "ifrs-full:AdjustmentsForInterestExpense"
OCF = "ifrs-full:CashFlowsFromUsedInOperatingActivities"
CAPEX = "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
EQUITY = "ifrs-full:Equity"
STD = "ifrs-full:ShorttermBorrowings"
LTB = "ifrs-full:LongtermBorrowings"
BONDS = "ifrs-full:NoncurrentPortionOfNoncurrentBondsIssued"
LEASE = "ifrs-full:NoncurrentFinanceLeaseLiabilities"

# 4 月底：最新完整年度 2025、Q1 尚未過申報期限 → 無期中報告，TTM 即 FY2025
NO_INTERIM = date(2026, 4, 30)
# 9 月中：TTM 用 2026Q2（今年上半年 ＋ 去年全年 − 去年上半年）
WITH_Q2 = date(2026, 9, 15)


def const(value):
    return {y: value for y in YEARS}


def duration(tag, per_year):
    return {tag: {f"From{y}0101To{y}1231": v for y, v in per_year.items()}}


def instant(tag, per_year):
    return {tag: {f"AsOf{y}1231": v for y, v in per_year.items()}}


def merge(*parts):
    out = {}
    for part in parts:
        for tag, by_ctx in part.items():
            out.setdefault(tag, {}).update(by_ctx)
    return out


class StubSource(OpenDataSource):
    def __init__(self, pool, dividends=None, unresolved=None, as_of=NO_INTERIM, as_filed=None):
        super().__init__(load_field_map(None), years=5, as_of=as_of)
        self._pool = pool
        self._as_filed = pool if as_filed is None else as_filed   # 預設：無追溯調整
        self._dividends = dividends or {}
        self._unresolved = unresolved or []

    def load_archives(self):
        return []

    def company_facts(self, stock_id):
        return self._pool, ["stub"], self._as_filed

    def dividend_history(self, stock_ids):
        return {s: {"by_year": self._dividends, "unresolved": self._unresolved} for s in stock_ids}


def facts_for(pool, company=COMPANY, **kw):
    return StubSource(pool, **kw).fetch_facts([company])[0]
