"""
BrokerBase — 券商适配器抽象基类
所有执行后端必须实现此接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Order:
    stock_code: str
    direction: str        # BUY / SELL
    price: float
    quantity: int
    order_type: str = "LIMIT"  # LIMIT / MARKET
    order_id: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class OrderResult:
    success: bool
    order_id: str
    stock_code: str
    direction: str
    filled_price: float = 0.0
    filled_quantity: int = 0
    message: str = ""
    timestamp: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Position:
    stock_code: str
    name: str
    quantity: int
    available: int        # 可卖数量（T+1 限制）
    cost_price: float
    current_price: float
    pnl: float = 0.0
    pnl_pct: float = 0.0


class BrokerBase(ABC):
    """券商适配器抽象基类"""

    @abstractmethod
    async def connect(self) -> bool:
        """连接/登录"""

    @abstractmethod
    async def submit_order(self, order: Order) -> OrderResult:
        """提交订单"""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """撤单"""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """查询持仓"""

    @abstractmethod
    async def get_balance(self) -> dict:
        """查询资金余额"""
