"""设置模块 —— 所有"写进代码的补丁 / 特定(非通用)约束 / 可调项"一律外挂到此处的 JSON，
代码只保留通用机制（扫描/匹配/检索/推理）。改 JSON 即改行为，无需动代码。

原则（用户治理令）：
1. 写进代码的补丁 → 零容忍，外挂数据集；
2. 特定而非通用的约束行为 → 零容忍，外挂数据集；
3. 外挂数据集统一由本模块管理；
4. 指南切片/更新/入库/归类 一并纳入本模块（见 ingestion.json）。
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def load(rel_path: str):
    """加载 settings/ 下的 JSON 配置（实时读盘，改即生效）。rel_path 形如 'clinical/redflags.json'。"""
    with open(os.path.join(_HERE, rel_path), encoding="utf-8") as f:
        return json.load(f)


def text(rel_path: str) -> str:
    """加载纯文本配置（如 prompt 模板）。"""
    with open(os.path.join(_HERE, rel_path), encoding="utf-8") as f:
        return f.read()
