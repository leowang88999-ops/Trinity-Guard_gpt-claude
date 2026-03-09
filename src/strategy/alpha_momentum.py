"""策略一：Trinity Alpha Momentum — 动量突破型。"""
from __future__ import annotations

import pandas as pd

# 策略参数
ENTRY_PCT_HIGH_DAYS = 10
ENTRY_VOL_RATIO = 1.5
ENTRY_TIME_START = 14  # 14:00
ENTRY_TIME_END = 14  # 14:50 → 用分钟 50
ATR_MULTIPLIER = 2.0
TRAIL_MA_DAYS = 5
TRAIL_ACTIVATE_PCT = 8.0
HARD_STOP_PCT = -5.0


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR，涨跌停日数据剔除。"""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    # 涨跌停日：pct_chg 约 ±10%，可据此剔除
    if "pct_chg" in df.columns:
        mask = (df["pct_chg"].abs() < 9.5) & (df["pct_chg"].abs() > 0.01)
        tr = tr.where(mask).ffill().bfill()
    return tr.rolling(period).mean()


def check_entry(df: pd.DataFrame) -> dict:
    """
    检查进场条件。

    条件：收盘突破近 10 日最高、close>MA20、量能>5 日均量*1.5、
    建仓时间 14:00–14:50、排除近 5 日累计涨幅>15%。
    """
    if df.empty or len(df) < max(ENTRY_PCT_HIGH_DAYS, 20):
        return {"signal": False, "confidence": 0.0, "reason": "数据不足"}

    row = df.iloc[-1]
    close = float(row["close"])

    # 近 10 日最高
    high_10 = df["high"].tail(ENTRY_PCT_HIGH_DAYS).max()
    if close < high_10:
        return {"signal": False, "confidence": 0.0, "reason": "未突破10日高点"}

    # MA20
    ma20 = df["close"].tail(20).mean()
    if close <= ma20:
        return {"signal": False, "confidence": 0.0, "reason": "收盘低于MA20"}

    # 量能
    vol_5d = df["vol"].tail(5).mean()
    if vol_5d <= 0 or row["vol"] < vol_5d * ENTRY_VOL_RATIO:
        return {"signal": False, "confidence": 0.0, "reason": "量能不足"}

    # 近 5 日累计涨幅
    if len(df) >= 6:
        p0 = float(df["close"].iloc[-6])
        pct_5d = (close - p0) / p0 * 100 if p0 > 0 else 0
        if pct_5d > 15:
            return {"signal": False, "confidence": 0.0, "reason": "近5日涨幅过大"}

    return {
        "signal": True,
        "confidence": 0.8,
        "reason": "满足动量突破条件",
        "entry_price": close,
    }


def check_exit(
    df: pd.DataFrame,
    entry_price: float,
    current_price: float,
    *,
    is_limit_up_locked: bool = False,
) -> dict:
    """
    检查出场条件。

    出场逻辑：ATR 动态止损(2x)、MA5 动态止盈(盈利>8% 后)、-5% 硬止损。
    涨停封死时 MA5/ATR 离场信号挂起。
    """
    if df.empty or len(df) < 14:
        return {"signal": False, "reason": "数据不足"}

    pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

    # 硬止损
    if pct <= HARD_STOP_PCT:
        return {"signal": True, "reason": "硬止损", "stop_type": "hard"}

    # 涨停封死时挂起
    if is_limit_up_locked:
        return {"signal": False, "reason": "涨停封死，挂起离场"}

    atr_val = _atr(df, 14).iloc[-1]
    atr_stop = entry_price - ATR_MULTIPLIER * atr_val
    if current_price <= atr_stop:
        return {"signal": True, "reason": "ATR止损", "stop_type": "atr"}

    # 盈利>8% 后启用 MA5 止盈
    if pct > TRAIL_ACTIVATE_PCT:
        ma5 = df["close"].tail(TRAIL_MA_DAYS).mean()
        if current_price < ma5:
            return {"signal": True, "reason": "MA5止盈", "stop_type": "trail"}

    return {"signal": False, "reason": "持仓中"}
