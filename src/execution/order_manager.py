"""
OrderManager — 订单管理器
职责：
  - 接收交易信号 → 校验风控 → 转换为券商委托
  - 跟踪成交回报 → 更新持仓
  - 日志记录（含 context snapshot）
  - 去重 / 超时 / 重试保护
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from .brokers.base import BrokerBase, Order, OrderResult
from .risk_control import RiskControl

try:
    sys.path.insert(0, "/app/shared")
    from storage.qiniu_storage import QiniuStorage
    _storage = QiniuStorage(bucket_name="trinity-proddev-data")
except Exception:
    _storage = None


class OrderManager:
    """订单管理器：风控校验 → 下单 → 日志"""

    def __init__(
        self,
        broker: BrokerBase,
        risk_control: RiskControl,
        log_dir: str = "data/trades",
    ):
        self.broker = broker
        self.rc = risk_control
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._today_orders: list[dict] = []
        self._pending_ids: set[str] = set()

    async def execute_buy(
        self,
        stock_code: str,
        price: float,
        quantity: int,
        context_snapshot: dict | None = None,
    ) -> OrderResult:
        """
        执行买入：风控 → 去重 → 下单 → 日志

        context_snapshot: 决策时的三指数状态、三系统意见等
        """
        if stock_code in self._pending_ids:
            logger.warning(f"去重拦截: {stock_code} 已有未完成委托")
            return OrderResult(False, "", stock_code, "BUY", message="去重拦截")

        amount = price * quantity
        rc_result = self.rc.check_buy(stock_code, amount, price)
        if not rc_result["allowed"]:
            logger.warning(f"风控拦截 {stock_code}: {rc_result['reason']}")
            return OrderResult(False, "", stock_code, "BUY",
                               message=f"风控: {rc_result['reason']}")

        order = Order(
            stock_code=stock_code,
            direction="BUY",
            price=price,
            quantity=quantity,
        )

        self._pending_ids.add(stock_code)
        try:
            result = await self.broker.submit_order(order)
        finally:
            self._pending_ids.discard(stock_code)

        self._log_order(order, result, context_snapshot)
        return result

    async def execute_sell(
        self,
        stock_code: str,
        price: float,
        quantity: int,
        reason: str = "",
    ) -> OrderResult:
        """执行卖出"""
        order = Order(
            stock_code=stock_code,
            direction="SELL",
            price=price,
            quantity=quantity,
        )
        result = await self.broker.submit_order(order)
        self._log_order(order, result, {"sell_reason": reason})
        return result

    def _log_order(self, order: Order, result: OrderResult,
                   context: dict | None):
        record = {
            "timestamp": datetime.now().isoformat(),
            "stock_code": order.stock_code,
            "direction": order.direction,
            "price": order.price,
            "quantity": order.quantity,
            "success": result.success,
            "order_id": result.order_id,
            "filled_price": result.filled_price,
            "filled_quantity": result.filled_quantity,
            "message": result.message,
            "context_snapshot": context or {},
        }
        self._today_orders.append(record)

        today = datetime.now().strftime("%Y%m%d")
        log_file = self.log_dir / f"orders_{today}.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if _storage:
            remote_key = QiniuStorage.make_key("trades", today, f"orders_{today}.jsonl")
            _storage.save_jsonl_line(remote_key, record, local_path=str(log_file))

    def get_today_orders(self) -> list[dict]:
        return list(self._today_orders)

    def get_today_pnl_summary(self) -> dict:
        """简版日盈亏统计"""
        buys = [o for o in self._today_orders if o["direction"] == "BUY" and o["success"]]
        sells = [o for o in self._today_orders if o["direction"] == "SELL" and o["success"]]
        total_buy = sum(o["filled_price"] * o["filled_quantity"] for o in buys)
        total_sell = sum(o["filled_price"] * o["filled_quantity"] for o in sells)
        return {
            "buy_count": len(buys),
            "sell_count": len(sells),
            "total_buy_amount": total_buy,
            "total_sell_amount": total_sell,
            "realized_pnl": total_sell - total_buy,
        }
