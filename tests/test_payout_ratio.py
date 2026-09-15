"""PRD §8.4 股息支付率的組成方式（需求方 2026-09-15 確認）。

    TTM Payout Ratio = 最新完整年度現金股利 ÷ TTM EPS

分子為年度值、分母為最近四季滾動值，期間刻意不對齊 —— 股利是一年一議的年度決策，
沒有對應的 TTM 分子。tests/test_factors.py 覆蓋的是「給定比率後如何給分」，
這裡固定的是「比率本身怎麼算出來」，避免日後被無聲改掉。
"""

import pytest

from _xbrl_stub import EPS, WITH_Q2, const, duration, facts_for, merge

FLAT_EPS = const(4.0)                                   # 每年 EPS = 4.0


def _facts(eps_per_year, dividends, interim=None, **kw):
    pool = duration(EPS, eps_per_year)
    if interim:
        pool = merge(pool, {EPS: interim})
    return facts_for(pool, dividends=dividends, **kw)


class TestConstruction:
    def test_latest_fy_dividend_over_ttm_eps(self):
        f = _facts(FLAT_EPS, const(2.0))
        assert f.ttm_eps == pytest.approx(4.0)
        assert f.ttm_payout_ratio == pytest.approx(50.0)     # 2.0 / 4.0

    def test_uses_latest_year_dividend_not_average(self):
        f = _facts(FLAT_EPS, {2021: 9.0, 2022: 9.0, 2023: 9.0, 2024: 9.0, 2025: 1.0})
        assert f.ttm_payout_ratio == pytest.approx(25.0)     # 只看 2025 的 1.0

    def test_growing_eps_understates_payout(self):
        """已知偏誤：EPS 成長時分母跑在分子前面，支付率被系統性低估。"""
        flat = _facts(FLAT_EPS, const(2.0))
        growing = _facts({**FLAT_EPS, 2025: 8.0}, const(2.0))
        assert flat.ttm_payout_ratio == pytest.approx(50.0)
        assert growing.ttm_payout_ratio == pytest.approx(25.0)

    def test_ttm_rolls_forward_with_interim_report(self):
        """9 月中已有 Q2 報告：TTM = 今年上半年 3.0 ＋ 去年全年 4.0 − 去年上半年 2.0 = 5.0。"""
        interim = {"From20260101To20260630": 3.0, "From20250101To20250630": 2.0}
        f = _facts(FLAT_EPS, const(2.0), interim=interim, as_of=WITH_Q2)
        assert f.ttm_eps == pytest.approx(5.0)
        assert f.ttm_payout_ratio == pytest.approx(40.0)


class TestEdges:
    def test_negative_ttm_eps_gives_zero_ratio_not_na(self):
        """TTM EPS<0 時比率設 0，由 score_payout_ratio 依「TTM EPS<0」規則給 0 分。"""
        f = _facts({**FLAT_EPS, 2025: -4.0}, const(2.0))
        assert f.ttm_eps == pytest.approx(-4.0)
        assert f.ttm_payout_ratio == 0.0

    def test_zero_ttm_eps_is_na_not_zero_percent(self):
        f = _facts({**FLAT_EPS, 2025: 0.0}, const(2.0))
        assert f.ttm_payout_ratio is None
        assert "payout_ratio" in f.missing_reasons

    def test_no_dividend_record_means_zero_payout(self):
        """除權息結果表是全市場完整名單：查無紀錄即未配息 → 支付率 0%，不是 N/A。"""
        f = _facts(FLAT_EPS, {})
        assert f.dividend_annual == [0.0] * 5
        assert f.ttm_payout_ratio == 0.0

    def test_unresolved_mixed_dividend_is_na(self):
        """權息同除但查不到現金股利明細 → 不以合計值頂替，股利與支付率皆 N/A。"""
        f = _facts(FLAT_EPS, const(2.0), unresolved=[2024])
        assert f.dividend_annual is None
        assert "2024" in f.missing_reasons["dividend"]
        assert f.ttm_payout_ratio is None

    def test_interim_missing_for_company_is_na(self):
        """期中報告未涵蓋本公司時 TTM 無法還原，不退回用年度值頂替。"""
        f = _facts(FLAT_EPS, const(2.0), as_of=WITH_Q2)
        assert f.ttm_eps is None
        assert "ttm_eps" in f.missing_reasons
        assert f.ttm_payout_ratio is None
