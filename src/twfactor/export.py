"""結果表與匯出（PRD §13.1、FR-10）。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .models import CompanyFacts, ScoreCard
from .scoring.factors import FACTOR_LABELS

FACTOR_ORDER = list(FACTOR_LABELS.keys())


def cards_to_dataframe(cards: list[ScoreCard], facts: dict[str, CompanyFacts] | None = None) -> pd.DataFrame:
    facts = facts or {}
    rows = []
    for c in cards:
        f = facts.get(c.stock_id)
        row = {
            "股票代碼": c.stock_id,
            "公司名稱": c.stock_name,
            "市場別": c.market,
            "StockBoss 產業別": getattr(f, "industry_stockboss", None) if f else None,
            "Goodinfo 產業別": getattr(f, "industry_goodinfo", None) if f else None,
            "交易所產業別代碼": getattr(f, "industry_code", None) if f else None,
            "市值": getattr(f, "market_cap", None) if f else None,
        }
        for key in FACTOR_ORDER:
            fs = c.factors.get(key)
            row[f"{FACTOR_LABELS[key]} 分數"] = "N/A" if fs is None or fs.is_na else fs.score
        row.update({
            "五年含息總報酬": getattr(f, "total_return_5y", None) if f else None,
            "總分": "N/A" if c.raw_total is None else c.raw_total,
            "加權總分": "N/A" if c.weighted_total is None else c.weighted_total,
            "排名池滿分": c.pool_max_score,
            "實際可評滿分": c.attainable_max,
            "排名": "N/A" if c.rank is None else c.rank,
            "排名模式": "金融業排名" if c.rank_pool == "financial" else "一般產業排名",
            "ROE／ROIC 標籤": "、".join(c.tags),
            "高風險警示": "是" if "高風險警示" in c.flags else "",
            "資料完整狀態": c.data_completeness,
            "最後更新時間": c.last_updated,
            "備註": "；".join(c.notes),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def write_excel(cards: list[ScoreCard], facts: dict[str, CompanyFacts], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = cards_to_dataframe(cards, facts)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df[df["排名模式"] == "一般產業排名"].to_excel(xw, sheet_name="一般產業", index=False)
        df[df["排名模式"] == "金融業排名"].to_excel(xw, sheet_name="金融業", index=False)
        _detail_frame(cards).to_excel(xw, sheet_name="得分理由", index=False)
    return path


def _detail_frame(cards: list[ScoreCard]) -> pd.DataFrame:
    """PRD §13.2：逐項「原始值 → 命中規則 → 得分」的判斷鏈。"""
    rows = []
    for c in cards:
        for key in FACTOR_ORDER:
            fs = c.factors.get(key)
            if fs is None:
                continue
            rows.append({
                "股票代碼": c.stock_id, "公司名稱": c.stock_name,
                "因子": FACTOR_LABELS[key],
                "原始值": fs.value_repr,
                "命中規則": fs.rule,
                "得分": "N/A" if fs.is_na else fs.score,
                "是否適用": "適用" if fs.applicable else "不適用",
            })
    return pd.DataFrame(rows)


def write_snapshot(cards: list[ScoreCard], facts: dict[str, CompanyFacts], path: str | Path) -> Path:
    """PRD §16 資料保存：保留每次成功更新之結果快照。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cards": [
            {
                "stock_id": c.stock_id, "stock_name": c.stock_name, "rank_pool": c.rank_pool,
                "raw_total": c.raw_total, "weighted_total": c.weighted_total, "rank": c.rank,
                "status": c.status, "tags": c.tags, "flags": c.flags, "notes": c.notes,
                "factors": {k: {"score": v.score, "rule": v.rule, "value": v.value_repr,
                                "applicable": v.applicable} for k, v in c.factors.items()},
            } for c in cards
        ],
        "provenance": {
            sid: {k: vars(v) for k, v in f.provenance.items()} for sid, f in facts.items()
        },
        "missing": {sid: f.missing_reasons for sid, f in facts.items() if f.missing_reasons},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
