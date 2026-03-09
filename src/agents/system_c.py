"""
System C — 对抗审计（Adversarial-Audit）
信息域：市场结构、板块联动、龙头与跟风关系、流动性
认知视角：做空研究员 / 风险审计师

5 角色：数据探针-C / 压力测试官 / 反对派观察者 / 行政观察官 / CDO-C

核心职责：
  - Devil's Advocate 必须提供反向证据
  - 龙头关联度校验
  - 流动性风险审计
  - 失败模式库维护
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from loguru import logger


class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DataHealth(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    STALE = "stale"
    MISSING = "missing"


def discretize_confidence(raw: float) -> ConfidenceLevel:
    if raw >= 0.7:
        return ConfidenceLevel.HIGH
    if raw >= 0.4:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


@dataclass
class SealedVerdict:
    system: str
    timestamp: str
    signal: str
    confidence: ConfidenceLevel
    reasoning: str
    devils_advocate_reason: str
    sector_leader_status: str
    liquidity_assessment: str
    data_domain: str = "regulatory"
    data_health: DataHealth = DataHealth.OK
    as_of_time: str = ""
    objection: str | None = None
    objection_reason: str = ""
    raw: dict = field(default_factory=dict)


class SystemC:
    """System C 骨架：结构 → 风险 → 反驳 → 密封结论"""

    def __init__(self, llm_client=None):
        self.llm = llm_client

    async def analyze(
        self,
        stock_code: str,
        leader_info: dict,
        liquidity_info: dict,
        context: dict,
    ) -> SealedVerdict:
        """
        System C 独立审计

        leader_info: {"leader_code", "leader_pct_chg", "sector_consistency"}
        liquidity_info: {"daily_amount", "seal_ratio", "near_limit_down"}
        """
        if self.llm is None:
            return self._mock_verdict(stock_code, leader_info, liquidity_info)

        prompt = self._build_prompt(stock_code, leader_info, liquidity_info, context)
        resp = await self.llm.chat(model="gemini-2.5-pro", prompt=prompt)

        try:
            data = json.loads(resp)

            now = datetime.now().isoformat()
            da_reason = data.get("devils_advocate_reason", "")
            if not da_reason or len(da_reason) < 10:
                logger.warning("System C 未提供有效反向证据，结论驳回")
                return SealedVerdict(
                    system="C",
                    timestamp=now,
                    signal="REJECT",
                    confidence=ConfidenceLevel.LOW,
                    reasoning="反向证据缺失，决策驳回",
                    devils_advocate_reason="[驳回] 未提供有效反向证据",
                    sector_leader_status=data.get("sector_leader_status", "未知"),
                    liquidity_assessment=data.get("liquidity_assessment", "未知"),
                    data_health=DataHealth.OK,
                    as_of_time=now,
                    objection="veto",
                    objection_reason="Devil's Advocate 义务未履行",
                    raw=data,
                )

            return SealedVerdict(
                system="C",
                timestamp=now,
                signal=data.get("signal", "WAIT"),
                confidence=discretize_confidence(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
                devils_advocate_reason=da_reason,
                sector_leader_status=data.get("sector_leader_status", ""),
                liquidity_assessment=data.get("liquidity_assessment", ""),
                data_health=DataHealth.OK,
                as_of_time=now,
                objection=data.get("objection"),
                objection_reason=data.get("objection_reason", ""),
                raw=data,
            )
        except (json.JSONDecodeError, TypeError):
            logger.error(f"System C LLM 解析失败: {resp}")
            return self._mock_verdict(stock_code, leader_info, liquidity_info)

    def _build_prompt(self, stock_code: str, leader_info: dict,
                      liquidity_info: dict, context: dict) -> str:
        return (
            "你是 Trinity Guard 的 System C（对抗审计系统）的首席决策官。\n"
            "你的信息域：市场结构、板块联动、龙头与跟风关系、流动性。\n"
            "你的核心职责是 Devil's Advocate — 必须找到反向证据。\n"
            "你必须独立判断，不知道 System A/B 的意见。\n\n"
            f"标的：{stock_code}\n"
            f"板块龙头信息：{json.dumps(leader_info, ensure_ascii=False)}\n"
            f"流动性信息：{json.dumps(liquidity_info, ensure_ascii=False)}\n"
            f"上下文：{json.dumps(context, ensure_ascii=False)}\n\n"
            "【强制】devils_advocate_reason 字段不可为空，"
            "必须基于硬数据（主力资金流向、大股东行为、历史阻力位等）。\n\n"
            "请输出 JSON：\n"
            "{\n"
            '  "signal": "BUY/WAIT/SELL/REJECT",\n'
            '  "confidence": 0.0-1.0,\n'
            '  "reasoning": "...",\n'
            '  "devils_advocate_reason": "具体的反向证据",\n'
            '  "sector_leader_status": "涨停/分歧/走弱/跌停",\n'
            '  "liquidity_assessment": "充裕/一般/危险",\n'
            '  "objection": null/"soft"/"hard"/"veto",\n'
            '  "objection_reason": "..."\n'
            "}"
        )

    def _mock_verdict(self, stock_code: str, leader_info: dict,
                      liquidity_info: dict) -> SealedVerdict:
        now = datetime.now().isoformat()
        leader_pct = leader_info.get("leader_pct_chg", 0)
        if leader_pct < -7:
            objection = "veto"
            signal = "REJECT"
            reason = "板块龙头跌幅>7%，熔断同板块多头指令"
        else:
            objection = None
            signal = "WAIT"
            reason = "[MOCK] LLM 未连接，默认审计观望"

        return SealedVerdict(
            system="C",
            timestamp=now,
            signal=signal,
            confidence=ConfidenceLevel.LOW,
            reasoning=reason,
            devils_advocate_reason="[MOCK] 需要 LLM 生成真实反向证据",
            sector_leader_status=f"龙头涨幅{leader_pct:.1f}%",
            liquidity_assessment="未评估",
            data_health=DataHealth.MISSING,
            as_of_time=now,
            objection=objection,
            objection_reason=reason if objection else "",
        )
