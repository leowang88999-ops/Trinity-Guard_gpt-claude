"""
CGI — 资金博弈指数
四种博弈态：一致进攻 / 独角龙 / 补涨末端 / 拥挤陷阱

核心问题：热度是否还能转化成利润？
"""

from dataclasses import dataclass
from enum import Enum

from loguru import logger


class CGIState(str, Enum):
    UNIFIED_ATTACK = "一致进攻"
    UNICORN = "独角龙"
    LATE_FOLLOW = "补涨末端"
    CROWDED_TRAP = "拥挤陷阱"


@dataclass
class CGIResult:
    state: CGIState
    leader_strong: bool       # 龙头是否继续强化
    follower_effective: bool  # 跟风是否有效
    crowding_negative: bool   # 拥挤是否进入负反馈
    detail: str

    @property
    def action_constraint(self) -> str:
        m = {
            CGIState.UNIFIED_ATTACK: "可做龙头 + 核心补涨",
            CGIState.UNICORN: "只做龙头，禁做扩散",
            CGIState.LATE_FOLLOW: "警惕龙头见顶，禁做后排",
            CGIState.CROWDED_TRAP: "硬回避，禁止博弈",
        }
        return m[self.state]


def compute_cgi(
    leader_status: str,
    follower_profit: float,
    breakout_count: int,
    limit_up_quality: float,
    tail_anomaly: bool,
) -> CGIResult:
    """
    计算 CGI 博弈态

    参数：
        leader_status: "涨停" / "分歧承接" / "走弱"
        follower_profit: 跟风股平均盈利（%）
        breakout_count: 当日突破股票数量
        limit_up_quality: 封板质量（封单额/成交额比值）
        tail_anomaly: 尾盘异动（放量滞涨等）
    """
    leader_strong = leader_status in ("涨停", "分歧承接")
    follower_effective = follower_profit > 1.0

    crowding_negative = (
        breakout_count > 50 and limit_up_quality < 0.3
    ) or tail_anomaly

    if leader_strong and follower_effective and not crowding_negative:
        state = CGIState.UNIFIED_ATTACK
        detail = "龙头强+跟风强+拥挤可控"
    elif leader_strong and not follower_effective:
        state = CGIState.UNICORN
        detail = "龙头强但跟风弱，集中博弈市场"
    elif not leader_strong and follower_effective:
        state = CGIState.LATE_FOLLOW
        detail = "龙头弱+跟风强，情绪扩散末端"
    else:
        state = CGIState.CROWDED_TRAP
        detail = f"伪繁荣环境 | 突破数={breakout_count}, 封板质量={limit_up_quality:.2f}"

    result = CGIResult(
        state=state,
        leader_strong=leader_strong,
        follower_effective=follower_effective,
        crowding_negative=crowding_negative,
        detail=detail,
    )
    logger.info(f"CGI = {result.state.value} | {result.detail}")
    return result
