"""評分參數載入（PRD §12：門檻與權重不得寫死於程式邏輯）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_PARAMS_PATH = Path(__file__).resolve().parents[2] / "config" / "scoring_params.yaml"
DEFAULT_FIELDS_PATH = Path(__file__).resolve().parents[2] / "config" / "xbrl_fields.yaml"

REQUIRED_FACTORS = [
    "eps", "free_cash_flow", "dividend", "payout_ratio", "net_margin",
    "lt_debt_equity", "interest_coverage", "roe", "roic", "director_holding",
]


def load_params(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else DEFAULT_PARAMS_PATH
    with open(path, encoding="utf-8") as fh:
        params = yaml.safe_load(fh)
    validate_params(params)
    return params


def load_field_map(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else DEFAULT_FIELDS_PATH
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def validate_params(params: dict[str, Any]) -> None:
    """啟動時就抓出設定檔錯誤，而不是在評分中途才炸掉。"""
    missing = [k for k in REQUIRED_FACTORS if k not in params.get("factors", {})]
    if missing:
        raise ValueError(f"scoring_params.yaml 缺少因子設定：{missing}")

    declared = sum(params["factors"][k]["max_score"] for k in REQUIRED_FACTORS)
    general_max = params["general_sector"]["max_score"]
    if declared != general_max:
        raise ValueError(
            f"一般產業滿分不一致：十項因子 max_score 合計 {declared}，"
            f"但 general_sector.max_score = {general_max}（PRD §11 應為 16）"
        )

    fin = params["financial_sector"]
    fin_declared = sum(params["factors"][k]["max_score"] for k in fin["applicable_factors"])
    if fin_declared != fin["max_score"]:
        raise ValueError(
            f"金融業滿分不一致：適用因子合計 {fin_declared}，"
            f"但 financial_sector.max_score = {fin['max_score']}（PRD §8.12 應為 9）"
        )

    unknown = [k for k in fin["applicable_factors"] if k not in REQUIRED_FACTORS]
    if unknown:
        raise ValueError(f"financial_sector.applicable_factors 含未知因子：{unknown}")

    for k in REQUIRED_FACTORS:
        if k not in params.get("weights", {}):
            raise ValueError(f"weights 缺少因子權重：{k}")
