"""PRD §15 驗收要求的邊界值測試。

涵蓋：EPS 負值與衰退恢復、股息下降 5%、Payout 0%/50%/100%、
ROE 12%/15%、股東權益≤0、質押 20%、無利息負擔、金融業部分評分。
"""

import pytest

from twfactor.models import CompanyFacts
from twfactor.params import load_params
from twfactor.scoring import factors as F

P = load_params()
Y = P["periods"]["annual_years"]


def facts(**kw) -> CompanyFacts:
    kw.setdefault("stock_id", "TEST")
    kw.setdefault("fiscal_years", [2021, 2022, 2023, 2024, 2025])
    return CompanyFacts(**kw)


def eps_score(series):
    return F.score_eps(facts(eps_annual=series), P["factors"]["eps"], Y).score


# --- 8.1 EPS -------------------------------------------------------------
class TestEPS:
    def test_strict_growth_gets_full_2(self):
        assert eps_score([1.0, 2.0, 3.0, 4.0, 5.0]) == 2.0

    def test_flat_series_stability_only(self):
        assert eps_score([5.0, 5.0, 5.0, 5.0, 5.0]) == 1.0

    def test_negative_eps_zeroes_whole_factor(self):
        # 即使其餘四年逐年成長，仍為 0（§10.1）
        assert eps_score([-1.0, 2.0, 3.0, 4.0, 5.0]) == 0.0

    def test_decline_exactly_5pct_is_acceptable(self):
        # 10 → 9.5 正好 -5%，落在 1 分區間
        assert eps_score([10.0, 9.5, 9.5, 9.5, 9.5]) == 1.0

    def test_decline_just_over_5pct_drops_to_half(self):
        assert eps_score([10.0, 9.4, 9.4, 9.4, 9.4]) == 0.5

    def test_decline_exactly_12pct_stays_in_half_band(self):
        # 10 → 8.8 正好 -12%，PRD 明示落在 0.5 區間（非大幅衰退）
        assert eps_score([10.0, 8.8, 8.8, 8.8, 8.8]) == 0.5

    def test_severe_decline_recovered_next_year(self):
        # 10 → 8（-20%）→ 10.5 恢復至衰退前水準以上 → 0.5
        assert eps_score([10.0, 8.0, 10.5, 10.5, 10.5]) == 0.5

    def test_severe_decline_recovery_exactly_to_prior_level(self):
        # 次年「恢復至衰退前一年度水準以上」含等於
        assert eps_score([10.0, 8.0, 10.0, 10.0, 10.0]) == 0.5

    def test_severe_decline_not_recovered(self):
        assert eps_score([10.0, 8.0, 9.9, 9.9, 9.9]) == 0.0

    def test_two_severe_declines(self):
        assert eps_score([10.0, 8.0, 12.0, 9.0, 12.0]) == 0.0

    def test_severe_decline_in_last_year_cannot_prove_recovery(self):
        assert eps_score([10.0, 11.0, 12.0, 13.0, 10.0]) == 0.0

    def test_insufficient_history_is_na(self):
        fs = F.score_eps(facts(eps_annual=[1.0, 2.0, 3.0]), P["factors"]["eps"], Y)
        assert fs.score is None and fs.is_na


# --- 8.2 自由現金流 -------------------------------------------------------
class TestFreeCashFlow:
    def test_all_positive(self):
        assert F.score_free_cash_flow(facts(fcf_annual=[1, 2, 3, 4, 5]), P["factors"]["free_cash_flow"], Y).score == 1.0

    def test_zero_counts_as_fail(self):
        assert F.score_free_cash_flow(facts(fcf_annual=[1, 2, 0, 4, 5]), P["factors"]["free_cash_flow"], Y).score == 0.0


# --- 8.3 現金股利 ---------------------------------------------------------
class TestDividend:
    def s(self, series):
        return F.score_dividend(facts(dividend_annual=series), P["factors"]["dividend"], Y).score

    def test_paid_and_stable(self):
        assert self.s([2.0, 2.0, 2.5, 3.0, 3.0]) == 2.0

    def test_decline_exactly_5pct_still_passes(self):
        assert self.s([10.0, 9.5, 9.5, 9.5, 9.5]) == 2.0

    def test_decline_over_5pct_loses_second_point(self):
        assert self.s([10.0, 9.4, 9.4, 9.4, 9.4]) == 1.0

    def test_multiple_small_declines_allowed(self):
        assert self.s([10.0, 9.6, 9.3, 9.0, 8.7]) == 2.0

    def test_missed_year_loses_both_points(self):
        # 斷配一年：第一項失分，且 2.0→0 為 -100% 年減幅，第二項同時失分
        assert self.s([2.0, 0.0, 2.0, 2.0, 2.0]) == 0.0

    def test_only_last_year_missing_dividend(self):
        assert self.s([2.0, 2.0, 2.0, 2.0, 0.0]) == 0.0

    def test_growing_dividend_from_zero_base(self):
        # 首年未配但其後逐年成長：第一項失分，無年減幅故第二項得分
        assert self.s([0.0, 1.0, 2.0, 3.0, 4.0]) == 1.0


# --- 8.4 股息支付率 -------------------------------------------------------
class TestPayout:
    def s(self, ratio, ttm_eps=3.0):
        return F.score_payout_ratio(facts(ttm_payout_ratio=ratio, ttm_eps=ttm_eps),
                                    P["factors"]["payout_ratio"], Y).score

    @pytest.mark.parametrize("ratio,expected", [
        (0.0, 0.0), (0.1, 2.0), (49.99, 2.0),
        (50.0, 1.0), (99.99, 1.0),
        (100.0, 0.0), (150.0, 0.0),
    ])
    def test_tiers(self, ratio, expected):
        assert self.s(ratio) == expected

    def test_negative_ttm_eps_overrides_tier(self):
        assert self.s(30.0, ttm_eps=-1.0) == 0.0


# --- 8.5 淨利率 -----------------------------------------------------------
class TestNetMargin:
    def s(self, series):
        return F.score_net_margin(facts(net_margin_annual=series), P["factors"]["net_margin"], Y).score

    def test_all_above_20(self):
        assert self.s([21, 22, 23, 24, 25]) == 2.0

    def test_exactly_20_is_not_above_20(self):
        assert self.s([20, 22, 23, 24, 25]) == 1.0   # 落到 >10% 條件

    def test_all_above_10(self):
        assert self.s([11, 12, 13, 14, 15]) == 1.0

    def test_strict_growth_below_10(self):
        assert self.s([1, 2, 3, 4, 5]) == 1.0

    def test_no_condition_met(self):
        assert self.s([5, 4, 6, 3, 8]) == 0.0

    def test_high_band_does_not_stack_to_3(self):
        assert self.s([21, 22, 23, 24, 25]) == 2.0  # 非 2+1


# --- 8.6 長期負債權益比 ---------------------------------------------------
class TestLtDebtEquity:
    def f(self, ltd, eq):
        return F.score_lt_debt_equity(facts(long_term_debt=ltd, total_equity=eq),
                                      P["factors"]["lt_debt_equity"], Y)

    def test_below_threshold(self):
        assert self.f(40, 100).score == 1.0

    def test_exactly_threshold_fails(self):
        assert self.f(50, 100).score == 0.0

    def test_negative_equity_flags_high_risk(self):
        fs = self.f(10, -5)
        assert fs.score == 0.0 and "高風險警示" in fs.flags

    def test_zero_equity_flags_high_risk(self):
        fs = self.f(10, 0)
        assert fs.score == 0.0 and "高風險警示" in fs.flags


# --- 8.7 利息保障倍數 -----------------------------------------------------
class TestInterestCoverage:
    def s(self, **kw):
        return F.score_interest_coverage(facts(**kw), P["factors"]["interest_coverage"], Y).score

    @pytest.mark.parametrize("cov,expected", [
        (20.1, 2.0), (20.0, 1.0), (10.1, 1.0), (10.0, 0.0), (3.0, 0.0),
    ])
    def test_tiers(self, cov, expected):
        assert self.s(interest_coverage=cov, interest_expense=100.0) == expected

    def test_no_interest_burden_with_positive_op_income(self):
        assert self.s(interest_expense=0.0, ttm_operating_income=500.0) == 2.0

    def test_no_interest_burden_with_loss(self):
        assert self.s(interest_expense=0.0, ttm_operating_income=-500.0) == 0.0


# --- 8.8 / 8.9 ROE, ROIC --------------------------------------------------
class TestRoeRoic:
    def roe(self, series):
        return F.score_roe(facts(roe_annual=series), P["factors"]["roe"], Y).score

    def test_exactly_15_gets_2(self):
        assert self.roe([15, 15, 15, 15, 15]) == 2.0

    def test_just_below_15_drops_to_1(self):
        assert self.roe([14.99, 16, 17, 18, 19]) == 1.0

    def test_exactly_12_gets_1(self):
        assert self.roe([12, 12, 12, 12, 12]) == 1.0

    def test_just_below_12_gets_0(self):
        assert self.roe([11.99, 20, 20, 20, 20]) == 0.0

    def test_roic_exactly_10(self):
        assert F.score_roic(facts(roic_annual=[10, 10, 10, 10, 10]), P["factors"]["roic"], Y).score == 1.0

    def test_roic_below_10(self):
        assert F.score_roic(facts(roic_annual=[9.99, 20, 20, 20, 20]), P["factors"]["roic"], Y).score == 0.0


# --- 8.10 董監持股 --------------------------------------------------------
class TestDirectorHolding:
    def s(self, hold, pledge):
        return F.score_director_holding(facts(director_holding_pct=hold, director_pledge_pct=pledge),
                                        P["factors"]["director_holding"], Y).score

    def test_exactly_10pct_does_not_score(self):
        assert self.s(10.0, 0.0) == 0.0

    def test_just_above_10pct_scores(self):
        assert self.s(10.01, 0.0) == 1.0

    def test_pledge_exactly_20_does_not_cancel(self):
        assert self.s(30.0, 20.0) == 1.0

    def test_pledge_above_20_cancels(self):
        assert self.s(30.0, 20.01) == 0.0

    def test_missing_holding_is_na(self):
        assert self.s(None, None) is None
