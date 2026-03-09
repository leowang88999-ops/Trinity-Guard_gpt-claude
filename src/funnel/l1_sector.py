"""L1 行业匹配：将 L0 候选与 System A 目标行业对齐，支持人工覆盖。"""
from __future__ import annotations

import pandas as pd
from loguru import logger


def run_l1_match(
    candidates: pd.DataFrame,
    target_sectors: list[str],
    override_codes: list[str] | None = None,
) -> pd.DataFrame:
    """
    L1 行业匹配：筛选目标行业标的，并追加 override 标的。

    Args:
        candidates: L0 输出，需含 industry 列
        target_sectors: System A 输出的目标行业（申万分类）
        override_codes: System A 指定的 2–3 只覆盖标的 ts_code

    Returns:
        匹配后的 DataFrame，override 标的会强制包含
    """
    if candidates.empty:
        return pd.DataFrame()

    sector_matched = candidates[candidates["industry"].isin(target_sectors)]

    if override_codes:
        override_df = candidates[candidates["ts_code"].isin(override_codes)]
        extra = override_df[~override_df["ts_code"].isin(sector_matched["ts_code"])]
        if not extra.empty:
            sector_matched = pd.concat([sector_matched, extra], ignore_index=True)
            logger.debug(f"L1 覆盖追加 {len(extra)} 只: {override_codes}")

    return sector_matched.drop_duplicates(subset=["ts_code"]).reset_index(drop=True)
