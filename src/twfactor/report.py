"""評分結果 PDF 報告（A4 橫式）。

以評分管線手上的 ScoreCard／CompanyFacts 直接產生 HTML，再交給本機的 Edge 或 Chrome
以 headless 模式列印成 PDF —— 不重新取數，報告與同一次執行的 CSV／snapshot 必然一致。

版面：摘要與總分分布 → 得分定義 → 得分前 N 名排名 → 前 N 名關鍵數字 → 前 N 名的組成
      → 金融業排名 → 附錄：全體排名 → 資料缺漏與資料來源。
配色依台股慣例以紅色表示強（滿分），不是警示。
"""

from __future__ import annotations

import html
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

from .models import CompanyFacts, ScoreCard

ORDER = ["eps", "free_cash_flow", "dividend", "payout_ratio", "net_margin",
         "lt_debt_equity", "interest_coverage", "roe", "roic", "director_holding"]
SHORT = {"eps": "EPS", "free_cash_flow": "自由現金流", "dividend": "現金股利", "payout_ratio": "支付率",
         "net_margin": "淨利率", "lt_debt_equity": "長債/權益", "interest_coverage": "利息保障",
         "roe": "ROE", "roic": "ROIC", "director_holding": "董監持股"}
# 證交所／櫃買中心共用的產業別代碼
INDUSTRY = {"01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織", "05": "電機機械", "06": "電器電纜",
            "08": "玻璃陶瓷", "09": "造紙", "10": "鋼鐵", "11": "橡膠", "12": "汽車", "14": "建材營造",
            "15": "航運", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨", "20": "其他", "21": "化學",
            "22": "生技醫療", "23": "油電燃氣", "24": "半導體", "25": "電腦週邊", "26": "光電",
            "27": "通信網路", "28": "電子零組件", "29": "電子通路", "30": "資訊服務", "31": "其他電子",
            "32": "文化創意", "33": "農業科技", "35": "綠能環保", "36": "數位雲端", "37": "運動休閒",
            "38": "居家生活", "91": "存託憑證"}
BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
BROWSER_NAMES = ["msedge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"]

e = html.escape


class ReportError(RuntimeError):
    pass


def find_browser() -> str | None:
    """TWFACTOR_BROWSER 優先，其次常見安裝路徑，最後找 PATH。"""
    override = os.environ.get("TWFACTOR_BROWSER")
    if override:
        return override if Path(override).exists() else None
    for p in BROWSER_CANDIDATES:
        if Path(p).exists():
            return p
    for n in BROWSER_NAMES:
        found = shutil.which(n)
        if found:
            return found
    return None


def html_to_pdf(html_path: str | Path, pdf_path: str | Path, browser: str | None = None,
                timeout: float = 300) -> Path:
    """以 headless Edge／Chrome 列印。版面由 HTML 的 @page 決定，不加瀏覽器頁首頁尾。"""
    browser = browser or find_browser()
    if not browser:
        raise ReportError("找不到 Edge 或 Chrome，無法列印 PDF。"
                          "可設定 TWFACTOR_BROWSER 指向瀏覽器執行檔，或直接用瀏覽器開啟 HTML 列印。")
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="twfactor-browser-") as profile:
        # 獨立的使用者資料夾，避免與正在使用的瀏覽器視窗衝突
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--user-data-dir={profile}", "--virtual-time-budget=10000",
                        f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                       check=True, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise ReportError(f"瀏覽器未產生 PDF：{pdf_path}")
    return pdf_path


# -- 格式 ---------------------------------------------------------------------
def _cap(v: float | None) -> str:
    if not v:
        return "—"
    return f"{v / 1e12:.2f} 兆" if v >= 1e12 else f"{v / 1e8:,.0f} 億"


def _fmt(v: float | None, d: int = 1) -> str:
    return "—" if v is None else f"{v:,.{d}f}"


def _spark(vals: list[float] | None, w: int = 78, h: int = 18) -> str:
    """五年序列的小折線；跨越 0 時畫出零軸。"""
    if not vals or len(vals) < 2:
        return ""
    lo = min(min(vals), 0) if min(vals) < 0 else min(vals)
    hi = max(vals)
    span = (hi - lo) or 1
    pts = [(3 + i * (w - 6) / (len(vals) - 1), h - 3 - (v - lo) / span * (h - 6)) for i, v in enumerate(vals)]
    zero = ""
    if lo < 0 < hi:
        zy = h - 3 - (0 - lo) / span * (h - 6)
        zero = f'<line x1="0" x2="{w}" y1="{zy:.1f}" y2="{zy:.1f}" class="zl"/>'
    x, y = pts[-1]
    path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}">{zero}'
            f'<polyline points="{path}" fill="none" class="sl"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" class="sd"/></svg>')


def _definitions(p: dict, years: list[int], ttm_label: str, price_date: str) -> tuple[list[tuple], list[tuple]]:
    """得分定義。門檻一律取自 scoring_params.yaml，設定改了報告跟著改。"""
    f = p["factors"]
    eps, dv, po, nm, ld, ic, roe, roic, dh = (f[k] for k in (
        "eps", "dividend", "payout_ratio", "net_margin", "lt_debt_equity", "interest_coverage",
        "roe", "roic", "director_holding"))
    rows = [
        ("eps", "獲利穩定與成長", f"FY{years[0]}–FY{years[-1]} 基本 EPS，追溯調整至最新股本基準",
         f"任一年 EPS<0 → 整項 0。穩定性：年減幅皆 ≤{eps['mild_decline_pct']:g}% 得 {eps['stability_full']:g}；"
         f"有 {eps['mild_decline_pct']:g}–{eps['severe_decline_pct']:g}% 下降得 {eps['stability_partial']:g}；"
         f"一次 >{eps['severe_decline_pct']:g}% 大幅衰退且次年回到衰退前水準得 {eps['stability_partial']:g}，否則 0；"
         f"兩次以上大幅衰退得 0。成長：五年逐年成長再 +{eps['growth_bonus']:g}"),
        ("free_cash_flow", "本業創造現金", "營業活動現金流 − 取得不動產、廠房及設備",
         f"五年皆 >{f['free_cash_flow']['min_value']:g} → {f['free_cash_flow']['max_score']:g}"),
        ("dividend", "配息紀律", "每股現金股利，依除息交易日所屬年度歸戶",
         f"五年皆有配發 +{dv['paid_all_years_score']:g}；四次年度比較年減幅皆 ≤{dv['max_yoy_decline_pct']:g}% "
         f"+{dv['stability_score']:g}（前一年未配息者不算減少）"),
        ("payout_ratio", "配息餘裕", f"FY{years[-1]} 現金股利 ÷ TTM EPS",
         f"0% < 支付率 < {po['tier1_upper_pct']:g}% → {po['tier1_score']:g}；"
         f"{po['tier1_upper_pct']:g}–{po['tier2_upper_pct']:g}% → {po['tier2_score']:g}；"
         f"0%、≥{po['tier2_upper_pct']:g}% 或 TTM EPS<0 → 0"),
        ("net_margin", "定價能力", "本期淨利 ÷ 營業收入",
         f"五年皆 >{nm['high_pct']:g}% → {nm['high_score']:g}；皆 >{nm['base_pct']:g}% → {nm['base_score']:g}；"
         f"五年逐年成長 → {nm['growth_score']:g}（取最高一項，不累加）"),
        ("lt_debt_equity", "財務槓桿", f"（長期借款＋應付公司債＋租賃負債−非流動）÷ 權益總額，FY{years[-1]} 年底",
         f"< {ld['threshold']:g} → {ld['pass_score']:g}；權益總額 ≤0 → 0 並標示高風險警示"),
        ("interest_coverage", "償債能力", "TTM 營業利益 ÷ TTM 利息費用",
         f"> {ic['high_threshold']:g} 倍 → {ic['high_score']:g}；> {ic['mid_threshold']:g} 倍 → {ic['mid_score']:g}；"
         f"無利息負擔且營業利益 >0 → {ic['no_debt_positive_op_score']:g}"),
        ("roe", "股東報酬", "本期淨利 ÷ 期末權益總額",
         f"五年皆 ≥{roe['high_pct']:g}% → {roe['high_score']:g}；皆 ≥{roe['base_pct']:g}% → {roe['base_score']:g}"),
        ("roic", "資本效率", "營業利益 ×（1 − 有效稅率）÷（權益總額＋短期借款＋長期借款＋應付公司債）",
         f"五年皆 ≥{roic['threshold_pct']:g}% → {roic['pass_score']:g}；有稅前虧損年度無法算有效稅率 → N/A"),
        ("director_holding", "經營者利益一致", "非獨立董監持股 ÷ 已發行股數；設質股數 ÷ 董監持股",
         f"持股 >{dh['min_holding_pct']:g}% 且質押 ≤{dh['max_pledge_pct']:g}% → {dh['pass_score']:g}"),
    ]
    gs, fs = p["general_sector"], p["financial_sector"]
    notes = [
        ("總分與排名",
         f"一般產業十項合計滿分 {gs['max_score']}。金融保險業（產業代碼 17）只評 "
         + "、".join(SHORT[k] for k in fs["applicable_factors"])
         + f" 五項，滿分 {fs['max_score']}，另外排名。同分同名次，下一名跳號（1、2、2、4）。"
         f"一般產業總分 ≥{gs['screening_threshold']} 為選股參考門檻。"),
        ("標籤（不加分）",
         f"「高 ROE」＝五年 ROE 皆 ≥{roe['high_pct']:g}%；「高 ROIC」＝五年 ROIC 皆 ≥{roic['threshold_pct']:g}%；"
         "兩者皆符合標「高 ROE 及 ROIC」。"),
        ("N/A 與缺漏",
         "資料不足以判斷時標 N/A：該項不計分、不計入可評滿分，也不按比例放大其他分數。"
         "不滿五個完整年度 EPS 的公司整家不評分、不排名。"),
        ("期間與基準",
         f"五年＝FY{years[0]}–FY{years[-1]}（年報申報期限 3/31 後才納入）。TTM＝{ttm_label} 年初至今累計"
         f"＋FY{years[-1]} 全年−去年同期累計。" + (f"市值＝{price_date} 收盤價 × 已發行普通股數。" if price_date else "")),
    ]
    return rows, notes


def render_html(cards: list[ScoreCard], facts: dict[str, CompanyFacts], companies: list[dict],
                params: dict, *, years: list[int], ttm_label: str, price_date: str = "",
                archives: list[str] | None = None, scope_label: str | None = None,
                universe_size: int | None = None, focus: int = 50, generated: date | None = None) -> str:
    generated = generated or date.today()
    info = {c["stock_id"]: c for c in companies}
    maxs = {k: params["factors"][k]["max_score"] for k in ORDER}
    fin_keys = params["financial_sector"]["applicable_factors"]
    thr = params["general_sector"]["screening_threshold"]
    gen_max = params["general_sector"]["max_score"]
    fin_max = params["financial_sector"]["max_score"]
    scope_label = scope_label or f"市值前 {len(companies)} 檔"
    title = f"臺股{scope_label}財務因子評分"

    def mcap(sid: str) -> float:
        return info.get(sid, {}).get("market_cap") or 0.0

    def name(sid: str) -> str:
        return e(str(info.get(sid, {}).get("stock_name") or facts[sid].stock_name).rstrip("*"))

    def industry(sid: str) -> str:
        code = str(info.get(sid, {}).get("industry_code") or facts[sid].industry_code or "")
        return INDUSTRY.get(code, code) or "未分類"

    by_cap = sorted(companies, key=lambda c: -(c.get("market_cap") or 0))
    cap_rank = {c["stock_id"]: i + 1 for i, c in enumerate(by_cap)}
    general = sorted([c for c in cards if c.rank_pool == "general" and c.rank],
                     key=lambda c: (c.rank, -mcap(c.stock_id)))
    financial = sorted([c for c in cards if c.rank_pool == "financial" and c.rank],
                       key=lambda c: (c.rank, -mcap(c.stock_id)))
    unrated = sorted([c for c in cards if c.raw_total is None], key=lambda c: -mcap(c.stock_id))
    passers = [c for c in general if c.raw_total >= thr]
    top = [c for c in general if c.rank <= focus]
    top_ids = {c.stock_id for c in top}
    top_cut = min((c.raw_total for c in top), default=None)
    n_gen = len(general)
    basis = " ・ ".join(x for x in [f"市值 {price_date} 收盤" if price_date else "",
                                    f"財報 FY{years[0]}–FY{years[-1]}", f"TTM 至 {ttm_label}"] if x)

    def cell(card: ScoreCard, k: str) -> str:
        fs = card.factors.get(k)
        if fs is None or fs.score is None or not fs.applicable:
            return '<td class="sc na">—</td>'
        cls = "full" if fs.score >= maxs[k] else "part" if fs.score > 0 else "zero"
        return f'<td class="sc {cls}">{fs.score:g}</td>'

    def rates(pool: list[ScoreCard]) -> dict[str, tuple[int, int, int, int]]:
        out = {}
        for k in ORDER:
            s = [c.factors[k].score for c in pool if k in c.factors]
            out[k] = (sum(1 for v in s if v is not None and v >= maxs[k]),
                      sum(1 for v in s if v is not None and 0 < v < maxs[k]),
                      sum(1 for v in s if v == 0), sum(1 for v in s if v is None))
        return out

    all_rates, top_rates = rates(general), rates(top)

    # -- 摘要：總分分布直方圖 ------------------------------------------------
    bins = [i * 0.5 for i in range(int(gen_max / 0.5) + 1)]
    counts = {b: sum(1 for c in general if c.raw_total == b) for b in bins}
    cmax = max(counts.values(), default=0) or 1
    hist = ""
    for b in bins:
        n = counts[b]
        cls = "top" if top_cut is not None and b >= top_cut else "pass" if b >= thr else ""
        hist += (f'<div class="hb"><div class="hwrap"><span class="hc">{n or ""}</span>'
                 f'<span class="hbar {cls}" style="height:{n / cmax * 100:.1f}%"></span></div>'
                 f'<span class="hx{" int" if b == int(b) else ""}">{f"{b:g}" if b == int(b) else ""}</span></div>')

    # 市值分層：母體依市值切成三段，看前 N 名從哪一段來
    third = max(len(by_cap) // 3, 1)
    tiers = []
    for i, label in enumerate(("上段", "中段", "下段")):
        ids = [c["stock_id"] for c in by_cap[i * third:(i + 1) * third if i < 2 else None]]
        cs = [c for c in general if c.stock_id in ids]
        tiers.append((f"{label}（市值第 {i * third + 1}–{i * third + len(ids)} 名）", len(ids),
                      sum(1 for c in cs if c.stock_id in top_ids), sum(1 for c in cs if c.raw_total >= thr),
                      sum(c.raw_total for c in cs) / len(cs) if cs else None,
                      _cap(mcap(ids[-1])) if ids else "—"))

    ind_top = Counter(industry(c.stock_id) for c in top)
    ind_all = Counter(industry(c.stock_id) for c in general)
    hardest = sorted(ORDER, key=lambda k: all_rates[k][0])[:3]

    obs = []
    if universe_size:
        obs.append(f"全市場可排名普通股 {universe_size:,} 檔，取{scope_label}共 <b>{len(companies)} 檔</b>，"
                   f"市值門檻 {_cap(mcap(by_cap[-1]['stock_id']))}。")
    if general:
        obs.append(f"一般產業 {n_gen} 檔中 <b>{len(passers)} 檔（{len(passers) / n_gen:.0%}）</b>達 {thr} 分選股門檻；"
                   f"最高分 {general[0].raw_total:g} 分：" + "、".join(name(c.stock_id) for c in general if c.rank == 1) + "。")
    if top:
        obs.append(f"得分前 {focus} 名（含同分共 {len(top)} 檔）總分皆 ≥ <b>{top_cut:g} 分</b>；"
                   + "、".join(f"{k}（{v} 檔）" for k, v in ind_top.most_common(3)) + " 最多。")
        obs.append(f"前 {focus} 名來自市值" + "、".join(f"{t[0][:2]} {t[2]} 檔" for t in tiers)
                   + "；市值大不等於體質分數高。")
        obs.append("全體最常失分的因子：" + "、".join(f"{SHORT[k]}（滿分 {all_rates[k][0]}/{n_gen}）" for k in hardest) + "。")
    if financial:
        obs.append(f"金融業 {len(financial)} 檔以 {fin_max} 分制另外排名，最高分 {financial[0].raw_total:g} 分："
                   + "、".join(name(c.stock_id) for c in financial if c.rank == 1) + "。")

    # -- 得分定義 --------------------------------------------------------
    def_rows, def_notes = _definitions(params, years, ttm_label, price_date)
    defs_html = "".join(
        f'<tr><td class="dn">{SHORT[k]}</td><td class="dm">{maxs[k]}</td><td class="dw">{e(w)}</td>'
        f'<td>{e(how)}</td><td>{e(rule)}</td></tr>' for k, w, how, rule in def_rows)
    notes_html = "".join(f'<div class="dnote"><h3>{e(t)}</h3><p>{e(body)}</p></div>' for t, body in def_notes)

    # -- 前 N 名排名表 ----------------------------------------------------
    def rank_rows(pool: list[ScoreCard]) -> str:
        rows, cut_done = [], False
        for c in pool:
            if not cut_done and c.raw_total < thr and any(x.raw_total >= thr for x in pool):
                rows.append(f'<tr class="cut"><td colspan="{7 + len(ORDER)}">以上達 {thr} 分選股門檻</td></tr>')
                cut_done = True
            i = info.get(c.stock_id, {})
            rows.append(
                f'<tr><td class="r">{c.rank}</td><td class="code">{c.stock_id}</td><td class="nm">{name(c.stock_id)}'
                f'{"<i>櫃</i>" if i.get("market") == "tpex" else ""}</td><td class="ind">{e(industry(c.stock_id))}</td>'
                f'<td class="num">{_cap(mcap(c.stock_id))}<small> #{cap_rank.get(c.stock_id, "")}</small></td>'
                + "".join(cell(c, k) for k in ORDER)
                + f'<td class="tot"><span class="bar" style="--w:{c.raw_total / gen_max * 100:.1f}"></span>'
                f'<b>{c.raw_total:g}</b></td><td class="tag">{e("、".join(c.tags))}</td></tr>')
        return "".join(rows)

    gen_head = ('<thead><tr><th class="r">排名</th><th>代碼</th><th>公司</th><th>產業</th><th class="num">市值 <small>#市值排名</small></th>'
                + "".join(f'<th class="f">{SHORT[k]}<small>/{maxs[k]}</small></th>' for k in ORDER)
                + f'<th class="tot">總分<small>/{gen_max}</small></th><th>標籤</th></tr></thead>')

    # -- 前 N 名關鍵數字 ----------------------------------------------------
    def roe5(sid: str) -> str:
        roe = facts[sid].roe_annual
        return f"{_fmt(sum(roe) / len(roe))}%" if roe else "—"

    det = []
    for c in top:
        f = facts[c.stock_id]
        ic = ("無利息負擔" if f.interest_expense == 0
              else f"{f.interest_coverage:,.0f} 倍" if f.interest_coverage else "—")
        ltde = f.long_term_debt / f.total_equity if f.long_term_debt is not None and f.total_equity else None
        eps = f.eps_annual or []
        det.append(
            f'<tr><td class="r">{c.rank}</td><td class="nm2"><span class="code">{c.stock_id}</span> {name(c.stock_id)}</td>'
            f'<td class="sp">{_spark(eps)}</td>'
            f'<td class="num">{f"{eps[0]:.2f} → {eps[-1]:.2f}" if eps else "—"}</td>'
            f'<td class="num">{_fmt(f.ttm_eps, 2)}</td><td class="num">{roe5(c.stock_id)}</td>'
            f'<td class="num">{_fmt(f.net_margin_annual[-1] if f.net_margin_annual else None)}%</td>'
            f'<td class="num">{_fmt(f.ttm_payout_ratio)}%</td><td class="num">{ic}</td>'
            f'<td class="num">{_fmt(ltde, 2)}</td>'
            f'<td class="num">{_fmt(f.director_holding_pct)}%<small> / 質押 {_fmt(f.director_pledge_pct)}%</small></td>'
            f'<td class="tot"><b>{c.raw_total:g}</b></td></tr>')

    # -- 前 N 名的組成 ------------------------------------------------------
    imax = max(ind_top.values(), default=1)
    ind_html = "".join(
        f'<div class="irow"><span class="il">{e(k)}</span><span class="itrack">'
        f'<span class="ibar" style="width:{v / imax * 100:.1f}%"></span></span>'
        f'<span class="iv">{v}<small> / {ind_all[k]} 檔</small></span></div>'
        for k, v in ind_top.most_common(12))
    tier_html = "".join(
        f'<tr><td>{e(t)}</td><td class="num">{n}</td><td class="num"><b>{tp}</b></td><td class="num">{ps}</td>'
        f'<td class="num">{_fmt(avg, 2)}</td><td class="num">{low}</td></tr>' for t, n, tp, ps, avg, low in tiers)
    cmp_html = "".join(
        f'<div class="crow"><span class="cl">{SHORT[k]}<small>/{maxs[k]}</small></span><span class="cbars">'
        f'<span class="cb top" style="width:{top_rates[k][0] / max(len(top), 1) * 100:.1f}%"></span>'
        f'<span class="cb all" style="width:{all_rates[k][0] / max(n_gen, 1) * 100:.1f}%"></span></span>'
        f'<span class="cv">{top_rates[k][0] / max(len(top), 1):.0%}<small> / {all_rates[k][0] / max(n_gen, 1):.0%}</small></span></div>'
        for k in ORDER)

    # -- 金融業 ---------------------------------------------------------------
    fin_rows = "".join(
        f'<tr><td class="r">{c.rank}</td><td class="code">{c.stock_id}</td><td class="nm">{name(c.stock_id)}</td>'
        f'<td class="num">{_cap(mcap(c.stock_id))}<small> #{cap_rank.get(c.stock_id, "")}</small></td>'
        + "".join(cell(c, k) for k in fin_keys)
        + f'<td class="tot"><span class="bar" style="--w:{c.raw_total / fin_max * 100:.1f}"></span>'
        f'<b>{c.raw_total:g}</b></td><td class="num">{roe5(c.stock_id)}</td></tr>'
        for c in financial)
    fin_head = ('<thead><tr><th class="r">排名</th><th>代碼</th><th>公司</th><th class="num">市值 <small>#市值排名</small></th>'
                + "".join(f'<th class="f">{SHORT[k]}<small>/{maxs[k]}</small></th>' for k in fin_keys)
                + f'<th class="tot">總分<small>/{fin_max}</small></th><th class="num">五年均 ROE</th></tr></thead>')

    # -- 附錄：全體排名 --------------------------------------------------------
    appendix = "".join(
        f'<div class="ar{" t" if c.stock_id in top_ids else " p" if c.raw_total >= thr else ""}">'
        f'<span class="r">{c.rank}</span><span class="code">{c.stock_id}</span>'
        f'<span class="an">{name(c.stock_id)}</span><span class="as">{c.raw_total:g}</span></div>'
        for c in general)
    unrated_html = "、".join(f"{c.stock_id} {name(c.stock_id)}" for c in unrated)

    # -- 資料缺漏 ---------------------------------------------------------------
    miss_rows = []
    for k in ORDER:
        reasons = Counter()
        for c in general:
            fs = c.factors.get(k)
            if fs is not None and fs.score is None and fs.applicable:
                reasons[(facts[c.stock_id].missing_reasons.get(k) or fs.rule).replace("N/A：", "")[:60]] += 1
        if reasons:
            miss_rows.append(f'<tr><td class="dn">{SHORT[k]}</td><td class="num">{sum(reasons.values())}</td>'
                             f'<td>{"；".join(f"{e(r)}（{n}）" for r, n in reasons.most_common(2))}</td></tr>')
    unrated_reasons = Counter((facts[c.stock_id].missing_reasons.get("financials")
                               or facts[c.stock_id].missing_reasons.get("eps") or "未滿五年")[:60] for c in unrated)
    if unrated:
        miss_rows.append(f'<tr><td class="dn">整家不評分</td><td class="num">{len(unrated)}</td>'
                         f'<td>{"；".join(f"{e(r)}（{n}）" for r, n in unrated_reasons.most_common(3))}</td></tr>')
    archive_note = f"本次使用 {'、'.join(archives)} 共 {len(archives)} 檔，" if archives else ""

    css = CSS.replace("{{TITLE}}", title.replace('"', ""))
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>{e(title)}</title>
<style>{css}</style></head><body>

<section class="page">
  <div class="mast">
    <div>
      <div class="eyebrow">TAIWAN LISTED ・ PRD v1.1 十項財務因子 ・ 產出 {generated.isoformat()}</div>
      <h1><span class="seal">評</span>{e(title)}</h1>
      <div class="sub" style="margin-top:2mm">{basis}</div>
    </div>
    <div class="figs">
      <div class="fig"><div class="n">{len(companies)}</div><div class="l">{e(scope_label)}<br>{f"全市場 {universe_size:,} 檔" if universe_size else ""}</div></div>
      <div class="fig"><div class="n red">{len(passers)}<small style="font-size:11pt">/{n_gen}</small></div><div class="l">達 {thr} 分門檻<br>一般產業・滿分 {gen_max}</div></div>
      <div class="fig"><div class="n">{f"{top_cut:g}" if top_cut is not None else "—"}<small style="font-size:11pt"> 分</small></div><div class="l">得分前 {focus} 名門檻<br>含同分共 {len(top)} 檔</div></div>
      <div class="fig"><div class="n">{len(financial)}</div><div class="l">金融業<br>{fin_max} 分制另外排名</div></div>
      <div class="fig"><div class="n">{len(unrated)}</div><div class="l">不評分<br>未滿五年或無 XBRL</div></div>
    </div>
  </div>
  <div class="dist">
    <div class="dhead"><h2>一般產業 {n_gen} 檔的總分分布</h2>
      <div class="legend"><span><i class="full"></i>得分前 {focus} 名</span><span><i class="mid"></i>達 {thr} 分門檻</span><span><i class="zero"></i>其他</span><span>柱上數字＝檔數</span></div></div>
    <div class="hist" style="grid-template-columns: repeat({len(bins)}, 1fr)">{hist}</div>
    <div class="axis"><span>0 分</span><span>總分（滿分 {gen_max}，級距 0.5）</span><span>{gen_max} 分</span></div>
  </div>
  <ul class="obs">{"".join(f"<li>{o}</li>" for o in obs)}</ul>
</section>

<section class="page">
  <div class="phead"><h2>得分定義</h2><span class="sub">門檻取自 config/scoring_params.yaml（PRD v1.1 §8、§10、§11）</span></div>
  <table class="defs"><thead><tr><th>因子</th><th class="dm">滿分</th><th>衡量什麼</th><th>計算方式</th><th>給分規則</th></tr></thead>
    <tbody>{defs_html}</tbody></table>
  <div class="dnotes">{notes_html}</div>
</section>

<section class="page flow">
  <div class="phead"><h2>一般產業得分前 {focus} 名</h2><span class="sub">{basis}</span></div>
  <div class="legend"><span><i class="full"></i>滿分</span><span><i class="part"></i>部分得分</span><span><i></i>0 分</span><span>— N/A（不評分，不計入可評滿分）</span><span>「櫃」＝上櫃　#＝在母體中的市值排名</span></div>
  <table>{gen_head}<tbody>{rank_rows(top)}</tbody></table>
</section>

<section class="page flow">
  <div class="phead"><h2>前 {focus} 名的關鍵數字</h2><span class="sub">EPS 已追溯調整至 FY{years[-1]} 股本基準</span></div>
  <table id="detail"><thead><tr><th class="r">排名</th><th>公司</th><th>EPS 五年走勢</th><th class="num">EPS FY{years[0]} → FY{years[-1]}</th>
    <th class="num">TTM EPS</th><th class="num">五年均 ROE</th><th class="num">淨利率 FY{years[-1]}</th><th class="num">TTM 支付率</th>
    <th class="num">利息保障</th><th class="num">長債/權益</th><th class="num">非獨立董監持股</th><th class="tot">總分<small>/{gen_max}</small></th></tr></thead>
    <tbody>{"".join(det)}</tbody></table>
  <div class="note">支付率 ＝ FY{years[-1]} 除息年度現金股利 ÷ TTM EPS，EPS 高速成長時會被系統性低估（需求方確認接受）。</div>
</section>

<section class="page">
  <div class="phead"><h2>前 {focus} 名的組成</h2><span class="sub">與一般產業全體 {n_gen} 檔比較</span></div>
  <div class="split3">
    <div class="block">
      <h2 class="h3">產業</h2>
      <div class="note">長條＝前 {focus} 名中的檔數；右側為該產業在母體中的檔數</div>
      {ind_html}
    </div>
    <div class="block">
      <h2 class="h3">各因子滿分率</h2>
      <div class="legend"><span><i class="full"></i>前 {focus} 名</span><span><i class="zero"></i>全體</span></div>
      {cmp_html}
    </div>
    <div class="block">
      <h2 class="h3">市值分層</h2>
      <div class="note">母體依市值切成三段</div>
      <table class="tiers"><thead><tr><th>分層</th><th class="num">檔數</th><th class="num">前 {focus}</th><th class="num">≥{thr} 分</th><th class="num">平均分</th><th class="num">段內最小市值</th></tr></thead>
        <tbody>{tier_html}</tbody></table>
      <div class="note">檔數含金融業與不評分者；前 {focus}、≥{thr} 分與平均分只計一般產業。</div>
    </div>
  </div>
</section>

<section class="page flow">
  <div class="phead"><h2>金融業排名</h2><span class="sub">只評 {"、".join(SHORT[k] for k in fin_keys)}，滿分 {fin_max}，不與一般產業混排、不套用 {thr} 分門檻</span></div>
  <table>{fin_head}<tbody>{fin_rows}</tbody></table>
</section>

<section class="page flow">
  <div class="phead"><h2>附錄：一般產業全體排名</h2><span class="sub">紅底＝前 {focus} 名　淺紅＝達 {thr} 分門檻　共 {n_gen} 檔</span></div>
  <div class="appx">{appendix}</div>
  <div class="note">不評分（{len(unrated)} 檔）：{unrated_html or "無"}</div>
</section>

<section class="page">
  <div class="phead"><h2>資料缺漏與資料來源</h2><span class="sub">{basis}</span></div>
  <div class="split">
    <div class="block">
      <h2 class="h3">一般產業各因子 N/A 檔數與主要原因</h2>
      <table class="defs"><thead><tr><th>因子</th><th class="num">檔數</th><th>主要原因（檔數）</th></tr></thead><tbody>{"".join(miss_rows) or "<tr><td colspan='3'>無</td></tr>"}</tbody></table>
      <div class="note">N/A 與 0 分嚴格區分：N/A 表示資料不足以判斷，該項不計分也不計入可評滿分（PRD §10）。</div>
    </div>
    <div class="block src">
      <h3>母體與市值</h3><p>證交所 t187ap03_L／STOCK_DAY_ALL、櫃買中心 mopsfin_t187ap03_O／tpex_mainboard_quotes。只取 4 碼普通股（排除 ETF），市值 ＝ 收盤價 × 已發行普通股數。</p>
      <h3>財報</h3><p>公開資訊觀測站 XBRL 整批檔（t203sb02），各年度 Q4 年報加最新期中報告，合併報表優先。{archive_note}資料層已與公告數及原 FinMind 資料交叉驗證（2026-09-15）。</p>
      <h3>現金股利</h3><p>證交所 TWT49U、櫃買中心 exDailyQ 除權息結果表，依除權息交易日所屬年度歸戶；某年查無紀錄即未配現金股利。</p>
      <h3>董監持股</h3><p>證交所 t187ap11_L、櫃買中心 mopsfin_t187ap11_O；白名單挑出非獨立董監，法人董事多席重複列示者去重。</p>
      <h3>已知限制</h3><ul>
        <li>每股現金股利未依股本變動追溯調整。</li>
        <li>利息保障倍數為重建值（PRD 原訂沿用 StockBoss），需求方已確認接受。</li>
        <li>淨利率與 ROE 採本期淨利（含非控制權益）與期末權益總額，待與人工評分表比對定案。</li></ul>
    </div>
  </div>
  <div class="disc">本報告由規則式程式依公開資料自動產生，僅供研究與流程驗證，不構成投資建議。分數反映 PRD v1.1 的財務體質規則，不含評價、成長預期與產業景氣判斷。</div>
</section>
</body></html>"""


CSS = """
@page { size: A4 landscape; margin: 11mm 13mm 12mm;
  @bottom-left { content: "{{TITLE}}"; font: 7pt 'Noto Sans TC', sans-serif; color: #5D6671; }
  @bottom-right { content: counter(page) " / " counter(pages); font: 7pt 'Noto Sans TC', sans-serif; color: #5D6671; } }
:root { --paper:#FFFFFF; --ink:#17202A; --muted:#5D6671; --rule:#D5DAE0; --faint:#E9ECEF;
        --seal:#B3261E; --seal-mid:#E7A197; --seal-tint:#F7DEDA; --zero:#9AA2AC; }
* { box-sizing: border-box; }
html { background: var(--paper); }
body { margin:0; color: var(--ink); background: var(--paper);
       font-family: 'Noto Sans TC','Microsoft JhengHei','PingFang TC',sans-serif;
       font-size: 8.6pt; line-height: 1.5; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.page { break-after: page; height: 186mm; display: flex; flex-direction: column; gap: 4mm; overflow: hidden; }
.page:last-child { break-after: auto; }
.page.flow { height: auto; overflow: visible; display: block; }
.page.flow > * + * { margin-top: 3mm; }
tr { break-inside: avoid; } thead { display: table-header-group; }
h1, h2, h3 { font-family: 'Noto Serif TC','PMingLiU','Songti TC',serif; font-weight: 800; margin: 0; text-wrap: balance; letter-spacing: .02em; }
h1 { font-size: 22pt; line-height: 1.15; white-space: nowrap; }
h2 { font-size: 13pt; } h2.h3 { font-size: 10.5pt; }
h3 { font-size: 9.4pt; }
.eyebrow { font-size: 7.2pt; letter-spacing: .14em; color: var(--muted); }
.sub { color: var(--muted); }
.phead { display:flex; justify-content: space-between; align-items: baseline; gap: 6mm; border-bottom: 1.2pt solid var(--ink); padding-bottom: 1.5mm; }
.phead .sub { font-size: 7.6pt; text-align: right; }
b { font-weight: 700; }
small { font-size: .78em; color: var(--muted); font-weight: 400; }
.num, td.r { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.note { color: var(--muted); font-size: 7.6pt; max-width: 90ch; }

.mast { display: grid; grid-template-columns: auto 1fr; gap: 7mm; align-items: end; border-bottom: 1.2pt solid var(--ink); padding-bottom: 4mm; }
.seal { display:inline-grid; place-items:center; width: 11mm; height: 11mm; border: 1.3pt solid var(--seal); color: var(--seal);
        font-weight: 900; font-size: 13pt; border-radius: 1.2mm; margin-right: 3mm; vertical-align: 2mm; }
.figs { display: grid; grid-template-columns: repeat(5, 1fr); gap: 3mm; }
.fig { border-left: .8pt solid var(--rule); padding-left: 2.5mm; }
.fig .n { font-family:'Noto Serif TC','PMingLiU',serif; font-weight: 800; font-size: 21pt; line-height: 1; font-variant-numeric: tabular-nums; }
.fig .n.red { color: var(--seal); }
.fig .l { font-size: 7.2pt; color: var(--muted); margin-top: 1mm; }

.dist { display:flex; flex-direction: column; gap: 2mm; }
.dhead { display:flex; justify-content: space-between; align-items: baseline; }
.hist { height: 72mm; display: grid; align-items: stretch; border-bottom: .8pt solid var(--ink); gap: .8mm; }
.hb { display: flex; flex-direction: column; position: relative; }
.hwrap { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; align-items: stretch; }
.hc { font-size: 6.6pt; text-align: center; color: var(--muted); font-variant-numeric: tabular-nums; line-height: 1.4; }
.hbar { display: block; background: var(--faint); min-height: 0; }
.hbar.pass { background: var(--seal-mid); } .hbar.top { background: var(--seal); }
.hx { position: absolute; bottom: -4.4mm; left: 50%; transform: translateX(-50%); font-size: 6.8pt; font-weight: 700; font-variant-numeric: tabular-nums; }
.axis { display:flex; justify-content: space-between; font-size: 7pt; color: var(--muted); margin-top: 4mm; }
.obs { display:grid; grid-template-columns: repeat(2, 1fr); gap: 1.4mm 8mm; margin: 0; padding: 0; list-style: none; }
.obs li { padding-left: 3.5mm; position: relative; max-width: 70ch; }
.obs li::before { content:""; position:absolute; left:0; top: 1.9mm; width: 1.6mm; height: 1.6mm; background: var(--seal); }

table { width: 100%; border-collapse: collapse; }
th { font-weight: 700; font-size: 7.2pt; color: var(--muted); text-align: left; padding: 1mm 1.2mm; border-bottom: .9pt solid var(--ink); white-space: nowrap; vertical-align: bottom; }
th small { display:block; font-size: 6.4pt; }
td { padding: .25mm 1.2mm; line-height: 1.2; border-bottom: .5pt solid var(--rule); white-space: nowrap; vertical-align: middle; }
td.code, .code { font-variant-numeric: tabular-nums; color: var(--muted); letter-spacing: .03em; }
td.nm { font-weight: 700; }
td.nm i { font-style: normal; font-size: 6.2pt; font-weight: 400; color: var(--muted); border: .5pt solid var(--rule); border-radius: .6mm; padding: 0 .6mm; margin-left: 1mm; }
td.ind, td.tag { color: var(--muted); font-size: 7.6pt; }
th.f, td.sc { text-align: center; width: 12mm; }
td.sc { font-variant-numeric: tabular-nums; font-weight: 700; padding: .2mm .5mm; }
td.sc.full { background: var(--seal); color: var(--paper); box-shadow: inset 0 0 0 .5mm var(--paper); }
td.sc.part { background: var(--seal-tint); color: var(--seal); box-shadow: inset 0 0 0 .5mm var(--paper); }
td.sc.zero { color: var(--zero); font-weight: 400; }
td.sc.na { color: var(--muted); font-weight: 400; }
th.tot, td.tot { width: 23mm; }
td.tot { position: relative; }
td.tot .bar { position:absolute; left: 1.2mm; top: 50%; height: 2.2mm; margin-top: -1.1mm;
              width: calc(13mm * var(--w) / 100); background: var(--ink); opacity: .14; }
td.tot b { position: relative; float: right; font-variant-numeric: tabular-nums; font-size: 9pt; }
tr.cut td { border-bottom: 1pt dashed var(--seal); color: var(--seal); font-size: 7pt; text-align: right; padding: .4mm 1.2mm; }
.legend { display:flex; flex-wrap: wrap; gap: 1mm 5mm; font-size: 7pt; color: var(--muted); align-items: center; }
.legend span { display:inline-flex; align-items:center; gap: 1.2mm; }
.legend i { width: 3.2mm; height: 3.2mm; display:inline-block; border: .5pt solid var(--rule); background: var(--paper); }
.legend i.full { background: var(--seal); border-color: var(--seal); } .legend i.part { background: var(--seal-tint); }
.legend i.mid { background: var(--seal-mid); } .legend i.zero { background: var(--faint); }

.defs td { white-space: normal; vertical-align: top; padding: 1.1mm 1.4mm; line-height: 1.45; font-size: 7.9pt; }
.defs td.dn { font-weight: 700; white-space: nowrap; } .defs .dm { text-align: center; font-weight: 700; color: var(--seal); width: 10mm; }
.defs td.dw { color: var(--muted); white-space: nowrap; }
.dnotes { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6mm; border-top: .8pt solid var(--ink); padding-top: 2.5mm; }
.dnote p { margin: 1mm 0 0; font-size: 7.8pt; line-height: 1.5; }

td.sp { width: 24mm; } .spark { display:block; }
.spark .sl { stroke: var(--seal); stroke-width: 1.3; } .spark .sd { fill: var(--seal); }
.spark .zl { stroke: var(--rule); stroke-width: .8; stroke-dasharray: 2 2; }
td.nm2 { font-weight: 700; } td.nm2 .code { font-weight: 400; margin-right: 1mm; }
#detail td { padding-top: .9mm; padding-bottom: .9mm; }

.split3 { display:grid; grid-template-columns: 1fr 1fr 1.1fr; gap: 8mm; flex: 1; min-height: 0; }
.split { display:grid; grid-template-columns: 1.2fr 1fr; gap: 9mm; flex: 1; min-height: 0; }
.block { display:flex; flex-direction: column; gap: 2.2mm; }
.irow, .crow { display:grid; grid-template-columns: 20mm 1fr 18mm; align-items:center; gap: 2mm; }
.il, .cl { font-weight: 700; } .cl small { margin-left: .6mm; }
.itrack, .cbars { display:flex; flex-direction: column; gap: .6mm; }
.ibar { display:block; height: 3.6mm; background: var(--seal); }
.cb { display:block; height: 2.2mm; } .cb.top { background: var(--seal); } .cb.all { background: var(--faint); }
.iv, .cv { font-variant-numeric: tabular-nums; font-weight: 700; }
.tiers td { padding: 1.4mm 1.2mm; }

.appx { columns: 5; column-gap: 5mm; column-rule: .5pt solid var(--rule); font-size: 7.2pt; }
.ar { display:grid; grid-template-columns: 7mm 9mm 1fr 7mm; gap: 1mm; padding: .25mm .8mm; break-inside: avoid; line-height: 1.35; }
.ar .r, .ar .as { text-align: right; font-variant-numeric: tabular-nums; }
.ar .an { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ar .as { font-weight: 700; }
.ar.t { background: var(--seal); color: var(--paper); } .ar.t .code { color: var(--seal-tint); }
.ar.p { background: var(--seal-tint); }

.src h3 { margin-top: 1.5mm; } .src p, .src ul { margin: .6mm 0 0; font-size: 7.8pt; } .src ul { padding-left: 4mm; }
.disc { border-top: .8pt solid var(--ink); padding-top: 2mm; font-size: 7.4pt; color: var(--muted); }
"""
