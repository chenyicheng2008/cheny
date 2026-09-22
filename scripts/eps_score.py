"""EPS 穩定成長因子評分 —— 與資料來源無關。

吃一個本機 EPS 檔案（XBRL 萃取結果、MOPS 匯出、自建資料庫匯出皆可），
輸出新舊兩套評分的對照 CSV。不連網、不需 token。

支援兩種版面，程式自動判斷：

  長表（每列一個 公司×年度）        寬表（每列一家公司）
  ┌──────┬──────┬──────┐        ┌──────┬──────┬──────┬───┐
  │代碼  │年度  │EPS   │        │代碼  │2021  │2022  │…  │
  ├──────┼──────┼──────┤        ├──────┼──────┼──────┼───┤
  │2330  │2021  │23.02 │        │2330  │23.02 │39.20 │…  │
  │2330  │2022  │39.20 │        └──────┴──────┴──────┴───┘
  └──────┴──────┴──────┘

欄名以同義字比對（代碼/公司代號/股票代號/stock_id…），大小寫與空白不敏感。

用法：
    python scripts/eps_score.py --in eps.csv --out eps_scored.csv
    python scripts/eps_score.py --in eps.csv --years 2021 2025
    python scripts/eps_score.py --in eps.csv --g0 0.03 --g1 0.15
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ID_COLS = ("公司代號", "股票代號", "證券代號", "代碼", "股票代碼",
           "SecuritiesCompanyCode", "stock_id", "code", "ticker")
NAME_COLS = ("公司名稱", "公司簡稱", "名稱", "股票名稱", "CompanyName", "stock_name", "name")
YEAR_COLS = ("年度", "會計年度", "年份", "資料年度", "Year", "year", "fiscal_year")
EPS_COLS = ("基本每股盈餘", "每股盈餘", "基本每股盈餘（元）", "EPS", "eps", "基本每股盈餘(元)")
MARKET_COLS = ("市場別", "市場", "market")
INDUSTRY_COLS = ("產業別", "產業", "industry_category", "industry")


def _norm(s: str) -> str:
    return re.sub(r"[\s_（）()]", "", str(s)).strip().lower()


def _find(header, names):
    table = {_norm(h): h for h in header}
    for n in names:
        if _norm(n) in table:
            return table[_norm(n)]
    return None


def _year(v):
    """年度正規化。MOPS／XBRL 資料集常以民國年表示（Year=115），西元四位數則原樣。"""
    n = _num(v)
    if n is None:
        return None
    n = int(n)
    return n + 1911 if n < 1000 else n


def _num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "--", "N/A", "n/a", "nan", "None"):
        return None
    if s.startswith("(") and s.endswith(")"):        # 會計負數寫法 (1.23)
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def read_eps(path: Path):
    """回傳 {代碼: {年度: EPS}} 與 {代碼: {名稱, 市場別, 產業別}}。"""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit(f"{path} 沒有資料列")
    header = list(rows[0].keys())
    c_id = _find(header, ID_COLS)
    if not c_id:
        sys.exit(f"找不到代碼欄。現有欄位：{header}")
    c_name = _find(header, NAME_COLS)
    c_market, c_industry = _find(header, MARKET_COLS), _find(header, INDUSTRY_COLS)
    c_year, c_eps = _find(header, YEAR_COLS), _find(header, EPS_COLS)

    data: dict[str, dict[int, float]] = defaultdict(dict)
    meta: dict[str, dict] = {}
    used = {c_id, c_name, c_market, c_industry, c_year, c_eps}
    year_cols = [(h, int(m.group())) for h in header
                 if h not in used and (m := re.search(r"(19|20)\d{2}", str(h)))]

    for r in rows:
        sid = str(r[c_id]).strip()
        if not sid:
            continue
        meta.setdefault(sid, {
            "name": (r.get(c_name) or "").strip() if c_name else "",
            "market": (r.get(c_market) or "").strip() if c_market else "",
            "industry": (r.get(c_industry) or "").strip() if c_industry else "",
        })
        if c_year and c_eps:                                     # 長表
            y, v = _year(r[c_year]), _num(r[c_eps])
            if y and v is not None:
                data[sid][y] = v
        else:                                                    # 寬表
            for h, y in year_cols:
                v = _num(r[h])
                if v is not None:
                    data[sid][y] = v
    if not any(data.values()):
        sys.exit("解析不到任何 EPS 值。長表需有『年度』與『每股盈餘』欄，"
                 f"寬表需有年份欄名。現有欄位：{header}")
    return data, meta


# ---- 指標與評分 ---------------------------------------------------------
def clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def trend_metrics(E):
    """(趨勢年化成長率, R², 最大回撤)。任一年 EPS<=0 無法取對數，回 None。"""
    if any(v <= 0 for v in E):
        return None
    y = [math.log(v) for v in E]
    t = list(range(len(y)))
    mt, my = st.mean(t), st.mean(y)
    beta = sum((a - mt) * (c - my) for a, c in zip(t, y)) / sum((a - mt) ** 2 for a in t)
    alpha = my - beta * mt
    sst = sum((c - my) ** 2 for c in y)
    ssr = sum((c - (alpha + beta * x)) ** 2 for x, c in zip(t, y))
    peak, dd = E[0], 0.0
    for v in E:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    return math.exp(beta) - 1, (1.0 - ssr / sst if sst > 1e-12 else 1.0), dd


def score_new(E, p):
    m = trend_metrics(E)
    if m is None:
        return 0.0, {"note": "五年內有 EPS ≤0，不計分"}
    g, r2, dd = m
    G = clip((g - p.g0) / (p.g1 - p.g0))
    Q = clip((r2 - p.r0) / (p.r1 - p.r0))
    P = clip((p.d1 - dd) / (p.d1 - p.d0))
    note = (f"趨勢成長率未達下限 {p.g0:.0%}" if G == 0 else
            f"最大回撤 ≥{p.d1:.0%}" if P == 0 else
            f"R² 低於 {p.r0:.2f}" if Q == 0 else "")
    return 2 * G * Q * P, {"g": g, "r2": r2, "dd": dd, "G": G, "Q": Q, "P": P, "note": note}


def score_old(E, mild=5.0, severe=12.0):
    """PRD §8.1 現行規則，供對照。"""
    n = len(E)
    if any(v < 0 for v in E):
        return 0.0
    S, M = [], []
    for i in range(1, n):
        prev, cur = E[i - 1], E[i]
        if cur < prev and prev > 0:
            d = (prev - cur) / prev * 100
            (S if d > severe else M if d > mild else []).append(i)
    if len(S) >= 2:
        stab = 0.0
    elif len(S) == 1:
        k = S[0]
        stab = 0.0 if k == n - 1 else (0.5 if E[k + 1] >= E[k - 1] else 0.0)
    elif M:
        stab = 0.5
    else:
        stab = 1.0
    return stab + (1.0 if all(E[i] > E[i - 1] for i in range(1, n)) else 0.0)


FIELDS = ["股票代碼", "公司名稱", "市場別", "產業別", "舊規則分數", "新公式分數", "分數差異",
          "趨勢成長率g%", "R²", "最大回撤%", "成長分量G", "趨勢分量Q", "回撤分量P",
          "資料狀態", "備註"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EPS 穩定成長因子評分（讀本機 EPS 檔）")
    ap.add_argument("--in", dest="src", required=True, help="EPS 來源 CSV")
    ap.add_argument("--out", default="eps_scored.csv")
    ap.add_argument("--years", nargs=2, type=int, metavar=("起", "迄"),
                    help="評分年度區間，預設取資料中最新的連續五年")
    ap.add_argument("--g0", type=float, default=0.03, help="成長率下限（預設 3%%）")
    ap.add_argument("--g1", type=float, default=0.15, help="成長率滿格（預設 15%%）")
    ap.add_argument("--r0", type=float, default=0.30, help="R² 下限")
    ap.add_argument("--r1", type=float, default=0.80, help="R² 滿格")
    ap.add_argument("--d0", type=float, default=0.10, help="回撤不罰上限")
    ap.add_argument("--d1", type=float, default=0.40, help="回撤歸零點")
    args = ap.parse_args(argv)

    data, meta = read_eps(Path(args.src))
    all_years = sorted({y for d in data.values() for y in d})
    if args.years:
        years = list(range(args.years[0], args.years[1] + 1))
    else:
        end = max(all_years)
        years = list(range(end - 4, end + 1))
    print(f"來源 {args.src}：{len(data)} 家公司，資料年度 {all_years[0]}–{all_years[-1]}")
    print(f"評分年度：{years[0]}–{years[-1]}")
    print(f"門檻：g {args.g0:.0%}→{args.g1:.0%}　R² {args.r0}→{args.r1}　回撤 {args.d0:.0%}→{args.d1:.0%}")

    rows = []
    for sid, per_year in data.items():
        m = meta.get(sid, {})
        rec = dict.fromkeys(FIELDS, "")
        rec.update({"股票代碼": sid, "公司名稱": m.get("name", ""),
                    "市場別": m.get("market", ""), "產業別": m.get("industry", ""),
                    "新公式分數": 0.0})
        E = [per_year.get(y) for y in years]
        if any(v is None for v in E):
            miss = [y for y, v in zip(years, E) if v is None]
            rec.update({"資料狀態": "年度不全", "備註": f"缺 {'、'.join(map(str, miss))} 年"})
            rows.append(rec)
            continue
        old = score_old(E)
        new, d = score_new(E, args)
        rec.update({"舊規則分數": round(old, 4), "新公式分數": round(new, 4),
                    "分數差異": round(new - old, 4), "資料狀態": "完整",
                    "備註": d.get("note", "")})
        for y, v in zip(years, E):
            rec[f"EPS{y}"] = round(v, 4)
        if "g" in d:
            rec.update({"趨勢成長率g%": round(d["g"] * 100, 2), "R²": round(d["r2"], 4),
                        "最大回撤%": round(d["dd"] * 100, 2), "成長分量G": round(d["G"], 4),
                        "趨勢分量Q": round(d["Q"], 4), "回撤分量P": round(d["P"], 4)})
        rows.append(rec)

    fields = FIELDS[:4] + [f"EPS{y}" for y in years] + FIELDS[4:]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (-r["新公式分數"], r["股票代碼"])))

    ok = [r for r in rows if r["資料狀態"] == "完整"]
    print(f"\n輸出 {out}：{len(rows)} 檔，其中 {len(ok)} 檔五年完整")
    if ok:
        pos = [r for r in ok if r["新公式分數"] > 0]
        print(f"  新公式 >0 分：{len(pos)} 檔（{len(pos)/len(ok):.1%}）　"
              f"滿分：{sum(1 for r in ok if r['新公式分數'] >= 1.999)} 檔")
        print("  前 10 名：" + "、".join(f"{r['股票代碼']}{r['公司名稱']}({r['新公式分數']:.2f})"
                                       for r in sorted(ok, key=lambda r: -r["新公式分數"])[:10]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
