"""方案A 结构化症状包 → 总链路所需的中文文本（extracted_zh）。

接缝：/assess 与 pipeline.run_dual 真正消费的是一段“病人中文文本”，不是某个死 schema。
本模块把方案A 注册式表单产出的结构化症状包，忠实摊平成带标签的中文供下游消费。

双盲纪律：只输出【原始采集数据 + 佐证识别文本】，【不】输出方案A 自己用小模型预生成的
“阳性发现/建议补充”等结论，也【不】替推理器划重点（红旗高亮区已撤）——推理保持中立，留给 run_dual。
"""
from typing import Any

from . import report_parser
from . import settings

# 具体内容全外挂（设置最大化）：跳过字段
_SKIP_KEYS = set(settings.load("clinical/serializer.json")["skip_keys"])
_EMPTY = (None, "", [], {})


def _render_val(v: Any) -> str:
    if isinstance(v, list):
        return "、".join(str(x).strip() for x in v if str(x).strip())
    return str(v).strip()


def to_extracted_zh(pkg: dict) -> str:
    """结构化症状包 dict → 中文文本。空值/结论字段跳过；佐证识别文本作为关键客观证据保留；
    可疑客观发现原文摘录置于主诉之后高亮。"""
    if not isinstance(pkg, dict):
        return str(pkg or "").strip()

    head: list[str] = []
    if pkg.get("科室"):
        head.append(f"【科室】{_render_val(pkg['科室'])}")
    if pkg.get("主诉"):
        head.append(f"【主诉】{_render_val(pkg['主诉'])}")
    # 不再在主诉后插"红旗高亮区"：① 与下方【检查报告】内容重复；② 污染主诉区；
    # ③ 替推理器划重点违中立原则。报告原文只在【检查报告】出现一次，推理保持中立。

    body: list[str] = []
    for k, v in pkg.items():
        if k in ("科室", "主诉", "佐证材料") or k in _SKIP_KEYS or v in _EMPTY:
            continue
        if isinstance(v, dict):
            sub = [f"  {sk}：{_render_val(sv)}" for sk, sv in v.items() if sv not in _EMPTY]
            if sub:
                body.append(f"【{k}】")
                body.extend(sub)
        else:
            rv = _render_val(v)
            if rv:
                body.append(f"【{k}】{rv}")

    mats = pkg.get("佐证材料")
    if isinstance(mats, list) and mats:
        # 报告文本走 report_parser 校真：内容去重 + 确定性解析（提示加权、测值/尺寸保真），
        # 不再原样灌 OCR/不经小模型概括。无结构的文本自动回退保留原文。
        report_texts = [(m.get("识别文本") or "").strip() for m in mats
                        if isinstance(m, dict) and (m.get("识别文本") or "").strip()]
        report_zh = report_parser.reports_to_zh(report_texts) if report_texts else ""
        if report_zh:
            body.append("【检查报告（医疗机构出具·客观为主）】")
            body.append(report_zh)
        # 仅有文件名/备注、无识别文本的附件，仍登记一行
        for m in mats:
            if isinstance(m, dict) and not (m.get("识别文本") or "").strip():
                name = (m.get("文件名") or "报告").strip()
                note = (m.get("备注") or "").strip()
                if name or note:
                    body.append(f"· 附件：{name}" + (f"（{note}）" if note else ""))

    return "\n".join(head + body).strip()
