"""
Meta-Judge — 中央仲裁官
职责：
  1. 收集三系统密封结论
  2. 开封比对
  3. 根据 MSI/CGI 状态确定共识门槛
  4. 异议分级处理（soft / hard / veto）
  5. 少数派保护 + 暂停权
  6. 输出最终动作

核心原则：
  一致性不是目标，发现结构性分歧才是价值来源
"""

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from src.core.cgi import CGIState
from src.core.msi import MSIState

STALENESS_THRESHOLDS: dict[str, dict[str, int]] = {
    "market_data":  {"warning": 5,  "degraded": 15},
    "news_event":   {"warning": 30, "degraded": 60},
    "regulatory":   {"warning": 60, "degraded": 120},
    "shareholder":  {"warning": 60, "degraded": 180},
}

DEFAULT_STALENESS = {"warning": 10, "degraded": 30}


@dataclass
class ProtocolHealth:
    decision_path_length: int = 0
    block_rate: float = 0.0
    rule_conflict_count: int = 0
    avg_decision_latency_ms: float = 0.0

    @property
    def has_warning(self) -> bool:
        return (
            self.decision_path_length > 5
            or self.block_rate > 0.3
            or self.rule_conflict_count > 2
            or self.avg_decision_latency_ms > 2000
        )


@dataclass
class FinalDecision:
    action: str            # BUY / WAIT / SELL / REJECT / ABSTAIN
    position_pct: float
    stock_code: str
    timestamp: str
    msi_state: str
    cgi_state: str
    consensus: str
    minority_system: str
    minority_reason: str
    minority_ignored_reason: str
    verdicts: dict = field(default_factory=dict)
    detail: str = ""
    abstained_systems: list[str] = field(default_factory=list)
    staleness_warnings: list[str] = field(default_factory=list)
    protocol_health: ProtocolHealth = field(default_factory=ProtocolHealth)


class MetaJudge:
    """三系统仲裁 + 试探仓机制"""

    PROBE_POSITION_RATIO = 1 / 3

    def __init__(self) -> None:
        self._decision_count: int = 0
        self._block_count: int = 0
        self._conflict_count: int = 0
        self._total_latency_ms: float = 0.0

    @property
    def protocol_health(self) -> ProtocolHealth:
        n = max(self._decision_count, 1)
        return ProtocolHealth(
            decision_path_length=self._decision_count,
            block_rate=self._block_count / n,
            rule_conflict_count=self._conflict_count,
            avg_decision_latency_ms=self._total_latency_ms / n,
        )

    def arbitrate(
        self,
        stock_code: str,
        verdict_a,
        verdict_b,
        verdict_c,
        msi_state: MSIState,
        cgi_state: CGIState,
    ) -> FinalDecision:
        t0 = datetime.now()
        now = t0.isoformat()
        self._decision_count += 1

        staleness_warnings = self._check_staleness(verdict_a, verdict_b, verdict_c, t0)
        abstained = self._check_abstentions(verdict_a, verdict_b, verdict_c)

        if len(abstained) >= 2:
            self._block_count += 1
            self._record_latency(t0)
            return FinalDecision(
                action="ABSTAIN", position_pct=0.0, stock_code=stock_code,
                timestamp=now, msi_state=msi_state.value,
                cgi_state=cgi_state.value, consensus="N/A",
                minority_system="", minority_reason="",
                minority_ignored_reason="",
                detail=f"数据不可用系统≥2({','.join(abstained)})，整体弃权",
                abstained_systems=abstained,
                staleness_warnings=staleness_warnings,
                protocol_health=self.protocol_health,
            )

        if msi_state == MSIState.DEFENSE:
            self._block_count += 1
            self._record_latency(t0)
            return FinalDecision(
                action="REJECT", position_pct=0.0, stock_code=stock_code,
                timestamp=now, msi_state=msi_state.value,
                cgi_state=cgi_state.value, consensus="N/A",
                minority_system="", minority_reason="",
                minority_ignored_reason="",
                detail="MSI=防守，禁止新开仓",
                abstained_systems=abstained,
                staleness_warnings=staleness_warnings,
                protocol_health=self.protocol_health,
            )

        if cgi_state == CGIState.CROWDED_TRAP:
            self._block_count += 1
            self._record_latency(t0)
            return FinalDecision(
                action="REJECT", position_pct=0.0, stock_code=stock_code,
                timestamp=now, msi_state=msi_state.value,
                cgi_state=cgi_state.value, consensus="N/A",
                minority_system="", minority_reason="",
                minority_ignored_reason="",
                detail="CGI=拥挤陷阱，硬回避",
                abstained_systems=abstained,
                staleness_warnings=staleness_warnings,
                protocol_health=self.protocol_health,
            )

        veto = self._check_veto(verdict_a, verdict_b, verdict_c)
        if veto:
            self._block_count += 1
            self._record_latency(t0)
            return FinalDecision(
                action="REJECT", position_pct=0.0, stock_code=stock_code,
                timestamp=now, msi_state=msi_state.value,
                cgi_state=cgi_state.value, consensus="VETO",
                minority_system=veto["system"],
                minority_reason=veto["reason"],
                minority_ignored_reason="veto不可忽略",
                detail=f"System {veto['system']} 发出 veto: {veto['reason']}",
                abstained_systems=abstained,
                staleness_warnings=staleness_warnings,
                protocol_health=self.protocol_health,
            )

        buy_votes, minority = self._count_votes(verdict_a, verdict_b, verdict_c)
        threshold = self._get_threshold(msi_state, cgi_state)
        consensus_str = f"{buy_votes}/3"

        if buy_votes >= threshold:
            hard_obj = self._check_hard_objection(verdict_a, verdict_b, verdict_c)
            if hard_obj and staleness_warnings:
                self._conflict_count += 1
            position = self._calc_position(msi_state, cgi_state, hard_obj is not None)

            ignored_reason = ""
            if minority:
                ignored_reason = self._explain_ignore(msi_state, minority)

            self._record_latency(t0)
            return FinalDecision(
                action="BUY", position_pct=position,
                stock_code=stock_code, timestamp=now,
                msi_state=msi_state.value, cgi_state=cgi_state.value,
                consensus=consensus_str,
                minority_system=minority.get("system", "") if minority else "",
                minority_reason=minority.get("reason", "") if minority else "",
                minority_ignored_reason=ignored_reason,
                verdicts={
                    "A": verdict_a.signal,
                    "B": verdict_b.signal,
                    "C": verdict_c.signal,
                },
                detail=f"共识{consensus_str}达到门槛{threshold}/3，试探仓{position:.0%}",
                abstained_systems=abstained,
                staleness_warnings=staleness_warnings,
                protocol_health=self.protocol_health,
            )

        self._record_latency(t0)
        return FinalDecision(
            action="WAIT", position_pct=0.0, stock_code=stock_code,
            timestamp=now, msi_state=msi_state.value,
            cgi_state=cgi_state.value, consensus=consensus_str,
            minority_system=minority.get("system", "") if minority else "",
            minority_reason=minority.get("reason", "") if minority else "",
            minority_ignored_reason="共识未达门槛",
            detail=f"共识{consensus_str}未达门槛{threshold}/3",
            abstained_systems=abstained,
            staleness_warnings=staleness_warnings,
            protocol_health=self.protocol_health,
        )

    def _check_staleness(self, va, vb, vc, now: datetime) -> list[str]:
        warnings: list[str] = []
        for v in [va, vb, vc]:
            as_of = getattr(v, "as_of_time", "")
            if not as_of:
                warnings.append(f"System {v.system}: as_of_time 缺失")
                continue
            try:
                ts = datetime.fromisoformat(as_of)
                age_min = (now - ts).total_seconds() / 60
                domain = getattr(v, "data_domain", "")
                thresholds = STALENESS_THRESHOLDS.get(domain, DEFAULT_STALENESS)
                if age_min > thresholds["degraded"]:
                    warnings.append(
                        f"System {v.system}({domain}): "
                        f"数据延迟{age_min:.0f}min，超过降级阈值{thresholds['degraded']}min"
                    )
                elif age_min > thresholds["warning"]:
                    warnings.append(
                        f"System {v.system}({domain}): "
                        f"数据延迟{age_min:.0f}min，超过预警阈值{thresholds['warning']}min"
                    )
            except (ValueError, TypeError):
                warnings.append(f"System {v.system}: as_of_time 格式异常({as_of})")
        if warnings:
            for w in warnings:
                logger.warning(f"[staleness] {w}")
        return warnings

    def _check_abstentions(self, va, vb, vc) -> list[str]:
        abstained: list[str] = []
        for v in [va, vb, vc]:
            health = getattr(v, "data_health", None)
            if health is not None and health.value in ("stale", "missing"):
                logger.warning(f"System {v.system} 数据状态={health.value}，视为弃权")
                abstained.append(v.system)
        return abstained

    def _record_latency(self, t0: datetime) -> None:
        elapsed = (datetime.now() - t0).total_seconds() * 1000
        self._total_latency_ms += elapsed

    def _check_veto(self, va, vb, vc) -> dict | None:
        for v in [va, vb, vc]:
            if getattr(v, "objection", None) == "veto":
                return {
                    "system": v.system,
                    "reason": getattr(v, "objection_reason", "veto触发"),
                }
        return None

    def _check_hard_objection(self, va, vb, vc) -> dict | None:
        for v in [va, vb, vc]:
            if getattr(v, "objection", None) == "hard":
                return {
                    "system": v.system,
                    "reason": getattr(v, "objection_reason", "hard反对"),
                }
        return None

    def _count_votes(self, va, vb, vc) -> tuple[int, dict | None]:
        votes = 0
        minority = None
        for v in [va, vb, vc]:
            if v.signal == "BUY":
                votes += 1
            else:
                minority = {"system": v.system, "reason": v.reasoning}
        return votes, minority

    def _get_threshold(self, msi: MSIState, cgi: CGIState) -> int:
        if msi == MSIState.ATTACK and cgi in (CGIState.UNIFIED_ATTACK, CGIState.UNICORN):
            return 2
        return 3

    def _calc_position(self, msi: MSIState, cgi: CGIState, has_hard: bool) -> float:
        base = msi.value == MSIState.ATTACK.value and 0.20 or 0.10
        if cgi == CGIState.UNICORN:
            base *= 0.7
        if has_hard:
            base *= 0.5
        return round(base * self.PROBE_POSITION_RATIO, 4)

    def _explain_ignore(self, msi: MSIState, minority: dict) -> str:
        if msi == MSIState.ATTACK:
            return f"MSI=强攻，门槛2/3，少数派(System {minority['system']})的反对被记录但允许执行"
        return f"少数派(System {minority['system']})意见已记录"
