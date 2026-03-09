"""策略二：Volatility Compression Breakout — 波动率压缩突破。"""
from __future__ import annotations

import pandas as pd

ATR_PERIOD = 20
COMPRESSION_THRESHOLD = 0.02  # ATR(20)/close < 2% 视为压缩
VOL_BREAKOUT_RATIO = 1.3
HARD_STOP_PCT = -5.0


def _atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """ATR(period)。"""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def check_entry(df: pd.DataFrame) -> dict:
    """
    检查进场条件。

    波动率压缩（ATR20/close < 阈值）后放量突破。
    """
    if df.empty or len(df) < ATR_PERIOD + 5:
        return {"signal": False, "confidence": 0.0, "reason": "数据不足"}

    row = df.iloc[-1]
    close = float(row["close"])
    atr_val = _atr(df, ATR_PERIOD).iloc[-1]

    # 压缩判断
    if atr_val / close >= COMPRESSION_THRESHOLD:
        return {"signal": False, "confidence": 0.0, "reason": "波动率未压缩"}

    # 放量突破：当日量能 > 近期均量
    vol_recent = df["vol"].tail(10).mean()
    if vol_recent <= 0 or row["vol"] < vol_recent * VOL_BREAKOUT_RATIO:
        return {"signal": False, "confidence": 0.0, "reason": "量能不足"}

    # 价格突破：收盘突破近期高点
    high_10 = df["high"].tail(10).iloc[:-1].max()
    if close <= high_10:
        return {"signal": False, "confidence": 0.0, "reason": "未突破"}

    return {
        "signal": True,
        "confidence": 0.7,
        "reason": "波动率压缩后放量突破",
        "entry_price": close,
    }


def check_exit(
    df: pd.DataFrame,
    entry_price: float,
    current_price: float,
) -> dict:
    """
    检查出场条件。

    简化逻辑：硬止损 -5%，ATR 跟踪止损。
    """
    if df.empty or len(df) < ATR_PERIOD:
        return {"signal": False, "reason": "数据不足"}

    pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

    if pct <= HARD_STOP_PCT:
        return {"signal": True, "reason": "硬止损", "stop_type": "hard"}

    atr_val = _atr(df, ATR_PERIOD).iloc[-1]
    atr_stop = entry_price - 2.0 * atr_val
    if current_price <= atr_stop:
        return {"signal": True, "reason": "ATR止损", "stop_type": "atr"}

    return {"signal": False, "reason": "持仓中"}
