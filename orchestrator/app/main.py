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
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)


class AssessRequest(BaseModel):
    member_id: str
    record_id: str | None = None
    zh_data: str | None = None   # 中文健康数据；留空则取该成员最新症状包（含在档佐证）


class InterviewStartRequest(BaseModel):
    department: str
    member_id: str        # 需求4：本次问诊形成的症状包归档到该成员


class InterviewChatRequest(BaseModel):
    message: str


class AssessResponse(BaseModel):
    # 双盲并排：流程 A（国内指南·llama）与流程 B（国际指南·meditron）各自独立结论
    report_a_zh: str       # 流程 A 中文结论
    sources_a: list[str]   # 流程 A 引用的国内指南
    report_zh: str         # 流程 B 中文结论（沿用原字段名，向后兼容）
    risk_sources: list[str]  # 流程 B 引用的国际指南（沿用原字段名）
    findings_en: str       # 流程 B 英文中间结果，供前端折叠展示/审计
    rule_hits: list[dict]  # 两轨共享的确定性规则命中（双盲共同地基）
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


async def _archive_symptom_package(conn, member_id: str, pkg: dict,
                                   evidence_text: str = "") -> str:
    """需求4：症状包打时间戳归档，并入主治医师清洗后的在档佐证料，返回记录 id。"""
    body = _package_to_zh(pkg)
    ev = (evidence_text or "").strip()
    if ev and "无相关既往佐证" not in ev:
        body += "\n\n【在档佐证料（主治医师筛选）】\n" + ev
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

    # 问诊结束：先由主治医师(llama3.3 仍温)清洗在档佐证料 → 释放显存 → 归档症状包（需求4）
    del INTERVIEW_SESSIONS[user_id]
    archived_id = None
    if iv.member_id:
        async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
            raw_evidence = await _gather_member_evidence(conn, iv.member_id)
            curated = ""
            if raw_evidence.strip():
                # llama3.3 仍驻留，顺手清洗，零额外冷加载
                curated = await iv.curate_evidence(raw_evidence, payload.get("chief_complaint", ""))
            await iv.release()      # 清洗完成，释放 llama3.3 显存（keep_alive=0）
            archived_id = await _archive_symptom_package(conn, iv.member_id, payload, curated)
        await auth.log_access(principal, "archive_symptom_package", iv.member_id)
    else:
        await iv.release()
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
    """执行双盲评估（流程 A2 国内指南 + 流程 B 国际指南，并排）并落库。受分级授权保护。"""
    # 授权校验：member 不得评估他人档案
    auth.authorize_member_access(principal, req.member_id)
    await auth.log_access(principal, "run_assessment", req.member_id)
    try:
        async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
            # 患者数据：显式 zh_data 优先；否则取该成员最新症状包（已含主治清洗后的在档佐证）
            patient_zh = (req.zh_data or "").strip()
            if not patient_zh:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT extracted_zh FROM member_data.health_records
                        WHERE member_id = %s AND record_type = 'symptom_package'
                          AND extracted_zh IS NOT NULL AND btrim(extracted_zh) <> ''
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (req.member_id,),
                    )
                    row = await cur.fetchone()
                if not row:
                    raise HTTPException(status_code=400,
                                        detail="无可评估数据：请提供 zh_data 或先完成一次问诊生成症状包")
                patient_zh = row[0]

            # psycopg3 fetch 封装（简化版，生产可换 asyncpg）
            result = await pipeline.run_dual(_ConnAdapter(conn), patient_zh)
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO member_data.assessments
                        (member_id, record_id, findings_en, report_zh, cited_sources, reasoner_model,
                         flow_a_zh, flow_a_sources, flow_a_model)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (req.member_id, req.record_id, result["findings_en"],
                     result["report_b_zh"], psycopg.types.json.Json(result["sources_b"]),
                     result["b_model"],
                     result["report_a_zh"], psycopg.types.json.Json(result["sources_a"]),
                     result["a_model"]),
                )
                await conn.commit()
        return AssessResponse(
            report_a_zh=result["report_a_zh"],
            sources_a=result["sources_a"],
            report_zh=result["report_b_zh"],
            risk_sources=result["sources_b"],
            findings_en=result["findings_en"],
            rule_hits=result["rule_hits"],
            metrics=result["extracted_metrics"],
        )
    except HTTPException:
        raise
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

# ── 症状包编辑端点（需求：用户可编辑症状包、附加文件、上传新文件） ──

class EditPackageRequest(BaseModel):
    chief_complaint: str | None = None
    symptoms: str | None = None
    history: str | None = None
    medications: str | None = None
    family_history: str | None = None

@app.put("/symptom-packages/{package_id}")
async def edit_symptom_package(package_id: str,
                                req: EditPackageRequest,
                                principal: Principal = Depends(auth.get_principal)):
    """编辑症状包文本字段。需授权校验。"""
    try:
        async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
            async with conn.cursor() as cur:
                # 获取症状包所属的 member_id，进行权限校验
                await cur.execute(
                    "SELECT member_id, extracted_zh FROM member_data.health_records WHERE id=%s AND record_type='symptom_package'",
                    (package_id,)
                )
                row = await cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="症状包不存在")
                member_id, old_zh = row
                auth.authorize_member_access(principal, member_id)

                # 解析旧症状包 JSON（简单实现：假设 extracted_zh 包含行式键值对，可改为真正 JSON）
                try:
                    pkg = json.loads(old_zh) if old_zh.startswith('{') else _parse_package_text(old_zh)
                except (ValueError, TypeError, AttributeError):
                    pkg = {}

                # 更新非空字段
                if req.chief_complaint is not None:
                    pkg['chief_complaint'] = req.chief_complaint
                if req.symptoms is not None:
                    pkg['symptoms'] = req.symptoms
                if req.history is not None:
                    pkg['history'] = req.history
                if req.medications is not None:
                    pkg['medications'] = req.medications
                if req.family_history is not None:
                    pkg['family_history'] = req.family_history

                new_zh = _package_to_zh_dict(pkg)
                await cur.execute(
                    "UPDATE member_data.health_records SET extracted_zh=%s WHERE id=%s",
                    (new_zh, package_id)
                )
                await conn.commit()
        await auth.log_access(principal, "edit_symptom_package", member_id)
        return {"status": "ok", "package_id": package_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class EvidenceRequest(BaseModel):
    evidence_ids: list[str]
    action: str  # "add" or "remove"

@app.post("/symptom-packages/{package_id}/evidence")
async def manage_package_evidence(package_id: str,
                                   req: EvidenceRequest,
                                   principal: Principal = Depends(auth.get_principal)):
    """关联/删除在档文件到症状包。"""
    try:
        async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
            async with conn.cursor() as cur:
                # 权限校验
                await cur.execute(
                    "SELECT member_id FROM member_data.health_records WHERE id=%s AND record_type='symptom_package'",
                    (package_id,)
                )
                row = await cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="症状包不存在")
                member_id = row[0]
                auth.authorize_member_access(principal, member_id)

                if req.action == "add":
                    for eid in req.evidence_ids:
                        await cur.execute(
                            """INSERT INTO member_data.package_evidence (package_id, evidence_id)
                               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                            (package_id, eid)
                        )
                elif req.action == "remove":
                    for eid in req.evidence_ids:
                        await cur.execute(
                            "DELETE FROM member_data.package_evidence WHERE package_id=%s AND evidence_id=%s",
                            (package_id, eid)
                        )
                await conn.commit()
        await auth.log_access(principal, "manage_package_evidence", member_id)
        return {"status": "ok", "action": req.action, "count": len(req.evidence_ids)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/upload-for-curation")
async def upload_for_curation(file: UploadFile = File(...),
                               member_id: str = Form(...),
                               record_type: str = Form("lab_report"),
                               chief_complaint: str = Form(""),
                               source_org: str | None = Form(None),
                               principal: Principal = Depends(auth.get_principal)):
    """上传报告并触发llama3.3清洗。返回清洗摘要供患者审核。"""
    auth.authorize_member_access(principal, member_id)

    try:
        # 1. 文件上传 & OCR（复用既有逻辑）
        data = await file.read()
        raw_key = storage.put_report(member_id, file.filename or "report", data, file.content_type or "application/octet-stream")
        try:
            extracted = await ocr.extract_text(data, file.content_type or "application/octet-stream")
            ocr_error = None
        except Exception as _e:
            extracted = ""
            ocr_error = str(_e)

        # 2. 入库 health_records
        async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO member_data.health_records
                       (member_id, record_type, source_org, record_date, raw_file_key, extracted_zh)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id""",
                    (member_id, record_type, source_org, date.today(), raw_key, extracted or None),
                )
                rec_id = str((await cur.fetchone())[0])
                await conn.commit()

        # 3. llama3.3 清洗（仅在有 OCR 结果时进行）
        curator_notes = ""
        if extracted:
            iv = interviewer_module.MedicalInterviewer.__new__(interviewer_module.MedicalInterviewer)
            curator_notes = await iv.curate_evidence(
                f"[{EVIDENCE_TYPE_LABEL.get(record_type, record_type)} · {date.today()} · {source_org or '—'}]\n{extracted}",
                chief_complaint or "（未明确）"
            )

        # 4. 存储待审核记录
        async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO member_data.curation_review_pending
                       (upload_id, curator_notes, raw_extracted_zh)
                    VALUES (%s, %s, %s)
                    RETURNING id""",
                    (rec_id, curator_notes, extracted)
                )
                pending_id = str((await cur.fetchone())[0])
                await conn.commit()

        await auth.log_access(principal, "upload_for_curation", member_id)
        return {
            "upload_id": rec_id,
            "pending_id": pending_id,
            "record_type": record_type,
            "curator_notes": curator_notes,
            "raw_extracted_zh": extracted,
            "ocr_error": ocr_error,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CurationReviewRequest(BaseModel):
    accepted: bool
    package_id: str | None = None
    curator_notes: str = ""

@app.post("/curation-review/{upload_id}")
async def review_curation(upload_id: str,
                          req: CurationReviewRequest,
                          principal: Principal = Depends(auth.get_principal)):
    """患者审核清洗结果，确认accept/reject。可选关联到症状包。"""
    try:
        async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
            async with conn.cursor() as cur:
                # 获取上传记录的 member_id，权限校验
                await cur.execute(
                    "SELECT member_id FROM member_data.health_records WHERE id=%s",
                    (upload_id,)
                )
                row = await cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="上传记录不存在")
                member_id = row[0]
                auth.authorize_member_access(principal, member_id)

                # 更新待审核记录
                await cur.execute(
                    """UPDATE member_data.curation_review_pending
                       SET accepted=%s, reviewed_at=now()
                       WHERE upload_id=%s""",
                    (req.accepted, upload_id)
                )

                # 若已接受且指定了 package_id，则关联到症状包
                if req.accepted and req.package_id:
                    await cur.execute(
                        """INSERT INTO member_data.package_evidence (package_id, evidence_id, curator_notes)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (package_id, evidence_id) DO UPDATE SET curator_notes=%s""",
                        (req.package_id, upload_id, req.curator_notes, req.curator_notes)
                    )

                await conn.commit()
        await auth.log_access(principal, "review_curation", member_id)
        return {"status": "ok", "accepted": req.accepted, "upload_id": upload_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _parse_package_text(text: str) -> dict:
    """从行式文本解析症状包（备用方案）。"""
    pkg = {}
    for line in (text or "").split("\n"):
        if line.startswith("主诉："):
            pkg["chief_complaint"] = line[3:].strip()
        elif line.startswith("症状："):
            pkg["symptoms"] = line[3:].strip()
        elif line.startswith("既往史："):
            pkg["history"] = line[4:].strip()
        elif line.startswith("用药："):
            pkg["medications"] = line[3:].strip()
        elif line.startswith("家族史："):
            pkg["family_history"] = line[4:].strip()
    return pkg

def _package_to_zh_dict(pkg: dict) -> str:
    """症状包字典转中文摘要。"""
    lines = []
    for key, label in _PKG_LABELS:
        val = pkg.get(key, "").strip()
        if val:
            lines.append(f"{label}：{val}")
    return "\n".join(lines)

from app.progress_router import router as progress_router
app.include_router(progress_router)