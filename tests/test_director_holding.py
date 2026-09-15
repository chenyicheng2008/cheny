"""MOPS 董監持股解析（PRD §8.10、§19.4）。

PRD §19.4 要求 PoC 確認來源能否排除獨立董事、是否提供質押比例。
這兩件事在本 provider 是用「欄位是否存在」判定的，湊不齊就標 N/A，不推估。

另外固定 PoC 從 open data 實測出的兩個坑（見 director_holding 模組說明）：
職稱白名單（這份資料是內部人全表，含總經理／大股東）、
以及法人董事佔多席時同一法人持股重複列示需去重。
"""

import pytest

from twfactor.sources.director_holding import CsvDirectorHoldingProvider

HEADER = "公司代號,職稱,目前持股,設質股數,發行股數\n"
NAMED_HEADER = "公司代號,姓名,職稱,目前持股,設質股數,發行股數\n"


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


class TestTitleWhitelist:
    """這份資料是「內部人」全表，不是董監表 —— 只排除獨立董事並不夠。"""

    @pytest.mark.parametrize("title", ["董事長本人", "董事本人", "董事之法人代表人",
                                       "常務董事本人", "副董事長本人", "監察人本人",
                                       "監察人之法人代表人"])
    def test_director_titles_are_counted(self, tmp_path, title):
        assert _write(tmp_path, f"2330,{title},500,0,1000\n").get("2330")[0] == pytest.approx(50.0)

    @pytest.mark.parametrize("title", ["總經理本人", "副總經理本人", "協理本人", "經理本人",
                                       "會計部門主管本人", "財務部門主管本人",
                                       "大股東本人", "大股東之法人代表人", "其他"])
    def test_non_director_insiders_are_excluded(self, tmp_path, title):
        p = _write(tmp_path, f"2330,{title},500,0,1000\n2330,董事本人,100,0,1000\n")
        assert p.get("2330")[0] == pytest.approx(10.0)      # 只算董事那 100 股

    @pytest.mark.parametrize("title", ["獨立董事本人", "獨立董事之法人代表人", "獨董"])
    def test_independent_directors_excluded(self, tmp_path, title):
        p = _write(tmp_path, f"2330,{title},500,0,1000\n2330,董事本人,100,0,1000\n")
        assert p.get("2330")[0] == pytest.approx(10.0)


class TestLegalEntityDeduplication:
    """法人董事佔多席時，MOPS 會把同一法人的持股每席重複列一次。

    以環球晶（6488）實際情形為例：中美矽晶 223,007,864 股佔兩席、列了兩次，
    直接加總會超過發行股數。實測 887 家上櫃公司有 380 家有此情形。
    """

    def test_repeated_legal_entity_counted_once(self, tmp_path):
        body = ("6488,徐秀蘭,董事長本人,847879,0,435000000\n"
                "6488,中美矽晶製品股份有限公司,董事本人,223007864,0,435000000\n"
                "6488,中美矽晶製品股份有限公司,董事本人,223007864,0,435000000\n")
        hold, _ = _write(tmp_path, body, header=NAMED_HEADER).get("6488")
        assert hold == pytest.approx((847879 + 223007864) / 435000000 * 100)
        assert hold < 100                                   # 重複加總會超過 100%

    def test_same_name_different_holdings_not_deduped(self, tmp_path):
        """同名但持股不同就是不同筆，不得誤併。"""
        body = ("2330,王小明,董事本人,300,0,1000\n"
                "2330,王小明,董事本人,200,0,1000\n")
        assert _write(tmp_path, body, header=NAMED_HEADER).get("2330")[0] == pytest.approx(50.0)

    def test_without_name_column_no_dedup(self, tmp_path):
        """無姓名欄時無從判斷是否為同一法人，保持原樣加總而非亂併。"""
        body = "2330,董事本人,300,0,1000\n2330,董事本人,300,0,1000\n"
        assert _write(tmp_path, body).get("2330")[0] == pytest.approx(60.0)


class TestSharesOutstandingInjection:
    """這份 open data 沒有發行股數，需由外部（FinMind）提供。"""

    def test_injected_shares_outstanding_used(self, tmp_path):
        from twfactor.sources.director_holding import CsvDirectorHoldingProvider
        path = tmp_path / "d.csv"
        path.write_text("公司代號,職稱,目前持股,設質股數\n2330,董事本人,250,0\n",
                        encoding="utf-8")
        provider = CsvDirectorHoldingProvider(path, shares_outstanding={"2330": 1000.0})
        assert provider.get("2330")[0] == pytest.approx(25.0)

    def test_file_column_wins_over_injected(self, tmp_path):
        from twfactor.sources.director_holding import CsvDirectorHoldingProvider
        path = tmp_path / "d.csv"
        path.write_text("公司代號,職稱,目前持股,設質股數,發行股數\n2330,董事本人,250,0,500\n",
                        encoding="utf-8")
        provider = CsvDirectorHoldingProvider(path, shares_outstanding={"2330": 1000.0})
        assert provider.get("2330")[0] == pytest.approx(50.0)

    def test_without_shares_outstanding_is_na(self, tmp_path):
        p = _write(tmp_path, "2330,董事本人,250,0\n",
                   header="公司代號,職稱,目前持股,設質股數\n")
        assert p.get("2330")[0] is None
