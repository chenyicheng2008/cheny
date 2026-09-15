"""MOPS XBRL 整批案例文件（公開資訊觀測站 t203sb02）。

每季一個 zip（例如 tifrs-2025Q4.zip），內含當季所有公發公司申報的 inline XBRL
財報，一檔一個 html。檔名格式：

    tifrs-fr1-m1-ci-cr-2330-2025Q4.html
          │   │  │  │  └ 公司代號
          │   │  │  └ cr 合併報表／ir 個體報表／er 其他（證券商）
          │   │  └ 產業 taxonomy：ci 一般業、basi 銀行、fh 金控、ins 保險、bd 證券、mim 異業
          └───┴ 版本與申報類別

實測（2026-09-15，2025Q4 檔 2,722 份申報、2,694 家公司）：

- 數值一律為 ix:nonFraction，format 全為 ixt:numdotdecimal（千分位逗號），
  scale="3" 表示單位千元、EPS 為 scale="0"，負值以 sign="-" 標示而非寫在文字裡。
- 主報表的 context 為 AsOf{yyyymmdd}（期末存量）與 From{yyyymmdd}To{yyyymmdd}（期間）；
  帶底線後綴的是權益變動表等明細維度（例如 AsOf20251231_TreasurySharesMember），不取。
- 年報同時列示前一年度比較數，因此 2025Q4 一檔即有 FY2025 與 FY2024。
- 同一家公司可能同時有合併與個體報表（28 家），以合併報表為準。
- 期間數為年初至今累計：Q2 檔的 From20260101To20260630 是上半年，不是單季。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Callable

import requests

DOWNLOAD_URL = ("https://mopsov.twse.com.tw/server-java/FileDownLoad?step=9"
                "&functionName=show_file2&fileName={name}&filePath=/ifrs/{year}/")

FILE_RE = re.compile(r"tifrs-(?P<fr>fr\d)-(?P<m>m\d)-(?P<tax>[a-z]+)-(?P<kind>[a-z]+)"
                     r"-(?P<sid>[0-9A-Za-z]+)-(?P<period>\d{4}Q\d)\.html$")
FACT_RE = re.compile(r"<ix:nonFraction([^>]*)>(.*?)</ix:nonFraction>", re.S)
ATTR_RE = re.compile(r'([\w:]+)="([^"]*)"')
PLAIN_CONTEXT_RE = re.compile(r"AsOf\d{8}|From\d{8}To\d{8}")
MARKUP_RE = re.compile(r"<[^>]+>")

# 合併報表優先；同類別有多個版本時以 fr1 優先
REPORT_PREFERENCE = {"cr": 0, "ir": 1, "er": 2}

Facts = dict[str, dict[str, float]]          # {tag: {context: value}}


class XbrlError(RuntimeError):
    pass


class XbrlMissingError(XbrlError):
    """需要的整批檔尚未下載，且未允許自動下載。"""


def archive_name(year: int, quarter: int) -> str:
    return f"tifrs-{year}Q{quarter}.zip"


def parse_facts(html: str) -> Facts:
    """inline XBRL → {tag: {context: value}}，只取主報表 context，數值已乘上 scale、套上正負號。"""
    out: Facts = {}
    for attrs, body in FACT_RE.findall(html):
        a = dict(ATTR_RE.findall(attrs))
        ctx = a.get("contextRef", "")
        if not PLAIN_CONTEXT_RE.fullmatch(ctx) or "name" not in a:
            continue
        text = MARKUP_RE.sub("", body).strip().replace(",", "")
        try:
            value = float(text)
        except ValueError:
            continue                           # 空白或非數值不推估為 0
        value *= 10 ** int(a.get("scale") or 0)
        if a.get("sign") == "-":
            value = -value
        # 同一事實常在附註重複出現，數值相同，保留第一個即可
        out.setdefault(a["name"], {}).setdefault(ctx, value)
    return out


class XbrlArchive:
    """單一季度的整批 zip。只在需要時才解壓並解析個別公司的檔案。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        m = re.search(r"(\d{4})Q(\d)", self.path.name)
        if not m:
            raise XbrlError(f"無法從檔名判斷年度季別：{self.path.name}")
        self.year, self.quarter = int(m.group(1)), int(m.group(2))
        try:
            self._zip = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as exc:
            raise XbrlError(f"{self.path.name} 不是有效的 zip（下載可能不完整，請刪除後重下）") from exc
        self._index: dict[str, tuple[tuple[int, int], str, str]] = {}
        for name in self._zip.namelist():
            fm = FILE_RE.search(name)
            if not fm:
                continue
            rank = (REPORT_PREFERENCE.get(fm["kind"], 9), 0 if fm["fr"] == "fr1" else 1)
            cur = self._index.get(fm["sid"])
            if cur is None or rank < cur[0]:
                self._index[fm["sid"]] = (rank, name, fm["tax"])
        self._cache: dict[str, Facts] = {}

    @property
    def label(self) -> str:
        return f"{self.year}Q{self.quarter}"

    def __contains__(self, stock_id: str) -> bool:
        return stock_id in self._index

    def __len__(self) -> int:
        return len(self._index)

    def filename(self, stock_id: str) -> str | None:
        hit = self._index.get(stock_id)
        return hit[1] if hit else None

    def taxonomy(self, stock_id: str) -> str | None:
        hit = self._index.get(stock_id)
        return hit[2] if hit else None

    def facts(self, stock_id: str) -> Facts | None:
        if stock_id not in self._index:
            return None
        if stock_id not in self._cache:
            html = self._zip.read(self._index[stock_id][1]).decode("utf-8", "ignore")
            self._cache[stock_id] = parse_facts(html)
        return self._cache[stock_id]


def download_archive(year: int, quarter: int, dest_dir: str | Path,
                     session: requests.Session | None = None,
                     limit_bytes: int = 500 * 1024 * 1024,
                     log: Callable[[str], None] = print) -> Path:
    """下載單季整批檔。先寫 .part，完整下載且確認是 zip 才改名，中斷不會留下殘檔。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = archive_name(year, quarter)
    dest, part = dest_dir / name, dest_dir / (name + ".part")
    url = DOWNLOAD_URL.format(name=name, year=year)
    session = session or requests.Session()
    log(f"      下載 {name} …（伺服器不提供檔案大小，單檔約 100 MB 以上）")
    size = 0
    with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(part, "wb") as fh:
            for chunk in resp.iter_content(1 << 20):
                fh.write(chunk)
                size += len(chunk)
                if size > limit_bytes:
                    fh.close()
                    part.unlink(missing_ok=True)
                    raise XbrlError(f"{name} 超過 {limit_bytes // 1048576} MB 上限，已中止")
    with open(part, "rb") as fh:
        if fh.read(2) != b"PK":
            part.unlink(missing_ok=True)
            raise XbrlError(f"{name} 回應不是 zip（可能尚未公告或被擋），已捨棄")
    part.replace(dest)
    log(f"      {name} 完成，{size / 1048576:.1f} MB")
    return dest
