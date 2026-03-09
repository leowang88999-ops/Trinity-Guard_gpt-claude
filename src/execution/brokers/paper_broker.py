"""
LocalPaperBroker — 本地模拟盘
使用 JSON 文件记录持仓和交易
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

from loguru import logger

from .base import BrokerBase, Order, OrderResult, Position


class LocalPaperBroker(BrokerBase):

    def __init__(self, data_dir: str = "data/trades"):
        self.data_dir = Path(data_dir)
        self.positions_file = self.data_dir / "positions.json"
        self.trades_file = self.data_dir / "trades.json"
        self.balance_file = self.data_dir / "balance.json"

        self._initial_cash = 1_000_000.0

    async def connect(self) -> bool:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.balance_file.exists():
            self._save_json(self.balance_file, {
                "cash": self._initial_cash,
                "frozen": 0.0,
                "total": self._initial_cash,
            })
        if not self.positions_file.exists():
            self._save_json(self.positions_file, [])
        if not self.trades_file.exists():
            self._save_json(self.trades_file, [])
        logger.info(f"本地模拟盘已连接 | 数据目录: {self.data_dir}")
        return True

    async def submit_order(self, order: Order) -> OrderResult:
        oid = str(uuid.uuid4())[:8]
        balance = self._load_json(self.balance_file)
        positions = self._load_json(self.positions_file)
        trades = self._load_json(self.trades_file)

        amount = order.price * order.quantity

        if order.direction == "BUY":
            if balance["cash"] < amount:
                return OrderResult(
                    success=False, order_id=oid,
                    stock_code=order.stock_code,
                    direction="BUY", message="资金不足",
                )
            balance["cash"] -= amount
            balance["frozen"] += amount

            pos = next((p for p in positions if p["stock_code"] == order.stock_code), None)
            if pos:
                total_cost = pos["cost_price"] * pos["quantity"] + amount
                pos["quantity"] += order.quantity
                pos["cost_price"] = total_cost / pos["quantity"]
            else:
                positions.append({
                    "stock_code": order.stock_code,
                    "name": "",
                    "quantity": order.quantity,
                    "available": 0,  # T+1，当日不可卖
                    "cost_price": order.price,
                    "current_price": order.price,
                })

        elif order.direction == "SELL":
            pos = next((p for p in positions if p["stock_code"] == order.stock_code), None)
            if not pos or pos["available"] < order.quantity:
                return OrderResult(
                    success=False, order_id=oid,
                    stock_code=order.stock_code,
                    direction="SELL", message="可卖数量不足(T+1)",
                )
            pos["available"] -= order.quantity
            pos["quantity"] -= order.quantity
            balance["cash"] += amount
            if pos["quantity"] <= 0:
                positions = [p for p in positions if p["stock_code"] != order.stock_code]

        trade_record = {
            "order_id": oid,
            "stock_code": order.stock_code,
            "direction": order.direction,
            "price": order.price,
            "quantity": order.quantity,
            "amount": amount,
            "timestamp": datetime.now().isoformat(),
        }
        trades.append(trade_record)

        balance["frozen"] = 0.0
        balance["total"] = balance["cash"] + sum(
            p["cost_price"] * p["quantity"] for p in positions
        )

        self._save_json(self.balance_file, balance)
        self._save_json(self.positions_file, positions)
        self._save_json(self.trades_file, trades)

        logger.info(f"模拟成交 | {order.direction} {order.stock_code} "
                     f"x{order.quantity} @{order.price}")

        return OrderResult(
            success=True, order_id=oid,
            stock_code=order.stock_code,
            direction=order.direction,
            filled_price=order.price,
            filled_quantity=order.quantity,
            message="模拟成交",
            timestamp=datetime.now().isoformat(),
            raw=trade_record,
        )

    async def cancel_order(self, order_id: str) -> bool:
        logger.info(f"模拟撤单 | {order_id}")
        return True

    async def get_positions(self) -> list[Position]:
        data = self._load_json(self.positions_file)
        return [Position(**p, pnl=0.0, pnl_pct=0.0) for p in data]

    async def get_balance(self) -> dict:
        return self._load_json(self.balance_file)

    def settle_t1(self):
        """收盘后调用：将当日买入的持仓解锁为可卖"""
        positions = self._load_json(self.positions_file)
        for p in positions:
            p["available"] = p["quantity"]
        self._save_json(self.positions_file, positions)
        logger.info("T+1 结算完成，所有持仓已解锁")

    def _load_json(self, path: Path):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {} if "balance" in str(path) else []

    def _save_json(self, path: Path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
