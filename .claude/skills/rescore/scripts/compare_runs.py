"""比對兩次評分執行的結果，指出什麼變了、變多少。

分數會變的原因不只一種：財報更新、母體換人、門檻調整、程式改動。
把差異攤開來看，才分得出是哪一種 —— 只看總分排名會漏掉「總分沒變但
因子組成變了」這類情況。

用法：
    python compare_runs.py 舊的scores.csv 新的scores.csv
    python compare_runs.py 舊的.csv 新的.csv --top 30      # 只列前 30 大變動
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

FACTORS = ["EPS", "自由現金流", "現金股利", "股息支付率", "淨利率",
           "LT-Debt/Equity", "利息保障倍數", "ROE", "ROIC", "非獨立董監持股"]


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        sys.exit(f"找不到檔案：{path}")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit(f"{path} 沒有資料列")
    if "股票代碼" not in rows[0]:
        sys.exit(f"{path} 不像評分結果表（缺『股票代碼』欄）。現有欄位：{list(rows[0])[:6]}")
    return {r["股票代碼"]: r for r in rows}


def num(v):
    """'N/A'、空字串一律回 None，與 0 分嚴格區分（PRD §10）。"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "N/A", "n/a", "—", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt(v):
    return "N/A" if v is None else f"{v:g}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="比對兩次評分執行")
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--top", type=int, default=20, help="最多列出幾筆變動（預設 20）")
    args = ap.parse_args(argv)

    old, new = load(args.old), load(args.new)
    print(f"舊：{args.old.name}　{len(old)} 檔")
    print(f"新：{args.new.name}　{len(new)} 檔\n")

    gone = sorted(set(old) - set(new))
    added = sorted(set(new) - set(old))
    if gone:
        print(f"── 落出母體 {len(gone)} 檔 " + "─" * 40)
        for s in gone:
            print(f"   {s} {old[s].get('公司名稱','')}　舊總分 {old[s].get('總分','')}")
    if added:
        print(f"── 新進母體 {len(added)} 檔 " + "─" * 40)
        for s in added:
            print(f"   {s} {new[s].get('公司名稱','')}　新總分 {new[s].get('總分','')}")
    if gone or added:
        print()

    both = sorted(set(old) & set(new))
    changed = []
    for s in both:
        o, n = num(old[s].get("總分")), num(new[s].get("總分"))
        if o != n:
            changed.append((abs((n or 0) - (o or 0)), s, o, n))

    print(f"── 總分變動 {len(changed)}/{len(both)} 檔 " + "─" * 36)
    if not changed:
        print("   無變動 —— 結果可重現")
    else:
        changed.sort(reverse=True)
        for _, s, o, n in changed[:args.top]:
            row = new[s]
            delta = f"{(n or 0) - (o or 0):+g}" if None not in (o, n) else "—"
            print(f"   {s} {row.get('公司名稱',''):<10} {fmt(o):>5} → {fmt(n):<5} ({delta})")
            for f in FACTORS:
                col = f"{f} 分數"
                a, b = num(old[s].get(col)), num(new[s].get(col))
                if a != b:
                    print(f"        {f:<16} {fmt(a):>4} → {fmt(b)}")
        if len(changed) > args.top:
            print(f"   …另有 {len(changed) - args.top} 檔變動（--top 可調整）")
    print()

    print("── 各因子 N/A 家數 " + "─" * 42)
    print(f"   {'因子':<18}{'舊':>5}{'新':>5}{'增減':>7}")
    for f in FACTORS:
        col = f"{f} 分數"
        a = sum(1 for s in both if num(old[s].get(col)) is None)
        b = sum(1 for s in both if num(new[s].get(col)) is None)
        if a or b:
            mark = "" if a == b else ("  ← 覆蓋率變差" if b > a else "  ← 覆蓋率改善")
            print(f"   {f:<18}{a:>5}{b:>5}{b-a:>+7}{mark}")
    print("\n   N/A 家數變動通常反映資料來源狀況，不是公司體質改變。"
          "\n   突然變多時先看 snapshot 的 missing 原因，再判斷是否為來源故障。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
