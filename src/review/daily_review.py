"""
DailyReview — 盘后复盘机
职责：
  1. 读取当日所有交易 + context snapshot
  2. 错误归因（环境/结构/执行/流动性/叙事）
  3. 生成候选修正（不直接推送实盘）
  4. 监控系统间相关性（防退化）
  5. 生成简版日报（对外）+ 完整复盘（后台）
  6. 错过机会分析（missedOpp）
  7. 收益质量评估
  8. 弃权率统计
  9. 多标签样本生成（正/负/反事实）
  10. 反事实评估（MetaJudge 1/3 试探仓分析）
  11. P级参数晋升/降级/退役触发器
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from enum import Enum
from pathlib import Path
from loguru import logger

try:
    sys.path.insert(0, "/app/shared")
    from storage.qiniu_storage import QiniuStorage
    _storage = QiniuStorage(bucket_name="trinity-proddev-data")
except Exception:
    _storage = None


class FixType(str, Enum):
    PROMPT = "prompt"
    RULE = "rule"
    ENGINE = "engine"
    PIPELINE = "pipeline"


class SampleLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    COUNTERFACTUAL = "counterfactual"
    AMBIGUOUS = "ambiguous"


class EvoAction(str, Enum):
    PROMOTE = "promote"
    DEMOTE = "demote"
    RETIRE = "retire"
    HOLD = "hold"


class DailyReview:
    """盘后复盘与进化"""

    ERROR_CATEGORIES = [
        "环境误判",
        "结构误判",
        "执行误判",
        "流动性误判",
        "叙事误判",
    ]

    PROMOTE_WIN_RATE = 0.60
    PROMOTE_PL_RATIO = 2.0
    PROMOTE_MIN_TRADES = 10

    DEMOTE_WIN_RATE = 0.40
    DEMOTE_PL_RATIO = 0.8
    DEMOTE_MIN_TRADES = 5

    RETIRE_CONSECUTIVE_DEMOTE = 3

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.review_dir = self.data_dir / "review"
        self.sample_dir = self.data_dir / "samples"
        self.review_dir.mkdir(parents=True, exist_ok=True)
        self.sample_dir.mkdir(parents=True, exist_ok=True)

    def run_review(
        self,
        orders: list[dict],
        positions: list[dict],
        msi_history: list[dict],
        cgi_history: list[dict],
        verdicts_history: list[dict],
        market_opportunities: list[dict] | None = None,
        session_results: dict[str, list[dict]] | None = None,
    ) -> dict:
        today = date.today().isoformat()

        pnl = self._calc_pnl(orders)
        error_analysis = self._analyze_errors(orders, msi_history, cgi_history)
        consensus_stats = self._check_consensus_health(verdicts_history)
        candidate_fixes = self._generate_candidate_fixes(error_analysis)
        missed_opps = self._analyze_missed_opportunities(
            orders, verdicts_history, market_opportunities or [],
        )
        profit_quality = self._evaluate_profit_quality(orders, pnl)
        abstention_rate = self._calc_abstention_rate(verdicts_history)

        samples = self._generate_samples(
            orders, verdicts_history, market_opportunities or [], pnl,
        )
        counterfactual = self._evaluate_counterfactual(
            orders, verdicts_history, session_results or {},
        )
        evo_signals = self._compute_evo_signals(profit_quality, pnl, today)

        report = {
            "date": today,
            "pnl_summary": pnl,
            "error_analysis": error_analysis,
            "consensus_health": consensus_stats,
            "candidate_fixes": candidate_fixes,
            "missed_opportunities": missed_opps,
            "profit_quality": profit_quality,
            "abstention_rate": abstention_rate,
            "samples": samples,
            "counterfactual_eval": counterfactual,
            "evo_signals": evo_signals,
            "positions_eod": positions,
        }

        report_file = self.review_dir / f"review_{today}.json"
        report_file.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"复盘报告已保存: {report_file}")
        if _storage:
            date_key = today.replace("-", "")
            remote_key = QiniuStorage.make_key("review", date_key, f"review_{today}.json")
            _storage.save_json(remote_key, report)
            logger.info(f"复盘报告已上传七牛: {remote_key}")

        sample_file = self.sample_dir / f"samples_{today}.json"
        sample_file.write_text(
            json.dumps(samples, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"样本文件已保存: {sample_file} ({len(samples['all'])} 条)")
        if _storage:
            date_key = today.replace("-", "")
            remote_key = QiniuStorage.make_key("samples", date_key, f"samples_{today}.json")
            _storage.save_json(remote_key, samples)
            logger.info(f"样本文件已上传七牛: {remote_key}")

        return report

    def generate_brief_report(self, full_report: dict) -> str:
        pnl = full_report.get("pnl_summary", {})
        errors = full_report.get("error_analysis", {})
        health = full_report.get("consensus_health", {})
        pq = full_report.get("profit_quality", {})
        abst = full_report.get("abstention_rate", {})
        missed = full_report.get("missed_opportunities", {})
        evo = full_report.get("evo_signals", {})
        cf = full_report.get("counterfactual_eval", {})
        samples = full_report.get("samples", {})

        lines = [
            f"日期：{full_report.get('date', '?')}",
            f"今日盈亏：{pnl.get('realized_pnl', 0):+.2f}",
            f"成交笔数：{pnl.get('total_trades', 0)}",
            f"主要错误类型：{errors.get('primary_error', '无')}",
            f"系统一致率：{health.get('agreement_rate', 0):.0%}",
            f"少数派出现次数：{health.get('minority_count', 0)}",
            f"系统状态：{'正常' if not health.get('degradation_warning') else '⚠️ 退化风险'}",
            f"收益质量：{pq.get('grade', '?')}（胜率{pq.get('win_rate', 0):.0%}，盈亏比{pq.get('profit_loss_ratio', 0):.2f}）",
            f"弃权率：{abst.get('rate', 0):.0%}（{abst.get('abstain_count', 0)}/{abst.get('total_decisions', 0)}）",
            f"错过机会：{missed.get('count', 0)}只（潜在收益{missed.get('potential_pnl', 0):+.1f}%）",
            f"样本生成：正{samples.get('positive_count', 0)} 负{samples.get('negative_count', 0)} 反事实{samples.get('counterfactual_count', 0)}",
            f"反事实评估：试探仓命中率{cf.get('probe_hit_rate', 0):.0%}，反向证据触发{cf.get('anti_thesis_triggered', 0)}次",
            f"进化信号：{evo.get('action', 'hold').upper()} — {evo.get('reason', '')}",
        ]
        return "\n".join(lines)

    def _calc_pnl(self, orders: list[dict]) -> dict:
        buy_total = sum(
            o.get("filled_price", 0) * o.get("filled_quantity", 0)
            for o in orders
            if o.get("direction") == "BUY" and o.get("success")
        )
        sell_total = sum(
            o.get("filled_price", 0) * o.get("filled_quantity", 0)
            for o in orders
            if o.get("direction") == "SELL" and o.get("success")
        )
        return {
            "total_trades": len([o for o in orders if o.get("success")]),
            "buy_amount": buy_total,
            "sell_amount": sell_total,
            "realized_pnl": sell_total - buy_total,
        }

    def _analyze_errors(
        self,
        orders: list[dict],
        msi_history: list[dict],
        cgi_history: list[dict],
    ) -> dict:
        loss_orders = [
            o for o in orders
            if o.get("success") and o.get("direction") == "SELL"
            and o.get("filled_price", 0) < o.get("context_snapshot", {}).get("entry_price", float("inf"))
        ]

        if not loss_orders:
            return {"primary_error": "无亏损交易", "details": []}

        details = []
        for o in loss_orders:
            ctx = o.get("context_snapshot", {})
            msi_at_entry = ctx.get("msi_state", "未知")
            cgi_at_entry = ctx.get("cgi_state", "未知")

            if msi_at_entry == "防守":
                category = "环境误判"
            elif cgi_at_entry in ("补涨末端", "拥挤陷阱"):
                category = "结构误判"
            else:
                category = "执行误判"

            details.append({
                "stock_code": o.get("stock_code"),
                "category": category,
                "msi_state": msi_at_entry,
                "cgi_state": cgi_at_entry,
                "loss": o.get("filled_price", 0) - ctx.get("entry_price", 0),
            })

        cats = Counter(d["category"] for d in details)
        primary = cats.most_common(1)[0][0] if cats else "未知"

        return {"primary_error": primary, "details": details}

    def _check_consensus_health(self, verdicts_history: list[dict]) -> dict:
        if not verdicts_history:
            return {
                "agreement_rate": 0.0,
                "minority_count": 0,
                "degradation_warning": False,
            }

        total = len(verdicts_history)
        agreements = sum(
            1 for v in verdicts_history
            if v.get("consensus", "") == "3/3"
        )
        minorities = sum(
            1 for v in verdicts_history
            if v.get("minority_system", "")
        )

        agreement_rate = agreements / total if total > 0 else 0.0
        degradation_warning = agreement_rate > 0.85 and minorities < total * 0.1

        if degradation_warning:
            logger.warning(
                f"系统退化风险！一致率={agreement_rate:.0%}，"
                f"少数派仅出现{minorities}次/{total}次"
            )

        return {
            "agreement_rate": agreement_rate,
            "minority_count": minorities,
            "degradation_warning": degradation_warning,
        }

    def _generate_candidate_fixes(self, error_analysis: dict) -> list[dict]:
        fixes: list[dict] = []
        for detail in error_analysis.get("details", []):
            cat = detail.get("category", "")
            if cat == "环境误判":
                fixes.append({
                    "fix_type": FixType.RULE.value,
                    "description": "MSI 防守状态下仍有开仓，建议收紧 MSI 阈值",
                    "target": "msi_threshold",
                    "status": "candidate",
                    "verified": False,
                    "validation_path": "回测 → 样本外 → 模拟盘",
                })
            elif cat == "结构误判":
                fixes.append({
                    "fix_type": FixType.RULE.value,
                    "description": (
                        f"CGI={detail.get('cgi_state')} 环境下仍做跟风，"
                        f"建议在此状态禁做非龙头"
                    ),
                    "target": "cgi_constraint",
                    "status": "candidate",
                    "verified": False,
                    "validation_path": "回测 → 样本外 → 模拟盘",
                })
            elif cat == "执行误判":
                fixes.append({
                    "fix_type": FixType.ENGINE.value,
                    "description": "执行时机偏差，建议收紧入场窗口或降低首仓比例",
                    "target": "execution_timing",
                    "status": "candidate",
                    "verified": False,
                    "validation_path": "回测 → 样本外 → 模拟盘",
                })
            elif cat == "流动性误判":
                fixes.append({
                    "fix_type": FixType.PIPELINE.value,
                    "description": "流动性数据采集或 LRI 阈值需调整",
                    "target": "lri_pipeline",
                    "status": "candidate",
                    "verified": False,
                    "validation_path": "回测 → 样本外 → 模拟盘",
                })
            elif cat == "叙事误判":
                fixes.append({
                    "fix_type": FixType.PROMPT.value,
                    "description": "叙事/政策解读偏差，建议优化 System A prompt 或补充上下文",
                    "target": "system_a_prompt",
                    "status": "candidate",
                    "verified": False,
                    "validation_path": "A/B test → 模拟盘",
                })

        return fixes

    def _analyze_missed_opportunities(
        self,
        orders: list[dict],
        verdicts_history: list[dict],
        market_opportunities: list[dict],
    ) -> dict:
        if not market_opportunities:
            return {"count": 0, "potential_pnl": 0.0, "details": []}

        traded_codes = {o.get("stock_code") for o in orders if o.get("success")}
        verdict_codes = {v.get("stock_code") for v in verdicts_history}

        missed: list[dict] = []
        for opp in market_opportunities:
            code = opp.get("stock_code", "")
            pct = opp.get("pct_chg", 0.0)
            if pct < 5.0:
                continue
            if code in traded_codes:
                continue

            reason = "未覆盖"
            if code in verdict_codes:
                matching = [
                    v for v in verdicts_history if v.get("stock_code") == code
                ]
                actions = [v.get("action", "") for v in matching]
                if any(a in ("WAIT", "REJECT", "ABSTAIN") for a in actions):
                    reason = f"讨论后未执行(action={actions})"

            missed.append({
                "stock_code": code,
                "pct_chg": pct,
                "sector": opp.get("sector", ""),
                "reason": reason,
            })

        potential_pnl = sum(m["pct_chg"] for m in missed)

        return {
            "count": len(missed),
            "potential_pnl": potential_pnl,
            "details": missed,
        }

    def _evaluate_profit_quality(self, orders: list[dict], pnl: dict) -> dict:
        successful = [o for o in orders if o.get("success")]
        if not successful:
            return {
                "win_rate": 0.0,
                "profit_loss_ratio": 0.0,
                "max_single_loss_pct": 0.0,
                "grade": "N/A",
            }

        profits: list[float] = []
        losses: list[float] = []
        for o in successful:
            ctx = o.get("context_snapshot", {})
            entry = ctx.get("entry_price", 0)
            filled = o.get("filled_price", 0)
            if o.get("direction") != "SELL" or entry == 0:
                continue
            delta = filled - entry
            if delta >= 0:
                profits.append(delta)
            else:
                losses.append(abs(delta))

        total_closed = len(profits) + len(losses)
        if total_closed == 0:
            return {
                "win_rate": 0.0,
                "profit_loss_ratio": 0.0,
                "max_single_loss_pct": 0.0,
                "grade": "N/A",
            }

        win_rate = len(profits) / total_closed
        avg_profit = sum(profits) / len(profits) if profits else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 1.0
        pl_ratio = avg_profit / avg_loss if avg_loss > 0 else float("inf")

        realized = pnl.get("realized_pnl", 0)
        max_loss = max(losses) if losses else 0.0
        max_loss_pct = max_loss / abs(realized) if realized != 0 else 0.0

        if win_rate >= 0.6 and pl_ratio >= 2.0:
            grade = "A"
        elif win_rate >= 0.5 and pl_ratio >= 1.5:
            grade = "B"
        elif win_rate >= 0.4 or pl_ratio >= 1.0:
            grade = "C"
        else:
            grade = "D"

        return {
            "win_rate": round(win_rate, 4),
            "profit_loss_ratio": round(pl_ratio, 4),
            "max_single_loss_pct": round(max_loss_pct, 4),
            "grade": grade,
        }

    def _calc_abstention_rate(self, verdicts_history: list[dict]) -> dict:
        if not verdicts_history:
            return {"rate": 0.0, "abstain_count": 0, "total_decisions": 0, "details": []}

        total = len(verdicts_history)
        abstained = [
            v for v in verdicts_history
            if v.get("action") == "ABSTAIN"
        ]
        abstain_count = len(abstained)
        rate = abstain_count / total if total > 0 else 0.0

        if rate > 0.3:
            logger.warning(
                f"弃权率过高！{abstain_count}/{total} = {rate:.0%}，"
                f"请检查数据源健康状况"
            )

        details = [
            {
                "stock_code": v.get("stock_code", ""),
                "abstained_systems": v.get("abstained_systems", []),
                "staleness_warnings": v.get("staleness_warnings", []),
            }
            for v in abstained
        ]

        return {
            "rate": round(rate, 4),
            "abstain_count": abstain_count,
            "total_decisions": total,
            "details": details,
        }

    def _generate_samples(
        self,
        orders: list[dict],
        verdicts_history: list[dict],
        market_opportunities: list[dict],
        pnl: dict,
    ) -> dict:
        """
        多标签样本生成

        标签规则：
          positive     — 达到 target_profit_pct 的成交单
          negative     — 亏损或未达目标的成交单
          counterfactual — 系统讨论但未执行、且市场实际大涨的标的
          ambiguous    — 盈亏不明确（持仓未平）

        每条样本携带完整 context_snapshot 供后续权重模型训练。
        """
        all_samples: list[dict] = []
        positive_count = 0
        negative_count = 0
        counterfactual_count = 0
        ambiguous_count = 0

        traded_codes = {o.get("stock_code") for o in orders if o.get("success")}

        for o in orders:
            if not o.get("success"):
                continue
            ctx = o.get("context_snapshot", {})
            entry = ctx.get("entry_price", 0.0)
            filled = o.get("filled_price", 0.0)
            target_pct = ctx.get("target_profit_pct", 0.03)

            if o.get("direction") == "SELL" and entry > 0:
                actual_pct = (filled - entry) / entry
                if actual_pct >= target_pct:
                    label = SampleLabel.POSITIVE
                    positive_count += 1
                elif actual_pct < 0:
                    label = SampleLabel.NEGATIVE
                    negative_count += 1
                else:
                    label = SampleLabel.AMBIGUOUS
                    ambiguous_count += 1
            elif o.get("direction") == "BUY":
                label = SampleLabel.AMBIGUOUS
                ambiguous_count += 1
            else:
                continue

            all_samples.append({
                "label": label.value,
                "stock_code": o.get("stock_code"),
                "direction": o.get("direction"),
                "entry_price": entry,
                "filled_price": filled,
                "target_profit_pct": target_pct,
                "session": ctx.get("session", ""),
                "msi_state": ctx.get("msi_state", ""),
                "cgi_state": ctx.get("cgi_state", ""),
                "verdicts": ctx.get("verdicts", {}),
                "consensus": ctx.get("consensus", ""),
                "execution_risk": ctx.get("execution_risk", {}),
                "probe_only": ctx.get("probe_only", False),
                "date": date.today().isoformat(),
            })

        verdict_codes = {v.get("stock_code") for v in verdicts_history}
        for opp in market_opportunities:
            code = opp.get("stock_code", "")
            pct = opp.get("pct_chg", 0.0)
            if pct < 5.0 or code in traded_codes:
                continue

            if code in verdict_codes:
                matching_verdicts = [
                    v for v in verdicts_history if v.get("stock_code") == code
                ]
                all_samples.append({
                    "label": SampleLabel.COUNTERFACTUAL.value,
                    "stock_code": code,
                    "actual_pct_chg": pct,
                    "sector": opp.get("sector", ""),
                    "system_action": [v.get("action", "") for v in matching_verdicts],
                    "system_consensus": [v.get("consensus", "") for v in matching_verdicts],
                    "date": date.today().isoformat(),
                    "note": "系统讨论后未执行，市场实际大涨",
                })
                counterfactual_count += 1

        return {
            "all": all_samples,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "counterfactual_count": counterfactual_count,
            "ambiguous_count": ambiguous_count,
            "total": len(all_samples),
        }

    def _evaluate_counterfactual(
        self,
        orders: list[dict],
        verdicts_history: list[dict],
        session_results: dict[str, list[dict]],
    ) -> dict:
        """
        反事实评估

        核心对象：MetaJudge 的 1/3 试探仓
          - 试探仓命中率：probe_only=True 的单子中，实际盈利比例
          - 反向证据触发次数：anti_thesis 字段非空且最终 action=WAIT 的次数
          - 少数派正确率：minority_system 最终被市场验证正确的比例
        """
        probe_orders = [
            o for o in orders
            if o.get("success") and o.get("context_snapshot", {}).get("probe_only", False)
        ]
        probe_hits = 0
        for o in probe_orders:
            ctx = o.get("context_snapshot", {})
            entry = ctx.get("entry_price", 0.0)
            filled = o.get("filled_price", 0.0)
            if o.get("direction") == "SELL" and entry > 0 and filled > entry:
                probe_hits += 1

        probe_hit_rate = probe_hits / len(probe_orders) if probe_orders else 0.0

        anti_thesis_triggered = sum(
            1 for v in verdicts_history
            if v.get("anti_thesis_present", False) and v.get("action") in ("WAIT", "ABSTAIN")
        )

        minority_correct = 0
        minority_total = 0
        for v in verdicts_history:
            minority_sys = v.get("minority_system", "")
            if not minority_sys:
                continue
            minority_total += 1
            stock_code = v.get("stock_code", "")
            matching_orders = [
                o for o in orders
                if o.get("stock_code") == stock_code and o.get("success")
            ]
            if not matching_orders:
                minority_correct += 1

        minority_accuracy = minority_correct / minority_total if minority_total > 0 else 0.0

        session_gain: dict[str, float] = {}
        for session, results in session_results.items():
            session_orders = [
                o for o in orders
                if o.get("context_snapshot", {}).get("session") == session
                and o.get("success")
                and o.get("direction") == "SELL"
            ]
            if session_orders:
                gains = []
                for o in session_orders:
                    ctx = o.get("context_snapshot", {})
                    entry = ctx.get("entry_price", 0.0)
                    filled = o.get("filled_price", 0.0)
                    if entry > 0:
                        gains.append((filled - entry) / entry)
                session_gain[session] = round(sum(gains) / len(gains), 4) if gains else 0.0

        return {
            "probe_orders_count": len(probe_orders),
            "probe_hit_rate": round(probe_hit_rate, 4),
            "anti_thesis_triggered": anti_thesis_triggered,
            "minority_accuracy": round(minority_accuracy, 4),
            "minority_total": minority_total,
            "session_gain_by_phase": session_gain,
        }

    def _compute_evo_signals(
        self,
        profit_quality: dict,
        pnl: dict,
        today: str,
    ) -> dict:
        """
        P级参数晋升/降级/退役触发器

        规则（需 MSI 状态条件约束，避免熊市误判）：
          PROMOTE  — 连续 PROMOTE_MIN_TRADES 笔以上，胜率≥60%，盈亏比≥2.0
          DEMOTE   — 连续 DEMOTE_MIN_TRADES 笔以上，胜率<40% 或盈亏比<0.8
          RETIRE   — 连续 RETIRE_CONSECUTIVE_DEMOTE 次 DEMOTE 信号
          HOLD     — 其他情况

        输出供 P2Params.save() 触发参数更新候选，不直接修改实盘参数。
        """
        win_rate = profit_quality.get("win_rate", 0.0)
        pl_ratio = profit_quality.get("profit_loss_ratio", 0.0)
        total_trades = pnl.get("total_trades", 0)
        grade = profit_quality.get("grade", "N/A")

        history_file = self.review_dir / "evo_history.json"
        evo_history: list[dict] = []
        if history_file.exists():
            try:
                evo_history = json.loads(history_file.read_text(encoding="utf-8"))
            except Exception:
                evo_history = []

        action = EvoAction.HOLD
        reason = "样本不足或指标正常"

        if total_trades >= self.PROMOTE_MIN_TRADES:
            if win_rate >= self.PROMOTE_WIN_RATE and pl_ratio >= self.PROMOTE_PL_RATIO:
                action = EvoAction.PROMOTE
                reason = f"胜率{win_rate:.0%} 盈亏比{pl_ratio:.2f} 达到晋升阈值"
            elif (
                total_trades >= self.DEMOTE_MIN_TRADES
                and (win_rate < self.DEMOTE_WIN_RATE or pl_ratio < self.DEMOTE_PL_RATIO)
            ):
                recent_demotes = sum(
                    1 for h in evo_history[-self.RETIRE_CONSECUTIVE_DEMOTE:]
                    if h.get("action") == EvoAction.DEMOTE.value
                )
                if recent_demotes >= self.RETIRE_CONSECUTIVE_DEMOTE - 1:
                    action = EvoAction.RETIRE
                    reason = f"连续{self.RETIRE_CONSECUTIVE_DEMOTE}次降级，建议退役当前策略实例"
                else:
                    action = EvoAction.DEMOTE
                    reason = f"胜率{win_rate:.0%} 盈亏比{pl_ratio:.2f} 低于降级阈值"

        signal = {
            "date": today,
            "action": action.value,
            "reason": reason,
            "win_rate": win_rate,
            "pl_ratio": pl_ratio,
            "total_trades": total_trades,
            "grade": grade,
            "requires_backtest_validation": action != EvoAction.HOLD,
        }

        evo_history.append(signal)
        history_file.write_text(
            json.dumps(evo_history[-90:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if action == EvoAction.PROMOTE:
            logger.info(f"🟢 进化信号 PROMOTE: {reason}")
        elif action == EvoAction.DEMOTE:
            logger.warning(f"🟡 进化信号 DEMOTE: {reason}")
        elif action == EvoAction.RETIRE:
            logger.error(f"🔴 进化信号 RETIRE: {reason}")

        return signal
