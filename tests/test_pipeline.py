"""端到端管線測試（fixture 來源，無網路依賴）。"""

from twfactor.export import cards_to_dataframe
from twfactor.params import load_params
from twfactor.scoring import ScoringEngine
from twfactor.sources.fixtures import FixtureSource

P = load_params()


def run(n=50):
    src = FixtureSource(years=P["periods"]["annual_years"])
    companies = src.top_by_market_cap(n)
    facts = src.fetch_facts(companies)
    cards = ScoringEngine(P).score_universe(facts)
    return facts, cards


def test_universe_size_and_partition():
    facts, cards = run(50)
    assert len(cards) == 50
    general = [c for c in cards if c.rank_pool == "general"]
    financial = [c for c in cards if c.rank_pool == "financial"]
    assert len(general) + len(financial) == 50
    assert financial, "fixture 應包含金融業樣本"


def test_scores_never_exceed_pool_max():
    _, cards = run()
    for c in cards:
        if c.raw_total is not None:
            assert 0 <= c.raw_total <= c.pool_max_score


def test_ranks_are_contiguous_competition_ranks():
    _, cards = run()
    for pool in ("general", "financial"):
        ranked = sorted([c for c in cards if c.rank_pool == pool and c.rank],
                        key=lambda c: c.rank)
        assert ranked[0].rank == 1
        # 分數遞減、排名遞增，且同分同名次
        for a, b in zip(ranked, ranked[1:]):
            assert a.raw_total >= b.raw_total
            assert (a.rank == b.rank) == (a.raw_total == b.raw_total)


def test_export_columns_cover_prd_13_1():
    facts, cards = run()
    df = cards_to_dataframe(cards, {f.stock_id: f for f in facts})
    required = ["股票代碼", "公司名稱", "市場別", "總分", "排名", "排名模式",
                "五年含息總報酬", "資料完整狀態", "最後更新時間", "備註",
                "ROE／ROIC 標籤", "高風險警示"]
    assert not [c for c in required if c not in df.columns]
    assert len(df) == len(cards)


def test_na_is_exported_as_na_not_zero():
    facts, cards = run()
    df = cards_to_dataframe(cards, {f.stock_id: f for f in facts})
    fin = df[df["排名模式"] == "金融業排名"]
    assert (fin["淨利率 分數"] == "N/A").all()      # 不適用 → N/A，不是 0
    young = df[df["總分"] == "N/A"]
    assert len(young) >= 1                          # 未滿五年公司


def test_snapshot_is_reproducible():
    """PRD §16：同一資料快照 + 同一規則版本 → 相同評分。"""
    _, a = run()
    _, b = run()
    assert [(c.stock_id, c.raw_total, c.rank) for c in a] == \
           [(c.stock_id, c.raw_total, c.rank) for c in b]
