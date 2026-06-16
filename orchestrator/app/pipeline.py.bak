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


async def retrieve_guidelines(conn, query_en: str, top_k: int = 5) -> list[dict]:
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
    双轨融合：规则引擎的确定性命中作为既定事实注入，
    Meditron 在此基础上做循证解释，不得推翻硬阈值结论。
    """
    context = "\n\n".join(
        f"[{g['citation_id']}] ({g['org']}, {g['section']})\n{g['chunk_text']}"
        for g in guidelines
    )
    # 规则命中作为确定性事实前置
    rules_block = ""
    if rule_hits:
        rules_block = "DETERMINISTIC RULE FINDINGS (established facts, do not contradict):\n" + "\n".join(
            f"- {h.metric} = {h.value}: {h.conclusion} "
            f"[{h.citation_id or 'rule'}] severity={h.severity or 'n/a'}"
            for h in rule_hits
        ) + "\n\n"

    few_shot = (
        "=== FORMAT EXAMPLE ONLY - NOT REAL PATIENT DATA ===\n"
        "EXAMPLE\n"
        "GUIDELINE CONTEXT: [EAU LUTS] Prostate volume >30 mL indicates "
        "benign prostatic enlargement. For men in their 60s, PSA >2.0 ng/mL "
        "predicts volume >40 mL.\n"
        "PATIENT DATA: Prostate volume 40.8 mL, BPH confirmed by ultrasound. "
        "IPSS 12 (moderate). Nocturia 2-3x/night.\n"
        "ASSESSMENT:\n"
        "1. FINDING: Prostate volume 40.8 mL with confirmed BPH and moderate "
        "LUTS (IPSS 12).\n"
        "2. GUIDELINE: Per EAU LUTS, volume >30 mL confirms benign enlargement; "
        "40.8 mL exceeds the 40 mL threshold where PSA >2.0 ng/mL is "
        "predictive in men in their 60s. IPSS 8-19 indicates moderate "
        "symptoms warranting active treatment consideration.\n"
        "3. CONCLUSION: This patient's prostate volume and symptom score both "
        "support initiating pharmacological treatment per EAU guidelines, "
        "typically alpha-blockers or 5-alpha reductase inhibitors for "
        "volume >40 mL.\n"
        "END EXAMPLE\n"
        "=== END OF EXAMPLE. NOW ASSESS THE ACTUAL PATIENT BELOW ===\n"
        "=== DO NOT USE ANY DATA FROM THE EXAMPLE ABOVE ===\n\n"
    )

    critical_rules = (
        "CRITICAL RULES:\n"
        "- Assess ONLY findings explicitly present in PATIENT DATA below.\n"
        "- Do NOT introduce any measurement not in PATIENT DATA "
        "(e.g. if bladder wall thickness is absent, never mention it).\n"
        "- NEVER output placeholders like [value] or [数值].\n"
        "- Structure your response as:\n"
        "  1. FINDING: restate the specific patient finding\n"
        "  2. GUIDELINE: what the cited guideline says about this finding\n"
        "  3. CONCLUSION: what this means for this patient specifically\n\n"
    )

    prompt = (
        f"{rules_block}"
        f"GUIDELINE CONTEXT:\n{context}\n\n"
        f"{few_shot}"
        f"{critical_rules}"
        f"PATIENT DATA:\n{patient_en}\n\n"
        f"Provide an evidence-based assessment following the structure above. "
        f"Treat the deterministic rule findings above as established; explain "
        f"and contextualize them using only the guideline context. Cite sources as given."
    )
    return await oc.generate(MODEL_REASONING, prompt)


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
