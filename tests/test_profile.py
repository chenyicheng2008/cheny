"""單一公司 XBRL 剖析：單季拆分、衍生比率、跨季檔覆蓋與 CLI。"""

import zipfile

import pytest

from twfactor.cli import main
from twfactor.profile import (annual_rows, build_quarters, derived, find_archives, merge_facts,
                              render_markdown, slim_archives)

REV = "ifrs-full:Revenue"
GP = "ifrs-full:GrossProfit"
RND = "ifrs-full:ResearchAndDevelopmentExpense"
OP = "ifrs-full:ProfitLossFromOperatingActivities"
PRETAX = "ifrs-full:ProfitLossBeforeTax"
NI = "ifrs-full:ProfitLoss"
OCF = "ifrs-full:CashFlowsFromUsedInOperatingActivities"
CAPEX = "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
CL = "ifrs-full:CurrentContractLiabilities"
EQ = "ifrs-full:Equity"


def _fact(tag, ctx, value, sign=False):
    s = ' sign="-"' if sign else ""
    return f'<ix:nonFraction name="{tag}" contextRef="{ctx}" scale="0"{s}>{value}</ix:nonFraction>'


def _archive(directory, label, sid, facts):
    path = directory / f"tifrs-{label}.zip"
    body = "<html>" + "".join(_fact(*f) for f in facts) + "</html>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"tifrs-fr1-m1-ci-cr-{sid}-{label}.html", body)
    return path


@pytest.fixture
def cache(tmp_path):
    q1, h1 = "From20260101To20260331", "From20260101To20260630"
    _archive(tmp_path, "2026Q1", "2360", [
        (REV, q1, 100), (GP, q1, 63), (OP, q1, 40), (PRETAX, q1, 45), (NI, q1, 38),
        (CL, "AsOf20260331", 30), (EQ, "AsOf20260331", 500),
    ])
    _archive(tmp_path, "2026Q2", "2360", [
        (REV, q1, 101),                        # 較新季檔的追溯數應覆蓋 Q1 檔原始數
        (REV, h1, 236), (GP, h1, 145), (RND, h1, 16), (OP, h1, 93), (PRETAX, h1, 100),
        (NI, h1, 90), (OCF, h1, 50), (CAPEX, h1, 10, True),
        (CL, "AsOf20260630", 42), (EQ, "AsOf20260630", 560),
    ])
    return tmp_path


def test_single_quarter_is_ytd_difference(cache):
    facts, used = merge_facts(find_archives(cache), "2360")
    assert used == ["2026Q1", "2026Q2"]
    q1, q2 = build_quarters(facts)
    assert q1.values["revenue"] == 101        # 以較新申報為準
    assert q2.values["revenue"] == 135
    assert q2.values["gross_profit"] == 82
    assert q2.values["contract_liabilities"] == 42


def test_missing_prior_ytd_is_na_not_full_ytd(cache):
    facts, _ = merge_facts(find_archives(cache), "2360")
    q2 = build_quarters(facts)[1]
    assert q2.ytd["rnd"] == 16
    assert q2.values["rnd"] is None           # Q1 沒有研發數，不可把半年數當單季


def test_derived_ratios():
    d = derived({"revenue": 200, "gross_profit": 120, "operating_income": 80, "pretax_income": 100,
                 "income_tax": 20, "net_income": 80, "operating_cash_flow": 60, "capex": -20,
                 "rnd": 14})
    assert d["gross_margin"] == pytest.approx(0.6)
    assert d["non_operating"] == 20
    assert d["non_operating_share"] == pytest.approx(0.2)
    assert d["free_cash_flow"] == 40
    assert d["fcf_conversion"] == pytest.approx(0.5)
    assert d["rnd_ratio"] == pytest.approx(0.07)


def test_derived_missing_inputs_are_na():
    d = derived({"revenue": 100})
    assert d["gross_margin"] is None and d["free_cash_flow"] is None


def test_annual_rows_only_full_calendar_years(cache):
    facts, _ = merge_facts(find_archives(cache), "2360")
    assert annual_rows(facts) == []


def test_render_and_unknown_stock(cache):
    facts, used = merge_facts(find_archives(cache), "2360")
    md = render_markdown("2360", facts, used)
    assert "| 營業收入 |" in md and "2026Q2" in md
    assert "N/A" in md
    empty, used = merge_facts(find_archives(cache), "0000")
    assert "找不到 0000" in render_markdown("0000", empty, used)


def test_cli_writes_markdown(cache, tmp_path):
    out = tmp_path / "out" / "2360.md"
    assert main(["profile", "--stock", "2360", "--from-year", "2026",
                 "--cache-dir", str(cache), "--out", str(out)]) == 0
    assert "## 單季" in out.read_text(encoding="utf-8")


def test_cli_without_archives_fails(tmp_path):
    assert main(["profile", "--stock", "2360", "--cache-dir", str(tmp_path),
                 "--facts", str(tmp_path / "missing.csv.gz")]) == 2


def test_slim_keeps_only_requested_companies(cache, tmp_path):
    _archive(cache, "2025Q4", "1111", [(REV, "From20250101To20251231", 5)])
    out = tmp_path / "slim"
    written = slim_archives(cache, ["2360"], out)
    assert [p.name for p, _ in written] == ["tifrs-2026Q1.zip", "tifrs-2026Q2.zip"]
    with zipfile.ZipFile(out / "tifrs-2026Q2.zip") as z:
        assert z.namelist() == ["tifrs-fr1-m1-ci-cr-2360-2026Q2.html"]
    full, _ = merge_facts(find_archives(cache), "2360")
    slim, _ = merge_facts(find_archives(out), "2360")
    assert slim == full                       # 精簡檔解析結果與整批檔一致


def test_cli_slim(cache, tmp_path):
    out = tmp_path / "slim"
    assert main(["xbrl-slim", "--stocks", "2360", "--cache-dir", str(cache), "--out", str(out)]) == 0
    assert main(["xbrl-slim", "--stocks", "0000", "--cache-dir", str(cache), "--out", str(out)]) == 2
    assert main(["xbrl-slim", "--cache-dir", str(cache)]) == 2


def _facts_csv(path, rows):
    import gzip
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write("archive,stock_id,taxonomy,tag,context,value\n")
        for r in rows:
            fh.write(",".join(map(str, r)) + "\n")
    return path


def test_load_facts_csv_newer_archive_wins(tmp_path):
    from twfactor.profile import load_facts_csv
    f = _facts_csv(tmp_path / "f.csv.gz", [
        ("2026Q2", "2360", "ci", REV, "From20260101To20260630", 236),
        ("2025Q4", "2360", "ci", REV, "From20250101To20251231", 280),
        ("2026Q2", "2360", "ci", REV, "From20250101To20251231", 283),
        ("2026Q2", "1111", "ci", REV, "From20260101To20260630", 1),
    ])
    facts, used = load_facts_csv(f, "2360")
    assert used == ["2025Q4", "2026Q2"]
    assert facts[REV] == {"From20260101To20260630": 236, "From20250101To20251231": 283}
    assert load_facts_csv(f, "2360", from_year=2026)[1] == ["2026Q2"]


def test_cli_falls_back_to_facts_file(tmp_path):
    f = _facts_csv(tmp_path / "f.csv.gz", [
        ("2025Q4", "2360", "ci", REV, "From20250101To20251231", 28_311_000_000),
        ("2025Q4", "2360", "ci", OP, "From20250101To20251231", 9_197_000_000),
    ])
    out = tmp_path / "p.md"
    assert main(["profile", "--stock", "2360", "--from-year", "2021", "--cache-dir", str(tmp_path / "none"),
                 "--facts", str(f), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "283.11" in text and "32.5%" in text


def test_direct_single_quarter_context_is_used():
    facts = {REV: {"From20260101To20260630": 236, "From20260401To20260630": 135}}
    (q2,) = build_quarters(facts)
    assert q2.label == "2026Q2" and q2.values["revenue"] == 135


def test_interim_rows_and_all_na_rows_hidden():
    from twfactor.profile import interim_rows
    facts = {REV: {"From20250101To20250630": 133, "From20260101To20260630": 254}}
    assert [label for label, _ in interim_rows(facts)] == ["2025H1", "2026H1"]
    md = render_markdown("2360", facts, ["2026Q2"])
    assert "| 營業毛利 |" not in md and "未申報或資料來源未收錄" in md
