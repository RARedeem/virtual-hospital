"""
progress_router.py — 挂入 orchestrator/app/main.py

用法：
    from progress_router import router as progress_router
    app.include_router(progress_router)

端点：
    GET  /progress        返回当前进度 JSON
    POST /progress        Claude 输出的进度 JSON 写入（覆盖）
    GET  /progress/health 健康检查（仪表盘轮询用）
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any
import json, os, time

router = APIRouter(prefix="/progress", tags=["progress"])

PROGRESS_FILE = os.environ.get("PROGRESS_FILE", "/tmp/vh_progress.json")


def _read() -> dict:
    if not os.path.exists(PROGRESS_FILE):
        return {"tasks": [], "updated_at": None, "version": 0}
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(data: dict):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class ProgressPayload(BaseModel):
    tasks: list[dict[str, Any]]
    comment: str = ""


@router.get("")
def get_progress():
    return _read()


@router.post("")
def post_progress(payload: ProgressPayload):
    data = {
        "tasks": payload.tasks,
        "comment": payload.comment,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "version": _read().get("version", 0) + 1,
    }
    _write(data)
    return {"ok": True, "version": data["version"], "updated_at": data["updated_at"]}


@router.get("/health")
def health():
    return {"ok": True, "ts": time.time()}


# ════════════════════════════════════════════════════════
# 评估阶段进度（/assess 期间前端轮询，把 ~10min 黑屏变成可见进展）
# 单用户家庭系统：一次只跑一次评估，全局单文件足够。
# ════════════════════════════════════════════════════════
ASSESS_FILE = os.environ.get("ASSESS_PROGRESS_FILE", "/tmp/vh_assess_progress.json")

# 阶段按 pipeline.run_dual 实际执行顺序排列
ASSESS_STAGES = [
    ("translate_en", "汉译英 · gemma4"),
    ("rules",        "抽取指标 + 规则引擎"),
    ("a2_retrieve",  "检索国内指南 · bge-m3"),
    ("a2_reason",    "流程 A 国内循证推理 · llama3.3"),
    ("b_retrieve",   "检索国际指南 · nomic"),
    ("b_reason",     "流程 B 国际循证推理 · llama4（最耗时）"),
    ("b_translate",  "英译汉 · gemma4"),
]


def _assess_write(d: dict):
    with open(ASSESS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def assess_init():
    """评估开始：所有阶段置 pending，标记 running。"""
    _assess_write({
        "running": True, "started_at": time.time(),
        "stages": [{"key": k, "label": l, "status": "pending"} for k, l in ASSESS_STAGES],
    })


def assess_mark(key: str):
    """进入某阶段：该阶段 active，其之前的全部 done。"""
    try:
        d = json.load(open(ASSESS_FILE, encoding="utf-8"))
    except Exception:
        return
    hit = False
    for s in d.get("stages", []):
        if s["key"] == key:
            s["status"] = "active"; hit = True
        elif not hit:
            s["status"] = "done"
    d["updated_at"] = time.time()
    _assess_write(d)


def assess_done():
    """评估结束：全部 done，running=False。"""
    try:
        d = json.load(open(ASSESS_FILE, encoding="utf-8"))
    except Exception:
        d = {"stages": []}
    for s in d.get("stages", []):
        s["status"] = "done"
    d["running"] = False
    d["finished_at"] = time.time()
    _assess_write(d)


@router.get("/assess")
def get_assess_progress():
    try:
        return json.load(open(ASSESS_FILE, encoding="utf-8"))
    except Exception:
        return {"running": False, "stages": []}
