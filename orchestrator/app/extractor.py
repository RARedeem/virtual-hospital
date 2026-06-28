"""
指标抽取 — 从（中文）健康数据中提取规则引擎所需的结构化数值。

流程A 自洽：本环节属流程A，直接吃中文症状包（不依赖 B 的英文译文）。
模型与翻译器解耦、独立配置：默认复用 reasoning_a2(llama3.3)，使流程A 抽取→推理
同模型、配 keep_alive 可横跨中间 embed 步零重载（见记忆 model-selection-cost-model）。
受限抽取：只输出 JSON 数值、不做任何解读；指标键名与规则库 metric 字段对齐。
"""
import json
import os

from . import ollama_client as oc
from . import settings

MODEL_EXTRACT = os.environ.get("MODEL_EXTRACT") or settings.load("models.json")["extract_metrics"]

# 指标键名 + 抽取系统词 → 外挂 settings（设置最大化）；模板 {metrics} 注入键名清单
KNOWN_METRICS = settings.load("clinical/metrics.json")["known_metrics"]
_EXTRACT_SYSTEM = settings.text("prompts/extract.txt").replace(
    "{metrics}", "\n".join(f"   - {m}" for m in KNOWN_METRICS))


async def extract_metrics(patient_text: str, keep_alive=None) -> dict[str, float]:
    """从（中文）患者数据抽取结构化指标。失败返回空字典。
    keep_alive：抽取模型与下游 A2 推理同模型时传 >0，令其驻留横跨 embed、推理时零重载。"""
    raw = await oc.generate(MODEL_EXTRACT, patient_text, system=_EXTRACT_SYSTEM, keep_alive=keep_alive)
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
