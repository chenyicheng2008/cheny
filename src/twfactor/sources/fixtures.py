"""離線 fixture 來源：用於無網路環境下驗證管線端到端行為。

⚠ 這些是刻意標示的合成公司（SYN*），不對應任何真實上市櫃公司，
   數值為測試用，不得用於投資判斷或當成真實財報（PRD §19.6 不得補值假裝通過）。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import CompanyFacts, Provenance

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic_universe.json"


class FixtureSource:
    name = "fixture(synthetic)"

    def __init__(self, path: str | Path | None = None, years: int = 5):
        self.path = Path(path) if path else FIXTURE_PATH
        self.years = years
        self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def top_by_market_cap(self, n: int) -> list[dict]:
        rows = sorted(self._data["companies"], key=lambda r: r.get("market_cap") or 0, reverse=True)
        return rows[:n]

    def fetch_facts(self, companies: list[dict]) -> list[CompanyFacts]:
        out = []
        for c in companies:
            kw = {k: v for k, v in c.items() if k in CompanyFacts.__dataclass_fields__}
            facts = CompanyFacts(**kw)
            facts.provenance = {
                k: Provenance(source=self.name, field_name=k, period="synthetic", fetched_at="n/a")
                for k in ("eps", "cash_dividend")
            }
            out.append(facts)
        return out
