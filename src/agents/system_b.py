"""
System B — 量化执行（Quant-Centric）
信息域：量价、节奏、波动、技术形态
认知视角：量化交易员 / 数理回测

5 角色：数据采集官-B / 趋势分析官 / 量化分析官 / 空头分析官 / CDO-B
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pandas as pd
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
    data_domain: str = "market_data"
    data_health: DataHealth = DataHealth.OK
    as_of_time: str = ""
    technical_flags: dict = field(default_factory=dict)
    objection: str | None = None
    objection_reason: str = ""
    anti_thesis: str = ""
    raw: dict = field(default_factory=dict)


class SystemB:
    """System B 骨架：量价 → 形态 → 策略信号 → 密封结论"""

    def __init__(self, llm_client=None):
        self.llm = llm_client

    def compute_technical(self, hist: pd.DataFrame) -> dict:
        """
        本地计算技术指标，不依赖 LLM

        Trinity Alpha Momentum 核心逻辑：
        - MA20 生命线
        - 量比 > 1.5
        - ATR 动态止损
        - MA5 动态止盈
        """
        if hist is None or len(hist) < 20:
            return {"valid": False, "reason": "历史数据不足20天"}

        h = hist.sort_values("trade_date").reset_index(drop=True)
        current = h.iloc[-1]

        ma20 = h["close"].rolling(20).mean().iloc[-1]
        ma5 = h["close"].rolling(5).mean().iloc[-1]
        avg_vol_5 = h["vol"].iloc[-6:-1].mean()
        atr_14 = (h["high"] - h["low"]).rolling(14).mean().iloc[-1]
        high_10 = h["close"].iloc[-11:-1].max()

        trend_ok = current["close"] > ma20
        volume_ok = current["vol"] > (avg_vol_5 * 1.5)
        pct_ok = 2.0 <= current["pct_chg"] <= 7.0
        box_breakout = current["close"] > high_10

        signal = "BUY" if (trend_ok and volume_ok and pct_ok and box_breakout) else "WAIT"

        return {
            "valid": True,
            "signal": signal,
            "close": current["close"],
            "ma20": round(ma20, 2),
            "ma5": round(ma5, 2),
            "atr_14": round(atr_14, 2),
            "volume_ratio": round(current["vol"] / avg_vol_5, 2) if avg_vol_5 > 0 else 0,
            "trend_ok": trend_ok,
            "volume_ok": volume_ok,
            "pct_ok": pct_ok,
            "box_breakout": box_breakout,
            "stop_loss": round(current["close"] - 2 * atr_14, 2),
            "trailing_stop_ma5": round(ma5, 2),
        }

    async def analyze(self, stock_code: str, hist: pd.DataFrame, context: dict) -> SealedVerdict:
        """
        System B 独立分析：本地技术 + LLM 综合判断
        """
        tech = self.compute_technical(hist)

        if self.llm is None:
            return self._local_verdict(stock_code, tech)

        prompt = self._build_prompt(stock_code, tech, context)
        resp = await self.llm.chat(model="claude-4.6-sonnet", prompt=prompt)

        try:
            data = json.loads(resp)
            now = datetime.now().isoformat()
            health = DataHealth.OK if tech.get("valid") else DataHealth.PARTIAL
            return SealedVerdict(
                system="B",
                timestamp=now,
                signal=data.get("signal", tech.get("signal", "WAIT")),
                confidence=discretize_confidence(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
                data_health=health,
                as_of_time=now,
                technical_flags=tech,
                objection=data.get("objection"),
                objection_reason=data.get("objection_reason", ""),
                anti_thesis=data.get("anti_thesis", ""),
                raw=data,
            )
        except (json.JSONDecodeError, TypeError):
            logger.error(f"System B LLM 解析失败: {resp}")
            return self._local_verdict(stock_code, tech)

    def _build_prompt(self, stock_code: str, tech: dict, context: dict) -> str:
        return (
            "你是 Trinity Guard 的 System B（量化执行系统）的首席决策官。\n"
            "你的信息域：量价、节奏、波动。不要看政策新闻。\n"
            "你必须独立判断，不知道 System A/C 的意见。\n\n"
            f"标的：{stock_code}\n"
            f"技术指标：{json.dumps(tech, ensure_ascii=False)}\n"
            f"市场上下文：{json.dumps(context, ensure_ascii=False)}\n\n"
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

    def _local_verdict(self, stock_code: str, tech: dict) -> SealedVerdict:
        sig = tech.get("signal", "WAIT") if tech.get("valid") else "WAIT"
        now = datetime.now().isoformat()
        health = DataHealth.OK if tech.get("valid") else DataHealth.PARTIAL
        return SealedVerdict(
            system="B",
            timestamp=now,
            signal=sig,
            confidence=ConfidenceLevel.HIGH if sig == "BUY" else ConfidenceLevel.LOW,
            reasoning=f"[LOCAL] 技术判断: {sig}",
            data_health=health,
            as_of_time=now,
            technical_flags=tech,
        )
