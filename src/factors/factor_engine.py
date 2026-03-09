"""
因子引擎 — A-F 六类因子计算

A类：市场情绪因子（舆论/新闻量化）
B类：技术结构因子（K线/均线/量价）
C类：资金流向因子（主力/散户/北向）
D类：操纵识别因子（异常量价/对倒/尾盘异动）
E类：赛道景气因子（行业轮动/板块强度）
F类：执行风险因子（流动性/滑点/T+1约束）— 已在 l2_debate.py 实现，此处提供统一接口

输出：FactorBundle，供 weight_model.py 按盈利目标加权
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from loguru import logger


@dataclass
class FactorBundle:
    """单只股票的全类因子快照"""
    stock_code: str
    trade_date: str
    session: str = "open_session"

    a_sentiment: dict[str, float] = field(default_factory=dict)
    b_technical: dict[str, float] = field(default_factory=dict)
    c_capital_flow: dict[str, float] = field(default_factory=dict)
    d_manipulation: dict[str, float] = field(default_factory=dict)
    e_sector: dict[str, float] = field(default_factory=dict)
    f_execution: dict[str, float] = field(default_factory=dict)

    composite_score: float = 0.0
    valid: bool = True
    reason: str = ""

    def to_flat_dict(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for prefix, d in [
            ("A", self.a_sentiment),
            ("B", self.b_technical),
            ("C", self.c_capital_flow),
            ("D", self.d_manipulation),
            ("E", self.e_sector),
            ("F", self.f_execution),
        ]:
            for k, v in d.items():
                out[f"{prefix}_{k}"] = float(v)
        return out


def compute_a_sentiment(
    news_df: pd.DataFrame | None = None,
    stock_code: str = "",
) -> dict[str, float]:
    """
    A类：市场情绪因子

    news_df 列：title, content, source, pub_time, sentiment_score(-1~1)
    若无外部 NLP 结果，退化为规则打分（0.5 中性）。
    """
    if news_df is None or news_df.empty:
        return {
            "news_count": 0.0,
            "avg_sentiment": 0.5,
            "positive_ratio": 0.5,
            "negative_ratio": 0.0,
            "hot_topic_flag": 0.0,
        }

    stock_news = news_df[news_df.get("stock_code", pd.Series(dtype=str)) == stock_code] \
        if "stock_code" in news_df.columns else news_df

    if stock_news.empty:
        return {
            "news_count": 0.0,
            "avg_sentiment": 0.5,
            "positive_ratio": 0.5,
            "negative_ratio": 0.0,
            "hot_topic_flag": 0.0,
        }

    scores = stock_news["sentiment_score"].fillna(0.0) if "sentiment_score" in stock_news.columns \
        else pd.Series([0.0] * len(stock_news))

    avg_s = float(scores.mean())
    pos_ratio = float((scores > 0.2).mean())
    neg_ratio = float((scores < -0.2).mean())
    hot_flag = 1.0 if len(stock_news) >= 5 else 0.0

    return {
        "news_count": float(len(stock_news)),
        "avg_sentiment": (avg_s + 1.0) / 2.0,
        "positive_ratio": pos_ratio,
        "negative_ratio": neg_ratio,
        "hot_topic_flag": hot_flag,
    }


def compute_b_technical(
    hist: pd.DataFrame,
    row: pd.Series | None = None,
) -> dict[str, float]:
    """
    B类：技术结构因子

    hist: 近 30 日日线（含 open/high/low/close/vol/pct_chg）
    row:  当日行情（可选，用于实时补充）
    """
    if hist is None or len(hist) < 5:
        return {
            "above_ma5": 0.0,
            "above_ma10": 0.0,
            "above_ma20": 0.0,
            "vol_ratio_5d": 1.0,
            "pct_chg_5d": 0.0,
            "atr_ratio": 0.02,
            "box_breakout": 0.0,
            "macd_cross": 0.0,
            "rsi14": 50.0,
            "upper_shadow_ratio": 0.0,
        }

    hist = hist.sort_values("trade_date").reset_index(drop=True)
    close = hist["close"]
    vol = hist["vol"]
    current_close = float(close.iloc[-1])

    ma5 = float(close.tail(5).mean())
    ma10 = float(close.tail(10).mean()) if len(hist) >= 10 else ma5
    ma20 = float(close.tail(20).mean()) if len(hist) >= 20 else ma10

    vol_5d = float(vol.tail(5).mean())
    vol_ratio = float(vol.iloc[-1] / vol_5d) if vol_5d > 0 else 1.0

    pct_5d = float(hist["pct_chg"].tail(5).sum()) if "pct_chg" in hist.columns else 0.0

    high_10 = float(hist["high"].tail(10).iloc[:-1].max()) if "high" in hist.columns and len(hist) >= 10 else current_close
    box_breakout = 1.0 if current_close > high_10 else 0.0

    atr_ratio = 0.02
    if "high" in hist.columns and "low" in hist.columns and len(hist) >= 14:
        tr = pd.concat([
            hist["high"] - hist["low"],
            (hist["high"] - close.shift(1)).abs(),
            (hist["low"] - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr14 = float(tr.rolling(14).mean().iloc[-1])
        atr_ratio = atr14 / current_close if current_close > 0 else 0.02

    macd_cross = 0.0
    if len(hist) >= 26:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        if float(dif.iloc[-1]) > float(dea.iloc[-1]) and float(dif.iloc[-2]) <= float(dea.iloc[-2]):
            macd_cross = 1.0
        elif float(dif.iloc[-1]) < float(dea.iloc[-1]) and float(dif.iloc[-2]) >= float(dea.iloc[-2]):
            macd_cross = -1.0

    rsi14 = 50.0
    if len(hist) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if float(loss.iloc[-1]) > 0 else 100.0
        rsi14 = float(100 - 100 / (1 + rs))

    upper_shadow = 0.0
    if "high" in hist.columns and "open" in hist.columns:
        last = hist.iloc[-1]
        body_top = max(float(last["open"]), current_close)
        shadow = float(last["high"]) - body_top
        upper_shadow = shadow / current_close if current_close > 0 else 0.0

    return {
        "above_ma5": 1.0 if current_close > ma5 else 0.0,
        "above_ma10": 1.0 if current_close > ma10 else 0.0,
        "above_ma20": 1.0 if current_close > ma20 else 0.0,
        "vol_ratio_5d": min(vol_ratio, 5.0),
        "pct_chg_5d": pct_5d,
        "atr_ratio": atr_ratio,
        "box_breakout": box_breakout,
        "macd_cross": macd_cross,
        "rsi14": rsi14,
        "upper_shadow_ratio": upper_shadow,
    }


def compute_c_capital_flow(
    moneyflow_row: pd.Series | None = None,
    north_flow: float = 0.0,
) -> dict[str, float]:
    """
    C类：资金流向因子

    moneyflow_row: Tushare moneyflow 单行（buy_lg_amount/sell_lg_amount 等）
    north_flow: 北向资金净流入（亿元）
    """
    if moneyflow_row is None:
        return {
            "main_net_inflow": 0.0,
            "main_net_ratio": 0.0,
            "retail_net_ratio": 0.0,
            "north_flow_score": 0.5,
            "large_order_ratio": 0.0,
        }

    buy_lg = float(moneyflow_row.get("buy_lg_amount", 0) or 0)
    sell_lg = float(moneyflow_row.get("sell_lg_amount", 0) or 0)
    buy_md = float(moneyflow_row.get("buy_md_amount", 0) or 0)
    sell_md = float(moneyflow_row.get("sell_md_amount", 0) or 0)
    buy_sm = float(moneyflow_row.get("buy_sm_amount", 0) or 0)
    sell_sm = float(moneyflow_row.get("sell_sm_amount", 0) or 0)
    total = float(moneyflow_row.get("trade_amount", 1) or 1)

    main_net = (buy_lg - sell_lg) + (buy_md - sell_md)
    retail_net = buy_sm - sell_sm
    large_order = (buy_lg + sell_lg) / total if total > 0 else 0.0

    north_score = 0.5
    if north_flow > 20:
        north_score = 1.0
    elif north_flow > 5:
        north_score = 0.7
    elif north_flow < -20:
        north_score = 0.0
    elif north_flow < -5:
        north_score = 0.3

    return {
        "main_net_inflow": main_net / 1e4,
        "main_net_ratio": main_net / total if total > 0 else 0.0,
        "retail_net_ratio": retail_net / total if total > 0 else 0.0,
        "north_flow_score": north_score,
        "large_order_ratio": min(large_order, 1.0),
    }


def compute_d_manipulation(
    hist: pd.DataFrame,
    row: pd.Series | None = None,
    intraday_ticks: pd.DataFrame | None = None,
) -> dict[str, float]:
    """
    D类：操纵识别因子

    检测：尾盘异动、对倒嫌疑、量价背离、封板质量异常
    输出 0-1 风险分（越高越可疑）
    """
    result = {
        "tail_anomaly_score": 0.0,
        "wash_trade_score": 0.0,
        "price_vol_diverge": 0.0,
        "limit_quality_score": 1.0,
        "manipulation_composite": 0.0,
    }

    if hist is None or len(hist) < 5:
        return result

    hist = hist.sort_values("trade_date").reset_index(drop=True)
    vol = hist["vol"]

    vol_5d_mean = float(vol.tail(5).mean())
    vol_5d_std = float(vol.tail(5).std()) if len(hist) >= 5 else 0.0

    if vol_5d_mean > 0 and vol_5d_std > 0:
        last_vol_z = (float(vol.iloc[-1]) - vol_5d_mean) / vol_5d_std
        if last_vol_z > 3.0:
            result["wash_trade_score"] = min(last_vol_z / 5.0, 1.0)

    if len(hist) >= 3:
        pct_3d = hist["pct_chg"].tail(3).sum() if "pct_chg" in hist.columns else 0.0
        vol_3d_ratio = float(vol.tail(3).mean()) / vol_5d_mean if vol_5d_mean > 0 else 1.0
        if pct_3d < 0 and vol_3d_ratio > 1.5:
            result["price_vol_diverge"] = min((vol_3d_ratio - 1.0) * 0.5, 1.0)
        elif pct_3d > 5 and vol_3d_ratio < 0.7:
            result["price_vol_diverge"] = 0.3

    if row is not None:
        pct_chg = float(row.get("pct_chg", 0))
        turnover = float(row.get("turnover_rate", 0))
        if pct_chg >= 9.5:
            limit_quality = 1.0 - min(turnover / 30.0, 0.8)
            result["limit_quality_score"] = limit_quality
            if turnover > 20:
                result["tail_anomaly_score"] = 0.6

    composite = (
        result["tail_anomaly_score"] * 0.3
        + result["wash_trade_score"] * 0.3
        + result["price_vol_diverge"] * 0.2
        + (1.0 - result["limit_quality_score"]) * 0.2
    )
    result["manipulation_composite"] = min(composite, 1.0)

    return result


def compute_e_sector(
    industry: str,
    sector_daily: pd.DataFrame | None = None,
    market_daily: pd.DataFrame | None = None,
) -> dict[str, float]:
    """
    E类：赛道景气因子

    sector_daily: 同行业股票当日行情（含 pct_chg）
    market_daily: 全市场当日行情（用于相对强度计算）
    """
    result = {
        "sector_avg_pct": 0.0,
        "sector_leader_pct": 0.0,
        "sector_relative_strength": 0.0,
        "sector_breadth": 0.5,
        "hot_sector_flag": 0.0,
    }

    if sector_daily is None or sector_daily.empty:
        return result

    sector_pct = sector_daily["pct_chg"].dropna() if "pct_chg" in sector_daily.columns else pd.Series(dtype=float)
    if sector_pct.empty:
        return result

    avg_pct = float(sector_pct.mean())
    leader_pct = float(sector_pct.max())
    breadth = float((sector_pct > 0).mean())

    market_avg = 0.0
    if market_daily is not None and not market_daily.empty and "pct_chg" in market_daily.columns:
        market_avg = float(market_daily["pct_chg"].mean())

    relative_strength = avg_pct - market_avg

    result["sector_avg_pct"] = avg_pct
    result["sector_leader_pct"] = leader_pct
    result["sector_relative_strength"] = relative_strength
    result["sector_breadth"] = breadth
    result["hot_sector_flag"] = 1.0 if avg_pct > 2.0 and breadth > 0.6 else 0.0

    return result


def compute_f_execution_summary(
    row: pd.Series,
    msi_state: str = "观望",
    cgi_state: str = "拥挤陷阱",
) -> dict[str, float]:
    """
    F类：执行风险因子摘要（与 l2_debate.compute_execution_risk 保持一致）
    此处仅提供 FactorBundle 所需的标量摘要，不重复完整逻辑。
    """
    amount = float(row.get("amount", 0))
    turnover = float(row.get("turnover_rate", 0))
    pct_chg = float(row.get("pct_chg", 0))

    liquidity = min(amount / 5000.0, 1.0) if amount > 0 else 0.1
    slippage = 0.0
    if amount < 1000:
        slippage += 0.4
    if turnover > 30:
        slippage += 0.3
    slippage = min(slippage, 1.0)

    t1_difficulty = 0.0
    if pct_chg >= 9.5:
        t1_difficulty = 0.8
    elif pct_chg >= 7.0:
        t1_difficulty = 0.5

    msi_penalty = 0.3 if msi_state == "防守" else 0.0
    cgi_penalty = 0.4 if cgi_state == "拥挤陷阱" else 0.0

    composite_risk = (
        (1.0 - liquidity) * 0.3
        + slippage * 0.2
        + t1_difficulty * 0.3
        + msi_penalty * 0.1
        + cgi_penalty * 0.1
    )

    return {
        "liquidity_score": liquidity,
        "slippage_risk": slippage,
        "t1_exit_difficulty": t1_difficulty,
        "composite_risk": min(composite_risk, 1.0),
    }


def build_factor_bundle(
    stock_code: str,
    trade_date: str,
    row: pd.Series,
    hist: pd.DataFrame | None = None,
    news_df: pd.DataFrame | None = None,
    moneyflow_row: pd.Series | None = None,
    north_flow: float = 0.0,
    sector_daily: pd.DataFrame | None = None,
    market_daily: pd.DataFrame | None = None,
    msi_state: str = "观望",
    cgi_state: str = "拥挤陷阱",
    session: str = "open_session",
) -> FactorBundle:
    """
    构建单只股票的完整 FactorBundle

    Args:
        stock_code: 股票代码
        trade_date: 交易日 YYYYMMDD
        row: 当日行情 Series
        hist: 近 30 日历史日线
        news_df: 新闻情绪 DataFrame
        moneyflow_row: 资金流向单行
        north_flow: 北向资金净流入（亿元）
        sector_daily: 同行业当日行情
        market_daily: 全市场当日行情
        msi_state: 市场强度状态
        cgi_state: 资金博弈状态
        session: 当前交易阶段
    """
    industry = str(row.get("industry", ""))

    a = compute_a_sentiment(news_df, stock_code)
    b = compute_b_technical(hist, row)
    c = compute_c_capital_flow(moneyflow_row, north_flow)
    d = compute_d_manipulation(hist, row)
    e = compute_e_sector(industry, sector_daily, market_daily)
    f = compute_f_execution_summary(row, msi_state, cgi_state)

    bundle = FactorBundle(
        stock_code=stock_code,
        trade_date=trade_date,
        session=session,
        a_sentiment=a,
        b_technical=b,
        c_capital_flow=c,
        d_manipulation=d,
        e_sector=e,
        f_execution=f,
    )

    return bundle


def batch_build_bundles(
    candidates: pd.DataFrame,
    trade_date: str,
    hist_map: dict[str, pd.DataFrame] | None = None,
    news_df: pd.DataFrame | None = None,
    moneyflow_map: dict[str, pd.Series] | None = None,
    north_flow: float = 0.0,
    sector_map: dict[str, pd.DataFrame] | None = None,
    market_daily: pd.DataFrame | None = None,
    msi_state: str = "观望",
    cgi_state: str = "拥挤陷阱",
    session: str = "open_session",
) -> list[FactorBundle]:
    """
    批量构建候选股的 FactorBundle 列表

    hist_map: {ts_code: hist_df}
    moneyflow_map: {ts_code: moneyflow_series}
    sector_map: {industry: sector_daily_df}
    """
    bundles: list[FactorBundle] = []

    for _, row in candidates.iterrows():
        code = str(row.get("ts_code", ""))
        industry = str(row.get("industry", ""))

        hist = (hist_map or {}).get(code)
        mf_row = (moneyflow_map or {}).get(code)
        sec_daily = (sector_map or {}).get(industry)

        try:
            bundle = build_factor_bundle(
                stock_code=code,
                trade_date=trade_date,
                row=row,
                hist=hist,
                news_df=news_df,
                moneyflow_row=mf_row,
                north_flow=north_flow,
                sector_daily=sec_daily,
                market_daily=market_daily,
                msi_state=msi_state,
                cgi_state=cgi_state,
                session=session,
            )
            bundles.append(bundle)
        except Exception as exc:
            logger.warning(f"FactorBundle 构建失败 {code}: {exc}")

    logger.info(f"FactorBundle 批量构建完成: {len(bundles)}/{len(candidates)} 只")
    return bundles
