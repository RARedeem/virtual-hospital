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
