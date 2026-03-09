"""
MSI — 市场强度指数
双重角色：进场漏斗 + 持仓风格切换器

输入（分主次）：
  1. 量能（前提变量）：两市成交额
  2. 赚钱效应（主变量）：昨日涨幅>5%股票今日平均涨幅 + 涨停结构
  3. 指数位置（背景变量）：上证 vs MA60

输出：强攻 / 观望 / 防守
"""

from dataclasses import dataclass
from enum import Enum

import pandas as pd
from loguru import logger


class MSIState(str, Enum):
    ATTACK = "强攻"
    WATCH = "观望"
    DEFENSE = "防守"


@dataclass
class MSIResult:
    state: MSIState
    volume_score: float      # 量能得分 0-1
    profit_effect: float     # 赚钱效应得分 0-1
    index_position: float    # 指数位置得分 0-1
    composite: float         # 综合分 0-1
    detail: str

    @property
    def consensus_threshold(self) -> str:
        if self.state == MSIState.ATTACK:
            return "2/3"
        elif self.state == MSIState.WATCH:
            return "3/3"
        return "禁止"

    @property
    def max_new_position_pct(self) -> float:
        if self.state == MSIState.ATTACK:
            return 0.20
        elif self.state == MSIState.WATCH:
            return 0.0
        return 0.0


def calc_volume_score(total_amount: float) -> float:
    """两市成交额评分（单位：亿元）"""
    if total_amount >= 12000:
        return 1.0
    elif total_amount >= 9000:
        return 0.7
    elif total_amount >= 7000:
        return 0.4
    return 0.1


def calc_profit_effect(
    yesterday_strong: pd.DataFrame,
    today_daily: pd.DataFrame,
) -> float:
    """
    赚钱效应：昨日涨幅>5%的股票今日平均涨幅
    yesterday_strong: 昨日涨幅>5%的股票列表（含 ts_code）
    today_daily: 今日全市场日线
    """
    if yesterday_strong.empty:
        return 0.0

    codes = yesterday_strong["ts_code"].tolist()
    today_subset = today_daily[today_daily["ts_code"].isin(codes)]
    if today_subset.empty:
        return 0.0

    avg_chg = today_subset["pct_chg"].mean()

    if avg_chg >= 2.0:
        return 1.0
    elif avg_chg >= 0.5:
        return 0.6
    elif avg_chg >= -1.0:
        return 0.3
    return 0.0


def calc_index_position(
    index_close: float,
    index_ma60: float,
) -> float:
    """指数位置：上证 vs MA60"""
    ratio = index_close / index_ma60 if index_ma60 > 0 else 1.0
    if ratio >= 1.03:
        return 1.0
    elif ratio >= 1.0:
        return 0.6
    elif ratio >= 0.97:
        return 0.3
    return 0.1


def compute_msi(
    total_amount: float,
    yesterday_strong: pd.DataFrame,
    today_daily: pd.DataFrame,
    index_close: float,
    index_ma60: float,
) -> MSIResult:
    """
    计算 MSI 综合状态
    权重：量能 0.3 + 赚钱效应 0.5 + 指数位置 0.2
    """
    v_score = calc_volume_score(total_amount)
    p_score = calc_profit_effect(yesterday_strong, today_daily)
    i_score = calc_index_position(index_close, index_ma60)

    composite = v_score * 0.3 + p_score * 0.5 + i_score * 0.2

    if v_score < 0.3:
        state = MSIState.DEFENSE
        detail = "量能不足，市场缺乏博弈基础"
    elif composite >= 0.65:
        state = MSIState.ATTACK
        detail = f"综合评分{composite:.2f}，允许进攻"
    elif composite >= 0.35:
        state = MSIState.WATCH
        detail = f"综合评分{composite:.2f}，仅观望"
    else:
        state = MSIState.DEFENSE
        detail = f"综合评分{composite:.2f}，防守模式"

    result = MSIResult(
        state=state,
        volume_score=v_score,
        profit_effect=p_score,
        index_position=i_score,
        composite=composite,
        detail=detail,
    )
    logger.info(f"MSI = {result.state.value} | {result.detail}")
    return result
