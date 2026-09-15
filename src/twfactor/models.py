"""資料模型：原始事實、因子分數與評分卡。

設計原則（PRD §6.1、§16）：每一個分數都要能回溯到原始值、規則與來源。
因此 FactorScore 同時保存 value_repr（原始值）與 rule（命中規則）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


NA = None  # 以 None 表示 N/A；與 0 分嚴格區分


@dataclass
class Provenance:
    """單一欄位的可追溯性資訊（PRD §6.1）。"""

    source: str = ""          # 例如 "FinMind:TaiwanStockFinancialStatements"
    field_name: str = ""      # 來源端原始欄位／API type
    period: str = ""          # 財報年度或 TTM 截止日
    fetched_at: str = ""      # 抓取時間 ISO8601
    note: str = ""


@dataclass
class CompanyFacts:
    """單一公司經標準化後、可供評分的事實集合。

    年度序列一律「由舊到新」排列，長度應等於 params.periods.annual_years。
    取不到的欄位以 None 表示，評分引擎會將對應因子標為 N/A。
    """

    stock_id: str
    stock_name: str = ""
    market: str = ""                  # twse / tpex / innovation
    industry_finmind: str | None = None
    industry_stockboss: str | None = None
    industry_goodinfo: str | None = None
    is_financial: bool = False
    is_ky: bool = False
    market_cap: float | None = None

    # 近五個完整年度序列（舊 → 新）
    eps_annual: list[float] | None = None
    fcf_annual: list[float] | None = None
    dividend_annual: list[float] | None = None
    net_margin_annual: list[float] | None = None      # 百分比
    roe_annual: list[float] | None = None             # 百分比
    roic_annual: list[float] | None = None            # 百分比
    fiscal_years: list[int] = field(default_factory=list)

    # TTM / 最新一季底
    ttm_payout_ratio: float | None = None             # 百分比
    ttm_eps: float | None = None
    long_term_debt: float | None = None
    total_equity: float | None = None
    interest_coverage: float | None = None
    interest_expense: float | None = None
    ttm_operating_income: float | None = None

    # 公司治理（PRD §8.10）
    director_holding_pct: float | None = None         # 非獨立董監合計持股 %
    director_pledge_pct: float | None = None          # 董監持股質押 %

    # 績效參考（PRD §9，不參與評分）
    total_return_5y: float | None = None

    provenance: dict[str, Provenance] = field(default_factory=dict)
    missing_reasons: dict[str, str] = field(default_factory=dict)

    def has_full_history(self, years: int) -> bool:
        """是否具備完整 N 個完整年度的 EPS（PRD §3 未滿五年判定的主要依據）。

        年數必須「剛好足夠」：長度不足即為未滿五年，長度為 0 亦然。
        """
        return (
            self.eps_annual is not None
            and len(self.eps_annual) >= years
            and all(v is not None for v in self.eps_annual[-years:])
        )


@dataclass
class FactorScore:
    """單一因子的評分結果。score 為 None 代表 N/A（PRD §10）。"""

    key: str
    score: float | None
    rule: str                              # 命中的規則描述
    value_repr: str = ""                   # 判斷所用的原始值
    applicable: bool = True                # False = 依規則不適用（如金融業）
    flags: list[str] = field(default_factory=list)

    @property
    def is_na(self) -> bool:
        return self.score is None


@dataclass
class ScoreCard:
    """一家公司的完整評分結果。"""

    stock_id: str
    stock_name: str
    market: str
    rank_pool: str                          # "general" / "financial"
    factors: dict[str, FactorScore] = field(default_factory=dict)
    raw_total: float | None = None          # 原始總分（權重 1.0）
    weighted_total: float | None = None     # 加權總分（PRD §12，兩者分開保存）
    pool_max_score: int = 0                 # 該排名池的滿分（16 或 9）
    attainable_max: float = 0.0             # 扣除 N/A 後實際可評滿分
    rank: int | None = None
    status: str = "ok"                      # ok / insufficient_history / stale
    tags: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    data_completeness: str = ""
    last_updated: str = ""
