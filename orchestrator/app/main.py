"""虚拟医院编排服务 — FastAPI 入口。"""
import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import pipeline
from . import auth
from .auth import Principal

PG_DSN = os.environ["PG_DSN"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pg_dsn = PG_DSN
    yield


app = FastAPI(title="Virtual Hospital Orchestrator", lifespan=lifespan)

# 前端为本地静态页，限定本地来源即可（数据不出家门原则）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000",
                   "http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class AssessRequest(BaseModel):
    member_id: str
    record_id: str | None = None
    zh_data: str          # 会员中文健康数据


class AssessResponse(BaseModel):
    report_zh: str
    risk_sources: list[str]
    findings_en: str       # 英文中间结果，供前端折叠展示/审计
    rule_hits: list[dict]  # 确定性规则命中（双轨之一）
    metrics: dict          # 抽取的结构化指标


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/me")
async def me(principal: Principal = Depends(auth.get_principal)):
    """返回当前登录主体信息。"""
    return {"sub": principal.sub, "name": principal.name,
            "member_id": principal.member_id, "role": principal.role}


@app.get("/members")
async def list_members(principal: Principal = Depends(auth.get_principal)):
    """
    成员列表。分级过滤：
    admin 返回全部成员；member 仅返回自己。
    """
    async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
        async with conn.cursor() as cur:
            if principal.role == "admin":
                await cur.execute(
                    "SELECT id, full_name, relation, birth_date, role "
                    "FROM member_data.members ORDER BY created_at"
                )
            else:
                await cur.execute(
                    "SELECT id, full_name, relation, birth_date, role "
                    "FROM member_data.members WHERE id = %s",
                    (principal.member_id,),
                )
            rows = await cur.fetchall()
    return [
        {"id": str(r[0]), "full_name": r[1], "relation": r[2],
         "birth_date": str(r[3]) if r[3] else None, "role": r[4]}
        for r in rows
    ]


@app.get("/members/{member_id}/records")
async def list_member_records(member_id: str,
                              principal: Principal = Depends(auth.get_principal)):
    """列出指定成员的健康记录（按日期倒序）。分级授权校验。"""
    auth.authorize_member_access(principal, member_id)
    async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, record_type, source_org, record_date, extracted_zh "
                "FROM member_data.health_records WHERE member_id = %s ORDER BY record_date DESC",
                (member_id,),
            )
            rows = await cur.fetchall()
    return [
        {"id": str(r[0]), "record_type": r[1], "source_org": r[2],
         "record_date": str(r[3]) if r[3] else None, "extracted_zh": r[4]}
        for r in rows
    ]


@app.post("/assess", response_model=AssessResponse)
async def assess(req: AssessRequest,
                 principal: Principal = Depends(auth.get_principal)):
    """执行评估管道并落库。受分级授权保护。"""
    # 授权校验：member 不得评估他人档案
    auth.authorize_member_access(principal, req.member_id)
    await auth.log_access(principal, "run_assessment", req.member_id)
    try:
        async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
            # psycopg3 fetch 封装（简化版，生产可换 asyncpg）
            result = await pipeline.run_pipeline(_ConnAdapter(conn), req.zh_data)
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO member_data.assessments
                        (member_id, record_id, findings_en, report_zh, cited_sources, reasoner_model)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (req.member_id, req.record_id, result["findings_en"],
                     result["report_zh"], psycopg.types.json.Json(result["cited_sources"]),
                     os.environ.get("MODEL_REASONING", "reasoner-meditron")),
                )
                await conn.commit()
        return AssessResponse(
            report_zh=result["report_zh"],
            risk_sources=result["cited_sources"],
            findings_en=result["findings_en"],
            rule_hits=result["rule_hits"],
            metrics=result["extracted_metrics"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class _ConnAdapter:
    """适配 pipeline.retrieve_guidelines 期望的 .fetch 接口。"""
    def __init__(self, conn):
        self._conn = conn

    async def fetch(self, sql, *params):
        sql = sql.replace("$1", "%s").replace("$2", "%s")
        async with self._conn.cursor() as cur:
            await cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in await cur.fetchall()]
