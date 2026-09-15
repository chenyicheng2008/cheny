"""PRD §8.9 ROIC（需求方 2026-09-15 指定計算式）。

    ROIC = 稅後營業利益 ÷（股東權益 ＋ 有息負債），皆取期末值
    稅後營業利益 = 年度營業利益 × (1 − 有效稅率)
    有效稅率     = 年度所得稅費用 ÷ 年度稅前淨利
    有息負債     = 期末 短期借款 + 長期借款 + 應付公司債

以假造的長表資料驅動 fetch_facts，確認算式、零餘額科目填補與各種 N/A 條件。
"""

import pytest

from twfactor.params import load_field_map
from twfactor.sources.finmind import FinMindSource

YEARS = [2021, 2022, 2023, 2024, 2025]
COMPANY = {"stock_id": "9999", "stock_name": "測試", "market": "twse",
           "industry_finmind": "電子工業"}


def _quarterly(type_name, per_year):
    """單季值：把年度值平均拆成四季，四季相加還原年度值。"""
    return [{"date": f"{y}-{m}-{d}", "stock_id": "9999", "type": type_name, "value": v / 4}
            for y, v in per_year.items()
            for m, d in (("03", "31"), ("06", "30"), ("09", "30"), ("12", "31"))]


def _instant(type_name, per_year):
    return [{"date": f"{y}-12-31", "stock_id": "9999", "type": type_name, "value": v}
            for y, v in per_year.items()]


def _const(value):
    return {y: value for y in YEARS}


class StubSource(FinMindSource):
    """以固定的長表回應取代 HTTP，其餘流程（解析、彙總、期間語意）照跑。"""

    def __init__(self, income, balance, cash=None, dividend=None):
        super().__init__(load_field_map(None), years=5)
        self._income, self._balance = income, balance
        self._cash, self._dividend = cash or [], dividend or []

    @property
    def target_years(self):
        return list(YEARS)

    def _get(self, dataset, **params):
        return {"TaiwanStockFinancialStatements": self._income,
                "TaiwanStockBalanceSheet": self._balance,
                "TaiwanStockCashFlowsStatement": self._cash,
                "TaiwanStockDividend": self._dividend}[dataset]


def _facts(income_overrides=None, balance_overrides=None):
    income = (_quarterly("OperatingIncome", _const(200.0))
              + _quarterly("PreTaxIncome", _const(200.0))
              + _quarterly("TAX", _const(40.0)))          # 有效稅率 20%
    balance = (_instant("Equity", _const(600.0))
               + _instant("ShorttermBorrowings", _const(100.0))
               + _instant("LongtermBorrowings", _const(300.0)))
    if income_overrides is not None:
        income = income_overrides
    if balance_overrides is not None:
        balance = balance_overrides
    return StubSource(income, balance).fetch_facts([COMPANY])[0]


class TestFormula:
    def test_matches_specified_formula(self):
        # NOPAT = 200 × (1 − 40/200) = 160；投入資本 = 600 + 100 + 300 = 1000 → 16%
        assert _facts().roic_annual == pytest.approx([16.0] * 5)

    def test_bonds_payable_included_in_interest_bearing_debt(self):
        balance = (_instant("Equity", _const(600.0))
                   + _instant("ShorttermBorrowings", _const(100.0))
                   + _instant("LongtermBorrowings", _const(200.0))
                   + _instant("BondsPayable", _const(100.0)))
        assert _facts(balance_overrides=balance).roic_annual == pytest.approx([16.0] * 5)

    def test_effective_tax_rate_is_per_year(self):
        income = (_quarterly("OperatingIncome", _const(200.0))
                  + _quarterly("PreTaxIncome", _const(200.0))
                  + _quarterly("TAX", {2021: 0.0, 2022: 40.0, 2023: 40.0,
                                       2024: 40.0, 2025: 100.0}))
        got = _facts(income_overrides=income).roic_annual
        assert got == pytest.approx([20.0, 16.0, 16.0, 16.0, 10.0])

    def test_zero_balance_debt_treated_as_zero(self):
        """無借款科目但有期末資產負債表 → 有息負債為 0，分母只剩股東權益。"""
        balance = _instant("Equity", _const(1000.0))
        assert _facts(balance_overrides=balance).roic_annual == pytest.approx([16.0] * 5)


class TestNotAvailable:
    def _reason(self, **kw):
        f = _facts(**kw)
        assert f.roic_annual is None
        return f.missing_reasons["roic"]

    def test_loss_year_has_no_derivable_tax_rate(self):
        income = (_quarterly("OperatingIncome", _const(200.0))
                  + _quarterly("PreTaxIncome", {2021: -50.0, 2022: 200.0, 2023: 200.0,
                                                2024: 200.0, 2025: 200.0})
                  + _quarterly("TAX", _const(40.0)))
        assert "有效稅率" in self._reason(income_overrides=income)

    def test_zero_pretax_income_is_na_not_division_by_zero(self):
        income = (_quarterly("OperatingIncome", _const(200.0))
                  + _quarterly("PreTaxIncome", {**_const(200.0), 2023: 0.0})
                  + _quarterly("TAX", _const(40.0)))
        assert "有效稅率" in self._reason(income_overrides=income)

    def test_non_positive_invested_capital(self):
        balance = (_instant("Equity", _const(-500.0))
                   + _instant("ShorttermBorrowings", _const(100.0))
                   + _instant("LongtermBorrowings", _const(300.0)))
        assert "投入資本" in self._reason(balance_overrides=balance)

    def test_missing_tax_field(self):
        income = (_quarterly("OperatingIncome", _const(200.0))
                  + _quarterly("PreTaxIncome", _const(200.0)))
        assert "缺少 ROIC 所需科目" in self._reason(income_overrides=income)

    def test_incomplete_year_history(self):
        income = (_quarterly("OperatingIncome", {y: 200.0 for y in YEARS[1:]})
                  + _quarterly("PreTaxIncome", _const(200.0))
                  + _quarterly("TAX", _const(40.0)))
        assert "缺少 ROIC 所需科目" in self._reason(income_overrides=income)

    def test_missing_equity_is_not_zero_filled(self):
        """股東權益缺漏不得視為 0（零餘額填補只適用於借款科目）。"""
        balance = (_instant("ShorttermBorrowings", _const(100.0))
                   + _instant("LongtermBorrowings", _const(300.0)))
        assert "缺少 ROIC 所需科目" in self._reason(balance_overrides=balance)
