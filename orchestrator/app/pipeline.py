"""
评估管道 — 翻译三明治实现
中文数据 → [汉译英] → [向量检索指南] → [Meditron 循证推理] → [英译汉] → 中文报告

设计要点（已与用户确认）：
- 术语表注入翻译环节，防止术语失真
- 英文中间结果（translated_en / findings_en）全程留存，供审计溯源
- 指南上下文严格来自 knowledge_base（国际白名单），不混入会员数据
"""
import os
import json
from pathlib import Path

from . import ollama_client as oc
from . import rules_engine
from . import extractor

MODEL_TRANSLATE = os.environ.get("MODEL_TRANSLATE", "translator-zh-en")
MODEL_TRANSLATE_BACK = os.environ.get("MODEL_TRANSLATE_BACK", "translator-en-zh")
MODEL_REASONING = os.environ.get("MODEL_REASONING", "reasoner-meditron")
MODEL_EMBED = os.environ.get("MODEL_EMBED", "nomic-embed-text:v1.5")

# RAG 上下文收敛（数据/检索层，非模型链）：喂给 meditron 的上下文过长会导致其
# 生成失控/回吐（联调实测 ~1万字符 → >33min 超时）。聚焦少量短块，与历史验证场景的
# 上下文量级一致，使 meditron 能消化并自然收尾。
RETRIEVE_TOP_K = 3
CONTEXT_CHUNK_CHARS = 250   # 仿已验证的 ebm-ai-pipeline：每条证据截断 250 字符，聚焦阈值

# 推理参数（仿生产）：温度 0、输出上限 1024，确定且有界
REASONING_OPTIONS = {"temperature": 0.0, "num_predict": 1024}

# 中英医学术语对照表，作为翻译提示补充
_TERMS_PATH = Path("/app/terminology/medical_terms.json")
_TERMS = json.loads(_TERMS_PATH.read_text(encoding="utf-8")) if _TERMS_PATH.exists() else {}


def _terminology_hint() -> str:
    """构造术语对照提示片段。"""
    if not _TERMS:
        return ""
    pairs = "\n".join(f"  {zh} = {en}" for zh, en in _TERMS.items())
    return f"\nUse these exact term mappings:\n{pairs}\n"


async def translate_to_en(zh_text: str) -> str:
    """步骤 1：汉译英，注入术语表。"""
    prompt = f"{_terminology_hint()}\nTranslate to English:\n{zh_text}"
    return await oc.generate(MODEL_TRANSLATE, prompt)


async def retrieve_guidelines(conn, query_en: str, top_k: int = RETRIEVE_TOP_K) -> list[dict]:
    """步骤 2：向量检索相关国际指南片段（仅未废弃来源）。"""
    query_vec = await oc.embed(MODEL_EMBED, query_en)
    rows = await conn.fetch(
        """
        SELECT c.chunk_text, c.section, s.citation_id, s.org
        FROM knowledge_base.guideline_chunks c
        JOIN knowledge_base.guideline_sources s ON c.source_id = s.id
        WHERE s.is_deprecated = false
          AND c.section !~* '(reference|bibliography|acknowledg|abbreviation|conflict of interest|appendix|disclaimer|publication info)'
        ORDER BY c.embedding <=> $1::vector
        LIMIT $2
        """,
        str(query_vec), top_k,
    )
    return [dict(r) for r in rows]


async def fetch_active_rules(conn) -> list[dict]:
    """查询所有临床规则（仅来源未废弃的）。"""
    rows = await conn.fetch(
        """
        SELECT r.name, r.metric, r.condition, r.conclusion,
               r.citation_id, r.severity
        FROM rules.clinical_rules r
        LEFT JOIN knowledge_base.guideline_sources s
            ON r.citation_id = s.citation_id
        WHERE s.is_deprecated IS NOT true
        """,
    )
    return [dict(r) for r in rows]


async def reason(patient_en: str, guidelines: list[dict],
                 rule_hits: list) -> str:
    """
    步骤 3：Meditron 循证推理。

    底层逻辑借鉴已验证无数次的 ebm-ai-pipeline：用 chat 角色消息
    (system + few-shot 的 user/assistant + 实际 user)，meditron 才以"助手应答"姿态
    输出 3 段结构化评估，而非把裸 prompt 当文本续写(回显)。证据 top3、每条截断 250 字符。
    （规则引擎的确定性命中作为独立轨在 run_pipeline 层呈现，不混入本提示，避免指令过载。）
    """
    evidence_str = "\n".join(
        f"- [{g['citation_id']}] {g['chunk_text'][:CONTEXT_CHUNK_CHARS]}"
        for g in guidelines
    )

    sys_prompt = (
        "You are an Evidence-Based Medicine Expert. You must strictly output the "
        "3-part structured assessment as shown in the example. Assess ONLY findings "
        "explicitly present in the patient case; never invent values or guideline content."
    )
    shot_user = (
        "CLINICAL EVIDENCE:\n"
        "- Stage 2 Hypertension is defined as BP >= 140/90.\n"
        "- If BP >= 130/80 and high CVD risk, start medication.\n\n"
        "PATIENT CASE: The patient has a resting blood pressure of 145/95 mmHg.\n\n"
        "ASSESSMENT:"
    )
    shot_asst = (
        "1. CLINICAL IMPRESSION: Stage 2 Hypertension.\n"
        "2. GUIDELINE ALIGNMENT: Based on the provided evidence, the patient's BP "
        "(145/95) strictly meets the criteria for Stage 2 Hypertension (>= 140/90).\n"
        "3. RECOMMENDED ACTION: Initiate pharmacological treatment and schedule a follow-up."
    )
    actual_user = (
        f"CLINICAL EVIDENCE:\n{evidence_str}\n\n"
        f"PATIENT CASE: {patient_en}\n\n"
        "ASSESSMENT:"
    )

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": shot_user},
        {"role": "assistant", "content": shot_asst},
        {"role": "user", "content": actual_user},
    ]
    return await oc.chat(MODEL_REASONING, messages, options=REASONING_OPTIONS)


async def translate_to_zh(en_report: str) -> str:
    """步骤 4：英译汉输出。"""
    return await oc.generate(MODEL_TRANSLATE_BACK, f"Translate to Chinese:\n{en_report}")


async def run_pipeline(conn, zh_patient_data: str) -> dict:
    """
    端到端双轨评估。

    轨道一（确定性）：抽取指标 → 规则引擎硬阈值判断
    轨道二（概率性）：向量检索指南 → Meditron 循证推理
    两轨在推理层融合，规则命中作为既定事实约束 LLM 输出。
    """
    translated_en = await translate_to_en(zh_patient_data)

    # 轨道一：确定性规则判断
    metrics = await extractor.extract_metrics(translated_en)
    active_rules = await fetch_active_rules(conn)
    rule_hits = rules_engine.evaluate_rules(metrics, active_rules)

    # 轨道二：RAG 检索
    guidelines = await retrieve_guidelines(conn, translated_en)

    # 融合推理
    findings_en = await reason(translated_en, guidelines, rule_hits)
    report_zh = await translate_to_zh(findings_en)

    return {
        "translated_en": translated_en,
        "extracted_metrics": metrics,
        "rule_hits": [
            {"metric": h.metric, "value": h.value, "conclusion": h.conclusion,
             "citation_id": h.citation_id, "severity": h.severity}
            for h in rule_hits
        ],
        "cited_sources": [g["citation_id"] for g in guidelines],
        "findings_en": findings_en,
        "report_zh": report_zh,
    }
