"""EPS 追溯調整至最新股本基準（需求方 2026-09-15 決定）。

配股、減資、面額變更時，次年年報會追溯調整前一年的 EPS。五年序列若混用原始數與追溯數，
股本變動就會被誤判成 EPS 衰退。2026-09-15 以 .fincache 比對實測：
富邦金 2022 原始 3.54 → 追溯 3.37、玉山金 1.10 → 1.06、國巨面額變更後 2024 由 42.12 → 9.53。
"""

import pytest

from _xbrl_stub import EPS, YEARS, duration, facts_for
from twfactor.sources.opendata import restate_to_latest_basis


class TestRestate:
    def test_no_share_change_is_identity(self):
        got, ratios = restate_to_latest_basis([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
        assert got == [1, 2, 3, 4, 5] and ratios == {}

    def test_stock_dividend_in_last_year_scales_all_prior_years(self):
        """最新一年配股 5%：2024 的追溯數已含調整，2021–2023 靠 2024 的比例串接下去。"""
        filed = [2.0, 2.2, 2.1, 2.3, 2.5]
        restated = [2.0, 2.2, 2.1, 2.3 / 1.05, 2.5]
        got, ratios = restate_to_latest_basis(restated, filed)
        assert got == pytest.approx([2.0 / 1.05, 2.2 / 1.05, 2.1 / 1.05, 2.3 / 1.05, 2.5])
        assert ratios == {3: pytest.approx(1 / 1.05)}

    def test_changes_in_several_years_compound(self):
        # 2023 年配股 10%（2023 年報追溯 2022），2025 年再配 5%（2025 年報追溯 2024）
        filed = [1.0, 1.1, 1.2, 1.3, 1.4]
        restated = [1.0, 1.1 / 1.1, 1.2, 1.3 / 1.05, 1.4]
        got, _ = restate_to_latest_basis(restated, filed)
        assert got == pytest.approx([1.0 / 1.1 / 1.05, 1.1 / 1.1 / 1.05, 1.2 / 1.05, 1.3 / 1.05, 1.4])

    def test_four_for_one_split_is_not_a_collapse(self):
        """面額 10 元改 2.5 元：追溯後五年都在同一基準，不會出現 40→10 的「大幅衰退」。"""
        filed = [46.0, 44.0, 42.0, 40.0, 11.5]
        restated = [46.0, 44.0, 42.0, 10.0, 11.5]
        got, _ = restate_to_latest_basis(restated, filed)
        assert got == pytest.approx([11.5, 11.0, 10.5, 10.0, 11.5])

    @pytest.mark.parametrize("filed_2023", [None, 0.0, -1.0])
    def test_uncomputable_ratio_is_na(self, filed_2023):
        filed = [1.0, 1.0, filed_2023, 1.0, 1.0]
        restated = [1.0, 1.0, 0.95, 1.0, 1.0]
        assert restate_to_latest_basis(restated, filed) == (None, {})

    def test_loss_years_keep_ratio_when_signs_agree(self):
        got, _ = restate_to_latest_basis([-1.0, -2.0, -0.95, 1.0, 1.0], [-1.0, -2.0, -1.0, 1.0, 1.0])
        assert got == pytest.approx([-0.95, -1.9, -0.95, 1.0, 1.0])


class TestFactsUseLatestBasis:
    def test_eps_annual_is_restated_and_noted(self):
        restated = duration(EPS, dict(zip(YEARS, [46.0, 44.0, 42.0, 10.0, 11.5])))
        filed = duration(EPS, dict(zip(YEARS, [46.0, 44.0, 42.0, 40.0, 11.5])))
        f = facts_for(restated, as_filed=filed)
        assert f.eps_annual == pytest.approx([11.5, 11.0, 10.5, 10.0, 11.5])
        assert "2024:0.2500" in f.provenance["eps"].note

    def test_uncomputable_basis_is_na_with_reason(self):
        restated = duration(EPS, dict(zip(YEARS, [1.0, 1.0, 0.95, 1.0, 1.0])))
        filed = duration(EPS, {2021: 1.0, 2022: 1.0, 2024: 1.0, 2025: 1.0})   # 缺 2023 原始數
        f = facts_for(restated, as_filed=filed)
        assert f.eps_annual is None
        assert "股本基準" in f.missing_reasons["eps"]
