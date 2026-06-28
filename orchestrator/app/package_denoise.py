"""症状包去噪：【确定性】剥离纯机器抽取噪声键，保层级、保全临床数据。

约束（用户 2026-06-28，方案 D 定案）：
  ① 确定性——纯规则按键名剥离，【不经 LLM】：零幻觉、可复现、可审计；
     且不让被评估的推理模型先替自己剪枝再推理（消除「自评自剪」破坏双盲完整性的隐忧）。
  ② JSON 格式化到底——输出仍为 JSON 树（不摊平），仅去字段、保层级；
  ③ 全程留痕——记录命中规则的剥离键集与配置版本，可审计。
  ④ fail-safe：宁可不删、绝不误删——只剥【删之零损临床】的键；clinical_guard 白名单强制保留。

规则全外挂 config/clinical/denoise_keys.json（设置最大化），代码只留删键机制：
  键被剥离 ⟺ (键名 ∈ noise_keys 或 键名 fullmatch 任一 noise_pattern) 且 键名 ∉ clinical_guard。
"""
import re

from . import settings

_CFG = settings.load("clinical/denoise_keys.json")
_NOISE_KEYS = set(_CFG["noise_keys"])
_GUARD = set(_CFG["clinical_guard"])
_PATTERNS = [re.compile(p) for p in _CFG.get("noise_patterns", [])]


def _is_noise(key: str) -> bool:
    """键名是否判为机器抽取噪声（命中 noise_keys 或 noise_patterns，且不在临床兜底白名单）。"""
    k = (key or "").strip()
    if k in _GUARD:
        return False
    if k in _NOISE_KEYS:
        return True
    return any(p.fullmatch(k) for p in _PATTERNS)


def _strip(node, stripped: set):
    """递归删除噪声键，保留 JSON 树层级。命中键记入 stripped（审计）。"""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if _is_noise(k):
                stripped.add(k)
                continue
            out[k] = _strip(v, stripped)
        return out
    if isinstance(node, list):
        return [_strip(x, stripped) for x in node]
    return node


def denoise(pkg: dict):
    """确定性去噪。返回 (清理后的JSON树, 审计字典)。无 LLM、无副作用、可复现。"""
    stripped: set = set()
    slimmed = _strip(pkg, stripped)
    audit = {
        "method": "deterministic_keys",
        "config": "clinical/denoise_keys.json",
        "stripped_keys": sorted(stripped),
    }
    return slimmed, audit
