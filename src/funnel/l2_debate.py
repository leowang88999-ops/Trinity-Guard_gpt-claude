"""L2 三系统辩论入口：并行调用 A/B/C，收集密封结论后统一开封裁决。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pandas as pd
from loguru import logger

from src.agents.system_a import SystemA
from src.agents.system_b import SystemB
from src.agents.system_c import SystemC


@dataclass
class ExecutionRiskProfile:
    """
    执行风险调制层输出（F类执行因子）
    不是预测涨跌，而是约束执行姿态
    """
    stock_code: str
    liquidity_score: float = 1.0
    slippage_risk: float = 0.0
    impact_cost_pct: float = 0.0
    t1_exit_difficulty: float = 0.0
    risk_trigger_prob: float = 0.0
    manipulation_risk_score: float = 0.0

    allow_entry: bool = True
    max_position_pct: float = 0.20
    allow_chase: bool = False
    min_hold_bars: int = 1
    max_hold_bars: int = 5
    min_confirm_strength: float = 0.6
    probe_only: bool = False

    composite_risk: float = 0.0
    risk_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "stock_code": self.stock_code,
            "allow_entry": self.allow_entry,
            "max_position_pct": self.max_position_pct,
            "allow_chase": self.allow_chase,
            "probe_only": self.probe_only,
            "composite_risk": self.composite_risk,
            "risk_reason": self.risk_reason,
        }


def compute_execution_risk(
    row: pd.Series,
    msi_state: str,
    cgi_state: str,
) -> ExecutionRiskProfile:
    """
    F类执行因子计算：流动性/滑点/冲击成本/T+1约束/操纵风险
    输出执行约束，不输出涨跌预测
    """
    code = row.get("ts_code", "")
    amount = float(row.get("amount", 0))
    pct_chg = float(row.get("pct_chg", 0))
    turnover = float(row.get("turnover_rate", 0))
    close = float(row.get("close", 10))

    liquidity_score = min(amount / 5000.0, 1.0) if amount > 0 else 0.1

    slippage_risk = 0.0
    if amount < 1000:
        slippage_risk += 0.4
    if turnover > 30:
        slippage_risk += 0.3
    slippage_risk = min(slippage_risk, 1.0)

    impact_cost_pct = 0.002
    if amount > 0:
        impact_cost_pct = min(0.05, 500_000 / (amount * 1e4) * close)

    t1_exit_difficulty = 0.0
    if pct_chg > 9.0:
        t1_exit_difficulty = 0.8
    elif pct_chg > 5.0:
        t1_exit_difficulty = 0.4
    elif liquidity_score < 0.3:
        t1_exit_difficulty = 0.5

    manipulation_risk_score = 0.0
    if turnover > 50 and pct_chg > 8.0:
        manipulation_risk_score += 0.5
    if amount < 500 and pct_chg > 5.0:
        manipulation_risk_score += 0.3
    manipulation_risk_score = min(manipulation_risk_score, 1.0)

    risk_trigger_prob = 0.0
    if cgi_state in ("补涨末端", "拥挤陷阱"):
        risk_trigger_prob += 0.3
    if msi_state == "观望":
        risk_trigger_prob += 0.2
    risk_trigger_prob = min(risk_trigger_prob, 1.0)

    composite_risk = (
        slippage_risk * 0.2
        + t1_exit_difficulty * 0.3
        + manipulation_risk_score * 0.3
        + risk_trigger_prob * 0.2
    )

    profile = ExecutionRiskProfile(
        stock_code=code,
        liquidity_score=liquidity_score,
        slippage_risk=slippage_risk,
        impact_cost_pct=impact_cost_pct,
        t1_exit_difficulty=t1_exit_difficulty,
        manipulation_risk_score=manipulation_risk_score,
        risk_trigger_prob=risk_trigger_prob,
        composite_risk=composite_risk,
    )

    if composite_risk >= 0.7:
        profile.allow_entry = False
        profile.risk_reason = f"综合执行风险过高({composite_risk:.2f})"
    elif composite_risk >= 0.5:
        profile.probe_only = True
        profile.max_position_pct = 0.07
        profile.allow_chase = False
        profile.risk_reason = f"中高执行风险({composite_risk:.2f})，仅允许试探仓"
    elif composite_risk >= 0.3:
        profile.max_position_pct = 0.13
        profile.allow_chase = False
        profile.risk_reason = f"中等执行风险({composite_risk:.2f})，降低仓位"
    else:
        profile.risk_reason = f"执行风险可控({composite_risk:.2f})"

    if manipulation_risk_score >= 0.6:
        profile.allow_entry = False
        profile.risk_reason = f"操纵风险过高({manipulation_risk_score:.2f})"

    return profile


async def _call_system_a(
    sys_a: SystemA,
    stock_code: str,
    context: dict,
) -> Any:
    """System A 政策视角分析（真实LLM调用）"""
    try:
        return await sys_a.analyze(stock_code, context)
    except Exception as e:
        logger.error(f"System A 调用异常 {stock_code}: {e}")
        return None


async def _call_system_b(
    sys_b: SystemB,
    stock_code: str,
    quant_data: pd.DataFrame,
    context: dict,
) -> Any:
    """System B 量价视角分析（真实LLM调用）"""
    try:
        return await sys_b.analyze(stock_code, quant_data, context)
    except Exception as e:
        logger.error(f"System B 调用异常 {stock_code}: {e}")
        return None


async def _call_system_c(
    sys_c: SystemC,
    stock_code: str,
    structure_data: dict,
    risk_data: dict,
    context: dict,
) -> Any:
    """System C 对抗审计视角分析（真实LLM调用）"""
    try:
        return await sys_c.analyze(stock_code, structure_data, risk_data, context)
    except Exception as e:
        logger.error(f"System C 调用异常 {stock_code}: {e}")
        return None


def _apply_execution_risk_filter(
    approved: list[dict],
    risk_profiles: dict[str, ExecutionRiskProfile],
) -> list[dict]:
    """
    执行风险调制：在共识通过后，用F类因子约束执行姿态
    优先级链：硬风控 > 执行风险调制 > MetaJudge共识 > 仓位计算
    """
    result = []
    for item in approved:
        code = item.get("stock_code", "")
        profile = risk_profiles.get(code)
        if profile is None:
            result.append(item)
            continue

        if not profile.allow_entry:
            logger.info(f"  执行风险调制拒绝: {code} — {profile.risk_reason}")
            continue

        item["execution_risk"] = profile.to_dict()
        item["probe_only"] = profile.probe_only
        if profile.probe_only:
            item["position_pct"] = min(
                item.get("position_pct", 0.20),
                profile.max_position_pct,
            )
            logger.info(f"  执行风险调制降仓: {code} → {item['position_pct']:.0%} — {profile.risk_reason}")
        result.append(item)
    return result


async def run_l2_debate(
    candidates: pd.DataFrame,
    msi_state: str,
    cgi_state: str,
    sys_a: SystemA | None = None,
    sys_b: SystemB | None = None,
    sys_c: SystemC | None = None,
    meta_judge=None,
) -> list[dict]:
    """
    L2 三系统辩论主入口。

    并行调用 A/B/C，收集密封结论，统一开封后交给 meta_judge 裁决，
    最后经执行风险调制层（F类因子）约束执行姿态。

    Args:
        candidates: L1 输出
        msi_state: 市场强度指数状态
        cgi_state: 资金博弈指数状态
        sys_a/b/c: 三系统实例（由主引擎注入）
        meta_judge: MetaJudge实例

    Returns:
        通过共识且经执行风险调制的标的列表
    """
    if candidates.empty:
        return []

    if sys_a is None:
        sys_a = SystemA()
    if sys_b is None:
        sys_b = SystemB()
    if sys_c is None:
        sys_c = SystemC()

    approved: list[dict] = []
    risk_profiles: dict[str, ExecutionRiskProfile] = {}

    for _, row in candidates.iterrows():
        stock_code = row.get("ts_code", "")

        exec_risk = compute_execution_risk(row, msi_state, cgi_state)
        risk_profiles[stock_code] = exec_risk

        if not exec_risk.allow_entry:
            logger.info(f"  F类因子预筛除: {stock_code} — {exec_risk.risk_reason}")
            continue

        context = {
            "msi_state": msi_state,
            "cgi_state": cgi_state,
            "stock_info": {
                "code": stock_code,
                "name": row.get("name", ""),
                "pct_chg": row.get("pct_chg", 0),
                "amount": row.get("amount", 0),
                "industry": row.get("industry", ""),
            },
            "execution_risk": exec_risk.to_dict(),
        }

        structure_data = {
            "leader_pct_chg": float(row.get("pct_chg", 0)),
            "sector_consistency": 0.5,
        }
        risk_data = {
            "daily_amount": float(row.get("amount", 0)),
            "manipulation_risk": exec_risk.manipulation_risk_score,
        }

        va, vb, vc = await asyncio.gather(
            _call_system_a(sys_a, stock_code, context),
            _call_system_b(sys_b, stock_code, pd.DataFrame(), context),
            _call_system_c(sys_c, stock_code, structure_data, risk_data, context),
        )

        if va is None or vb is None or vc is None:
            logger.warning(f"  {stock_code} 系统调用部分失败，跳过")
            continue

        logger.info(f"  三系统密封结论已收集: {stock_code}，执行开封")

        if meta_judge is not None:
            from src.core.msi import MSIState
            from src.core.cgi import CGIState
            try:
                msi_enum = MSIState(msi_state)
                cgi_enum = CGIState(cgi_state)
            except ValueError:
                msi_enum = MSIState.DEFENSE
                cgi_enum = CGIState.CROWDED_TRAP

            decision = meta_judge.arbitrate(
                stock_code, va, vb, vc, msi_enum, cgi_enum
            )

            if decision.action == "BUY":
                approved.append({
                    "stock_code": stock_code,
                    "action": decision.action,
                    "position_pct": decision.position_pct,
                    "consensus": decision.consensus,
                    "verdicts": decision.verdicts,
                    "detail": decision.detail,
                    "row": row.to_dict(),
                })
        else:
            signals = [v.signal for v in [va, vb, vc]]
            buy_count = signals.count("BUY")
            if buy_count >= 2:
                approved.append({
                    "stock_code": stock_code,
                    "action": "BUY",
                    "position_pct": 0.20 / 3,
                    "consensus": f"{buy_count}/3",
                    "verdicts": {"A": va.signal, "B": vb.signal, "C": vc.signal},
                    "detail": f"简单多数通过({buy_count}/3)",
                    "row": row.to_dict(),
                })

    approved = _apply_execution_risk_filter(approved, risk_profiles)

    logger.info(f"L2辩论完成: {len(candidates)}只候选 → {len(approved)}只通过")
    return approved
