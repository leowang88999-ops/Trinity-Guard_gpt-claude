"""
L0 硬过滤 — 零 API 成本，纯本地 CPU 计算
数据源：Tushare pro.daily + pro.stock_basic
"""

import os
from datetime import datetime

import pandas as pd
from loguru import logger

pro = None


def _get_pro():
    global pro
    if pro is None:
        import tushare as ts
        pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))
    return pro


def get_trade_date(date: str | None = None) -> str:
    """获取最近交易日，格式 YYYYMMDD"""
    if date:
        return date
    cal = _get_pro().trade_cal(exchange="SSE", is_open="1", limit=1,
                               end_date=datetime.now().strftime("%Y%m%d"))
    return cal.iloc[0]["cal_date"]


def load_basic_info() -> pd.DataFrame:
    """加载全市场股票基础信息（含行业）"""
    df = _get_pro().stock_basic(exchange="", list_status="L",
                                fields="ts_code,symbol,name,industry,market,list_date")
    return df


def load_daily(trade_date: str) -> pd.DataFrame:
    """加载指定交易日的日线行情"""
    df = _get_pro().daily(trade_date=trade_date)
    return df


def apply_hard_filters(df: pd.DataFrame) -> pd.DataFrame:
    """
    硬过滤条件（宪法第 4 章 L0）：
    - 涨幅 > 2%
    - 换手率 > 1%
    - 非 ST
    - 成交额前 500
    """
    filtered = df[
        (df["pct_chg"] > 2.0) &
        (df["turnover_rate"] > 1.0) &
        (~df["name"].str.contains("ST", na=False))
    ].copy()

    filtered = filtered.nlargest(500, "amount")
    return filtered


def apply_sector_filter(
    candidates: pd.DataFrame,
    target_sectors: list[str],
) -> pd.DataFrame:
    """
    行业图谱匹配（申万分类）
    target_sectors 由 System A 在 9:15 前产出
    """
    if not target_sectors:
        logger.warning("目标行业清单为空，跳过行业过滤")
        return candidates

    matched = candidates[candidates["industry"].isin(target_sectors)].copy()
    logger.info(f"行业匹配: {len(candidates)} → {len(matched)} (目标行业: {target_sectors})")
    return matched


def compute_technical_flags(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """
    为候选股计算技术标记：
    - MA20 上方
    - 成交量 > 5 日均量 x 1.5
    - 近 10 日箱体突破
    """
    codes = df["ts_code"].tolist()
    if not codes:
        return df

    end_dt = trade_date
    records = []
    for code in codes:
        try:
            hist = _get_pro().daily(ts_code=code, end_date=end_dt, limit=30)
            if hist is None or len(hist) < 20:
                continue
            hist = hist.sort_values("trade_date").reset_index(drop=True)

            ma20 = hist["close"].rolling(20).mean().iloc[-1]
            avg_vol_5 = hist["vol"].iloc[-6:-1].mean()
            high_10 = hist["close"].iloc[-11:-1].max()
            current = hist.iloc[-1]

            records.append({
                "ts_code": code,
                "above_ma20": current["close"] > ma20,
                "volume_breakout": current["vol"] > avg_vol_5 * 1.5,
                "box_breakout": current["close"] > high_10,
                "pct_chg_5d": hist["pct_chg"].iloc[-5:].sum(),
            })
        except Exception as e:
            logger.warning(f"技术指标计算失败 {code}: {e}")

    if not records:
        return df

    tech_df = pd.DataFrame(records)
    result = df.merge(tech_df, on="ts_code", how="inner")
    result = result[
        (result["above_ma20"]) &
        (result["volume_breakout"]) &
        (result["pct_chg_5d"] <= 15.0)
    ]
    return result


def run_l0_filter(
    trade_date: str | None = None,
    target_sectors: list[str] | None = None,
) -> pd.DataFrame:
    """
    L0 全流程：硬过滤 → 行业匹配 → 技术标记
    返回候选股 DataFrame
    """
    date = get_trade_date(trade_date)
    logger.info(f"L0 过滤启动 | 交易日: {date}")

    basic = load_basic_info()
    daily = load_daily(date)

    merged = daily.merge(basic[["ts_code", "name", "industry"]], on="ts_code", how="left")

    candidates = apply_hard_filters(merged)
    logger.info(f"硬过滤后: {len(candidates)} 只")

    if target_sectors:
        candidates = apply_sector_filter(candidates, target_sectors)

    if len(candidates) > 30:
        candidates = candidates.nlargest(30, "amount")
        logger.info("截断至 30 只进入技术筛选")

    result = compute_technical_flags(candidates, date)
    logger.info(f"L0 最终候选: {len(result)} 只")

    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    df = run_l0_filter(target_sectors=["半导体", "计算机应用", "光学光电子"])
    print(df[["ts_code", "name", "industry", "pct_chg", "amount"]].to_string())
