"""FinMind 期間語意彙總（PoC 2026-09-15 實測結論）。

實測發現三張報表期間語意不同：損益表為單季值、現金流量表為年初至今累計值、
資產負債表為期末存量。彙總方式若用錯，現金流量表會被高估約 2.5 倍，
因此這些測試以 2330 FY2024 的實際數列作為基準案例。
"""

import pytest

from twfactor.sources.finmind import FinMindError, FinMindSource


# 2330 FY2024 實際值（單位：新台幣元），取自 FinMind
IS_EPS_2024 = {"2024-03-31": 8.70, "2024-06-30": 9.56, "2024-09-30": 12.55, "2024-12-31": 14.45}
CF_OCF_2024 = {"2024-03-31": 436.3e9, "2024-06-30": 814.0e9,
               "2024-09-30": 1206.0e9, "2024-12-31": 1826.2e9}
BS_EQUITY_2024 = {"2024-03-31": 3665.7e9, "2024-06-30": 3820.1e9,
                  "2024-09-30": 4021.9e9, "2024-12-31": 4323.6e9}


class TestAnnual:
    def test_quarterly_sums_four_quarters(self):
        # 對外公告 FY2024 EPS 為 45.25，四季單季值相加應與之相符
        assert FinMindSource._annual(IS_EPS_2024, [2024], "quarterly") == pytest.approx([45.26])

    def test_cumulative_takes_year_end_not_sum(self):
        # 全年營業活動現金流為 1,826.2B；相加會得到 4,282.5B（高估約 2.3 倍）
        got = FinMindSource._annual(CF_OCF_2024, [2024], "cumulative_ytd")
        assert got == pytest.approx([1826.2e9])
        assert got[0] < sum(CF_OCF_2024.values()) / 2

    def test_instant_takes_year_end(self):
        assert FinMindSource._annual(BS_EQUITY_2024, [2024], "instant") == pytest.approx([4323.6e9])

    def test_quarterly_incomplete_year_is_none(self):
        partial = {k: v for k, v in IS_EPS_2024.items() if k != "2024-12-31"}
        assert FinMindSource._annual(partial, [2024], "quarterly") is None

    def test_cumulative_without_q4_is_none(self):
        """缺年底那一期就不以 Q3 累計值推估全年（PRD §19.6）。"""
        partial = {k: v for k, v in CF_OCF_2024.items() if k != "2024-12-31"}
        assert FinMindSource._annual(partial, [2024], "cumulative_ytd") is None

    def test_instant_without_q4_is_none(self):
        partial = {k: v for k, v in BS_EQUITY_2024.items() if k != "2024-12-31"}
        assert FinMindSource._annual(partial, [2024], "instant") is None

    def test_missing_year_is_none(self):
        assert FinMindSource._annual(IS_EPS_2024, [2023, 2024], "quarterly") is None

    def test_unknown_mode_raises(self):
        with pytest.raises(FinMindError):
            FinMindSource._annual(IS_EPS_2024, [2024], "yearly")


class TestTTM:
    def test_quarterly_ttm_sums_last_four(self):
        series = {"2023-12-31": 1.0, **IS_EPS_2024}
        assert FinMindSource._ttm(series, "quarterly") == pytest.approx(45.26)

    def test_quarterly_ttm_needs_four_quarters(self):
        assert FinMindSource._ttm({"2024-12-31": 14.45}, "quarterly") is None

    def test_cumulative_ttm_at_year_end_is_the_year(self):
        assert FinMindSource._ttm(CF_OCF_2024, "cumulative_ytd") == pytest.approx(1826.2e9)

    def test_cumulative_ttm_mid_year_rolls_prior_year(self):
        """Q2 TTM = 本期累計 + 去年全年 - 去年同期累計。"""
        series = {"2023-06-30": 100.0, "2023-12-31": 300.0, "2024-06-30": 150.0}
        assert FinMindSource._ttm(series, "cumulative_ytd") == pytest.approx(350.0)

    def test_cumulative_ttm_without_prior_year_is_none(self):
        assert FinMindSource._ttm({"2024-06-30": 150.0}, "cumulative_ytd") is None

    def test_empty_series_is_none(self):
        assert FinMindSource._ttm({}, "quarterly") is None
        assert FinMindSource._ttm({}, "cumulative_ytd") is None


class TestFieldNames:
    def test_confirmed_string_takes_priority(self):
        spec = {"confirmed": "Revenue", "candidates": ["NetSales", "Revenue"]}
        assert FinMindSource._field_names(spec) == ["Revenue", "NetSales"]

    def test_confirmed_list_for_aggregated_fields(self):
        spec = {"confirmed": ["LongtermBorrowings", "BondsPayable"], "candidates": ["BondsPayable"]}
        assert FinMindSource._field_names(spec) == ["LongtermBorrowings", "BondsPayable"]

    def test_unconfirmed_falls_back_to_candidates(self):
        assert FinMindSource._field_names({"confirmed": None, "candidates": ["A", "B"]}) == ["A", "B"]


class TestSemanticsProbe:
    def test_detects_cumulative_from_monotonic_series(self):
        assert FinMindSource._guess_semantics({"ocf": CF_OCF_2024}) == "cumulative_ytd"

    def test_detects_quarterly_from_non_monotonic_series(self):
        # 2891 FY2024 單季稅後淨利：21.2 / 16.6 / 21.8 / 13.8（十億元），非單調
        series = {"2024-03-31": 21.2, "2024-06-30": 16.6, "2024-09-30": 21.8, "2024-12-31": 13.8}
        assert FinMindSource._guess_semantics({"ni": series}) == "quarterly"

    def test_unknown_when_no_full_year(self):
        assert FinMindSource._guess_semantics({"x": {"2024-03-31": 1.0}}) == "unknown"

    def test_sign_crossing_series_is_skipped_not_counted_as_quarterly(self):
        """8299 FY2024 營業活動現金流由負轉正，但它其實是累計值。

        年內變號的科目不具判別力，若把它算成單季那一側，會把整檔誤判成 quarterly。
        這裡搭配一個確實累計的資本支出科目，結果應為 cumulative_ytd。
        """
        pools = {
            "ocf": {"2024-03-31": -79.0, "2024-06-30": -46.6,
                    "2024-09-30": -36.9, "2024-12-31": 20.9},
            "capex": {"2024-03-31": -0.9, "2024-06-30": -2.0,
                      "2024-09-30": -4.0, "2024-12-31": -9.6},
        }
        assert FinMindSource._guess_semantics(pools) == "cumulative_ytd"

    def test_negative_series_growing_in_magnitude_is_cumulative(self):
        pools = {"capex": {"2024-03-31": -181.3, "2024-06-30": -387.0,
                           "2024-09-30": -594.1, "2024-12-31": -956.0}}
        assert FinMindSource._guess_semantics(pools) == "cumulative_ytd"

    def test_series_containing_zero_is_skipped(self):
        assert FinMindSource._guess_semantics({"x": {"2024-03-31": 0.0, "2024-06-30": 1.0,
                                                    "2024-09-30": 2.0, "2024-12-31": 3.0}}) == "unknown"


class TestLatestYearOnly:
    """PRD §8.6 只看最新完整年度的存量值，不應要求五年齊全。

    FinMind 對餘額為零的會計科目不回傳，因此長期借款在某些年度會整年缺席；
    若沿用「五年缺一即 None」的規則，2454／8046／2408 這類確有長期借款的公司
    會被誤判成資料缺漏。
    """

    SERIES = {"2023-12-31": 5.0, "2025-12-31": 8.0}   # 2024 全年無此科目

    def test_five_year_series_is_none_when_a_year_missing(self):
        assert FinMindSource._annual(self.SERIES, [2023, 2024, 2025], "instant") is None

    def test_latest_year_alone_still_resolves(self):
        assert FinMindSource._annual(self.SERIES, [2025], "instant") == pytest.approx([8.0])

    def test_latest_year_missing_stays_none(self):
        assert FinMindSource._annual(self.SERIES, [2026], "instant") is None
