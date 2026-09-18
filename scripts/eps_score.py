"""全體台股 EPS 因子評分（PRD v1.1 §8.1，與十項因子評分同一套規則與門檻）。

    PYTHONPATH=src python scripts/eps_score.py                     # 全市場普通股，基準日＝今天
    PYTHONPATH=src python scripts/eps_score.py --as-of 2026-09-15  # 決定哪五個完整年度、TTM 用哪一季
    PYTHONPATH=src python scripts/eps_score.py --stocks 2330,2327  # 只算幾檔

資料與十項因子評分相同：TWSE／TPEx OpenAPI 母體＋XBRL（.xbrlcache 的 zip，缺 zip 的季別讀
data/xbrl_facts.csv.gz）。EPS 用追溯調整至最新股本的五年序列，配股、減資、面額變更不會被誤判成衰退。
評分直接呼叫 scoring.factors.score_eps，門檻取自 config/scoring_params.yaml：

  任一年 EPS<0 → 整項 0
  穩定性：年減幅皆 ≤5% 得 1；有 5–12% 下降得 0.5；一次 >12% 大幅衰退且次年回到衰退前水準得 0.5，
          否則 0；兩次以上大幅衰退得 0
  成長：五年逐年成長再 +1

輸出 output/eps_score_<基準日>.csv，終端機印出分布、產業與領先者摘要。
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from twfactor.models import CompanyFacts  # noqa: E402
from twfactor.params import load_field_map, load_params  # noqa: E402
from twfactor.report import INDUSTRY  # noqa: E402
from twfactor.scoring.factors import score_eps  # noqa: E402
from twfactor.sources.opendata import QUARTER_END, OpenDataSource, restate_to_latest_basis  # noqa: E402


def eps_value(tags: list[str], facts: dict, ctx: str) -> float | None:
    return next((facts[t][ctx] for t in tags if ctx in facts.get(t, {})), None)


def analyse(src: OpenDataSource, companies: list[dict], params: dict) -> list[dict]:
    years = src.target_years
    n_years = len(years)
    tags = src.fm["fields"]["eps"]["tags"]
    interim = src.latest_interim()
    archives = src.load_archives()
    print("XBRL：" + "、".join(f"{a.label}{'（精簡事實檔）' if a.kind == 'snapshot' else ''}" for a in archives),
          file=sys.stderr)
    rows = []
    for c in companies:
        sid = c["stock_id"]
        base = {"stock_id": sid, "name": c.get("stock_name", "").rstrip("*"), "market": c.get("market", ""),
                "industry": INDUSTRY.get(c.get("industry_code", ""), c.get("industry_code", "")),
                "market_cap_億": round((c.get("market_cap") or 0) / 1e8, 1)}
        pool, used, filed = src.company_facts(sid)
        if not pool:
            rows.append({**base, "status": "無 XBRL 申報"})
            continue
        durs = [f"From{y}0101To{y}1231" for y in years]
        restated = [eps_value(tags, pool, d) for d in durs]
        missing = [y for y, v in zip(years, restated) if v is None]
        if missing:
            rows.append({**base, "status": f"未滿五年（缺 {'、'.join(map(str, missing))}）"})
            continue
        eps, ratios = restate_to_latest_basis(restated, [eps_value(tags, filed, d) for d in durs])
        if eps is None:
            rows.append({**base, "status": "無法換算股本基準"})
            continue

        fs = score_eps(CompanyFacts(stock_id=sid, eps_annual=eps, fiscal_years=years),
                       params["factors"]["eps"], n_years)
        grew = all(eps[i] > eps[i - 1] for i in range(1, n_years)) and min(eps) >= 0
        ttm = None
        if interim:
            yy, q = interim
            ytd = eps_value(tags, pool, f"From{yy}0101To{yy}{QUARTER_END[q]}")
            prev = eps_value(tags, pool, f"From{years[-1]}0101To{years[-1]}{QUARTER_END[q]}")
            if ytd is not None and prev is not None:
                ttm = ytd + eps[-1] - prev
        cagr = ((eps[-1] / eps[0]) ** (1 / (n_years - 1)) - 1) * 100 if eps[0] > 0 and eps[-1] > 0 else None
        rows.append({**base, "status": "ok",
                     **{f"eps_{y}": round(v, 4) for y, v in zip(years, eps)},
                     "eps_ttm": None if ttm is None else round(ttm, 4),
                     "cagr_5y_%": None if cagr is None else round(cagr, 1),
                     "eps_score": fs.score, "grew_every_year": grew, "rule": fs.rule,
                     "restate": "、".join(f"{years[k]}:{r:.4f}" for k, r in ratios.items()),
                     "source": "、".join(used)})
    return rows


def summarise(rows: list[dict], top: int, max_score: float) -> None:
    ok = [r for r in rows if r["status"] == "ok"]
    if not ok:
        print("沒有可評分的公司")
        return
    skipped = Counter(r["status"].split("（")[0] for r in rows if r["status"] != "ok")
    print(f"\n母體 {len(rows)} 檔｜可評分 {len(ok)} 檔" + "".join(f"｜{k} {v}" for k, v in skipped.items()))

    dist = Counter(r["eps_score"] for r in ok)
    print(f"\nEPS 分數分布（滿分 {max_score:g}）：")
    for s in sorted(dist, reverse=True):
        print(f"  {s:>4g} 分 {dist[s]:>6} 檔 {dist[s] / len(ok):>7.1%}  {'█' * round(dist[s] / len(ok) * 60)}")

    by_ind = defaultdict(list)
    for r in ok:
        by_ind[r["industry"] or "未分類"].append(r["eps_score"])
    print("\n產業（≥15 檔）平均分與拿滿分比例：")
    for ind, s in sorted(((k, v) for k, v in by_ind.items() if len(v) >= 15),
                         key=lambda kv: -sum(kv[1]) / len(kv[1])):
        print(f"  {ind:<8}{len(s):>5} 檔  平均 {sum(s) / len(s):.2f}  滿分 {sum(x == max_score for x in s) / len(s):>6.1%}")

    leaders = sorted((r for r in ok if r["eps_score"] == max_score), key=lambda r: -(r["cagr_5y_%"] or -1e9))
    print(f"\n滿分且五年 EPS 年複合成長最高的前 {top} 名：")
    for r in leaders[:top]:
        series = " ".join(f"{r[k]:>7.2f}" for k in sorted(k for k in r if k.startswith("eps_2")))
        ttm = "—" if r["eps_ttm"] is None else f"{r['eps_ttm']:.2f}"
        print(f"  {r['stock_id']:<6}{r['name'][:6]:<8}{(r['industry'] or '')[:5]:<7}"
              f"{r['market_cap_億']:>9,.0f} 億 {series}  TTM {ttm:>7}  CAGR {r['cagr_5y_%']:>6}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="基準日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--stocks", help="只算這些代號，逗號分隔（預設全市場普通股）")
    ap.add_argument("--cache-dir", default=str(ROOT / ".xbrlcache"))
    ap.add_argument("--xbrl-snapshot", default=str(ROOT / "data" / "xbrl_facts.csv.gz"))
    ap.add_argument("--download-xbrl", action="store_true", help="zip 與精簡事實檔都沒有的季別才下載")
    ap.add_argument("--top", type=int, default=30, help="摘要列出的領先者檔數")
    ap.add_argument("--out", help="輸出 CSV（預設 output/eps_score_<基準日>.csv）")
    args = ap.parse_args()

    params = load_params()
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    src = OpenDataSource(load_field_map(), years=params["periods"]["annual_years"], as_of=as_of,
                         cache_dir=args.cache_dir, allow_download=args.download_xbrl,
                         snapshot_path=args.xbrl_snapshot)
    companies = src.top_by_market_cap(10**9)
    print(f"全市場可排名普通股 {len(companies)} 檔（市值 {companies[0]['price_date']} 收盤）", file=sys.stderr)
    if args.stocks:
        want = {s.strip() for s in args.stocks.split(",")}
        companies = [c for c in companies if c["stock_id"] in want]

    rows = analyse(src, companies, params)
    out = Path(args.out or ROOT / "output" / f"eps_score_{as_of:%Y%m%d}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = list(dict.fromkeys(k for r in rows for k in r))
    rows.sort(key=lambda r: (r["status"] != "ok", -(r.get("eps_score") or 0), -(r.get("cagr_5y_%") or -1e9)))
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"已寫入 {out}（{len(rows)} 列）", file=sys.stderr)
    summarise(rows, args.top, params["factors"]["eps"]["max_score"])


if __name__ == "__main__":
    main()
