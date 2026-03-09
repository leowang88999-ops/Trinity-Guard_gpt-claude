"""
System A — 政策高度（Policy-Centric）
信息域：政策、监管、叙事、事件
认知视角：政策分析师 / 宏观策略师

5 角色：数据采集官 / 舆情管理官 / 多头分析官 / 战略承接官 / CDO-A
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
    """密封结论 — 开封前不可见"""
    system: str
    timestamp: str
    signal: str                     # BUY / WAIT / SELL
    target_sectors: list[str]       # 今日目标行业清单
    confidence: ConfidenceLevel     # low / medium / high
    reasoning: str
    data_domain: str = "news_event"
    data_health: DataHealth = DataHealth.OK
    as_of_time: str = ""            # 数据截止时间 ISO-8601
    objection: str | None = None    # soft / hard / veto
    objection_reason: str = ""
    anti_thesis: str = ""           # 反向证据（A 系统也需自省）
    raw: dict = field(default_factory=dict)


class SystemA:
    """System A 骨架：政策 → 叙事 → 行业 → 密封结论"""

    def __init__(self, llm_client=None):
        self.llm = llm_client

    async def generate_target_sectors(self, news_summary: str) -> list[str]:
        """9:15 前由战略承接官产出今日核心受益行业清单"""
        if self.llm is None:
            logger.warning("LLM 未配置，返回默认行业")
            return ["半导体", "计算机应用"]

        prompt = (
            "你是 A 股政策分析师。根据以下新闻摘要，输出今日最可能受益的申万二级行业清单。"
            "只返回 JSON 数组，如 [\"半导体\", \"计算机应用\"]。\n\n"
            f"新闻摘要：\n{news_summary}"
        )
        resp = await self.llm.chat(model="gemini-2.5-pro", prompt=prompt)
        try:
            return json.loads(resp)
        except (json.JSONDecodeError, TypeError):
            logger.error(f"行业清单解析失败: {resp}")
            return []

    async def analyze(self, stock_code: str, context: dict) -> SealedVerdict:
        """
        System A 独立分析：密封结论
        context 包含：news, policy_events, sector_info 等
        """
        if self.llm is None:
            return self._mock_verdict(stock_code, context)

        prompt = self._build_prompt(stock_code, context)
        resp = await self.llm.chat(model="gemini-2.5-pro", prompt=prompt)

        try:
            data = json.loads(resp)
            now = datetime.now().isoformat()
            return SealedVerdict(
                system="A",
                timestamp=now,
                signal=data.get("signal", "WAIT"),
                target_sectors=data.get("target_sectors", []),
                confidence=discretize_confidence(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
                data_health=DataHealth.OK,
                as_of_time=now,
                objection=data.get("objection"),
                objection_reason=data.get("objection_reason", ""),
                anti_thesis=data.get("anti_thesis", ""),
                raw=data,
            )
        except (json.JSONDecodeError, TypeError):
            logger.error(f"System A 解析失败: {resp}")
            return self._mock_verdict(stock_code, context)

    def _build_prompt(self, stock_code: str, context: dict) -> str:
        return (
            "你是 Trinity Guard 的 System A（政策高度系统）的首席决策官。\n"
            "你的信息域：政策、监管、叙事、事件。不要看量价数据。\n"
            "你必须独立判断，不知道 System B/C 的意见。\n\n"
            f"标的：{stock_code}\n"
            f"政策/新闻上下文：{json.dumps(context, ensure_ascii=False)}\n\n"
            "请输出 JSON：\n"
            "{\n"
            '  "signal": "BUY/WAIT/SELL",\n'
            '  "confidence": 0.0-1.0,\n'
            '  "reasoning": "...",\n'
            '  "objection": null/"soft"/"hard"/"veto",\n'
            '  "objection_reason": "...",\n'
            '  "anti_thesis": "自我反驳的理由"\n'
            "}"
        )

    def _mock_verdict(self, stock_code: str, context: dict) -> SealedVerdict:
        now = datetime.now().isoformat()
        return SealedVerdict(
            system="A",
            timestamp=now,
            signal="WAIT",
            target_sectors=context.get("target_sectors", []),
            confidence=ConfidenceLevel.MEDIUM,
            reasoning="[MOCK] LLM 未连接，默认观望",
            data_health=DataHealth.MISSING,
            as_of_time=now,
        )
