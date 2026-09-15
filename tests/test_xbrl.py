"""MOPS XBRL 整批檔：inline XBRL 解析、檔案挑選、需要哪些季檔。"""

import zipfile
from datetime import date

import pytest

from twfactor.params import load_field_map
from twfactor.sources.opendata import OpenDataSource
from twfactor.sources.xbrl import XbrlArchive, XbrlError, XbrlMissingError, archive_name, parse_facts

# 取自 tifrs-2025Q4.zip 中 2330 申報的實際寫法（數值為 2330 FY2025）
SAMPLE = """<html><body>
<ix:nonFraction name="ifrs-full:Revenue" contextRef="From20250101To20251231" format="ixt:numdotdecimal" scale="3" decimals="-3" unitRef="TWD">3,809,054,272</ix:nonFraction>
<ix:nonFraction name="ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities" contextRef="From20250101To20251231" format="ixt:numdotdecimal" scale="3" sign="-" decimals="-3" unitRef="TWD"><span>1,272,410,529</span></ix:nonFraction>
<ix:nonFraction name="ifrs-full:BasicEarningsLossPerShare" contextRef="From20250101To20251231" format="ixt:numdotdecimal" scale="0" decimals="2" unitRef="TWD_per_share">66.26</ix:nonFraction>
<ix:nonFraction name="ifrs-full:Equity" contextRef="AsOf20251231_TreasurySharesMember" format="ixt:numdotdecimal" scale="3" unitRef="TWD">999</ix:nonFraction>
<ix:nonFraction name="ifrs-full:Equity" contextRef="AsOf20251231" format="ixt:numdotdecimal" scale="3" unitRef="TWD">5,460,795,283</ix:nonFraction>
<ix:nonFraction name="ifrs-full:Equity" contextRef="AsOf20251231" format="ixt:numdotdecimal" scale="3" unitRef="TWD">5,460,795,283</ix:nonFraction>
<ix:nonFraction name="ifrs-full:ProfitLoss" contextRef="From20250101To20251231" format="ixt:numdotdecimal" scale="3" unitRef="TWD"> </ix:nonFraction>
</body></html>"""


class TestParseFacts:
    facts = parse_facts(SAMPLE)

    def test_scale_and_thousands_separator(self):
        assert self.facts["ifrs-full:Revenue"]["From20250101To20251231"] == 3_809_054_272_000

    def test_sign_attribute_and_nested_markup(self):
        capex = "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
        assert self.facts[capex]["From20250101To20251231"] == -1_272_410_529_000

    def test_per_share_values_are_not_scaled(self):
        assert self.facts["ifrs-full:BasicEarningsLossPerShare"]["From20250101To20251231"] == 66.26

    def test_dimension_contexts_are_excluded(self):
        assert self.facts["ifrs-full:Equity"] == {"AsOf20251231": 5_460_795_283_000}

    def test_blank_value_is_skipped_not_zero(self):
        assert "ifrs-full:ProfitLoss" not in self.facts


def _fact(tag, ctx, value):
    return f'<ix:nonFraction name="{tag}" contextRef="{ctx}" scale="0">{value}</ix:nonFraction>'


def _zip(directory, name, files):
    path = directory / name
    with zipfile.ZipFile(path, "w") as z:
        for fn, body in files.items():
            z.writestr(fn, body)
    return path


class TestArchive:
    def test_prefers_consolidated_over_individual(self, tmp_path):
        ctx = "From20250101To20251231"
        path = _zip(tmp_path, "tifrs-2025Q4.zip", {
            "tifrs-fr1-m1-ci-ir-1234-2025Q4.html": _fact("ifrs-full:Revenue", ctx, 1),
            "tifrs-fr1-m1-ci-cr-1234-2025Q4.html": _fact("ifrs-full:Revenue", ctx, 2),
            "tifrs-fr1-m1-fh-cr-2891-2025Q4.html": _fact("ifrs-full:ProfitLoss", ctx, 3),
        })
        a = XbrlArchive(path)
        assert (a.year, a.quarter, a.label, len(a)) == (2025, 4, "2025Q4", 2)
        assert a.facts("1234")["ifrs-full:Revenue"][ctx] == 2
        assert a.taxonomy("2891") == "fh"

    def test_company_not_filed(self, tmp_path):
        a = XbrlArchive(_zip(tmp_path, "tifrs-2025Q4.zip", {}))
        assert "9999" not in a and a.facts("9999") is None

    def test_corrupt_zip_is_reported(self, tmp_path):
        path = tmp_path / "tifrs-2025Q4.zip"
        path.write_bytes(b"<html>blocked</html>")
        with pytest.raises(XbrlError):
            XbrlArchive(path)


def _source(as_of, **kw):
    return OpenDataSource(load_field_map(None), years=5, as_of=as_of, **kw)


class TestArchivePlan:
    """每個年度一個 Q4 年報檔（EPS 換算股本基準需要逐年的原始數），再加一個已過申報期限的期中檔。"""

    def test_september_uses_q2_for_ttm(self):
        assert _source(date(2026, 9, 15)).required_archives() == [
            (2021, 4), (2022, 4), (2023, 4), (2024, 4), (2025, 4), (2026, 2)]

    def test_on_deadline_day_quarter_not_yet_used(self):
        assert _source(date(2026, 5, 15)).required_archives() == [
            (2021, 4), (2022, 4), (2023, 4), (2024, 4), (2025, 4)]

    def test_before_april_prior_year_not_complete(self):
        # 3 月時 FY2025 年報未到申報期限 → 五年為 2020–2024，TTM 用 2025Q3
        assert _source(date(2026, 3, 1)).required_archives() == [
            (2020, 4), (2021, 4), (2022, 4), (2023, 4), (2024, 4), (2025, 3)]

    def test_missing_archive_requires_opt_in_download(self, tmp_path):
        with pytest.raises(XbrlMissingError, match="--download-xbrl"):
            _source(date(2026, 5, 1), cache_dir=tmp_path).load_archives()

    def test_newer_filing_overrides_comparative_and_keeps_as_filed(self, tmp_path):
        """合併事實以較新申報（可能經追溯調整）的比較數為準；原始申報數另外保留。"""
        eps = "ifrs-full:BasicEarningsLossPerShare"
        for y in (2021, 2022, 2023):
            _zip(tmp_path, archive_name(y, 4), {})
        _zip(tmp_path, archive_name(2024, 4),
             {"tifrs-fr1-m1-ci-cr-1234-2024Q4.html": _fact(eps, "From20240101To20241231", 4.2)})
        _zip(tmp_path, archive_name(2025, 4),
             {"tifrs-fr1-m1-ci-cr-1234-2025Q4.html": _fact(eps, "From20240101To20241231", 4.0)
              + _fact(eps, "From20250101To20251231", 5.0)})
        _zip(tmp_path, archive_name(2026, 2),
             {"tifrs-fr1-m1-ci-cr-1234-2026Q2.html": _fact(eps, "From20260101To20260630", 3.0)})
        pool, used, as_filed = _source(date(2026, 9, 15), cache_dir=tmp_path).company_facts("1234")
        assert pool[eps]["From20240101To20241231"] == 4.0          # 2025Q4 的追溯數
        assert as_filed[eps]["From20240101To20241231"] == 4.2      # 2024Q4 的原始數
        assert as_filed[eps]["From20260101To20260630"] == 3.0
        assert used == ["2024Q4", "2025Q4", "2026Q2"]
