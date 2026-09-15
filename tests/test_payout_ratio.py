"""PRD §8.4 股息支付率的組成方式（需求方 2026-09-15 確認）。

    TTM Payout Ratio = 最新完整年度現金股利 ÷ TTM EPS

分子為年度值、分母為最近四季滾動值，期間刻意不對齊 —— 股利是一年一議的年度決策，
沒有對應的 TTM 分子。tests/test_factors.py 覆蓋的是「給定比率後如何給分」，
這裡固定的是「比率本身怎麼算出來」，避免日後被無聲改掉。
"""

import pytest

from twfactor.params import load_field_map
from twfactor.sources.finmind import FinMindSource

YEARS = [2021, 2022, 2023, 2024, 2025]
COMPANY = {"stock_id": "9999", "stock_name": "測試", "market": "twse",
           "industry_finmind": "電子工業"}


def _eps_rows(per_year_quarters):
    """per_year_quarters: {year: [Q1, Q2, Q3, Q4]} 單季 EPS。"""
    days = ("03-31", "06-30", "09-30", "12-31")
    return [{"date": f"{y}-{d}", "stock_id": "9999", "type": "EPS", "value": v}
            for y, qs in per_year_quarters.items() for d, v in zip(days, qs)]


def _dividend_rows(per_year):
    return [{"date": f"{y}-07-01", "stock_id": "9999",
             "CashExDividendTradingDate": f"{y}-06-15",
             "CashEarningsDistribution": v, "CashStatutorySurplus": 0.0}
            for y, v in per_year.items()]


class StubSource(FinMindSource):
    def __init__(self, eps_rows, dividend_rows):
        super().__init__(load_field_map(None), years=5)
        self._eps, self._div = eps_rows, dividend_rows

    @property
    def target_years(self):
        return list(YEARS)

    def _get(self, dataset, **params):
        return {"TaiwanStockFinancialStatements": self._eps,
                "TaiwanStockBalanceSheet": [],
                "TaiwanStockCashFlowsStatement": [],
                "TaiwanStockDividend": self._div}[dataset]


def _facts(eps_per_year, dividends):
    return StubSource(_eps_rows(eps_per_year), _dividend_rows(dividends)).fetch_facts([COMPANY])[0]


FLAT_EPS = {y: [1.0, 1.0, 1.0, 1.0] for y in YEARS}          # 每年 TTM EPS = 4.0


class TestConstruction:
    def test_latest_fy_dividend_over_ttm_eps(self):
        f = _facts(FLAT_EPS, {y: 2.0 for y in YEARS})
        assert f.ttm_eps == pytest.approx(4.0)
        assert f.ttm_payout_ratio == pytest.approx(50.0)     # 2.0 / 4.0

    def test_uses_latest_year_dividend_not_average(self):
        f = _facts(FLAT_EPS, {2021: 9.0, 2022: 9.0, 2023: 9.0, 2024: 9.0, 2025: 1.0})
        assert f.ttm_payout_ratio == pytest.approx(25.0)     # 只看 2025 的 1.0

    def test_cash_and_statutory_surplus_are_summed(self):
        rows = _dividend_rows({y: 1.0 for y in YEARS})
        for r in rows:
            r["CashStatutorySurplus"] = 1.0                  # 盈餘配息 + 公積配息
        f = StubSource(_eps_rows(FLAT_EPS), rows).fetch_facts([COMPANY])[0]
        assert f.ttm_payout_ratio == pytest.approx(50.0)     # (1.0+1.0) / 4.0

    def test_growing_eps_understates_payout(self):
        """已知偏誤：EPS 成長時分母跑在分子前面，支付率被系統性低估。

        股利與前一年相同，但 TTM EPS 翻倍，支付率就從 50% 掉到 25%。
        這是接受的設計，記在這裡以免被誤認為 bug。
        """
        flat = _facts(FLAT_EPS, {y: 2.0 for y in YEARS})
        growing = _facts({**FLAT_EPS, 2025: [2.0, 2.0, 2.0, 2.0]}, {y: 2.0 for y in YEARS})
        assert flat.ttm_payout_ratio == pytest.approx(50.0)
        assert growing.ttm_payout_ratio == pytest.approx(25.0)


class TestEdges:
    def test_negative_ttm_eps_gives_zero_ratio_not_na(self):
        """TTM EPS<0 時比率設 0，由 score_payout_ratio 依「TTM EPS<0」規則給 0 分。"""
        f = _facts({**FLAT_EPS, 2025: [-1.0, -1.0, -1.0, -1.0]}, {y: 2.0 for y in YEARS})
        assert f.ttm_eps == pytest.approx(-4.0)
        assert f.ttm_payout_ratio == 0.0

    def test_zero_ttm_eps_is_na_not_zero_percent(self):
        f = _facts({**FLAT_EPS, 2025: [1.0, -1.0, 1.0, -1.0]}, {y: 2.0 for y in YEARS})
        assert f.ttm_eps == pytest.approx(0.0)
        assert f.ttm_payout_ratio is None
        assert "payout_ratio" in f.missing_reasons

    def test_no_dividend_record_is_na(self):
        f = _facts(FLAT_EPS, {})
        assert f.ttm_payout_ratio is None
        assert "payout_ratio" in f.missing_reasons
