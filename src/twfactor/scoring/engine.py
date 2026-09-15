"""評分引擎（PRD §7、§8、§10、§11）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import CompanyFacts, FactorScore, ScoreCard
from .factors import FACTOR_FUNCS, FACTOR_LABELS
from .ranking import assign_ranks


class ScoringEngine:
    def __init__(self, params: dict[str, Any]):
        self.params = params
        self.years: int = params["periods"]["annual_years"]
        self.weights: dict[str, float] = params["weights"]
        self.fin_applicable: set[str] = set(params["financial_sector"]["applicable_factors"])

    # -- 單一公司 ---------------------------------------------------------
    def score_company(self, facts: CompanyFacts) -> ScoreCard:
        pool = "financial" if facts.is_financial else "general"
        pool_max = (self.params["financial_sector"]["max_score"] if facts.is_financial
                    else self.params["general_sector"]["max_score"])
        card = ScoreCard(
            stock_id=facts.stock_id, stock_name=facts.stock_name, market=facts.market,
            rank_pool=pool, pool_max_score=pool_max,
            last_updated=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

        # PRD §3 / §8.1：未滿五個完整年度 → 整家公司 N/A，不計總分與排名
        if not facts.has_full_history(self.years):
            card.status = "insufficient_history"
            card.notes.append("未滿五年：不進行十項完整評分，總分與排名 N/A")
            card.data_completeness = "未滿五年"
            for key in FACTOR_FUNCS:
                card.factors[key] = FactorScore(key=key, score=None, rule="N/A：未滿五年")
            return card

        for key, func in FACTOR_FUNCS.items():
            if facts.is_financial and key not in self.fin_applicable:
                card.factors[key] = FactorScore(
                    key=key, score=None, applicable=False,
                    rule="N/A：金融業部分評分矩陣不適用本因子（§8.12）",
                )
                continue
            card.factors[key] = func(facts, self.params["factors"][key], self.years)

        self._aggregate(card, facts)
        self._tag(card, facts)
        return card

    # -- 總分（PRD §10、§11：單項 N/A 不按比例放大）------------------------
    def _aggregate(self, card: ScoreCard, facts: CompanyFacts) -> None:
        raw = weighted = 0.0
        attainable = 0.0
        missing: list[str] = []

        for key, fs in card.factors.items():
            if fs.score is None:
                if fs.applicable:  # 資料缺漏（金融業不適用者不算缺漏）
                    missing.append(FACTOR_LABELS[key])
                continue
            raw += fs.score
            weighted += fs.score * self.weights[key]
            attainable += self.params["factors"][key]["max_score"]
            card.flags.extend(fs.flags)

        card.raw_total = raw
        card.weighted_total = weighted
        card.attainable_max = attainable

        if missing:
            card.notes.append("缺漏因子：" + "、".join(missing) + "（其餘照常加總，不按比例放大）")
            card.data_completeness = f"部分缺漏（可評 {attainable:g}/{card.pool_max_score}）"
        else:
            card.data_completeness = "完整"

    # -- 8.11 高 ROE／ROIC 標籤（僅資訊辨識，不加分）-----------------------
    def _tag(self, card: ScoreCard, facts: CompanyFacts) -> None:
        fp = self.params["factors"]
        roe_max = (facts.roe_annual and len(facts.roe_annual) == self.years
                   and all(v >= fp["roe"]["high_pct"] for v in facts.roe_annual))
        roic_max = (facts.roic_annual and len(facts.roic_annual) == self.years
                    and all(v >= fp["roic"]["threshold_pct"] for v in facts.roic_annual))
        if roe_max and roic_max:
            card.tags.append("高 ROE 及 ROIC")
        elif roe_max:
            card.tags.append("高 ROE")
        elif roic_max:
            card.tags.append("高 ROIC")

    # -- 全市場 -----------------------------------------------------------
    def score_universe(self, facts_list: list[CompanyFacts]) -> list[ScoreCard]:
        cards = [self.score_company(f) for f in facts_list]
        assign_ranks(cards)
        return cards
