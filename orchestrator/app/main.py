"""虚拟医院编排服务 — FastAPI 入口。"""
import json
import os
from datetime import date
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import pipeline
from . import auth
from . import interviewer as interviewer_module
from . import storage
from . import ocr
from .auth import Principal

PG_DSN = os.environ["PG_DSN"]

# 内存 session 存储，key=user_id (principal.sub)
INTERVIEW_SESSIONS: dict[str, interviewer_module.MedicalInterviewer] = {}


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


class InterviewStartRequest(BaseModel):
    department: str
    member_id: str        # 需求4：本次问诊形成的症状包归档到该成员


class InterviewChatRequest(BaseModel):
    message: str


class AssessResponse(BaseModel):
    report_zh: str
    risk_sources: list[str]
    findings_en: str       # 英文中间结果，供前端折叠展示/审计
    rule_hits: list[dict]  # 确定性规则命中（双轨之一）
    metrics: dict          # 抽取的结构化指标


# 上传报告的格式与大小约束（服务端二次校验，不信客户端 Content-Type 之外的声明）
ALLOWED_UPLOAD_MIME = {"application/pdf", "image/jpeg", "image/png"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB
ALLOWED_RECORD_TYPES = {"lab_report", "imaging", "checkup", "prescription"}

# 症状包字段 → 中文摘要标签
_PKG_LABELS = [
    ("chief_complaint", "主诉"), ("symptoms", "症状"), ("history", "既往史"),
    ("medications", "用药"), ("family_history", "家族史"),
]


def _interview_response(payload: dict, done: bool) -> dict:
    """统一问诊轮次的 HTTP 形态。未结束→问题+选项；结束→症状包。"""
    if done:
        return {"done": True, "symptom_package": payload}
    return {"done": False, "question": payload.get("question", ""),
            "options": payload.get("options", []),
            "multi": payload.get("multi", False),
            "allow_free_text": payload.get("allow_free_text", True)}


def _package_to_zh(pkg: dict) -> str:
    """症状包 → 可读中文摘要，落 health_records.extracted_zh。"""
    lines = [f"{label}：{pkg.get(key) or '（未提供）'}" for key, label in _PKG_LABELS]
    return "\n".join(lines)


# 在档佐证料聚合：把成员既往报告并入症状包，使评估有全局依据、更权威。
# 限量 + 每条截断：避免把 meditron 的患者上下文撑回失控量级。
EVIDENCE_TYPE_LABEL = {
    "lab_report": "检验报告", "imaging": "影像", "checkup": "体检报告", "prescription": "处方",
}
EVIDENCE_MAX_RECORDS = 6
EVIDENCE_CHARS_PER_RECORD = 350


async def _gather_member_evidence(conn, member_id: str) -> str:
    """汇集该成员在档的报告类记录（非症状包）作为佐证料，压缩空白、限量截断。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT record_type, source_org, record_date, extracted_zh
            FROM member_data.health_records
            WHERE member_id = %s AND record_type <> 'symptom_package'
              AND extracted_zh IS NOT NULL AND btrim(extracted_zh) <> ''
            ORDER BY record_date DESC NULLS LAST, created_at DESC
            LIMIT %s
            """,
            (member_id, EVIDENCE_MAX_RECORDS),
        )
        rows = await cur.fetchall()
    parts = []
    for rt, org, rdate, zh in rows:
        label = EVIDENCE_TYPE_LABEL.get(rt, rt)
        compact = " ".join(zh.split())[:EVIDENCE_CHARS_PER_RECORD]
        parts.append(f"- [{label} · {rdate} · {org or '—'}] {compact}")
    return "\n".join(parts)


async def _archive_symptom_package(conn, member_id: str, pkg: dict) -> str:
    """需求4：症状包打时间戳归档，并并入在档佐证料（既往报告），返回记录 id。"""
    body = _package_to_zh(pkg)
    evidence = await _gather_member_evidence(conn, member_id)
    if evidence:
        body += "\n\n【在档佐证料（既往报告，供评估参考）】\n" + evidence
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO member_data.health_records
                (member_id, record_type, source_org, record_date, extracted_zh)
            VALUES (%s, 'symptom_package', 'AI问诊', %s, %s)
            RETURNING id
            """,
            (member_id, date.today(), body),
        )
        rec_id = (await cur.fetchone())[0]
        await conn.commit()
    return str(rec_id)


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


@app.post("/interview/start")
async def interview_start(
    req: InterviewStartRequest,
    principal: Principal = Depends(auth.get_principal)
):
    """启动 A1 问诊流程。症状包将归档到 req.member_id（需分级授权）。"""
    auth.authorize_member_access(principal, req.member_id)
    user_id = principal.sub
    iv = interviewer_module.MedicalInterviewer(user_id, req.department, member_id=req.member_id)
    INTERVIEW_SESSIONS[user_id] = iv

    payload, done = await iv.chat("开始问诊")
    return _interview_response(payload, done)


@app.post("/interview/chat")
async def interview_chat(req: InterviewChatRequest,
                         principal: Principal = Depends(auth.get_principal)):
    """进行 A1 问诊多轮对话。结束时把症状包打时间戳归档为新病史记录。"""
    user_id = principal.sub
    iv = INTERVIEW_SESSIONS.get(user_id)
    if not iv:
        raise HTTPException(status_code=400, detail="Interview session not started. Please call /interview/start first.")

    payload, done = await iv.chat(req.message)
    if not done:
        return _interview_response(payload, done)

    # 问诊结束：症状包归档（需求4）
    del INTERVIEW_SESSIONS[user_id]
    archived_id = None
    if iv.member_id:
        async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
            archived_id = await _archive_symptom_package(conn, iv.member_id, payload)
        await auth.log_access(principal, "archive_symptom_package", iv.member_id)
    return {"done": True, "symptom_package": payload, "archived_record_id": archived_id}


@app.post("/interview/more")
async def interview_more(principal: Principal = Depends(auth.get_principal)):
    """点"更多"：为当前问题追加更多选项，主题不变、不推进问诊。"""
    iv = INTERVIEW_SESSIONS.get(principal.sub)
    if not iv:
        raise HTTPException(status_code=400, detail="Interview session not started.")
    new_options = await iv.more_options()
    return {"options": new_options}


@app.post("/upload")
async def upload_report(
    file: UploadFile = File(...),
    member_id: str = Form(...),
    record_type: str = Form("lab_report"),
    source_org: str | None = Form(None),
    principal: Principal = Depends(auth.get_principal),
):
    """需求2：上传化验/检查/影像报告 → 存 MinIO → glm-ocr 提取文字 → 建病史档案。

    会员上传的实体医院报告属个人医疗事实，不受约束 B 限制；文件不出本机。
    """
    auth.authorize_member_access(principal, member_id)

    ct = (file.content_type or "").lower()
    if ct not in ALLOWED_UPLOAD_MIME:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{ct or '未知'}（仅 PDF/JPEG/PNG）")
    if record_type not in ALLOWED_RECORD_TYPES:
        record_type = "lab_report"

    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 20MB 上限")

    # 1) 存 MinIO 原件
    try:
        raw_key = storage.put_report(member_id, file.filename or "report", data, ct)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件存储失败：{e}")

    # 2) glm-ocr 提取文字（非评估链路，约束 A 红线内）
    try:
        extracted = await ocr.extract_text(data, ct)
    except Exception as e:
        extracted = ""
        ocr_error = str(e)
    else:
        ocr_error = None

    # 3) 建病史档案
    async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO member_data.health_records
                    (member_id, record_type, source_org, record_date, raw_file_key, extracted_zh)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (member_id, record_type, source_org, date.today(), raw_key, extracted or None),
            )
            rec_id = str((await cur.fetchone())[0])
            await conn.commit()
    await auth.log_access(principal, "upload_report", member_id)

    return {"id": rec_id, "record_type": record_type, "raw_file_key": raw_key,
            "extracted_zh": extracted, "ocr_error": ocr_error,
            "record_date": str(date.today()), "source_org": source_org}


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

from app.progress_router import router as progress_router
app.include_router(progress_router)