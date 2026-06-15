"""
临床规则执行器 — 确定性双轨之一。

从会员健康数据中提取结构化指标，对规则库中的 JSONB 条件做硬阈值判断。
结果完全确定、可复现，与 RAG 的概率性推理互补。

condition JSONB 格式：
  单条件:    {"op": ">=", "value": 7.0}
  区间:      {"op": "between", "low": 6.1, "high": 6.9}
  复合(与):  {"all": [{"op": ">=", "value": 7.0}, ...]}
  复合(或):  {"any": [{"op": "<", "value": 3.9}, {"op": ">", "value": 11.1}]}
"""
from dataclasses import dataclass


@dataclass
class RuleHit:
    rule_name: str
    metric: str
    value: float
    conclusion: str
    citation_id: str | None
    severity: str | None


def _eval_leaf(value: float, cond: dict) -> bool:
    """求值单个比较条件。"""
    op = cond.get("op")
    if op == ">=":
        return value >= cond["value"]
    if op == "<=":
        return value <= cond["value"]
    if op == ">":
        return value > cond["value"]
    if op == "<":
        return value < cond["value"]
    if op == "==":
        return value == cond["value"]
    if op == "between":
        return cond["low"] <= value <= cond["high"]
    raise ValueError(f"未知操作符: {op}")


def eval_condition(value: float, condition: dict) -> bool:
    """递归求值条件，支持 all/any 复合。"""
    if "all" in condition:
        return all(eval_condition(value, c) for c in condition["all"])
    if "any" in condition:
        return any(eval_condition(value, c) for c in condition["any"])
    return _eval_leaf(value, condition)


def evaluate_rules(metrics: dict[str, float], rules: list[dict]) -> list[RuleHit]:
    """
    对一组会员指标执行所有相关规则。

    metrics: 如 {"fasting_glucose_mmol_l": 7.4, "hba1c_percent": 6.8}
    rules:   规则库查出的规则行（含 metric / condition / conclusion 等）

    仅对 metrics 中存在对应指标的规则求值，命中则记录。
    """
    hits: list[RuleHit] = []
    for rule in rules:
        metric = rule["metric"]
        if metric not in metrics:
            continue                  # 该指标本次数据未提供，跳过
        value = metrics[metric]
        try:
            if eval_condition(value, rule["condition"]):
                hits.append(RuleHit(
                    rule_name=rule["name"],
                    metric=metric,
                    value=value,
                    conclusion=rule["conclusion"],
                    citation_id=rule.get("citation_id"),
                    severity=rule.get("severity"),
                ))
        except (ValueError, KeyError, TypeError):
            # 规则定义异常不应中断整体评估，跳过该条
            continue
    return hits
