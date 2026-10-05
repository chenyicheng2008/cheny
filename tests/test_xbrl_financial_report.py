"""scripts/xbrl_financial_report.py：期間推導與現金流量指標。"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "xbrl_financial_report.py"
spec = importlib.util.spec_from_file_location("xbrl_financial_report", SCRIPT)
xfr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xfr)

M = 1_000_000


def make_facts() -> "xfr.Facts":
    f = xfr.Facts()
    f.merge({
        "ifrs-full:Revenue": {"From20240101To20241231": 1000 * M, "From20250101To20251231": 1100 * M,
                              "From20240101To20240630": 450 * M, "From20250101To20250630": 500 * M,
                              "From20250401To20250630": 260 * M},
        "ifrs-full:ProfitLoss": {"From20240101To20241231": 180 * M, "From20250101To20251231": 200 * M,
                                 "From20240101To20240630": 80 * M, "From20250101To20250630": 90 * M,
                                 "From20250401To20250630": 50 * M},
        "ifrs-full:BasicEarningsLossPerShare": {"From20240101To20241231": 3.0, "From20250101To20251231": 3.4,
                                                "From20240101To20240630": 1.3, "From20250101To20250630": 1.5},
        "ifrs-full:CashFlowsFromUsedInOperatingActivities": {
            "From20240101To20241231": 250 * M, "From20250101To20251231": 300 * M,
            "From20240101To20240630": 100 * M, "From20250101To20250630": 120 * M},
        "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": {
            "From20240101To20241231": -40 * M, "From20250101To20251231": -60 * M,
            "From20240101To20240630": -10 * M, "From20250101To20250630": -50 * M},
        "ifrs-full:Equity": {"AsOf20231231": 900 * M, "AsOf20241231": 1100 * M, "AsOf20250630": 1150 * M},
        "ifrs-full:IssuedCapital": {"AsOf20250630": 600 * M},
        "ifrs-full:CashAndCashEquivalents": {"AsOf20250630": 500 * M},
    }, "test")
    return f


def test_interim_not_used_when_full_year_exists():
    p = xfr.Periods(make_facts())
    assert p.years == [2024, 2025]
    assert p.ytd_year is None                   # 期中檔須晚於最後完整年度才當作最新期
    assert p.ttm("revenue") == pytest.approx(1100 * M)


def test_ttm_uses_ytd_plus_prior_year_minus_prior_ytd():
    f = make_facts()
    # 移除 2025 全年，讓 2025 H1 成為最新期中
    for tag in list(f.raw):
        f.raw[tag].pop("From20250101To20251231", None)
    p = xfr.Periods(f)
    assert p.years == [2024]
    assert p.ytd_label == "2025 H1"
    assert p.ttm("revenue") == pytest.approx(500 * M + 1000 * M - 450 * M)
    assert p.ttm("eps") == pytest.approx(1.5 + 3.0 - 1.3)
    # Q1＝H1－Q2（只有半年報時）
    assert p.quarter("revenue", 2025, 1) == pytest.approx(240 * M)
    assert p.quarter("revenue", 2025, 2) == pytest.approx(260 * M)


def test_half_year_slice_when_q3_missing():
    p = xfr.Periods(make_facts())
    labels = [lbl for lbl, _ in p.slices()]
    assert "2024 H2" in labels and "2025 Q1" in labels and "2025 Q2" in labels
    h2 = dict(p.slices())["2024 H2"]
    assert h2("revenue") == pytest.approx(550 * M)


def test_cash_flow_metrics():
    f = make_facts()
    r = xfr.analyse(f, "9999")
    y = r["flows"]["2025"]
    assert r["shares"] == pytest.approx(60 * M)
    assert y["fcf"] == pytest.approx(240 * M)
    assert y["fcf_ps"] == pytest.approx(4.0)
    assert y["ocf_ni"] == pytest.approx(1.5)
    assert y["capex_rev"] == pytest.approx(60 / 1100)
    y24 = r["flows"]["2024"]
    assert y24["roe"] == pytest.approx(180 / 1000)


def test_missing_values_stay_none():
    f = make_facts()
    r = xfr.analyse(f, "9999")
    y = r["flows"]["2025"]
    assert y["gm"] is None and y["div_paid"] is None and y["fcf_after_div"] is None


def test_markdown_subset():
    html = xfr.md_to_html("## 標題\n\n- **重點** 一\n- 二\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n段落")
    assert "<h3>標題</h3>" in html
    assert "<ul><li><b>重點</b> 一</li><li>二</li></ul>" in html
    assert "<th>a</th>" in html and "<td>2</td>" in html
    assert "<p>段落</p>" in html


def test_valuation_decomposition_multiplies_back_to_pe():
    f = make_facts()
    f.merge({"ifrs-full:ProfitLossFromOperatingActivities": {"From20250101To20251231": 150 * M},
             "ifrs-full:ProfitLossBeforeTax": {"From20250101To20251231": 250 * M},
             "ifrs-full:IncomeTaxExpenseContinuingOperations": {"From20250101To20251231": 50 * M},
             "ifrs-full:Equity": {"AsOf20251231": 1200 * M},
             "ifrs-full:CashAndCashEquivalents": {"AsOf20251231": 500 * M},
             "ifrs-full:ShorttermBorrowings": {"AsOf20251231": 100 * M}}, "extra")
    r = xfr.analyse(f, "9999")
    v = xfr.valuation(r, 100.0)
    row = v["rows"][0]                          # 2025 年
    assert v["mcap"] == pytest.approx(6000 * M)
    assert v["ev"] == pytest.approx(6000 * M - (500 * M - 100 * M))     # 市值－淨現金
    assert row["ev_ebit"] == pytest.approx(v["ev"] / (150 * M))
    assert row["f_cash"] * row["ev_nopat"] * row["f_mix"] == pytest.approx(row["pe"])
    assert row["nonop_share"] == pytest.approx(100 / 250)


def test_ev_falls_back_to_debt_only_when_cash_missing():
    f = make_facts()
    for c in list(f.raw["ifrs-full:CashAndCashEquivalents"]):
        del f.raw["ifrs-full:CashAndCashEquivalents"][c]
    f.merge({"ifrs-full:Equity": {"AsOf20251231": 1200 * M},
             "ifrs-full:ShorttermBorrowings": {"AsOf20251231": 100 * M}}, "extra")
    v = xfr.valuation(xfr.analyse(f, "9999"), 100.0)
    assert v["ev_upper"] and v["ev"] == pytest.approx(6100 * M)
