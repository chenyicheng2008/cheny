"""資料來源介面（PRD §16 可維護性：更換來源不應重寫整體系統）。"""

from __future__ import annotations

from typing import Protocol

from ..models import CompanyFacts


class DataSource(Protocol):
    """任何資料來源都只需實作這兩個方法，評分引擎與之解耦。"""

    name: str

    def top_by_market_cap(self, n: int) -> list[dict]:
        """回傳市值前 n 名的公司清單（含 stock_id / stock_name / market / market_cap）。"""
        ...

    def fetch_facts(self, stock_ids: list[str]) -> list[CompanyFacts]:
        """取得並標準化為可供評分的 CompanyFacts。"""
        ...


class DirectorHoldingProvider(Protocol):
    """非獨立董監持股與質押（PRD §8.10）。

    FinMind 公開 dataset 不提供此資料，且無法區分獨立董事，
    因此獨立成一個可插拔介面，由 MOPS／Goodinfo 等來源實作。
    未提供時該因子標 N/A（PRD §10：不補分、不按比例放大）。
    """

    name: str

    def get(self, stock_id: str) -> tuple[float | None, float | None]:
        """回傳 (非獨立董監持股 %, 董監持股質押 %)。取不到回傳 (None, None)。"""
        ...


class NullDirectorHoldingProvider:
    """預設實作：一律 N/A，並記錄缺漏原因。"""

    name = "none"

    def get(self, stock_id: str) -> tuple[None, None]:
        return None, None
