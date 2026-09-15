"""十項因子的評分規則（PRD §8）。

每個函式都是純函式：輸入 CompanyFacts + 參數字典，輸出 FactorScore。
不讀設定檔、不碰網路、不寫死門檻 —— 門檻一律由 params 傳入（PRD §12）。
取不到資料時回傳 score=None（N/A），與 0 分嚴格區分（PRD §10）。
"""

from __future__ import annotations

from ..models import CompanyFacts, FactorScore


def _na(key: str, reason: str) -> FactorScore:
    return FactorScore(key=key, score=None, rule=f"N/A：{reason}")


def _series_ok(series: list[float] | None, years: int) -> bool:
    return bool(series) and len(series) == years and all(v is not None for v in series)


def _decline_pct(prev: float, cur: float) -> float | None:
    """年減率（%）。僅在 cur < prev 且 prev > 0 時有意義，否則回傳 None。"""
    if prev is None or cur is None or cur >= prev or prev <= 0:
        return None
    return (prev - cur) / prev * 100.0


def _fmt(series: list[float]) -> str:
    return ", ".join(f"{v:g}" for v in series)


# --------------------------------------------------------------------------
# 8.1 EPS
# --------------------------------------------------------------------------
def score_eps(facts: CompanyFacts, p: dict, years: int) -> FactorScore:
    eps = facts.eps_annual
    if not _series_ok(eps, years):
        return _na("eps", "缺少完整五年 EPS")

    repr_ = _fmt(eps)

    if p.get("negative_eps_zeroes_factor", True) and any(v < 0 for v in eps):
        neg_years = [facts.fiscal_years[i] if facts.fiscal_years else i for i, v in enumerate(eps) if v < 0]
        return FactorScore(
            key="eps", score=0.0, value_repr=repr_,
            rule=f"五年內出現 EPS<0（{neg_years}）→ 穩定性與成長分均為 0（§8.1 / §10.1）",
        )

    mild = p["mild_decline_pct"]
    severe = p["severe_decline_pct"]

    # 逐年比較：4 次 YoY（index i 表示 eps[i] 相對 eps[i-1]）
    moderate_hits: list[int] = []
    severe_hits: list[int] = []
    for i in range(1, years):
        d = _decline_pct(eps[i - 1], eps[i])
        if d is None:
            continue
        if d > severe:
            severe_hits.append(i)
        elif d > mild:
            moderate_hits.append(i)

    last_idx = years - 1
    if len(severe_hits) >= 2:
        stability = 0.0
        rule = f"出現 {len(severe_hits)} 次 >{severe:g}% 大幅衰退（第 {severe_hits} 次比較）→ 穩定性 0"
    elif len(severe_hits) == 1:
        k = severe_hits[0]
        if k == last_idx:
            stability = 0.0
            rule = f"最後一年度出現 >{severe:g}% 衰退，無次年可證明恢復 → 穩定性 0"
        elif eps[k + 1] >= eps[k - 1]:
            stability = p["stability_partial"]
            rule = (f"一次 >{severe:g}% 大幅衰退（{eps[k - 1]:g}→{eps[k]:g}），"
                    f"次年恢復至衰退前水準以上（{eps[k + 1]:g} ≥ {eps[k - 1]:g}）→ 穩定性 {stability:g}")
        else:
            stability = 0.0
            rule = (f"一次 >{severe:g}% 大幅衰退（{eps[k - 1]:g}→{eps[k]:g}），"
                    f"次年未恢復（{eps[k + 1]:g} < {eps[k - 1]:g}）→ 穩定性 0")
    elif moderate_hits:
        stability = p["stability_partial"]
        rule = f"出現 >{mild:g}% 且 ≤{severe:g}% 的下降，無未恢復之大幅衰退 → 穩定性 {stability:g}"
    else:
        stability = p["stability_full"]
        rule = f"各年度下降幅度皆 ≤{mild:g}%（或全為持平／成長）→ 穩定性 {stability:g}"

    grew = all(eps[i] > eps[i - 1] for i in range(1, years))
    growth = p["growth_bonus"] if grew else 0.0
    rule += f"；成長分 {growth:g}（{'五年逐年嚴格成長' if grew else '非逐年成長'}）"

    return FactorScore(key="eps", score=stability + growth, rule=rule, value_repr=repr_)


# --------------------------------------------------------------------------
# 8.2 自由現金流
# --------------------------------------------------------------------------
def score_free_cash_flow(facts: CompanyFacts, p: dict, years: int) -> FactorScore:
    fcf = facts.fcf_annual
    if not _series_ok(fcf, years):
        return _na("free_cash_flow", "缺少完整五年自由現金流")
    floor = p["min_value"]
    ok = all(v > floor for v in fcf)
    return FactorScore(
        key="free_cash_flow", score=p["max_score"] if ok else 0.0, value_repr=_fmt(fcf),
        rule=f"五年自由現金流{'全部' if ok else '未全部'} >{floor:g} → {p['max_score'] if ok else 0}",
    )


# --------------------------------------------------------------------------
# 8.3 現金股利
# --------------------------------------------------------------------------
def score_dividend(facts: CompanyFacts, p: dict, years: int) -> FactorScore:
    div = facts.dividend_annual
    if not _series_ok(div, years):
        return _na("dividend", "缺少完整五年現金股利")

    paid_all = all(v > 0 for v in div)
    part1 = p["paid_all_years_score"] if paid_all else 0.0

    limit = p["max_yoy_decline_pct"]
    breaches = []
    for i in range(1, years):
        d = _decline_pct(div[i - 1], div[i])
        if d is not None and d > limit:
            breaches.append((i, round(d, 2)))
    part2 = 0.0 if breaches else p["stability_score"]

    rule = (f"五年{'皆有' if paid_all else '未皆有'}配發現金股利 → +{part1:g}；"
            f"四次年度比較年減幅{'皆 ≤' if not breaches else '出現 >'}{limit:g}%"
            + (f"（{breaches}）" if breaches else "") + f" → +{part2:g}")
    return FactorScore(key="dividend", score=part1 + part2, rule=rule, value_repr=_fmt(div))


# --------------------------------------------------------------------------
# 8.4 股息支付率（TTM）
# --------------------------------------------------------------------------
def score_payout_ratio(facts: CompanyFacts, p: dict, years: int) -> FactorScore:
    ratio = facts.ttm_payout_ratio
    if ratio is None:
        return _na("payout_ratio", "缺少 TTM 股息支付率")

    repr_ = f"TTM Payout={ratio:g}%" + (f"、TTM EPS={facts.ttm_eps:g}" if facts.ttm_eps is not None else "")

    if facts.ttm_eps is not None and facts.ttm_eps < 0:
        return FactorScore(key="payout_ratio", score=p["negative_eps_score"], value_repr=repr_,
                           rule="TTM EPS<0 → 0（§8.4）")
    if ratio == 0:
        return FactorScore(key="payout_ratio", score=p["zero_score"], value_repr=repr_,
                           rule="TTM Payout Ratio = 0% → 0")
    if ratio < p["tier1_upper_pct"]:
        return FactorScore(key="payout_ratio", score=p["tier1_score"], value_repr=repr_,
                           rule=f"0% < {ratio:g}% < {p['tier1_upper_pct']:g}% → {p['tier1_score']:g}")
    if ratio < p["tier2_upper_pct"]:
        return FactorScore(key="payout_ratio", score=p["tier2_score"], value_repr=repr_,
                           rule=f"{p['tier1_upper_pct']:g}% ≤ {ratio:g}% < {p['tier2_upper_pct']:g}% → {p['tier2_score']:g}")
    return FactorScore(key="payout_ratio", score=p["over_cap_score"], value_repr=repr_,
                       rule=f"{ratio:g}% ≥ {p['tier2_upper_pct']:g}% → {p['over_cap_score']:g}")


# --------------------------------------------------------------------------
# 8.5 淨利率
# --------------------------------------------------------------------------
def score_net_margin(facts: CompanyFacts, p: dict, years: int) -> FactorScore:
    nm = facts.net_margin_annual
    if not _series_ok(nm, years):
        return _na("net_margin", "缺少完整五年淨利率")
    repr_ = _fmt(nm)
    if all(v > p["high_pct"] for v in nm):
        return FactorScore(key="net_margin", score=p["high_score"], value_repr=repr_,
                           rule=f"五年淨利率皆 >{p['high_pct']:g}% → {p['high_score']:g}（不與其他條件累加）")
    if all(v > p["base_pct"] for v in nm):
        return FactorScore(key="net_margin", score=p["base_score"], value_repr=repr_,
                           rule=f"五年淨利率皆 >{p['base_pct']:g}% → {p['base_score']:g}")
    if all(nm[i] > nm[i - 1] for i in range(1, years)):
        return FactorScore(key="net_margin", score=p["growth_score"], value_repr=repr_,
                           rule=f"五年淨利率逐年嚴格成長 → {p['growth_score']:g}")
    return FactorScore(key="net_margin", score=0.0, value_repr=repr_, rule="未達任一條件 → 0")


# --------------------------------------------------------------------------
# 8.6 長期負債權益比
# --------------------------------------------------------------------------
def score_lt_debt_equity(facts: CompanyFacts, p: dict, years: int) -> FactorScore:
    ltd, eq = facts.long_term_debt, facts.total_equity
    if ltd is None or eq is None:
        return _na("lt_debt_equity", "缺少長期負債或股東權益")
    if eq <= 0:
        return FactorScore(key="lt_debt_equity", score=p["negative_equity_score"],
                           value_repr=f"股東權益={eq:g}", flags=["高風險警示"],
                           rule="股東權益 ≤0 → 0 分並標示高風險警示（§8.6 / §10.1）")
    ratio = ltd / eq
    ok = ratio < p["threshold"]
    return FactorScore(
        key="lt_debt_equity", score=p["pass_score"] if ok else 0.0,
        value_repr=f"LT-Debt={ltd:g}、Equity={eq:g}、Ratio={ratio:.4f}",
        rule=f"LT-Debt/Equity {ratio:.4f} {'<' if ok else '≥'} {p['threshold']:g} → {p['pass_score'] if ok else 0:g}",
    )


# --------------------------------------------------------------------------
# 8.7 利息保障倍數
# --------------------------------------------------------------------------
def score_interest_coverage(facts: CompanyFacts, p: dict, years: int) -> FactorScore:
    cov = facts.interest_coverage
    op = facts.ttm_operating_income
    ie = facts.interest_expense

    # 無利息負擔的特殊情況（§8.7 第 4 列）
    no_interest_burden = (cov is None and ie is not None and ie == 0) or (ie == 0)
    if no_interest_burden:
        if op is None:
            return _na("interest_coverage", "無利息負擔但缺少營業利益，無法判斷")
        if op > 0:
            return FactorScore(key="interest_coverage", score=p["no_debt_positive_op_score"],
                               value_repr=f"利息支出=0、營業利益={op:g}",
                               rule=f"無利息負擔且營業利益 >0 → {p['no_debt_positive_op_score']:g}")
        return FactorScore(key="interest_coverage", score=p["no_debt_nonpositive_op_score"],
                           value_repr=f"利息支出=0、營業利益={op:g}",
                           rule=f"無利息負擔但營業利益 ≤0 → {p['no_debt_nonpositive_op_score']:g}")

    if cov is None:
        return _na("interest_coverage", "缺少利息保障倍數")
    repr_ = f"Interest Coverage={cov:g}"
    if cov > p["high_threshold"]:
        return FactorScore(key="interest_coverage", score=p["high_score"], value_repr=repr_,
                           rule=f"{cov:g} > {p['high_threshold']:g} → {p['high_score']:g}")
    if cov > p["mid_threshold"]:
        return FactorScore(key="interest_coverage", score=p["mid_score"], value_repr=repr_,
                           rule=f"{p['mid_threshold']:g} < {cov:g} ≤ {p['high_threshold']:g} → {p['mid_score']:g}")
    return FactorScore(key="interest_coverage", score=p["low_score"], value_repr=repr_,
                       rule=f"{cov:g} ≤ {p['mid_threshold']:g} → {p['low_score']:g}")


# --------------------------------------------------------------------------
# 8.8 ROE / 8.9 ROIC
# --------------------------------------------------------------------------
def score_roe(facts: CompanyFacts, p: dict, years: int) -> FactorScore:
    roe = facts.roe_annual
    if not _series_ok(roe, years):
        return _na("roe", "缺少完整五年 ROE")
    repr_ = _fmt(roe)
    if all(v >= p["high_pct"] for v in roe):
        return FactorScore(key="roe", score=p["high_score"], value_repr=repr_,
                           rule=f"五年 ROE 皆 ≥{p['high_pct']:g}% → {p['high_score']:g}")
    if all(v >= p["base_pct"] for v in roe):
        return FactorScore(key="roe", score=p["base_score"], value_repr=repr_,
                           rule=f"五年 ROE 皆 ≥{p['base_pct']:g}%（未全部 ≥{p['high_pct']:g}%）→ {p['base_score']:g}")
    return FactorScore(key="roe", score=0.0, value_repr=repr_,
                       rule=f"任一年 ROE <{p['base_pct']:g}% → 0")


def score_roic(facts: CompanyFacts, p: dict, years: int) -> FactorScore:
    roic = facts.roic_annual
    if not _series_ok(roic, years):
        return _na("roic", "缺少完整五年 ROIC")
    ok = all(v >= p["threshold_pct"] for v in roic)
    return FactorScore(key="roic", score=p["pass_score"] if ok else 0.0, value_repr=_fmt(roic),
                       rule=f"五年 ROIC {'皆' if ok else '未皆'} ≥{p['threshold_pct']:g}% → {p['pass_score'] if ok else 0:g}")


# --------------------------------------------------------------------------
# 8.10 非獨立董監持股與質押
# --------------------------------------------------------------------------
def score_director_holding(facts: CompanyFacts, p: dict, years: int) -> FactorScore:
    hold, pledge = facts.director_holding_pct, facts.director_pledge_pct
    if hold is None:
        return _na("director_holding", facts.missing_reasons.get("director_holding", "缺少非獨立董監持股資料"))
    repr_ = f"持股={hold:g}%" + (f"、質押={pledge:g}%" if pledge is not None else "、質押=N/A")

    if hold <= p["min_holding_pct"]:
        return FactorScore(key="director_holding", score=0.0, value_repr=repr_,
                           rule=f"非獨立董監持股 {hold:g}% ≤ {p['min_holding_pct']:g}% → 0（剛好 10% 不得分）")
    if pledge is None:
        return _na("director_holding", "有持股資料但缺少質押比例，無法判定")
    if pledge > p["max_pledge_pct"]:
        return FactorScore(key="director_holding", score=0.0, value_repr=repr_,
                           rule=f"持股 {hold:g}% >{p['min_holding_pct']:g}% 但質押 {pledge:g}% > {p['max_pledge_pct']:g}% → 0")
    return FactorScore(key="director_holding", score=p["pass_score"], value_repr=repr_,
                       rule=f"持股 {hold:g}% >{p['min_holding_pct']:g}% 且質押 {pledge:g}% ≤ {p['max_pledge_pct']:g}% → {p['pass_score']:g}")


FACTOR_FUNCS = {
    "eps": score_eps,
    "free_cash_flow": score_free_cash_flow,
    "dividend": score_dividend,
    "payout_ratio": score_payout_ratio,
    "net_margin": score_net_margin,
    "lt_debt_equity": score_lt_debt_equity,
    "interest_coverage": score_interest_coverage,
    "roe": score_roe,
    "roic": score_roic,
    "director_holding": score_director_holding,
}

FACTOR_LABELS = {
    "eps": "EPS",
    "free_cash_flow": "自由現金流",
    "dividend": "現金股利",
    "payout_ratio": "股息支付率",
    "net_margin": "淨利率",
    "lt_debt_equity": "LT-Debt/Equity",
    "interest_coverage": "利息保障倍數",
    "roe": "ROE",
    "roic": "ROIC",
    "director_holding": "非獨立董監持股",
}
