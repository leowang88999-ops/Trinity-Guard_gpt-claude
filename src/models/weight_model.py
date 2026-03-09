"""
WeightModel — 多目标权重模型

按盈利目标（+3% / +5% / +8%）分别维护独立权重向量，
对 FactorBundle 的 A-F 六类因子进行加权打分，输出买入信号强度。

架构：
  - 共享底层：市场状态编码（MSI/CGI/session）
  - 独立头部：每个盈利目标一套 factor_weights + bias
  - 进化接口：update_weights() 接收复盘反馈，滚动更新权重

P级分层：
  P2 策略规则层：各目标的初始权重（回测验证后写入 models.yaml）
  P3 权重学习层：在线更新步长 lr，模拟盘持续进化
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast, get_args

import numpy as np
from loguru import logger

from src.factors.factor_engine import FactorBundle

ProfitTarget = Literal["t3", "t5", "t8"]

_DEFAULT_WEIGHTS: dict[ProfitTarget, dict[str, float]] = {
    "t3": {
        "A_news_count": 0.02,
        "A_avg_sentiment": 0.08,
        "A_positive_ratio": 0.06,
        "A_negative_ratio": -0.10,
        "A_hot_topic_flag": 0.04,
        "B_above_ma5": 0.06,
        "B_above_ma10": 0.05,
        "B_above_ma20": 0.04,
        "B_vol_ratio_5d": 0.07,
        "B_pct_chg_5d": 0.05,
        "B_atr_ratio": -0.03,
        "B_box_breakout": 0.08,
        "B_macd_cross": 0.06,
        "B_rsi14": 0.00,
        "B_upper_shadow_ratio": -0.05,
        "C_main_net_inflow": 0.06,
        "C_main_net_ratio": 0.07,
        "C_retail_net_ratio": -0.03,
        "C_north_flow_score": 0.04,
        "C_large_order_ratio": 0.03,
        "D_tail_anomaly_score": -0.08,
        "D_wash_trade_score": -0.06,
        "D_price_vol_diverge": -0.05,
        "D_limit_quality_score": 0.04,
        "D_manipulation_composite": -0.10,
        "E_sector_avg_pct": 0.05,
        "E_sector_leader_pct": 0.03,
        "E_sector_relative_strength": 0.06,
        "E_sector_breadth": 0.04,
        "E_hot_sector_flag": 0.05,
        "F_liquidity_score": 0.04,
        "F_slippage_risk": -0.06,
        "F_t1_exit_difficulty": -0.08,
        "F_composite_risk": -0.10,
    },
    "t5": {
        "A_news_count": 0.03,
        "A_avg_sentiment": 0.07,
        "A_positive_ratio": 0.07,
        "A_negative_ratio": -0.08,
        "A_hot_topic_flag": 0.05,
        "B_above_ma5": 0.05,
        "B_above_ma10": 0.06,
        "B_above_ma20": 0.06,
        "B_vol_ratio_5d": 0.08,
        "B_pct_chg_5d": 0.06,
        "B_atr_ratio": -0.02,
        "B_box_breakout": 0.09,
        "B_macd_cross": 0.07,
        "B_rsi14": 0.00,
        "B_upper_shadow_ratio": -0.04,
        "C_main_net_inflow": 0.07,
        "C_main_net_ratio": 0.08,
        "C_retail_net_ratio": -0.02,
        "C_north_flow_score": 0.05,
        "C_large_order_ratio": 0.04,
        "D_tail_anomaly_score": -0.07,
        "D_wash_trade_score": -0.07,
        "D_price_vol_diverge": -0.06,
        "D_limit_quality_score": 0.05,
        "D_manipulation_composite": -0.09,
        "E_sector_avg_pct": 0.06,
        "E_sector_leader_pct": 0.04,
        "E_sector_relative_strength": 0.07,
        "E_sector_breadth": 0.05,
        "E_hot_sector_flag": 0.06,
        "F_liquidity_score": 0.05,
        "F_slippage_risk": -0.05,
        "F_t1_exit_difficulty": -0.07,
        "F_composite_risk": -0.09,
    },
    "t8": {
        "A_news_count": 0.04,
        "A_avg_sentiment": 0.06,
        "A_positive_ratio": 0.08,
        "A_negative_ratio": -0.06,
        "A_hot_topic_flag": 0.06,
        "B_above_ma5": 0.04,
        "B_above_ma10": 0.05,
        "B_above_ma20": 0.07,
        "B_vol_ratio_5d": 0.09,
        "B_pct_chg_5d": 0.07,
        "B_atr_ratio": -0.01,
        "B_box_breakout": 0.10,
        "B_macd_cross": 0.08,
        "B_rsi14": 0.00,
        "B_upper_shadow_ratio": -0.03,
        "C_main_net_inflow": 0.08,
        "C_main_net_ratio": 0.09,
        "C_retail_net_ratio": -0.01,
        "C_north_flow_score": 0.06,
        "C_large_order_ratio": 0.05,
        "D_tail_anomaly_score": -0.06,
        "D_wash_trade_score": -0.08,
        "D_price_vol_diverge": -0.07,
        "D_limit_quality_score": 0.06,
        "D_manipulation_composite": -0.08,
        "E_sector_avg_pct": 0.07,
        "E_sector_leader_pct": 0.05,
        "E_sector_relative_strength": 0.08,
        "E_sector_breadth": 0.06,
        "E_hot_sector_flag": 0.07,
        "F_liquidity_score": 0.06,
        "F_slippage_risk": -0.04,
        "F_t1_exit_difficulty": -0.06,
        "F_composite_risk": -0.08,
    },
}

_SESSION_MULTIPLIERS: dict[str, dict[ProfitTarget, float]] = {
    "pre_open":      {"t3": 1.0, "t5": 0.9, "t8": 0.8},
    "open_session":  {"t3": 1.0, "t5": 1.0, "t8": 1.0},
    "mid_session":   {"t3": 0.8, "t5": 0.9, "t8": 1.0},
    "close_session": {"t3": 0.9, "t5": 1.0, "t8": 1.0},
}

_MSI_MULTIPLIERS: dict[str, float] = {
    "进攻": 1.2,
    "观望": 1.0,
    "防守": 0.6,
}

_CGI_MULTIPLIERS: dict[str, float] = {
    "健康轮动": 1.1,
    "拥挤陷阱": 0.7,
    "冰点离场": 0.5,
}


@dataclass
class ScoreResult:
    """单只股票的多目标评分结果"""
    stock_code: str
    trade_date: str
    session: str
    scores: dict[ProfitTarget, float] = field(default_factory=dict)
    active_target: ProfitTarget = "t3"
    final_score: float = 0.0
    factor_contributions: dict[str, float] = field(default_factory=dict)
    valid: bool = True

    @property
    def recommended_target(self) -> ProfitTarget:
        if not self.scores:
            return "t3"
        return max(self.scores, key=lambda t: self.scores[t])


class WeightModel:
    """
    多目标权重模型

    每个盈利目标（t3/t5/t8）独立维护权重向量。
    支持：
      - 批量评分
      - 在线权重更新（梯度下降 + 正则化）
      - 权重持久化（JSON）
      - 市场状态条件调制（MSI/CGI/session）
    """

    def __init__(
        self,
        weights_path: str = "data/weights/weight_model.json",
        lr: float = 0.01,
        l2_lambda: float = 0.001,
    ):
        self.weights_path = Path(weights_path)
        self.lr = lr
        self.l2_lambda = l2_lambda

        self._weights: dict[ProfitTarget, dict[str, float]] = {}
        self._update_counts: dict[ProfitTarget, int] = {"t3": 0, "t5": 0, "t8": 0}

        self._load_weights()

    def _load_weights(self) -> None:
        if self.weights_path.exists():
            try:
                with open(self.weights_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                for target in get_args(ProfitTarget):
                    if target in saved:
                        self._weights[target] = saved[target]
                    else:
                        self._weights[target] = dict(_DEFAULT_WEIGHTS[target])
                self._update_counts = saved.get("_update_counts", {"t3": 0, "t5": 0, "t8": 0})
                logger.info(f"WeightModel 权重已加载: {self.weights_path}")
            except Exception as exc:
                logger.warning(f"WeightModel 权重加载失败，使用默认值: {exc}")
                self._reset_to_default()
        else:
            self._reset_to_default()

    def _reset_to_default(self) -> None:
        for target in get_args(ProfitTarget):
            self._weights[target] = dict(_DEFAULT_WEIGHTS[target])

    def save_weights(self) -> None:
        self.weights_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict = {t: dict(w) for t, w in self._weights.items()}
        payload["_update_counts"] = self._update_counts
        with open(self.weights_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.debug(f"WeightModel 权重已保存: {self.weights_path}")

    def _get_context_multiplier(
        self,
        target: ProfitTarget,
        session: str,
        msi_state: str,
        cgi_state: str,
    ) -> float:
        sess_mult = _SESSION_MULTIPLIERS.get(session, {}).get(target, 1.0)
        msi_mult = _MSI_MULTIPLIERS.get(msi_state, 1.0)
        cgi_mult = _CGI_MULTIPLIERS.get(cgi_state, 1.0)
        return sess_mult * msi_mult * cgi_mult

    def score_bundle(
        self,
        bundle: FactorBundle,
        msi_state: str = "观望",
        cgi_state: str = "拥挤陷阱",
        active_target: ProfitTarget = "t3",
    ) -> ScoreResult:
        flat = bundle.to_flat_dict()
        scores: dict[ProfitTarget, float] = {}
        contributions: dict[str, float] = {}

        for target in get_args(ProfitTarget):
            t = cast(ProfitTarget, target)
            w = self._weights[t]
            raw = 0.0
            for factor_key, factor_val in flat.items():
                weight = w.get(factor_key, 0.0)
                contrib = weight * factor_val
                raw += contrib
                if t == active_target:
                    contributions[factor_key] = contrib

            multiplier = self._get_context_multiplier(t, bundle.session, msi_state, cgi_state)
            scores[t] = float(np.tanh(raw * multiplier))

        final = scores[active_target]

        return ScoreResult(
            stock_code=bundle.stock_code,
            trade_date=bundle.trade_date,
            session=bundle.session,
            scores=scores,
            active_target=active_target,
            final_score=final,
            factor_contributions=contributions,
            valid=bundle.valid,
        )

    def score_bundles(
        self,
        bundles: list[FactorBundle],
        msi_state: str = "观望",
        cgi_state: str = "拥挤陷阱",
        active_target: ProfitTarget = "t3",
    ) -> list[ScoreResult]:
        results = [
            self.score_bundle(b, msi_state, cgi_state, active_target)
            for b in bundles
            if b.valid
        ]
        results.sort(key=lambda r: r.final_score, reverse=True)
        return results

    def update_weights(
        self,
        bundle: FactorBundle,
        target: ProfitTarget,
        label: float,
        msi_state: str = "观望",
        cgi_state: str = "拥挤陷阱",
    ) -> None:
        """
        在线权重更新（感知机 + L2 正则化）

        label: +1.0 正样本（达到盈利目标），-1.0 负样本
        """
        flat = bundle.to_flat_dict()
        w = self._weights[target]

        multiplier = self._get_context_multiplier(target, bundle.session, msi_state, cgi_state)
        raw = sum(w.get(k, 0.0) * v for k, v in flat.items())
        pred = float(np.tanh(raw * multiplier))

        error = label - pred

        for factor_key, factor_val in flat.items():
            grad = -error * factor_val * multiplier
            reg = self.l2_lambda * w.get(factor_key, 0.0)
            w[factor_key] = w.get(factor_key, 0.0) - self.lr * (grad + reg)

        self._update_counts[target] += 1

        if self._update_counts[target] % 100 == 0:
            self.save_weights()
            logger.info(f"WeightModel [{target}] 已更新 {self._update_counts[target]} 次，权重已保存")

    def batch_update(
        self,
        samples: list[dict],
    ) -> dict[ProfitTarget, int]:
        """
        批量更新权重，供 DailyReview 调用

        samples 格式：
          [{"bundle": FactorBundle, "target": "t3"|"t5"|"t8",
            "label": 1.0|-1.0, "msi_state": str, "cgi_state": str}, ...]
        """
        counts: dict[ProfitTarget, int] = {"t3": 0, "t5": 0, "t8": 0}
        for s in samples:
            bundle = s.get("bundle")
            target = s.get("target", "t3")
            label = float(s.get("label", 0.0))
            msi_state = s.get("msi_state", "观望")
            cgi_state = s.get("cgi_state", "拥挤陷阱")

            if bundle is None or label == 0.0:
                continue
            if target not in ("t3", "t5", "t8"):
                continue

            self.update_weights(bundle, target, label, msi_state, cgi_state)
            counts[target] += 1

        self.save_weights()
        logger.info(f"WeightModel 批量更新完成: {counts}")
        return counts

    def get_weight_summary(self) -> dict[str, dict[str, float]]:
        summary: dict[str, dict[str, float]] = {}
        for target, w in self._weights.items():
            top_pos = sorted(w.items(), key=lambda x: x[1], reverse=True)[:5]
            top_neg = sorted(w.items(), key=lambda x: x[1])[:5]
            summary[target] = {
                "update_count": float(self._update_counts.get(target, 0)),
                **{f"top_pos_{k}": v for k, v in top_pos},
                **{f"top_neg_{k}": v for k, v in top_neg},
            }
        return summary

    def select_active_target(
        self,
        msi_state: str,
        cgi_state: str,
        session: str,
    ) -> ProfitTarget:
        """
        根据市场状态自动选择当前盈利目标

        进攻 + 健康轮动 → t8
        进攻 + 其他     → t5
        观望            → t3
        防守            → t3（降低目标，保守操作）
        """
        if msi_state == "进攻" and cgi_state == "健康轮动":
            return "t8"
        if msi_state == "进攻":
            return "t5"
        return "t3"
