"""公開資料來源：母體與市值、除權息結果表解析、XBRL 事實 → CompanyFacts。

回應格式取自 2026-09-15 實際打 TWSE／TPEx 端點的結果。
"""

from datetime import date

import pytest

from _xbrl_stub import (CAPEX, COMPANY, EPS, EQUITY, INTEREST, LEASE, LTB, NET_INCOME, OCF, OP,
                        REVENUE, WITH_Q2, const, duration, facts_for, instant, merge)
from twfactor.params import load_field_map
from twfactor.sources.opendata import (OpenDataError, OpenDataSource, is_common_stock, roc_to_iso,
                                       tpex_exright_rows, twse_detail_cash, twse_exright_rows)

TWSE_FIELDS = ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價", "權值+息值",
               "權/息", "漲停價格", "跌停價格", "開盤競價基準", "減除股利參考價", "詳細資料"]


def _twse_row(day, sid, value, kind):
    return [day, sid, "名稱", "0", "0", value, kind, "0", "0", "0", "0", f"{sid},20250604"]


TPEX_FIELDS = ["除權息日期", "代號", "名稱", "權/息", "現金股利"]
DETAIL = {"fields": ["股票代號", "股票名稱", "(每股配發現金股利)除息", "(增資配股) 除權"],
          "data": [["1605  ", "華新", "0.5 元／股", ""]]}


class TestParsers:
    @pytest.mark.parametrize("text,iso", [("1150914", "2026-09-14"), ("114年06月04日", "2025-06-04"),
                                          ("114/01/08", "2025-01-08")])
    def test_roc_dates(self, text, iso):
        assert roc_to_iso(text) == iso

    def test_bad_roc_date(self):
        with pytest.raises(ValueError):
            roc_to_iso("2025")

    @pytest.mark.parametrize("sid,ok", [("2330", True), ("6488", True), ("0050", False),
                                        ("00878", False), ("2330A", False)])
    def test_common_stock_filter(self, sid, ok):
        assert is_common_stock(sid) is ok

    def test_twse_cash_only_stock_only_and_mixed(self):
        rows = twse_exright_rows({"fields": TWSE_FIELDS, "data": [
            _twse_row("110年03月17日", "2330", "2.500000", "息"),
            _twse_row("114年06月04日", "1785", "1.453209", "權"),
            _twse_row("114年06月04日", "1605", "0.536107", "權息"),
        ]})
        assert [(r["stock_id"], r["cash"]) for r in rows] == [("2330", 2.5), ("1785", 0.0), ("1605", None)]
        assert rows[0]["date"] == "2021-03-17"
        assert rows[2]["detail"] == "1605,20250604"

    def test_twse_detail_cash(self):
        assert twse_detail_cash(DETAIL) == 0.5
        assert twse_detail_cash({"fields": [], "data": []}) is None

    def test_tpex_has_cash_column(self):
        rows = tpex_exright_rows({"tables": [{"fields": TPEX_FIELDS, "data": [
            ["114/01/08", "8299", "群聯", "除息", "13.12356282"],
            ["114/01/05", "1785", "光洋科", "除權", "0.00000000"]]}]})
        assert [(r["stock_id"], r["date"], r["cash"]) for r in rows] == [
            ("8299", "2025-01-08", pytest.approx(13.12356282)), ("1785", "2025-01-05", 0.0)]

    def test_changed_format_fails_loudly(self):
        with pytest.raises(OpenDataError, match="欄位"):
            twse_exright_rows({"fields": ["日期"], "data": [["x"]]})


class CannedSource(OpenDataSource):
    """以固定回應取代 HTTP；key 以前綴比對，不在表內的回空。"""

    def __init__(self, responses, as_of=date(2026, 4, 30)):
        super().__init__(load_field_map(None), years=5, as_of=as_of, request_pause=0)
        self.responses = responses

    def _get_json(self, key, url):
        for prefix, payload in self.responses.items():
            if key.startswith(prefix):
                return payload
        return {}


class TestDividendHistory:
    def test_combines_markets_and_resolves_mixed_rows(self):
        src = CannedSource({
            "twse_TWT49U_2025": {"fields": TWSE_FIELDS, "data": [
                _twse_row("114年03月17日", "2330", "4.500000", "息"),
                _twse_row("114年06月17日", "2330", "5.000000", "息"),
                _twse_row("114年06月04日", "1605", "0.536107", "權息")]},
            "twse_TWT49UDetail_1605_20250604": DETAIL,
            "tpex_exDailyQ_2025": {"tables": [{"fields": TPEX_FIELDS, "data": [
                ["114/01/08", "8299", "群聯", "除息", "13.0"],
                ["114/07/01", "8299", "群聯", "除息", "12.0"]]}]},
        })
        got = src.dividend_history(["2330", "1605", "8299", "1234"])
        assert got["2330"] == {"by_year": {2025: 9.5}, "unresolved": []}
        assert got["1605"] == {"by_year": {2025: 0.5}, "unresolved": []}
        assert got["8299"]["by_year"] == {2025: 25.0}
        assert got["1234"] == {"by_year": {}, "unresolved": []}

    def test_unparseable_detail_is_unresolved(self):
        src = CannedSource({"twse_TWT49U_2024": {"fields": TWSE_FIELDS, "data": [
            _twse_row("113年06月04日", "1605", "0.5", "權息")]}})
        assert src.dividend_history(["1605"])["1605"] == {"by_year": {}, "unresolved": [2024]}


class TestUniverse:
    RESPONSES = {
        "twse_t187ap03_L": [
            {"公司代號": "2330", "公司簡稱": "台積電", "產業別": "24", "已發行普通股數或TDR原股發行股數": "100"},
            {"公司代號": "2891", "公司簡稱": "中信金", "產業別": "17", "已發行普通股數或TDR原股發行股數": "300"},
            {"公司代號": "0050", "公司簡稱": "元大台灣50", "產業別": "", "已發行普通股數或TDR原股發行股數": "999"}],
        "tpex_t187ap03_O": [
            {"SecuritiesCompanyCode": "8299", "CompanyAbbreviation": "群聯",
             "SecuritiesIndustryCode": "24", "IssueShares": "10"},
            {"SecuritiesCompanyCode": "6015", "CompanyAbbreviation": "宏遠證",
             "SecuritiesIndustryCode": "17", "IssueShares": "5"}],
        "twse_STOCK_DAY_ALL": [
            {"Code": "2330", "ClosingPrice": "1,000.00", "Date": "1150914"},
            {"Code": "2891", "ClosingPrice": "50", "Date": "1150914"},
            {"Code": "0050", "ClosingPrice": "50", "Date": "1150914"}],
        "tpex_mainboard_quotes": [
            {"SecuritiesCompanyCode": "8299", "Close": "1920.00", "Date": "1150915"},
            {"SecuritiesCompanyCode": "6015", "Close": "---", "Date": "1150915"}],
    }

    def test_ranks_whole_market_by_close_times_shares(self):
        top = CannedSource(self.RESPONSES).top_by_market_cap(10)
        assert [(c["stock_id"], c["market_cap"]) for c in top] == [
            ("2330", 100_000.0), ("8299", 19_200.0), ("2891", 15_000.0)]
        assert top[0]["market"] == "twse" and top[1]["market"] == "tpex"
        assert top[0]["price_date"] == "2026-09-14"

    def test_etf_and_untraded_are_excluded(self):
        ids = {c["stock_id"] for c in CannedSource(self.RESPONSES).top_by_market_cap(10)}
        assert "0050" not in ids and "6015" not in ids

    def test_candidates_restrict_pool(self):
        top = CannedSource(self.RESPONSES).top_by_market_cap(10, candidates=["8299", "2891"])
        assert [c["stock_id"] for c in top] == ["8299", "2891"]

    def test_unknown_candidate_fails(self):
        with pytest.raises(OpenDataError, match="9999"):
            CannedSource(self.RESPONSES).top_by_market_cap(10, candidates=["9999"])


def _full_pool(**overrides):
    parts = {
        "eps": duration(EPS, const(4.0)),
        "rev": duration(REVENUE, const(1000.0)),
        "ni": duration(NET_INCOME, const(250.0)),
        "op": duration(OP, const(300.0)),
        "interest": duration(INTEREST, const(10.0)),
        "ocf": duration(OCF, const(400.0)),
        "capex": duration(CAPEX, const(-150.0)),
        "equity": instant(EQUITY, const(1000.0)),
        "debt": merge(instant(LTB, const(100.0)), instant(LEASE, const(50.0))),
    }
    parts.update(overrides)
    return merge(*[p for p in parts.values() if p])


class TestFacts:
    def test_annual_series_and_ratios(self):
        f = facts_for(_full_pool())
        assert f.eps_annual == [4.0] * 5
        assert f.fcf_annual == [250.0] * 5                   # 400 − |−150|
        assert f.net_margin_annual == pytest.approx([25.0] * 5)
        assert f.roe_annual == pytest.approx([25.0] * 5)
        assert f.interest_coverage == pytest.approx(30.0)    # 300 / 10

    def test_long_term_debt_includes_lease_per_prd(self):
        f = facts_for(_full_pool())
        assert f.long_term_debt == 150.0                     # 長期借款 100 ＋ 租賃負債 50
        assert "租賃負債" in f.provenance["long_term_debt"].note

    def test_no_debt_elements_with_balance_sheet_is_zero(self):
        f = facts_for(_full_pool(debt=None))
        assert f.long_term_debt == 0.0
        assert "認定為 0" in f.provenance["long_term_debt"].note

    def test_no_balance_sheet_is_na(self):
        f = facts_for(_full_pool(debt=None, equity=None))
        assert f.long_term_debt is None
        assert "lt_debt_equity" in f.missing_reasons

    def test_no_interest_element_with_cash_flow_is_zero_burden(self):
        f = facts_for(_full_pool(interest=None))
        assert f.interest_expense == 0.0
        assert f.interest_coverage is None                   # 交由「無利息負擔」規則評分

    def test_financial_industry_code(self):
        f = facts_for(_full_pool(), company={**COMPANY, "industry_code": "17"})
        assert f.is_financial

    def test_ttm_operating_income_uses_interim(self):
        interim = {OP: {"From20260101To20260630": 200.0, "From20250101To20250630": 100.0},
                   INTEREST: {"From20260101To20260630": 5.0, "From20250101To20250630": 5.0},
                   OCF: {"From20260101To20260630": 1.0, "From20250101To20250630": 1.0},
                   EPS: {"From20260101To20260630": 3.0, "From20250101To20250630": 2.0}}
        f = facts_for(merge(_full_pool(), interim), as_of=WITH_Q2)
        assert f.ttm_operating_income == pytest.approx(400.0)   # 200 + 300 − 100
        assert f.interest_coverage == pytest.approx(40.0)       # 400 / 10
        assert f.ttm_eps == pytest.approx(5.0)
