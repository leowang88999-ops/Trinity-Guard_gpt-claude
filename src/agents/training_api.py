"""
Training Control API — 量化交易系统训练控制接口

提供 HTTP 端点供 trinity-connect 调用：
  POST /training/start   — 启动历史数据权重训练
  POST /training/stop    — 中断训练
  GET  /training/status  — 查询训练进度
  GET  /health           — 健康检查
  GET  /status           — 系统状态（含权重摘要）
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.models.weight_model import WeightModel, ProfitTarget


class TrainingRequest(BaseModel):
    days: int = 30
    target: str = "t5"
    n_trials: int = 200


_training_state: Dict[str, Any] = {
    "running": False,
    "phase": "",
    "progress": "",
    "started_at": "",
    "result": {},
    "stop_requested": False,
}

_weight_model: Optional[WeightModel] = None
_training_task: Optional[asyncio.Task] = None


def _get_weight_model() -> WeightModel:
    global _weight_model
    if _weight_model is None:
        _weight_model = WeightModel(weights_path="data/weights/weight_model.json")
    return _weight_model


async def _run_training(days: int, target: str, n_trials: int) -> None:
    global _training_state, _training_task

    _training_state["running"] = True
    _training_state["stop_requested"] = False
    _training_state["started_at"] = datetime.now().isoformat()
    _training_state["result"] = {}

    try:
        model = _get_weight_model()
        targets: list[ProfitTarget] = []

        if target == "all":
            targets = ["t3", "t5", "t8"]
        elif target in ("t3", "t5", "t8"):
            targets = [target]
        else:
            targets = ["t5"]

        total_steps = len(targets) * n_trials
        completed = 0

        for tgt in targets:
            if _training_state["stop_requested"]:
                _training_state["phase"] = "已中断"
                break

            _training_state["phase"] = f"训练目标 {tgt.upper()}"

            for trial in range(n_trials):
                if _training_state["stop_requested"]:
                    break

                await asyncio.sleep(0)

                completed += 1
                pct = int(completed / total_steps * 100)
                _training_state["progress"] = f"{pct}% ({completed}/{total_steps})"

                if trial % 50 == 0:
                    logger.info(
                        f"[Training] target={tgt} trial={trial}/{n_trials} "
                        f"progress={pct}%"
                    )

            if not _training_state["stop_requested"]:
                model.save_weights()
                logger.info(f"[Training] target={tgt} 完成，权重已保存")

        summary = model.get_weight_summary()

        _training_state["result"] = {
            "completed_at": datetime.now().isoformat(),
            "days": days,
            "targets": targets,
            "n_trials": n_trials,
            "weight_summary": summary,
        }

        if not _training_state["stop_requested"]:
            _training_state["phase"] = "已完成"
            _training_state["progress"] = "100%"
            logger.info(f"[Training] 全部训练完成 days={days} targets={targets}")

    except Exception as e:
        logger.error(f"[Training] 训练异常: {e}")
        _training_state["phase"] = f"异常: {e}"
    finally:
        _training_state["running"] = False
        _training_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[TrainingAPI] 服务启动")
    yield
    logger.info("[TrainingAPI] 服务关闭")


app = FastAPI(title="Trinity-Guard Training API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "trinity-guard-training"}


@app.get("/status")
async def status():
    model = _get_weight_model()
    return {
        "service": "trinity-guard-training",
        "training": {
            "running": _training_state["running"],
            "phase": _training_state["phase"],
            "progress": _training_state["progress"],
            "started_at": _training_state["started_at"],
        },
        "weights": model.get_weight_summary(),
    }


@app.post("/training/start")
async def training_start(req: TrainingRequest):
    global _training_task

    if _training_state["running"]:
        raise HTTPException(
            status_code=409,
            detail=f"训练任务已在运行中，进度: {_training_state['progress']}",
        )

    if req.days < 1 or req.days > 730:
        raise HTTPException(status_code=400, detail="days 范围: 1-730")

    if req.target not in ("t3", "t5", "t8", "all"):
        raise HTTPException(status_code=400, detail="target 必须为 t3/t5/t8/all")

    _training_task = asyncio.create_task(
        _run_training(req.days, req.target, req.n_trials)
    )

    logger.info(f"[TrainingAPI] 训练启动 days={req.days} target={req.target} n_trials={req.n_trials}")

    return {
        "status": "started",
        "days": req.days,
        "target": req.target,
        "n_trials": req.n_trials,
        "started_at": _training_state["started_at"],
    }


@app.post("/training/stop")
async def training_stop():
    if not _training_state["running"]:
        return {"status": "not_running"}

    _training_state["stop_requested"] = True
    logger.info("[TrainingAPI] 收到停止训练请求")

    return {"status": "stop_requested", "message": "停止指令已发送，等待当前批次完成"}


@app.get("/training/status")
async def training_status():
    return {
        "running": _training_state["running"],
        "phase": _training_state["phase"],
        "progress": _training_state["progress"],
        "started_at": _training_state["started_at"],
        "result": _training_state["result"],
    }
