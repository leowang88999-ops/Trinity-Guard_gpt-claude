"""
LRI — 流动性风险指数
定位：硬性二元判断，不做打分，只做硬否决

服务于一句话：系统能不能安全进出？
"""

from dataclasses import dataclass

from loguru import logger

MIN_DAILY_AMOUNT = 5000  # 万元，模拟盘阈值
MIN_RELATIVE_ACTIVITY = 0.5  # 当前成交额 / 30日中位成交额


@dataclass
class LRIResult:
    allowed: bool
    reason: str
    daily_amount: float
    relative_activity: float
    impact_ratio: float  # 影子指标：计划仓位 / 可成交金额


def compute_lri(
    daily_amount: float,
    median_amount_30d: float,
    near_limit_down: bool,
    seal_fragile: bool,
    volume_shrink: bool,
    one_word_board: bool,
    planned_buy_amount: float = 0.0,
    recent_minute_amount: float = 0.0,
) -> LRIResult:
    """
    LRI 硬否决检查

    参数：
        daily_amount: 日成交额（万元）
        median_amount_30d: 30日中位成交额（万元）
        near_limit_down: 接近跌停
        seal_fragile: 封单脆弱
        volume_shrink: 成交萎缩明显
        one_word_board: 一字/准一字无法交易
        planned_buy_amount: 计划买入金额（万元），影子指标
        recent_minute_amount: 近N分钟可成交金额（万元），影子指标
    """
    relative_activity = (
        daily_amount / median_amount_30d
        if median_amount_30d > 0 else 0.0
    )

    impact_ratio = (
        planned_buy_amount / recent_minute_amount
        if recent_minute_amount > 0 else 999.0
    )

    if daily_amount < MIN_DAILY_AMOUNT:
        return LRIResult(False, f"日成交额{daily_amount:.0f}万 < {MIN_DAILY_AMOUNT}万",
                         daily_amount, relative_activity, impact_ratio)

    if relative_activity < MIN_RELATIVE_ACTIVITY:
        return LRIResult(False, f"相对活跃度{relative_activity:.2f} < {MIN_RELATIVE_ACTIVITY}",
                         daily_amount, relative_activity, impact_ratio)

    if near_limit_down:
        return LRIResult(False, "接近跌停", daily_amount, relative_activity, impact_ratio)

    if seal_fragile:
        return LRIResult(False, "封单脆弱", daily_amount, relative_activity, impact_ratio)

    if volume_shrink:
        return LRIResult(False, "成交萎缩明显", daily_amount, relative_activity, impact_ratio)

    if one_word_board:
        return LRIResult(False, "一字/准一字无法交易", daily_amount, relative_activity, impact_ratio)

    logger.debug(f"LRI 通过 | 成交额={daily_amount:.0f}万, 活跃度={relative_activity:.2f}, 冲击比={impact_ratio:.2f}")
    return LRIResult(True, "通过", daily_amount, relative_activity, impact_ratio)
