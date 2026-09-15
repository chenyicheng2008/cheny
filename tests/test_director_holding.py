"""MOPS 董監持股匯出檔解析（PRD §8.10、§19.4）。

PRD §19.4 要求 PoC 確認來源能否排除獨立董事、是否提供質押比例。
這兩件事在本 provider 是用「欄位是否存在」判定的，湊不齊就標 N/A，不推估。
"""

import pytest

from twfactor.sources.director_holding import CsvDirectorHoldingProvider

HEADER = "公司代號,職稱,目前持股,設質股數,發行股數\n"


def _write(tmp_path, body, header=HEADER):
    path = tmp_path / "t16sn02.csv"
    path.write_text(header + body, encoding="utf-8")
    return CsvDirectorHoldingProvider(path)


class TestExcludeIndependentDirectors:
    def test_only_non_independent_holdings_counted(self, tmp_path):
        p = _write(tmp_path,
                   "2330,董事長,300,0,1000\n"
                   "2330,董事,200,0,1000\n"
                   "2330,獨立董事,400,0,1000\n")
        hold, _ = p.get("2330")
        assert hold == pytest.approx(50.0)      # (300+200)/1000，獨董 400 不計入

    def test_independent_marker_variants(self, tmp_path):
        p = _write(tmp_path, "2330,獨董,900,0,1000\n2330,監察人,100,0,1000\n")
        hold, _ = p.get("2330")
        assert hold == pytest.approx(10.0)

    def test_without_title_column_returns_na(self, tmp_path):
        """無職稱欄就無法排除獨立董事 —— 標 N/A，不送出混入獨董的持股。"""
        p = _write(tmp_path, "2330,500,0,1000\n", header="公司代號,目前持股,設質股數,發行股數\n")
        assert p.has_title_column is False
        assert p.get("2330") == (None, None)


class TestPledge:
    def test_pledge_ratio_is_over_non_independent_holdings(self, tmp_path):
        p = _write(tmp_path, "2330,董事,400,100,1000\n2330,獨立董事,600,600,1000\n")
        hold, pledge = p.get("2330")
        assert hold == pytest.approx(40.0)
        assert pledge == pytest.approx(25.0)    # 100/400，獨董的質押不計入

    def test_zero_pledge_is_zero_not_na(self, tmp_path):
        """有質押欄但金額為 0，是「無質押」而不是缺漏。"""
        _, pledge = _write(tmp_path, "2330,董事,400,0,1000\n").get("2330")
        assert pledge == 0.0

    def test_without_pledge_column_pledge_is_na(self, tmp_path):
        p = _write(tmp_path, "2330,董事,400,1000\n", header="公司代號,職稱,目前持股,發行股數\n")
        assert p.has_pledge_column is False
        hold, pledge = p.get("2330")
        assert hold == pytest.approx(40.0)
        assert pledge is None


class TestHoldingPercent:
    def test_direct_percentage_column_preferred(self, tmp_path):
        p = _write(tmp_path, "2330,董事,12.5\n2330,獨立董事,30.0\n",
                   header="公司代號,職稱,持股比例\n")
        hold, _ = p.get("2330")
        assert hold == pytest.approx(12.5)

    def test_thousands_separator_and_percent_sign(self, tmp_path):
        p = _write(tmp_path, '2330,董事,"1,234.5"\n', header="公司代號,職稱,持股比例\n")
        assert p.get("2330")[0] == pytest.approx(1234.5)

    def test_shares_without_outstanding_is_na(self, tmp_path):
        p = _write(tmp_path, "2330,董事,500\n", header="公司代號,職稱,目前持股\n")
        assert p.get("2330")[0] is None

    def test_unknown_stock_is_na(self, tmp_path):
        assert _write(tmp_path, "2330,董事,500,0,1000\n").get("9999") == (None, None)

    def test_empty_file_is_na(self, tmp_path):
        assert _write(tmp_path, "").get("2330") == (None, None)
