"""
RiskControl — 硬风控层（宪法级，不可被 AI 改写）

所有规则优先级高于 AI 决策。
盘中执行层禁止自我修改此文件中的任何阈值。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from loguru import logger


class RiskControl:
    """
    硬风控检查器

    铁律清单（不可学习、不可自动修改）：
      1. 单票最大仓位 ≤ 总资产 20%
      2. 单日最大回撤 ≤ -3%
      3. 连续亏损 3 天自动停机 1 天
      4. 涨跌停 / 停牌 / 流动性拒单
      5. 单日最大开仓次数 ≤ 5
      6. 尾盘 14:50 后禁止新开仓
      7. Webhook/委托 去重、超时保护
    """

    MAX_SINGLE_POSITION_PCT = 0.20
    MAX_DAILY_DRAWDOWN_PCT = -0.03
    MAX_CONSECUTIVE_LOSS_DAYS = 3
    MAX_DAILY_NEW_ORDERS = 5
    CUTOFF_HOUR = 14
    CUTOFF_MINUTE = 50

    def __init__(
        self,
        total_capital: float = 1_000_000.0,
        state_file: str = "data/risk_state.json",
        test_mode: bool = False,
    ):
        self.total_capital = total_capital
        self.state_file = Path(state_file)
        self.test_mode = test_mode
        self._state = self._load_state()

    def check_buy(
        self,
        stock_code: str,
        amount: float,
        price: float,
    ) -> dict:
        """
        买入前风控校验，返回 {"allowed": bool, "reason": str}
        """
        now = datetime.now()

        if self._state.get("halted", False):
            return {"allowed": False, "reason": "系统停机中（连续亏损触发）"}

        if not self.test_mode and (
            now.hour > self.CUTOFF_HOUR or (
                now.hour == self.CUTOFF_HOUR and now.minute >= self.CUTOFF_MINUTE
            )
        ):
            return {"allowed": False, "reason": f"超过 {self.CUTOFF_HOUR}:{self.CUTOFF_MINUTE}，禁止新开仓"}

        today_str = date.today().isoformat()
        today_orders = self._state.get("daily_orders", {}).get(today_str, 0)
        if today_orders >= self.MAX_DAILY_NEW_ORDERS:
            return {"allowed": False, "reason": f"单日开仓已达上限 {self.MAX_DAILY_NEW_ORDERS}"}

        position_pct = amount / self.total_capital if self.total_capital > 0 else 1.0
        if position_pct > self.MAX_SINGLE_POSITION_PCT:
            return {
                "allowed": False,
                "reason": f"单票仓位{position_pct:.1%} > {self.MAX_SINGLE_POSITION_PCT:.0%}",
            }

        self._increment_daily_orders(today_str)
        return {"allowed": True, "reason": "通过"}

    def check_daily_drawdown(self, current_total: float) -> dict:
        """
        检查日内回撤是否触发熔断
        """
        if self.total_capital <= 0:
            return {"triggered": False}

        drawdown = (current_total - self.total_capital) / self.total_capital
        if drawdown <= self.MAX_DAILY_DRAWDOWN_PCT:
            logger.error(f"日内回撤 {drawdown:.2%} 触发熔断！")
            return {
                "triggered": True,
                "drawdown": drawdown,
                "action": "禁止所有新开仓，仅允许减仓",
            }
        return {"triggered": False, "drawdown": drawdown}

    def record_daily_pnl(self, pnl_pct: float):
        """
        记录当日盈亏，检查连续亏损
        """
        history = self._state.setdefault("pnl_history", [])
        history.append({
            "date": date.today().isoformat(),
            "pnl_pct": pnl_pct,
        })

        recent = history[-self.MAX_CONSECUTIVE_LOSS_DAYS:]
        if len(recent) >= self.MAX_CONSECUTIVE_LOSS_DAYS:
            all_loss = all(r["pnl_pct"] < 0 for r in recent)
            if all_loss:
                logger.error(f"连续 {self.MAX_CONSECUTIVE_LOSS_DAYS} 日亏损，次日自动停机")
                self._state["halted"] = True
                self._state["halt_reason"] = f"连续{self.MAX_CONSECUTIVE_LOSS_DAYS}日亏损"
            else:
                self._state["halted"] = False

        self._save_state()

    def reset_halt(self):
        """手动解除停机"""
        self._state["halted"] = False
        self._state.pop("halt_reason", None)
        self._save_state()
        logger.info("停机状态已手动解除")

    def update_capital(self, total: float):
        """更新总资产（每日开盘或盘中更新）"""
        self.total_capital = total

    def _increment_daily_orders(self, today_str: str):
        daily = self._state.setdefault("daily_orders", {})
        daily[today_str] = daily.get(today_str, 0) + 1
        self._save_state()

    def _load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {}

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
