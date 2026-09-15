"""PRD §8.9 ROIC（需求方 2026-09-15 指定計算式）。

    ROIC = 稅後營業利益 ÷（股東權益 ＋ 有息負債），皆取期末值
    稅後營業利益 = 年度營業利益 × (1 − 有效稅率)
    有效稅率     = 年度所得稅費用 ÷ 年度稅前淨利
    有息負債     = 期末 短期借款 + 長期借款 + 應付公司債

以假造的 XBRL 事實驅動 fetch_facts，確認算式、零餘額科目填補與各種 N/A 條件。
"""

import pytest

from _xbrl_stub import (BONDS, EQUITY, LEASE, LTB, OP, PRETAX, STD, TAX, YEARS, const,
                        duration, facts_for, instant, merge)


def _facts(income=None, balance=None):
    if income is None:
        income = merge(duration(OP, const(200.0)), duration(PRETAX, const(200.0)),
                       duration(TAX, const(40.0)))            # 有效稅率 20%
    if balance is None:
        balance = merge(instant(EQUITY, const(600.0)), instant(STD, const(100.0)),
                        instant(LTB, const(300.0)))
    return facts_for(merge(income, balance))


class TestFormula:
    def test_matches_specified_formula(self):
        # NOPAT = 200 × (1 − 40/200) = 160；投入資本 = 600 + 100 + 300 = 1000 → 16%
        assert _facts().roic_annual == pytest.approx([16.0] * 5)

    def test_bonds_payable_included_in_interest_bearing_debt(self):
        balance = merge(instant(EQUITY, const(600.0)), instant(STD, const(100.0)),
                        instant(LTB, const(200.0)), instant(BONDS, const(100.0)))
        assert _facts(balance=balance).roic_annual == pytest.approx([16.0] * 5)

    def test_lease_liabilities_excluded_by_definition(self):
        """需求方定義的有息負債不含租賃負債；租賃負債只進 §8.6 長期負債。"""
        balance = merge(instant(EQUITY, const(600.0)), instant(STD, const(100.0)),
                        instant(LTB, const(300.0)), instant(LEASE, const(500.0)))
        assert _facts(balance=balance).roic_annual == pytest.approx([16.0] * 5)

    def test_effective_tax_rate_is_per_year(self):
        income = merge(duration(OP, const(200.0)), duration(PRETAX, const(200.0)),
                       duration(TAX, {2021: 0.0, 2022: 40.0, 2023: 40.0, 2024: 40.0, 2025: 100.0}))
        assert _facts(income=income).roic_annual == pytest.approx([20.0, 16.0, 16.0, 16.0, 10.0])

    def test_zero_balance_debt_treated_as_zero(self):
        """無借款元素但有期末資產負債表 → 有息負債為 0，分母只剩股東權益。"""
        assert _facts(balance=instant(EQUITY, const(1000.0))).roic_annual == pytest.approx([16.0] * 5)


class TestNotAvailable:
    def _reason(self, **kw):
        f = _facts(**kw)
        assert f.roic_annual is None
        return f.missing_reasons["roic"]

    def test_loss_year_has_no_derivable_tax_rate(self):
        income = merge(duration(OP, const(200.0)),
                       duration(PRETAX, {**const(200.0), 2021: -50.0}),
                       duration(TAX, const(40.0)))
        assert "有效稅率" in self._reason(income=income)

    def test_zero_pretax_income_is_na_not_division_by_zero(self):
        income = merge(duration(OP, const(200.0)), duration(PRETAX, {**const(200.0), 2023: 0.0}),
                       duration(TAX, const(40.0)))
        assert "有效稅率" in self._reason(income=income)

    def test_non_positive_invested_capital(self):
        balance = merge(instant(EQUITY, const(-500.0)), instant(STD, const(100.0)),
                        instant(LTB, const(300.0)))
        assert "投入資本" in self._reason(balance=balance)

    def test_missing_tax_field(self):
        income = merge(duration(OP, const(200.0)), duration(PRETAX, const(200.0)))
        assert "缺少 ROIC 所需科目" in self._reason(income=income)

    def test_incomplete_year_history(self):
        income = merge(duration(OP, {y: 200.0 for y in YEARS[1:]}),
                       duration(PRETAX, const(200.0)), duration(TAX, const(40.0)))
        assert "缺少 ROIC 所需科目" in self._reason(income=income)

    def test_missing_equity_is_not_zero_filled(self):
        """股東權益缺漏不得視為 0（零餘額填補只適用於借款科目）。"""
        balance = merge(instant(STD, const(100.0)), instant(LTB, const(300.0)))
        assert "缺少 ROIC 所需科目" in self._reason(balance=balance)
