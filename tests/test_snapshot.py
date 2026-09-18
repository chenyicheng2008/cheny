"""XBRL 精簡事實檔：抽取、讀回，以及缺 zip 時的替代來源。"""

import gzip
import zipfile
from datetime import date

import pytest

from twfactor.params import load_field_map
from twfactor.sources.opendata import OpenDataSource
from twfactor.sources.xbrl import (XbrlArchive, XbrlError, XbrlMissingError, archive_name,
                                   build_snapshot, load_snapshot)

REV, EQ, ASSETS = "ifrs-full:Revenue", "ifrs-full:Equity", "ifrs-full:Assets"
QUIET = {"log": lambda m: None}


def _fact(tag, ctx, value, scale=0, sign=False):
    return (f'<ix:nonFraction name="{tag}" contextRef="{ctx}" scale="{scale}"'
            f'{" sign=\"-\"" if sign else ""}>{value}</ix:nonFraction>')


def _zip(directory, name, files):
    path = directory / name
    with zipfile.ZipFile(path, "w") as z:
        for fn, body in files.items():
            z.writestr(fn, body)
    return path


class TestRoundTrip:
    @pytest.fixture
    def snap(self, tmp_path):
        archive = XbrlArchive(_zip(tmp_path, "tifrs-2025Q4.zip", {
            "tifrs-fr1-m1-ci-ir-1234-2025Q4.html": _fact(REV, "From20250101To20251231", 1),
            "tifrs-fr1-m1-ci-cr-1234-2025Q4.html":
                _fact(REV, "From20250101To20251231", "1,234", scale=3)
                + _fact(REV, "From20240101To20241231", 0.5)
                + _fact(EQ, "AsOf20251231", 5, sign=True)
                + _fact(ASSETS, "AsOf20251231", 9),
            "tifrs-fr1-m1-fh-cr-2891-2025Q4.html": _fact(EQ, "AsOf20251231", 7),
        }))
        out = tmp_path / "facts.csv.gz"
        rows = build_snapshot([archive], {REV, EQ}, out, **QUIET)
        return rows, load_snapshot(out)["2025Q4"]

    def test_values_scale_and_sign_survive(self, snap):
        rows, s = snap
        assert rows == 4
        assert s.facts("1234") == {REV: {"From20250101To20251231": 1_234_000, "From20240101To20241231": 0.5},
                                   EQ: {"AsOf20251231": -5}}

    def test_consolidated_choice_and_taxonomy_kept(self, snap):
        _, s = snap
        assert (s.year, s.quarter, len(s)) == (2025, 4, 2)
        assert s.taxonomy("2891") == "fh" and "9999" not in s

    def test_only_requested_tags(self, snap):
        _, s = snap
        assert ASSETS not in s.facts("1234")

    def test_wrong_header_rejected(self, tmp_path):
        bad = tmp_path / "bad.csv.gz"
        with gzip.open(bad, "wt", encoding="utf-8") as fh:
            fh.write("a,b\n1,2\n")
        with pytest.raises(XbrlError, match="欄位不符"):
            load_snapshot(bad)


class TestFallback:
    """as_of 2026-05-01：需要 2021Q4–2025Q4 五個年報檔。"""

    AS_OF = date(2026, 5, 1)

    def _source(self, cache, snapshot):
        return OpenDataSource(load_field_map(None), years=5, as_of=self.AS_OF, cache_dir=cache,
                              snapshot_path=snapshot, request_pause=0, **QUIET)

    def _snapshot(self, tmp_path, archives):
        src = tmp_path / "zips"
        src.mkdir()
        built = [XbrlArchive(_zip(src, archive_name(y, 4), files)) for y, files in archives.items()]
        out = tmp_path / "facts.csv.gz"
        build_snapshot(built, {EQ}, out, **QUIET)
        return out

    def test_missing_zips_come_from_snapshot(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        for y in (2021, 2022, 2023):
            _zip(cache, archive_name(y, 4), {})
        snap = self._snapshot(tmp_path, {
            2024: {"tifrs-fr1-m1-ci-cr-1234-2024Q4.html": _fact(EQ, "AsOf20241231", 100)},
            2025: {"tifrs-fr1-m1-ci-cr-1234-2025Q4.html": _fact(EQ, "AsOf20241231", 90)
                   + _fact(EQ, "AsOf20251231", 110)},
        })
        src = self._source(cache, snap)
        assert [a.kind for a in src.load_archives()] == ["zip", "zip", "zip", "snapshot", "snapshot"]
        pool, used, as_filed = src.company_facts("1234")
        assert pool[EQ] == {"AsOf20241231": 90, "AsOf20251231": 110}     # 較新申報的追溯數優先
        assert as_filed[EQ]["AsOf20241231"] == 100                        # 原始數仍保留
        assert used == ["2024Q4", "2025Q4"]

    def test_quarter_missing_everywhere_still_raises(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        snap = self._snapshot(tmp_path, {2025: {}})
        with pytest.raises(XbrlMissingError, match="精簡事實檔"):
            self._source(cache, snap).load_archives()

    def test_zips_win_over_snapshot(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        for y in range(2021, 2026):
            _zip(cache, archive_name(y, 4), {})
        src = self._source(cache, tmp_path / "does-not-exist.csv.gz")
        assert {a.kind for a in src.load_archives()} == {"zip"}
