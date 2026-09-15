"""CLI（PRD §2 操作流程、FR-08 手動更新、FR-10 匯出）。

用法：
  python -m twfactor run --top 50 --token $FINMIND_TOKEN     # 正式：FinMind 全市場取市值前 50
  python -m twfactor run --top 50 --stocks-file config/universe.txt   # 指定候選母體
  python -m twfactor run --top 50 --source fixture           # 離線：合成資料驗證管線
  python -m twfactor probe-schema --stocks 2330,2891,8299    # PoC：探測 FinMind 實際欄位

FinMind 免費／匿名層級不允許「不帶 data_id 的全市場查詢」，因此不帶 --stocks/--stocks-file
時需要贊助（Sponsor）層級的 FINMIND_TOKEN，否則會以 FinMindLevelError 中止。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from .export import cards_to_dataframe, write_excel, write_snapshot
from .params import load_field_map, load_params
from .scoring import ScoringEngine
from .scoring.factors import FACTOR_LABELS


def _build_source(args, field_map, years):
    if args.source == "fixture":
        from .sources.fixtures import FixtureSource
        return FixtureSource(years=years)
    from .sources.finmind import FinMindSource
    token = args.token or os.environ.get("FINMIND_TOKEN", "")
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    return FinMindSource(field_map, token=token, years=years, as_of=as_of,
                         cache_dir=getattr(args, "cache_dir", None),
                         quota_wait=getattr(args, "quota_wait", 0.0),
                         max_quota_retries=getattr(args, "quota_retries", 0))


def _candidates(args) -> list[str] | None:
    """候選母體：--stocks 逗號清單，或 --stocks-file（# 之後為註解，其餘以空白分隔）。"""
    ids: list[str] = []
    if getattr(args, "stocks", None):
        ids += [s.strip() for s in args.stocks.split(",")]
    if getattr(args, "stocks_file", None):
        for line in Path(args.stocks_file).read_text(encoding="utf-8").splitlines():
            ids += line.split("#")[0].split()
    seen: dict[str, None] = {}
    for i in ids:
        if i:
            seen.setdefault(i, None)
    return list(seen) or None


def _attach_director_provider(args, source, companies) -> None:
    """建立董監持股 provider 並掛上資料來源（PRD §8.10）。

    持股比例需要發行股數，而發行股數在取得母體時才會連同市值一起取回，
    因此 provider 必須在 companies 之後才建得起來。
    """
    shares = {c["stock_id"]: c["shares_issued"] for c in companies if c.get("shares_issued")}
    provider = None
    if getattr(args, "director_holdings", None):
        from .sources.director_holding import CsvDirectorHoldingProvider
        provider = CsvDirectorHoldingProvider(args.director_holdings, shares_outstanding=shares)
    elif getattr(args, "director_openapi", False):
        from .sources.director_holding import OpenApiDirectorHoldingProvider
        provider = OpenApiDirectorHoldingProvider(
            shares_outstanding=shares,
            cache_dir=(Path(args.cache_dir) / "director" if args.cache_dir else None))
        for label, why in provider.failed_sources.items():
            print(f"      ⚠ 董監持股來源「{label}」取得失敗，該市場將標 N/A：{why}", file=sys.stderr)
    if provider is None:
        return
    print(f"      董監持股來源：{provider.name}", file=sys.stderr)
    if not provider.has_title_column:
        print("      ⚠ 來源無職稱欄，無法挑出非獨立董監，該因子將標 N/A（PRD §8.10）",
              file=sys.stderr)
    elif not provider.has_pledge_column:
        print("      ⚠ 來源無質押欄，質押比例將標 N/A（PRD §8.10）", file=sys.stderr)
    source.director_provider = provider


def cmd_run(args) -> int:
    params = load_params(args.params)
    field_map = load_field_map(args.fields)
    years = params["periods"]["annual_years"]

    source = _build_source(args, field_map, years)
    candidates = _candidates(args)
    scope = f"候選母體 {len(candidates)} 檔" if candidates else "全市場"
    print(f"[1/4] 取得母體：{source.name}，{scope} 取市值前 {args.top} 檔 …", file=sys.stderr)
    if args.source == "fixture":
        companies = source.top_by_market_cap(args.top)
    else:
        companies = source.top_by_market_cap(args.top, candidates=candidates)
    print(f"      取得 {len(companies)} 檔", file=sys.stderr)
    if candidates:
        print("      ⚠ 排名僅在指定候選母體內成立，非全市場市值排名（PRD §3）", file=sys.stderr)

    _attach_director_provider(args, source, companies)

    print("[2/4] 取得並標準化財務資料 …", file=sys.stderr)
    facts_list = source.fetch_facts(companies)
    facts_map = {f.stock_id: f for f in facts_list}

    print("[3/4] 評分與排名 …", file=sys.stderr)
    cards = ScoringEngine(params).score_universe(facts_list)

    print("[4/4] 輸出 …", file=sys.stderr)
    outdir = Path(args.outdir)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    df = cards_to_dataframe(cards, facts_map)
    csv_path = outdir / f"scores_{stamp}.csv"
    outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    xlsx_path = write_excel(cards, facts_map, outdir / f"scores_{stamp}.xlsx")
    snap_path = write_snapshot(cards, facts_map, outdir / f"snapshot_{stamp}.json")

    _print_summary(cards, params)
    print(f"\n輸出：\n  {csv_path}\n  {xlsx_path}\n  {snap_path}", file=sys.stderr)
    return 0


def _w(text: str) -> int:
    """東亞全形字寬度為 2，用於終端機表格對齊。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, align: str = "left") -> str:
    gap = max(width - _w(text), 0)
    return text + " " * gap if align == "left" else " " * gap + text


def _print_summary(cards, params) -> None:
    keys = list(FACTOR_LABELS.keys())
    short = {"eps": "EPS", "free_cash_flow": "FCF", "dividend": "股利",
             "payout_ratio": "配息率", "net_margin": "淨利率", "lt_debt_equity": "負債比",
             "interest_coverage": "利保", "roe": "ROE", "roic": "ROIC",
             "director_holding": "董監"}
    colw = {k: max(6, _w(short[k]) + 1) for k in keys}

    def table(rows, pool_max, title):
        if not rows:
            return
        print(f"\n== {title}（滿分 {pool_max}）==")
        head = (_pad("排名", 5, "right") + " " + _pad("代碼", 8) + _pad("名稱", 16)
                + "".join(_pad(short[k], colw[k], "right") for k in keys)
                + _pad("總分", 7, "right") + "  " + _pad("標籤", 14))
        print(head)
        print("-" * _w(head))
        for c in rows:
            cells = ""
            for k in keys:
                fs = c.factors.get(k)
                cells += _pad("—" if fs is None or fs.is_na else format(fs.score, "g"), colw[k], "right")
            flag = " ⚠高風險" if "高風險警示" in c.flags else ""
            print(_pad(str(c.rank), 5, "right") + " " + _pad(c.stock_id, 8) + _pad(c.stock_name, 16)
                  + cells + _pad(format(c.raw_total, "g"), 7, "right") + "  "
                  + _pad("、".join(c.tags), 14) + flag)

    general = sorted([c for c in cards if c.rank_pool == "general" and c.rank], key=lambda c: c.rank)
    financial = sorted([c for c in cards if c.rank_pool == "financial" and c.rank], key=lambda c: c.rank)
    na = [c for c in cards if c.raw_total is None]

    table(general, params["general_sector"]["max_score"], "一般產業排名")
    table(financial, params["financial_sector"]["max_score"], "金融業排名（不與一般產業混排）")

    thr = params["general_sector"]["screening_threshold"]
    hit = [c for c in general if c.raw_total >= thr]
    print(f"\n一般產業達 ≥{thr} 分門檻：{len(hit)}/{len(general)} 檔"
          + (f"　{'、'.join(c.stock_id for c in hit)}" if hit else ""))
    print(f"金融業：{len(financial)} 檔，依 9 分制獨立排名（不套用 {thr} 分門檻）")
    if na:
        print(f"未滿五年／總分 N/A：{len(na)} 檔　{'、'.join(c.stock_id for c in na)}")

    risk = [c for c in cards if "高風險警示" in c.flags]
    if risk:
        print(f"高風險警示（股東權益 ≤0）：{'、'.join(c.stock_id for c in risk)}")

    missing: dict[str, int] = {}
    for c in cards:
        if c.status != "ok":
            continue
        for k, fs in c.factors.items():
            if fs.is_na and fs.applicable:
                missing[k] = missing.get(k, 0) + 1
    if missing:
        print("\n因子資料缺漏統計（N/A，與 0 分嚴格區分）：")
        for k, n in sorted(missing.items(), key=lambda kv: -kv[1]):
            print(f"  {_pad(FACTOR_LABELS[k], 18)}{n} 檔")


def cmd_probe(args) -> int:
    """PRD §19.6：產出 FinMind 實際欄位清單，供回填 finmind_fields.yaml。"""
    from .sources.finmind import FinMindSource
    field_map = load_field_map(args.fields)
    params = load_params(args.params)
    src = FinMindSource(field_map, token=args.token or os.environ.get("FINMIND_TOKEN", ""),
                        years=params["periods"]["annual_years"], cache_dir=args.cache_dir)
    result = src.probe_schema([s.strip() for s in args.stocks.split(",") if s.strip()])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for ds, info in result.items():
        print(f"\n== {ds} ==")
        print(f"  期間語意：設定 {info['period_semantics_configured']}　"
              f"實測 {info['period_semantics_observed']}")
        for sid, types in info["types"].items():
            print(f"  {sid}: {len(types)} 個 type")
            for t in types[:40]:
                print(f"    - {t}")
    print(f"\n已寫入 {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="twfactor", description="臺股財務因子自動評分系統（PRD v1.1 第一階段）")
    ap.add_argument("--params", default=None, help="評分參數檔（預設 config/scoring_params.yaml）")
    ap.add_argument("--fields", default=None, help="FinMind 欄位對照檔")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="取得資料、評分、排名並匯出")
    r.add_argument("--top", type=int, default=50, help="取市值前 N 檔（預設 50）")
    r.add_argument("--source", choices=["finmind", "fixture"], default="finmind")
    r.add_argument("--token", default=None, help="FinMind API token（或用 FINMIND_TOKEN 環境變數）")
    r.add_argument("--as-of", default=None, help="資料基準日 YYYY-MM-DD，用於推定最新完整年度")
    r.add_argument("--stocks", default=None,
                   help="候選母體代碼，逗號分隔。免費層級無法做全市場掃描時使用")
    r.add_argument("--stocks-file", default=None, help="候選母體檔案，每行一個代碼")
    r.add_argument("--cache-dir", default=None, help="FinMind 回應快取目錄，供額度中斷後續跑")
    r.add_argument("--director-holdings", default=None,
                   help="MOPS 董監持股餘額明細匯出檔（CSV），供 PRD §8.10 評分")
    r.add_argument("--director-openapi", action="store_true",
                   help="直接取 TWSE／TPEx 公開 open data 的董監持股（免金鑰）")
    r.add_argument("--quota-wait", type=float, default=0.0,
                   help="遇 HTTP 402 時等待秒數後重試（需搭配 --cache-dir）")
    r.add_argument("--quota-retries", type=int, default=0, help="HTTP 402 最大重試次數")
    r.add_argument("--outdir", default="output")
    r.set_defaults(func=cmd_run)

    p = sub.add_parser("probe-schema", help="探測 FinMind 實際欄位（PoC）")
    p.add_argument("--stocks", default="2330,2891,8299")
    p.add_argument("--token", default=None)
    p.add_argument("--cache-dir", default=None, help="FinMind 回應快取目錄")
    p.add_argument("--out", default="output/poc_schema.json")
    p.set_defaults(func=cmd_probe)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
