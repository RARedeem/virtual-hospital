"""
指标抽取 — 从英文健康数据中提取规则引擎所需的结构化数值。

复用 Gemma（约束 A 合规）做受限抽取：只输出 JSON 数值，
不做任何解读。指标键名与规则库 metric 字段对齐。
"""
import json
import os

from . import ollama_client as oc
from . import settings

MODEL_TRANSLATE = os.environ.get("MODEL_TRANSLATE") or settings.load("models.json")["translate_zh_en"]

# 指标键名 + 抽取系统词 → 外挂 settings（设置最大化）；模板 {metrics} 注入键名清单
KNOWN_METRICS = settings.load("clinical/metrics.json")["known_metrics"]
_EXTRACT_SYSTEM = settings.text("prompts/extract.txt").replace(
    "{metrics}", "\n".join(f"   - {m}" for m in KNOWN_METRICS))


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
