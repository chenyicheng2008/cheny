"""PDF 報告：HTML 內容與瀏覽器偵測（不實際啟動瀏覽器）。"""

from fractions import Fraction

import pytest

from twfactor.cli import _parse_fraction
from twfactor.params import load_params
from twfactor.report import ReportError, find_browser, html_to_pdf, render_html
from twfactor.scoring import ScoringEngine
from twfactor.sources.fixtures import FixtureSource

P = load_params()
YEARS = [2021, 2022, 2023, 2024, 2025]


def _render(focus=10):
    src = FixtureSource(years=5)
    companies = src.top_by_market_cap(50)
    facts = src.fetch_facts(companies)
    cards = ScoringEngine(P).score_universe(facts)
    doc = render_html(cards, {f.stock_id: f for f in facts}, companies, P, years=YEARS,
                      ttm_label="2026Q2", price_date="2026-09-14", scope_label="市值前三分之一",
                      universe_size=150, focus=focus)
    return doc, cards


class TestHtml:
    def test_every_ranked_company_appears(self):
        doc, cards = _render()
        assert all(c.stock_id in doc for c in cards)

    def test_title_scope_and_basis(self):
        doc, _ = _render()
        assert "<title>臺股市值前三分之一財務因子評分</title>" in doc
        assert "TTM 至 2026Q2" in doc and "全市場 150 檔" in doc

    def test_definitions_follow_params(self):
        """得分定義的門檻取自設定檔，不是寫死在報告裡。"""
        doc, _ = _render()
        roe = P["factors"]["roe"]
        assert f"五年皆 ≥{roe['high_pct']:g}% → {roe['high_score']:g}" in doc
        assert f"滿分 {P['general_sector']['max_score']}" in doc

    def test_focus_limits_ranking_table(self):
        doc, cards = _render(focus=3)
        general = [c for c in cards if c.rank_pool == "general" and c.rank]
        assert "一般產業得分前 3 名" in doc
        assert f"共 {len(general)} 檔" in doc                  # 附錄仍列全體

    def test_no_python_values_leak(self):
        doc, _ = _render()
        assert "None" not in doc and "nan%" not in doc


class TestBrowser:
    def test_override_env(self, monkeypatch, tmp_path):
        exe = tmp_path / "browser.exe"
        exe.write_text("")
        monkeypatch.setenv("TWFACTOR_BROWSER", str(exe))
        assert find_browser() == str(exe)

    def test_override_to_missing_path_finds_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TWFACTOR_BROWSER", str(tmp_path / "nope.exe"))
        assert find_browser() is None

    def test_no_browser_raises_with_hint(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TWFACTOR_BROWSER", str(tmp_path / "nope.exe"))
        page = tmp_path / "r.html"
        page.write_text("<p>x</p>", encoding="utf-8")
        with pytest.raises(ReportError, match="TWFACTOR_BROWSER"):
            html_to_pdf(page, tmp_path / "r.pdf")


class TestFraction:
    @pytest.mark.parametrize("text,want", [("1/3", Fraction(1, 3)), ("0.25", Fraction(1, 4)), ("1", Fraction(1))])
    def test_valid(self, text, want):
        assert _parse_fraction(text) == want

    @pytest.mark.parametrize("text", ["0", "1.5", "abc", "1/0"])
    def test_invalid(self, text):
        with pytest.raises(SystemExit):
            _parse_fraction(text)
