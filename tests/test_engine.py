"""引擎層測試：金融業部分評分、未滿五年、單項缺漏、總分與排名（PRD §10、§11）。"""

import pytest

from twfactor.models import CompanyFacts
from twfactor.params import load_params
from twfactor.scoring import ScoringEngine, competition_rank

P = load_params()
ENGINE = ScoringEngine(P)


def perfect_facts(stock_id="P001", is_financial=False) -> CompanyFacts:
    """一家十項全滿（一般產業 16 分）的假想公司。"""
    return CompanyFacts(
        stock_id=stock_id, stock_name=f"測試{stock_id}", market="twse",
        is_financial=is_financial, fiscal_years=[2021, 2022, 2023, 2024, 2025],
        eps_annual=[5.0, 6.0, 7.0, 8.0, 9.0],
        fcf_annual=[100, 110, 120, 130, 140],
        dividend_annual=[3.0, 3.5, 4.0, 4.5, 5.0],
        net_margin_annual=[30, 31, 32, 33, 34],
        roe_annual=[20, 21, 22, 23, 24],
        roic_annual=[15, 16, 17, 18, 19],
        ttm_payout_ratio=40.0, ttm_eps=9.0,
        long_term_debt=10.0, total_equity=1000.0,
        interest_coverage=50.0, interest_expense=20.0, ttm_operating_income=900.0,
        director_holding_pct=35.0, director_pledge_pct=0.0,
    )


class TestGeneralTotals:
    def test_perfect_company_scores_16(self):
        card = ENGINE.score_company(perfect_facts())
        assert card.raw_total == 16.0
        assert card.pool_max_score == 16
        assert card.attainable_max == 16.0
        assert card.data_completeness == "完整"

    def test_weights_default_to_1_so_raw_equals_weighted(self):
        card = ENGINE.score_company(perfect_facts())
        assert card.raw_total == card.weighted_total

    def test_tag_high_roe_and_roic(self):
        assert "高 ROE 及 ROIC" in ENGINE.score_company(perfect_facts()).tags

    def test_tag_high_roe_only(self):
        f = perfect_facts()
        f.roic_annual = [5, 5, 5, 5, 5]
        assert ENGINE.score_company(f).tags == ["高 ROE"]

    def test_tags_do_not_add_score(self):
        f = perfect_facts()
        card = ENGINE.score_company(f)
        assert card.raw_total == sum(fs.score for fs in card.factors.values() if fs.score is not None)


class TestInsufficientHistory:
    def test_under_five_years_is_na_not_zero(self):
        f = perfect_facts("N001")
        f.eps_annual = [5.0, 6.0, 7.0]           # 創新板未滿五年
        card = ENGINE.score_company(f)
        assert card.status == "insufficient_history"
        assert card.raw_total is None
        assert all(fs.score is None for fs in card.factors.values())

    def test_excluded_from_ranking(self):
        good, young = perfect_facts("G001"), perfect_facts("N001")
        young.eps_annual = [5.0, 6.0]
        cards = ENGINE.score_universe([good, young])
        by_id = {c.stock_id: c for c in cards}
        assert by_id["G001"].rank == 1
        assert by_id["N001"].rank is None


class TestMissingFactor:
    def test_missing_factor_is_not_scaled_up(self):
        f = perfect_facts("M001")
        f.roic_annual = None                      # 單項抓不到
        card = ENGINE.score_company(f)
        assert card.factors["roic"].is_na
        assert card.raw_total == 15.0             # 16 - 1，不按比例放大
        assert card.attainable_max == 15.0
        assert "缺漏因子" in card.notes[0]

    def test_missing_factor_reported_in_completeness(self):
        f = perfect_facts("M002")
        f.director_holding_pct = None
        card = ENGINE.score_company(f)
        assert "部分缺漏" in card.data_completeness


class TestFinancialSector:
    def test_only_five_factors_applicable(self):
        card = ENGINE.score_company(perfect_facts("F001", is_financial=True))
        applicable = {k for k, fs in card.factors.items() if fs.applicable}
        assert applicable == set(P["financial_sector"]["applicable_factors"])

    def test_max_is_9(self):
        card = ENGINE.score_company(perfect_facts("F001", is_financial=True))
        assert card.raw_total == 9.0
        assert card.pool_max_score == 9

    def test_inapplicable_factors_are_na_not_zero_and_not_counted_as_missing(self):
        card = ENGINE.score_company(perfect_facts("F001", is_financial=True))
        assert card.factors["net_margin"].score is None
        assert card.factors["net_margin"].applicable is False
        assert card.data_completeness == "完整"      # 不適用 ≠ 缺漏

    def test_not_mixed_with_general_pool(self):
        cards = ENGINE.score_universe([
            perfect_facts("G001"),
            perfect_facts("F001", is_financial=True),
        ])
        by_id = {c.stock_id: c for c in cards}
        assert by_id["G001"].rank_pool == "general" and by_id["G001"].rank == 1
        assert by_id["F001"].rank_pool == "financial" and by_id["F001"].rank == 1


class TestRanking:
    def test_competition_rank_skips_numbers(self):
        assert competition_rank([16, 15, 15, 14]) == [1, 2, 2, 4]

    def test_three_way_tie(self):
        assert competition_rank([10, 10, 10, 9]) == [1, 1, 1, 4]

    def test_high_risk_flag_propagates_to_card(self):
        f = perfect_facts("R001")
        f.total_equity = -100.0
        card = ENGINE.score_company(f)
        assert "高風險警示" in card.flags


class TestReproducibility:
    def test_same_input_same_output(self):
        """PRD §16 可重現性：同一資料快照應產生相同評分。"""
        a = ENGINE.score_company(perfect_facts())
        b = ENGINE.score_company(perfect_facts())
        assert a.raw_total == b.raw_total
        assert {k: v.rule for k, v in a.factors.items()} == {k: v.rule for k, v in b.factors.items()}


class TestParamsAreNotHardcoded:
    def test_changing_threshold_changes_score(self):
        """PRD §12：調整設定即可改變結果，不需改程式。"""
        import copy
        tweaked = copy.deepcopy(P)
        tweaked["factors"]["roe"]["high_pct"] = 25.0     # 提高 ROE 門檻
        card = ScoringEngine(tweaked).score_company(perfect_facts())
        assert card.factors["roe"].score == 1.0          # 原為 2.0
        assert card.raw_total == 15.0

    def test_weight_change_splits_raw_and_weighted(self):
        import copy
        tweaked = copy.deepcopy(P)
        tweaked["weights"]["eps"] = 3.0
        card = ScoringEngine(tweaked).score_company(perfect_facts())
        assert card.raw_total == 16.0                    # 原始分數不受權重影響
        assert card.weighted_total == 16.0 + 2.0 * (3.0 - 1.0)
