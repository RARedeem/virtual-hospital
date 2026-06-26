"""
评估管道 — 翻译三明治实现
中文数据 → [汉译英] → [向量检索指南] → [Meditron 循证推理] → [英译汉] → 中文报告

设计要点（已与用户确认）：
- 术语表注入翻译环节，防止术语失真
- 英文中间结果（translated_en / findings_en）全程留存，供审计溯源
- 指南上下文严格来自 knowledge_base（国际白名单），不混入会员数据
"""
import os
import re
import json
from pathlib import Path

from . import ollama_client as oc
from . import rules_engine
from . import extractor
from . import settings

# 检索排除的非临床章节（参考文献/致谢…）→ 外挂 settings（设置最大化）
_EXCL = settings.load("constraints/retrieval_exclude_sections.json")
_EXCL_EN = "|".join(_EXCL["international"])
_EXCL_CN = "|".join(_EXCL["domestic"])

MODEL_TRANSLATE = os.environ.get("MODEL_TRANSLATE", "translator-zh-en")
MODEL_TRANSLATE_BACK = os.environ.get("MODEL_TRANSLATE_BACK", "translator-en-zh")
MODEL_REASONING = os.environ.get("MODEL_REASONING", "reasoner-meditron")
MODEL_EMBED = os.environ.get("MODEL_EMBED", "nomic-embed-text:v1.5")

# ── 流程 A2（双盲另一轨）：llama 中文原生推理 + 国内指南 ──
# 约束 A 例外：bge-m3（北京智源）仅用于流程 A 国内指南检索，严禁进入翻译/流程 B 任何环节。
# 边界见 ARCHITECTURE §2 / db/init/04_domestic_kb.sql。
MODEL_A2_REASONING = os.environ.get("MODEL_A2_REASONING", "llama3.3:70b")
MODEL_EMBED_CN = os.environ.get("MODEL_EMBED_CN", "bge-m3")
# 结构化预处理：把零散症状包+佐证整理成结构化转诊摘要（reorganize，非summarize）。约束A合规。
MODEL_STRUCTURE = os.environ.get("MODEL_STRUCTURE", "llama3.3:70b")

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
    """步骤 1：汉译英，注入术语表。用定制 gemma4 翻译器原本调校的简洁 prompt。"""
    prompt = f"{_terminology_hint()}\nTranslate to English:\n{zh_text}"
    return await oc.generate(MODEL_TRANSLATE, prompt)


async def retrieve_guidelines(conn, query_en: str, top_k: int = RETRIEVE_TOP_K) -> list[dict]:
    """步骤 2：向量检索相关国际指南片段（仅未废弃来源）。"""
    query_vec = await oc.embed(MODEL_EMBED, query_en)
    rows = await conn.fetch(
        f"""
        SELECT c.chunk_text, c.section, s.citation_id, s.org
        FROM knowledge_base.guideline_chunks c
        JOIN knowledge_base.guideline_sources s ON c.source_id = s.id
        WHERE s.is_deprecated = false
          AND c.section !~* '({_EXCL_EN})'
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
        "explicitly present in the patient case; never invent values or guideline content. "
        "In Part 1 (Clinical Impression), you MUST first enumerate ALL suspicious / "
        "to-be-investigated objective findings present in the case (e.g., space-occupying "
        "lesion or mass, heterogeneous echo, ill-defined margins, nodule, hydronephrosis, "
        "gross hematuria), even those with NO retrieved guideline; do not focus only on "
        "guideline-matchable findings. "
        "Base the assessment primarily on objective medical reports / examination findings; "
        "treat the patient's chief complaint and symptoms as secondary/supporting."
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
    """步骤 4：英译汉输出。用定制 gemma4 翻译器原本调校的简洁 prompt（简体、守纪律）。"""
    return await oc.generate(MODEL_TRANSLATE_BACK, f"Translate to Chinese:\n{en_report}")


async def run_pipeline(conn, zh_patient_data: str) -> dict:
    """
    端到端双轨评估（单流程 = 流程 B）。

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


# ════════════════════════════════════════════════════════
# 流程 A2（双盲另一轨）：llama 中文原生 + 国内指南 RAG
# ════════════════════════════════════════════════════════

async def retrieve_domestic_guidelines(conn, query_zh: str,
                                       top_k: int = RETRIEVE_TOP_K) -> list[dict]:
    """流程 A2 检索：bge-m3 向量化中文 query → 检索国内指南片段（domestic_kb，仅未废弃来源）。
    约束 A 例外：bge-m3 仅在此（流程 A 国内指南检索）使用。"""
    query_vec = await oc.embed(MODEL_EMBED_CN, query_zh)
    rows = await conn.fetch(
        f"""
        SELECT c.chunk_text, c.section, s.citation_id, s.org
        FROM domestic_kb.guideline_chunks_cn c
        JOIN domestic_kb.guideline_sources_cn s ON c.source_id = s.id
        WHERE s.is_deprecated = false
          AND c.section !~* '({_EXCL_CN})'
        ORDER BY c.embedding <=> $1::vector
        LIMIT $2
        """,
        str(query_vec), top_k,
    )
    return [dict(r) for r in rows]


async def reason_a2(patient_zh: str, guidelines: list[dict]) -> str:
    """流程 A2 推理：llama3.3 中文原生，chat 角色消息 + 中文 few-shot，输出 3 段中文结论。
    无翻译三明治（中文 query / 中文国内指南 / 中文输出）。证据 top-k、每条截断。
    （确定性规则作为独立轨在 merge 层与本结论并排呈现，不混入本提示，保持两轨对称、避免指令过载。）"""
    evidence_str = "\n".join(
        f"- [{g['citation_id']}] {g['chunk_text'][:CONTEXT_CHUNK_CHARS]}"
        for g in guidelines
    ) or "-（国内指南库暂无相关片段）"

    sys_prompt = (
        "你是一名循证医学专家。严格按示例输出 3 段式中文评估。"
        "只评估患者病例中明确给出的发现，绝不臆造数值或指南内容。"
        "第 1 段【临床印象】必须先逐条点出病例中所有‘可疑/需进一步排查’的客观发现"
        "（如占位、不均质回声、边界不清、结节、积水、肉眼血尿等），即使未检索到对应指南"
        "也不得遗漏；不要只挑能对齐指南的发现。"
        "评估以医疗机构出具的检查报告/客观所见为主要依据，患者主诉症状为辅。"
    )
    shot_user = (
        "临床证据：\n"
        "- 《中国高血压防治指南》：诊室血压 ≥140/90 mmHg 即诊断为高血压。\n"
        "- 高危患者收缩压 ≥130 mmHg 即应启动药物治疗。\n\n"
        "患者病例：患者静息血压 145/95 mmHg。\n\n评估："
    )
    shot_asst = (
        "1. 临床印象：高血压（2 级）。\n"
        "2. 指南符合情况：依据所给证据，患者血压（145/95）已达到《中国高血压防治指南》"
        "的高血压诊断标准（≥140/90）。\n"
        "3. 建议措施：启动降压药物治疗并安排随访。"
    )
    actual_user = (
        f"临床证据：\n{evidence_str}\n\n"
        f"患者病例：{patient_zh}\n\n评估："
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": shot_user},
        {"role": "assistant", "content": shot_asst},
        {"role": "user", "content": actual_user},
    ]
    return await oc.chat(MODEL_A2_REASONING, messages, options=REASONING_OPTIONS)


# ════════════════════════════════════════════════════════
# 双盲编排 + 呈现层（纯代码）
# ════════════════════════════════════════════════════════

async def structure_symptom_package(zh_blob: str) -> str:
    """把零散的症状包 + 在档佐证整理成【结构化转诊摘要】，供 A2/B 循证推理消费。
    解耦"理解格式"与"医学推理"——推理模型拿到的是干净结构而非碎片乱麻。
    ⚠ 只归类整理、完整保留每一项客观数值，绝不诊断/臆测/删减（同 evidence-curation-principle）。"""
    system = (
        "你是主治医师，把下列零散的病历信息整理成【结构化转诊摘要】，供会诊医师循证推理。严格规则：\n"
        "1. 完整保留每一项客观数值、检查发现、明确诊断、既往史——一项不漏、不改数值、不合并丢弃。\n"
        "2. 只做归类与整理，不做诊断、不臆测、不新增信息、不删减。\n"
        "3. 按以下模板分节输出（某节无内容写『无』）：\n"
        "【主诉】\n【现病史】（症状/起病时间/诱因）\n【既往史·手术史】\n【用药】\n【家族史】\n"
        "【客观检查发现】（按器官系统或报告分组，逐条列出数值与发现）\n"
        "【可疑·需进一步排查的发现】（仅客观摘录报告中如占位、不均质回声、边界不清、结节、"
        "积水、肉眼血尿等措辞，逐条列出，一项不漏；无则写『无』。只摘录不诊断）\n"
        "【关键异常指标】（指标=数值，逐条）"
    )
    user = f"【原始病历信息】\n{zh_blob}\n\n请输出结构化转诊摘要："
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        out = await oc.chat(MODEL_STRUCTURE, messages, options={"temperature": 0.2, "num_predict": 1000})
        return out.strip() or zh_blob
    except Exception:
        return zh_blob   # 整理失败兜底用原文，不阻断评估


# 评估「以检查报告/客观所见为主、主诉为辅」：客观医疗内容（专科所见/佐证报告原文/可疑发现/
# 关键异常）单独抽出作主检索 query——否则主诉症状会淹没客观占位（实测右肾占位被排尿主诉挤出
# top-15，而用完整右肾超声所见检索即命中 RCC）。
_OBJECTIVE_HEADERS = tuple(settings.load("clinical/objective_headers.json")["headers"])


def _extract_objective(zh: str) -> str:
    """抽取症状包里医疗机构报告/客观检查所见（专科所见 + 佐证报告原文 + 可疑发现），剔除主诉/症状。"""
    blocks = []
    for m in re.finditer(r"【([^】]+)】\n?(.*?)(?=\n【|\Z)", zh or "", re.S):
        head, body = m.group(1), m.group(2).strip()
        if body and any(h in head for h in _OBJECTIVE_HEADERS):
            blocks.append(body)
    return "\n".join(blocks)


def _merge_guidelines(main: list[dict], extra: list[dict], cap: int = 6) -> list[dict]:
    """合并主检索 + 红旗检索，按 (来源,切片) 去重保序、截断到 cap，确保红旗那路对症指南不被挤掉。"""
    seen, out = set(), []
    for g in list(main) + list(extra):
        key = (g.get("citation_id"), (g.get("chunk_text") or "")[:80])
        if key not in seen:
            seen.add(key)
            out.append(g)
    return out[:cap]


async def run_dual(conn, zh_patient_data: str, on_stage=None, on_partial=None) -> dict:
    """
    背靠背双盲评估（ARCHITECTURE §3）。同一份 A1 症状数据，A2 与 B 各自独立推理。

    双盲保证：A2 与 B 都【只】消费 zh_patient_data（A1 症状 + 在档佐证），
    互不传入对方的结论。确定性规则作为两轨共享的既定事实层，单算一次。

    on_stage(key)：可选回调，进入每个阶段时调用，供前端展示实时进度
    （key 见 progress_router.ASSESS_STAGES）。

    显存：依赖 OLLAMA_KEEP_ALIVE=0（ARCHITECTURE §6.1）自动串行换载。
    流程 B 路线（gemma4→nomic检索→llama4→gemma4）原样不动，仅与 A2 并排编排。
    """
    def _stage(k):
        if on_stage:
            try: on_stage(k)
            except Exception: pass

    def _partial(patch):
        if on_partial:
            try: on_partial(patch)
            except Exception: pass

    # 症状包已是 json 结构化（方案A 序列化产出），不再做 structure 预处理——直接消费。
    # 对已结构化的包再让 llama3.3 重整既慢又可能扭曲，故移除该阶段。
    _stage("translate_en")
    translated_en = await translate_to_en(zh_patient_data)   # 一次汉译英：供 B 检索/推理 + 规则抽取
    _stage("rules")
    metrics = await extractor.extract_metrics(translated_en)
    active_rules = await fetch_active_rules(conn)
    rule_hits = rules_engine.evaluate_rules(metrics, active_rules)
    rule_hits_dicts = [
        {"metric": h.metric, "value": h.value, "conclusion": h.conclusion,
         "citation_id": h.citation_id, "severity": h.severity}
        for h in rule_hits
    ]
    _partial({"rule_hits": rule_hits_dicts, "metrics": metrics})   # 规则先出

    # 检查报告为主、主诉为辅：客观检查所见单独作【主】检索 query（否则被主诉症状淹没）
    objective_zh = _extract_objective(zh_patient_data)

    # 流程 A2（国内指南）：客观所见为主检索 + 全包(含主诉)为辅，合并
    _stage("a2_retrieve")
    a_guidelines = _merge_guidelines(
        await retrieve_domestic_guidelines(conn, objective_zh or zh_patient_data),
        await retrieve_domestic_guidelines(conn, zh_patient_data))
    _stage("a2_reason")
    report_a_zh = await reason_a2(zh_patient_data, a_guidelines)
    sources_a = [g["citation_id"] for g in a_guidelines]
    _partial({"report_a_zh": report_a_zh, "sources_a": sources_a,
              "a_model": MODEL_A2_REASONING})                       # A 轨结论流出

    # 流程 B（国际指南 + llama4，路线不动）：客观所见为主检索 + 全包为辅，合并
    _stage("b_retrieve")
    obj_en = await translate_to_en(objective_zh) if objective_zh else translated_en
    b_guidelines = _merge_guidelines(
        await retrieve_guidelines(conn, obj_en),
        await retrieve_guidelines(conn, translated_en))
    _stage("b_reason")
    findings_en = await reason(translated_en, b_guidelines, rule_hits)
    _stage("b_translate")
    report_b_zh = await translate_to_zh(findings_en)
    sources_b = [g["citation_id"] for g in b_guidelines]
    _partial({"report_b_zh": report_b_zh, "sources_b": sources_b,
              "b_model": MODEL_REASONING})                          # B 轨结论流出

    return {
        "translated_en": translated_en,
        "extracted_metrics": metrics,
        "rule_hits": rule_hits_dicts,        # 两轨共享的确定性既定事实层
        "report_a_zh": report_a_zh,          # 流程 A（国内指南·llama）
        "sources_a": sources_a,
        "report_b_zh": report_b_zh,          # 流程 B（国际指南·meditron）
        "sources_b": sources_b,
        "findings_en": findings_en,          # B 的英文中间结果（审计溯源）
        "a_model": MODEL_A2_REASONING,
        "b_model": MODEL_REASONING,
    }
