"""
Trinity Guard — 日内流程编排（四阶段版）

阶段划分：
  P0  9:15-9:25  集合竞价前侦察（pre_open）
  P1  9:25-9:35  紧急熔断检查（circuit_breaker）
  P2  9:35-11:30 上午主战场（open_session）
  P3  11:30-13:00 午休整合（mid_session）
  P4  13:00-14:50 下午再确认（close_session）
  P5  盘后         复盘 + T+1 结算

P级参数分层：
  P1 宪法层：硬风控阈值（不可学习）
  P2 策略规则层：MSI/CGI 阈值、仓位上限（回测验证后更新）
  P3 权重学习层：各阶段因子权重（模拟盘持续进化）
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import yaml  # type: ignore[import-untyped]
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.msi import MSIState, compute_msi
from src.core.cgi import CGIState, compute_cgi
from src.core.lri import compute_lri
from src.agents.system_a import SystemA
from src.agents.system_b import SystemB
from src.agents.system_c import SystemC
from src.agents.meta_judge import MetaJudge
from src.funnel.l0_filter import run_l0_filter
from src.funnel.l1_sector import run_l1_match
from src.funnel.l2_debate import run_l2_debate
from src.execution.brokers import create_broker
from src.execution.order_manager import OrderManager
from src.execution.risk_control import RiskControl
from src.review.daily_review import DailyReview

try:
    sys.path.insert(0, "/app/shared")
    from storage.qiniu_storage import QiniuStorage
    _storage = QiniuStorage(bucket_name="trinity-proddev-data")
except Exception:
    _storage = None


@dataclass
class P2Params:
    """
    P2 策略规则层参数（回测验证后可更新，不可盘中自动修改）

    每个阶段独立权重，支持多盈利目标模型切换。
    """
    msi_attack_threshold: float = 0.65
    msi_watch_threshold: float = 0.40
    cgi_crowded_threshold: float = 0.30

    pre_open_weight: float = 1.0
    open_session_weight: float = 1.0
    mid_session_weight: float = 0.6
    close_session_weight: float = 0.8

    pre_open_data_ratio: float = 1.0
    open_session_prev_ratio: float = 0.7
    open_session_curr_ratio: float = 0.3
    mid_session_prev_ratio: float = 0.4
    mid_session_curr_ratio: float = 0.6
    close_session_prev_ratio: float = 0.3
    close_session_curr_ratio: float = 0.7

    target_profit_pct: float = 0.03
    max_candidates_per_session: int = 5

    @classmethod
    def load(cls, path: str = "config/p2_params.yaml") -> "P2Params":
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})
        except FileNotFoundError:
            return cls()

    def save(self, path: str = "config/p2_params.yaml") -> None:
        import dataclasses
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(dataclasses.asdict(self), f, allow_unicode=True)


@dataclass
class SessionContext:
    """单阶段决策上下文"""
    session: str
    msi_state: str
    cgi_state: str
    prev_daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    curr_daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    data_weight_prev: float = 1.0
    data_weight_curr: float = 0.0
    target_sectors: list[str] = field(default_factory=list)
    target_profit_pct: float = 0.03


class TrinityGuardEngine:
    """主引擎：四阶段日内状态机"""

    def __init__(self):
        self.broker = create_broker()
        self.rc = RiskControl()
        self.om = OrderManager(self.broker, self.rc)
        self.judge = MetaJudge()
        self.sys_a = SystemA()
        self.sys_b = SystemB()
        self.sys_c = SystemC()
        self.reviewer = DailyReview()
        self.p2 = P2Params.load()

        self.msi_state: MSIState = MSIState.DEFENSE
        self.cgi_state: CGIState = CGIState.CROWDED_TRAP
        self.halted: bool = False

        self._msi_history: list[dict] = []
        self._cgi_history: list[dict] = []
        self._verdicts_history: list[dict] = []
        self._session_results: dict[str, list[dict]] = {}

        self._prev_daily: pd.DataFrame = pd.DataFrame()
        self._curr_daily: pd.DataFrame = pd.DataFrame()
        self._target_sectors: list[str] = []

    async def start(self):
        logger.info("=" * 60)
        logger.info("Trinity Guard 引擎启动（四阶段版）")
        logger.info("=" * 60)

        ok = await self.broker.connect()
        if not ok:
            logger.error("券商连接失败，退出")
            return

        balance = await self.broker.get_balance()
        self.rc.update_capital(balance.get("total", 1_000_000))
        logger.info(f"账户总资产: {balance.get('total', 0):,.0f}")

        await self._run_daily_loop()

    async def _run_daily_loop(self):
        """四阶段日内主循环"""

        logger.info("--- P0：集合竞价前侦察 (9:15-9:25) ---")
        await self._phase_pre_open()

        logger.info("--- P1：熔断检查 (9:25-9:35) ---")
        if await self._phase_circuit_breaker():
            logger.error("熔断触发，今日停止交易")
            await self._phase_post_market()
            return

        logger.info("--- P2：上午主战场 (9:35-11:30) ---")
        await self._phase_open_session()

        logger.info("--- P3：午休整合 (11:30-13:00) ---")
        await self._phase_mid_session()

        logger.info("--- P4：下午再确认 (13:00-14:50) ---")
        await self._phase_close_session()

        logger.info("--- P5：盘后复盘 ---")
        await self._phase_post_market()

        logger.info("=" * 60)
        logger.info("Trinity Guard 日内流程结束")
        logger.info("=" * 60)

    async def _phase_pre_open(self):
        """
        P0：9:15-9:25 集合竞价前侦察
        数据源：前一日全量数据 + 盘前新闻
        权重：100% 前日数据（当日数据尚未产生）
        """
        try:
            import tushare as ts
            pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))

            today_str = datetime.now().strftime("%Y%m%d")
            cal = pro.trade_cal(exchange="SSE", is_open="1", limit=2, end_date=today_str)
            if len(cal) >= 2:
                prev_str = cal.iloc[1]["cal_date"]
                self._prev_daily = pro.daily(trade_date=prev_str) or pd.DataFrame()
            else:
                self._prev_daily = pd.DataFrame()

            news_summary = await self._fetch_preopen_news()
            self._target_sectors = await self.sys_a.generate_target_sectors(news_summary)
            logger.info(f"  P0 目标行业: {self._target_sectors}")
            logger.info(f"  P0 前日数据: {len(self._prev_daily)} 只")

        except Exception as e:
            logger.error(f"P0 侦察异常: {e}")
            self._prev_daily = pd.DataFrame()
            self._target_sectors = []

    async def _phase_circuit_breaker(self) -> bool:
        """
        P1：9:25-9:35 紧急熔断检查
        只能产生熔断信号，不能产生进攻信号
        """
        if self.rc._state.get("halted", False):
            logger.error("连续亏损停机中")
            return True

        balance = await self.broker.get_balance()
        dd = self.rc.check_daily_drawdown(balance.get("total", 0))
        if dd.get("triggered"):
            logger.error(f"日内回撤熔断: {dd.get('reason', '')}")
            return True

        if not self._prev_daily.empty:
            index_chg = self._prev_daily[
                self._prev_daily["ts_code"].isin(["000001.SH", "399001.SZ"])
            ]["pct_chg"].mean() if "ts_code" in self._prev_daily.columns else 0.0
            if index_chg < -3.0:
                logger.warning(f"  前日指数大跌 {index_chg:.1f}%，触发预防性熔断")
                return True

        return False

    async def _phase_open_session(self):
        """
        P2：9:35-11:30 上午主战场
        数据权重：prev_ratio:curr_ratio = P2参数控制
        主状态定性在此阶段完成
        """
        await self._compute_market_state(session="open_session")

        if self.msi_state == MSIState.DEFENSE:
            logger.info("  MSI=防守，上午不开新仓")
            return
        if self.cgi_state == CGIState.CROWDED_TRAP:
            logger.info("  CGI=拥挤陷阱，上午禁止博弈")
            return

        ctx = SessionContext(
            session="open_session",
            msi_state=self.msi_state.value,
            cgi_state=self.cgi_state.value,
            prev_daily=self._prev_daily,
            curr_daily=self._curr_daily,
            data_weight_prev=self.p2.open_session_prev_ratio,
            data_weight_curr=self.p2.open_session_curr_ratio,
            target_sectors=self._target_sectors,
            target_profit_pct=self.p2.target_profit_pct,
        )
        results = await self._run_session_debate(ctx)
        self._session_results["open_session"] = results
        await self._execute_approved(results, session="open_session")

    async def _phase_mid_session(self):
        """
        P3：11:30-13:00 午休整合
        数据权重：前日 40% + 上午 60%
        用于调整持仓，不开新仓（除非上午强势延续）
        """
        await self._compute_market_state(session="mid_session")

        if self.msi_state == MSIState.DEFENSE:
            logger.info("  MSI=防守，午休不操作")
            return

        ctx = SessionContext(
            session="mid_session",
            msi_state=self.msi_state.value,
            cgi_state=self.cgi_state.value,
            prev_daily=self._prev_daily,
            curr_daily=self._curr_daily,
            data_weight_prev=self.p2.mid_session_prev_ratio,
            data_weight_curr=self.p2.mid_session_curr_ratio,
            target_sectors=self._target_sectors,
            target_profit_pct=self.p2.target_profit_pct,
        )

        open_results = self._session_results.get("open_session", [])
        if not open_results:
            logger.info("  上午无成交，午休跳过辩论")
            return

        logger.info(f"  午休整合：上午通过 {len(open_results)} 只，评估持仓延续性")
        results = await self._run_session_debate(ctx)
        self._session_results["mid_session"] = results

    async def _phase_close_session(self):
        """
        P4：13:00-14:50 下午再确认
        数据权重：前日 30% + 全天 70%
        尾盘集合竞价前最后决策窗口
        """
        await self._compute_market_state(session="close_session")

        balance = await self.broker.get_balance()
        dd = self.rc.check_daily_drawdown(balance.get("total", 0))
        if dd.get("triggered"):
            logger.error("  下午回撤触发，停止操作")
            return

        if self.msi_state == MSIState.DEFENSE:
            logger.info("  MSI=防守，下午不开新仓")
            return

        ctx = SessionContext(
            session="close_session",
            msi_state=self.msi_state.value,
            cgi_state=self.cgi_state.value,
            prev_daily=self._prev_daily,
            curr_daily=self._curr_daily,
            data_weight_prev=self.p2.close_session_prev_ratio,
            data_weight_curr=self.p2.close_session_curr_ratio,
            target_sectors=self._target_sectors,
            target_profit_pct=self.p2.target_profit_pct,
        )
        results = await self._run_session_debate(ctx)
        self._session_results["close_session"] = results
        await self._execute_approved(results, session="close_session")

    async def _compute_market_state(self, session: str):
        """
        计算 MSI/CGI 状态，支持多阶段复用
        """
        try:
            import tushare as ts
            pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))

            today_str = datetime.now().strftime("%Y%m%d")
            daily = pro.daily(trade_date=today_str)
            if daily is None or daily.empty:
                cal = pro.trade_cal(exchange="SSE", is_open="1", limit=1, end_date=today_str)
                if not cal.empty:
                    today_str = cal.iloc[0]["cal_date"]
                    daily = pro.daily(trade_date=today_str)

            if daily is None or daily.empty:
                logger.warning(f"  [{session}] 无日线数据，保持防守")
                self.msi_state = MSIState.DEFENSE
                self.cgi_state = CGIState.CROWDED_TRAP
                return

            self._curr_daily = daily
            total_amount = daily["amount"].sum() / 1e4

            prev_date = pro.trade_cal(exchange="SSE", is_open="1", limit=2, end_date=today_str)
            if len(prev_date) >= 2:
                prev_str = prev_date.iloc[1]["cal_date"]
                prev_daily = pro.daily(trade_date=prev_str)
                if self._prev_daily.empty and prev_daily is not None:
                    self._prev_daily = prev_daily
                yesterday_strong = (
                    prev_daily[prev_daily["pct_chg"] > 5.0]
                    if prev_daily is not None else pd.DataFrame()
                )
            else:
                yesterday_strong = pd.DataFrame()

            index_daily = pro.index_daily(ts_code="000001.SH", limit=61)
            if index_daily is not None and len(index_daily) >= 60:
                idx = index_daily.sort_values("trade_date").reset_index(drop=True)
                index_close = idx.iloc[-1]["close"]
                index_ma60 = idx["close"].tail(60).mean()
            else:
                index_close = 3000.0
                index_ma60 = 3000.0

            msi_result = compute_msi(
                total_amount=total_amount,
                yesterday_strong=yesterday_strong,
                today_daily=daily,
                index_close=index_close,
                index_ma60=index_ma60,
            )
            self.msi_state = msi_result.state
            self._msi_history.append({
                "time": datetime.now().isoformat(),
                "session": session,
                "state": msi_result.state.value,
                "composite": msi_result.composite,
            })

            limit_up_count = len(daily[daily["pct_chg"] >= 9.5])
            leader_status = (
                "涨停" if limit_up_count > 10
                else "分歧承接" if limit_up_count > 3
                else "走弱"
            )
            follower_profit = (
                daily[daily["pct_chg"] > 3.0]["pct_chg"].mean()
                if len(daily[daily["pct_chg"] > 3.0]) > 0 else 0.0
            )
            breakout_count = len(daily[daily["pct_chg"] > 5.0])

            cgi_result = compute_cgi(
                leader_status=leader_status,
                follower_profit=follower_profit,
                breakout_count=breakout_count,
                limit_up_quality=0.5,
                tail_anomaly=False,
            )
            self.cgi_state = cgi_result.state
            self._cgi_history.append({
                "time": datetime.now().isoformat(),
                "session": session,
                "state": cgi_result.state.value,
            })

            logger.info(
                f"  [{session}] MSI={self.msi_state.value} "
                f"CGI={self.cgi_state.value} "
                f"量能={total_amount:.0f}亿"
            )

        except Exception as e:
            logger.error(f"  [{session}] 市场状态计算异常: {e}，保持防守")
            self.msi_state = MSIState.DEFENSE
            self.cgi_state = CGIState.CROWDED_TRAP

    async def _run_session_debate(self, ctx: SessionContext) -> list[dict]:
        """
        单阶段漏斗筛选 + L2三系统辩论
        根据阶段数据权重合并候选池
        """
        sectors = ctx.target_sectors or self._target_sectors

        l0_candidates = run_l0_filter(target_sectors=sectors)
        if l0_candidates.empty:
            logger.info(f"  [{ctx.session}] L0 无候选标的")
            return []

        l1_candidates = run_l1_match(l0_candidates, sectors)
        if l1_candidates.empty:
            logger.info(f"  [{ctx.session}] L1 无匹配标的")
            return []

        max_cands = self.p2.max_candidates_per_session
        l1_top = l1_candidates.head(max_cands)
        logger.info(f"  [{ctx.session}] L1 候选: {len(l1_top)} 只")

        approved = await run_l2_debate(
            candidates=l1_top,
            msi_state=ctx.msi_state,
            cgi_state=ctx.cgi_state,
            sys_a=self.sys_a,
            sys_b=self.sys_b,
            sys_c=self.sys_c,
            meta_judge=self.judge,
        )

        for item in approved:
            item["session"] = ctx.session
            item["data_weight_prev"] = ctx.data_weight_prev
            item["data_weight_curr"] = ctx.data_weight_curr
            item["target_profit_pct"] = ctx.target_profit_pct

        self._verdicts_history.extend([
            {
                "stock_code": item["stock_code"],
                "session": ctx.session,
                "consensus": item.get("consensus", ""),
                "action": item.get("action", ""),
                "minority_system": item.get("verdicts", {}).get("minority", ""),
                "minority_reason": "",
                "minority_ignored_reason": "",
            }
            for item in approved
        ])

        return approved

    async def _execute_approved(self, approved: list[dict], session: str):
        """
        执行通过辩论的标的
        优先级链：硬风控 > 执行风险调制 > MetaJudge共识 > 仓位计算
        """
        if not approved:
            return

        balance = await self.broker.get_balance()
        total_capital = balance.get("total", 0)

        for item in approved:
            stock_code = item["stock_code"]
            row = item.get("row", {})
            price = float(row.get("close", row.get("amount", 0) / 100 if row.get("amount") else 10))
            position_pct = item.get("position_pct", 0.20 / 3)

            lri_result = compute_lri(
                daily_amount=float(row.get("amount", 0)),
                median_amount_30d=float(row.get("amount", 0)) * 0.8,
                near_limit_down=float(row.get("pct_chg", 0)) < -9.0,
                seal_fragile=False,
                volume_shrink=False,
                one_word_board=float(row.get("pct_chg", 0)) > 9.9,
            )
            if not lri_result.allowed:
                logger.info(f"  [{session}] LRI 否决 {stock_code}: {lri_result.reason}")
                continue

            buy_amount = total_capital * position_pct
            quantity = int(buy_amount / price / 100) * 100 if price > 0 else 0

            if quantity < 100:
                logger.info(f"  [{session}] {stock_code} 计算数量不足100股，跳过")
                continue

            result = await self.om.execute_buy(
                stock_code, price, quantity,
                context_snapshot={
                    "session": session,
                    "msi_state": self.msi_state.value,
                    "cgi_state": self.cgi_state.value,
                    "entry_price": price,
                    "verdicts": item.get("verdicts", {}),
                    "consensus": item.get("consensus", ""),
                    "target_profit_pct": item.get("target_profit_pct", 0.03),
                    "execution_risk": item.get("execution_risk", {}),
                    "probe_only": item.get("probe_only", False),
                },
            )
            logger.info(
                f"  [{session}] {stock_code} 下单: "
                f"{'成功' if result.success else '失败'} | {result.message}"
            )

    async def _phase_post_market(self):
        """P5：盘后复盘 + T+1 结算"""
        positions = await self.broker.get_positions()
        pos_dicts = [
            {
                "stock_code": p.stock_code,
                "quantity": p.quantity,
                "cost_price": p.cost_price,
            }
            for p in positions
        ]

        orders = self.om.get_today_orders()

        try:
            import tushare as ts
            pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))
            today_str = datetime.now().strftime("%Y%m%d")
            daily = pro.daily(trade_date=today_str)
            if daily is not None and not daily.empty:
                market_opps = [
                    {
                        "stock_code": row["ts_code"],
                        "pct_chg": row["pct_chg"],
                        "sector": "",
                    }
                    for _, row in daily[daily["pct_chg"] >= 5.0].iterrows()
                ]
            else:
                market_opps = []
        except Exception:
            market_opps = []

        report = self.reviewer.run_review(
            orders=orders,
            positions=pos_dicts,
            msi_history=self._msi_history,
            cgi_history=self._cgi_history,
            verdicts_history=self._verdicts_history,
            market_opportunities=market_opps,
            session_results=self._session_results,
        )

        brief = self.reviewer.generate_brief_report(report)
        logger.info(f"\n{'='*40}\n日报：\n{brief}\n{'='*40}")

        pnl_summary = self.om.get_today_pnl_summary()
        if pnl_summary["buy_count"] + pnl_summary["sell_count"] > 0:
            total = await self.broker.get_balance()
            pnl_pct = pnl_summary["realized_pnl"] / total.get("total", 1) * 100
            self.rc.record_daily_pnl(pnl_pct)

        if hasattr(self.broker, "settle_t1"):
            self.broker.settle_t1()
            logger.info("T+1 结算完成")

        if _storage:
            today = datetime.now().strftime("%Y-%m-%d")
            date_key = today.replace("-", "")
            snapshot = {
                "date": today,
                "msi_history": self._msi_history,
                "cgi_history": self._cgi_history,
                "verdicts_history": self._verdicts_history,
                "pnl_summary": pnl_summary,
            }
            remote_key = QiniuStorage.make_key("snapshots", date_key, f"daily_snapshot_{today}.json")
            local_path = f"data/snapshots/daily_snapshot_{today}.json"
            ok = _storage.save_json(remote_key, snapshot, local_path=local_path)
            logger.info(f"日内快照{'已上传七牛' if ok else '已保存本地'}: {remote_key}")

    async def _fetch_preopen_news(self) -> str:
        """获取盘前新闻摘要（占位，接入新闻API后替换）"""
        return ""


async def main():
    engine = TrinityGuardEngine()
    await engine.start()


if __name__ == "__main__":
    logger.add(
        "data/logs/trinity_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="30 days",
        level="INFO",
        encoding="utf-8",
    )
    asyncio.run(main())
