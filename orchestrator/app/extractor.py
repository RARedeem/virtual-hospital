"""
指标抽取 — 从英文健康数据中提取规则引擎所需的结构化数值。

复用 Gemma（约束 A 合规）做受限抽取：只输出 JSON 数值，
不做任何解读。指标键名与规则库 metric 字段对齐。
"""
import json
import os

from . import ollama_client as oc

MODEL_TRANSLATE = os.environ.get("MODEL_TRANSLATE", "translator-zh-en")

# 规则库已知指标键名，约束抽取输出，避免键名漂移
KNOWN_METRICS = [
    "fasting_glucose_mmol_l",
    "hba1c_percent",
    "ldl_mmol_l",
    "hdl_mmol_l",
    "triglycerides_mmol_l",
    "systolic_bp_mmhg",
    "diastolic_bp_mmhg",
    "creatinine_umol_l",
    "alt_u_l",
    "ast_u_l",
]

_EXTRACT_SYSTEM = """You are a clinical data extractor. From the English health data, extract ONLY numeric lab/vital values.

Rules:
1. Output a single JSON object. Keys MUST be from this exact list; omit any not present in the data:
""" + "\n".join(f"   - {m}" for m in KNOWN_METRICS) + """
2. Values must be numbers in the unit implied by the key name. Convert if the source uses a different unit.
3. Do NOT interpret, diagnose, or add fields. Output ONLY the JSON object, no preamble, no markdown fences.
4. If no recognizable values are present, output {}.
"""


async def extract_metrics(patient_en: str) -> dict[str, float]:
    """从英文患者数据抽取结构化指标。失败返回空字典。"""
    raw = await oc.generate(MODEL_TRANSLATE, patient_en, system=_EXTRACT_SYSTEM)
    # 容错：剥离可能的 markdown 围栏
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    # 仅保留已知键且值可转 float 的项
    result: dict[str, float] = {}
    for k, v in data.items():
        if k in KNOWN_METRICS:
            try:
                result[k] = float(v)
            except (ValueError, TypeError):
                continue
    return result
