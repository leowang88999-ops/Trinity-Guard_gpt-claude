"""券商适配器工厂 — 根据 config/broker.yaml 的 active_broker 创建实例"""

import os

import yaml  # type: ignore[import-untyped]
from loguru import logger

from .base import BrokerBase, Order, OrderResult, Position
from .paper_broker import LocalPaperBroker


def create_broker(config_path: str = "config/broker.yaml") -> BrokerBase:
    """
    读取 broker.yaml，根据 active_broker 字段创建对应适配器
    """
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    active = cfg.get("active_broker", "local_paper")
    brokers_cfg = cfg.get("brokers", {})

    if active == "local_paper":
        data_dir = brokers_cfg.get("local_paper", {}).get("data_dir", "data/trades")
        logger.info(f"创建 LocalPaperBroker | data_dir={data_dir}")
        return LocalPaperBroker(data_dir=data_dir)

    elif active == "ths":
        from .ths_broker import THSBroker
        ths_cfg = brokers_cfg.get("ths", {})
        return THSBroker(
            exe_path=os.path.expandvars(ths_cfg.get("exe_path", "")),
            user=os.path.expandvars(ths_cfg.get("user", "")),
            password=os.path.expandvars(ths_cfg.get("password", "")),
            comm_password=os.path.expandvars(ths_cfg.get("comm_password", "")),
        )

    elif active == "dfcf":
        from .dfcf_broker import DFCFBroker
        dfcf_cfg = brokers_cfg.get("dfcf", {})
        return DFCFBroker(
            user=os.path.expandvars(dfcf_cfg.get("user", "")),
            password=os.path.expandvars(dfcf_cfg.get("password", "")),
        )

    else:
        raise ValueError(f"未知的 active_broker: {active}")


__all__ = [
    "BrokerBase", "Order", "OrderResult", "Position",
    "LocalPaperBroker", "create_broker",
]
