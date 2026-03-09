"""
THSBroker — 同花顺模拟盘适配器
依赖 easytrader 库
"""

from typing import Any

from loguru import logger

from .base import BrokerBase, Order, OrderResult, Position


class THSBroker(BrokerBase):
    """同花顺模拟盘，通过 easytrader 驱动客户端"""

    def __init__(self, exe_path: str, user: str, password: str, comm_password: str = ""):
        self.exe_path = exe_path
        self.user = user
        self.password = password
        self.comm_password = comm_password
        self._client: Any = None

    async def connect(self) -> bool:
        try:
            import easytrader
            self._client = easytrader.use("ths")
            self._client.connect(self.exe_path)
            logger.info("同花顺客户端已连接")
            return True
        except Exception as e:
            logger.error(f"同花顺连接失败: {e}")
            return False

    async def submit_order(self, order: Order) -> OrderResult:
        if self._client is None:
            return OrderResult(False, "", order.stock_code, order.direction,
                               message="未连接")
        try:
            code = order.stock_code.split(".")[0]
            if order.direction == "BUY":
                result = self._client.buy(code, price=order.price,
                                          amount=order.quantity)
            else:
                result = self._client.sell(code, price=order.price,
                                           amount=order.quantity)

            return OrderResult(
                success=True,
                order_id=str(result.get("entrust_no", "")),
                stock_code=order.stock_code,
                direction=order.direction,
                filled_price=order.price,
                filled_quantity=order.quantity,
                message="已委托",
                raw=result,
            )
        except Exception as e:
            logger.error(f"同花顺下单失败: {e}")
            return OrderResult(False, "", order.stock_code, order.direction,
                               message=str(e))

    async def cancel_order(self, order_id: str) -> bool:
        if self._client is None:
            return False
        try:
            self._client.cancel_entrust(order_id)
            return True
        except Exception as e:
            logger.error(f"同花顺撤单失败: {e}")
            return False

    async def get_positions(self) -> list[Position]:
        if self._client is None:
            return []
        try:
            raw = self._client.position
            return [
                Position(
                    stock_code=p.get("证券代码", ""),
                    name=p.get("证券名称", ""),
                    quantity=int(p.get("股票余额", 0)),
                    available=int(p.get("可用余额", 0)),
                    cost_price=float(p.get("成本价", 0)),
                    current_price=float(p.get("市价", 0)),
                    pnl=float(p.get("盈亏", 0)),
                    pnl_pct=float(p.get("盈亏比(%)", 0)),
                )
                for p in raw
            ]
        except Exception as e:
            logger.error(f"同花顺查询持仓失败: {e}")
            return []

    async def get_balance(self) -> dict:
        if self._client is None:
            return {}
        try:
            b = self._client.balance
            return {
                "cash": float(b.get("可用金额", 0)),
                "frozen": float(b.get("冻结金额", 0)),
                "total": float(b.get("总资产", 0)),
            }
        except Exception as e:
            logger.error(f"同花顺查询余额失败: {e}")
            return {}
