"""排名（PRD §11）：競賽排名並跳號；一般產業與金融業不混排。"""

from __future__ import annotations

from ..models import ScoreCard


def competition_rank(values: list[float]) -> list[int]:
    """標準競賽排名（1, 2, 2, 4）。輸入為分數，由高至低排名。"""
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    ranks = [0] * len(values)
    prev_value: float | None = None
    prev_rank = 0
    for position, idx in enumerate(order, start=1):
        if prev_value is not None and values[idx] == prev_value:
            ranks[idx] = prev_rank
        else:
            ranks[idx] = position
            prev_rank = position
            prev_value = values[idx]
    return ranks


def assign_ranks(cards: list[ScoreCard]) -> None:
    """就地寫入 rank。總分為 N/A 者不參與排名（PRD §11 未滿五年公司）。"""
    for pool in {c.rank_pool for c in cards}:
        rankable = [c for c in cards if c.rank_pool == pool and c.raw_total is not None]
        for card, rank in zip(rankable, competition_rank([c.raw_total for c in rankable])):
            card.rank = rank
        for card in cards:
            if card.rank_pool == pool and card.raw_total is None:
                card.rank = None
