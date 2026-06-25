"""方案A 结构化症状包 → 总链路所需的中文文本（extracted_zh）。

接缝：/assess 与 pipeline.run_dual 真正消费的是一段“病人中文文本”，不是某个死 schema。
本模块把方案A 注册式表单产出的结构化症状包，忠实摊平成带标签的中文供下游消费。

双盲纪律：只输出【原始采集数据 + 佐证识别文本】，【不】输出方案A 自己用小模型预生成的
“阳性发现/建议补充”等结论——推理留给 run_dual 自行完成。

红旗归集（连调实测加入）：把报告原文里“占位/不均质/边界不清/结节/积水/肉眼血尿…”这类
【客观异常措辞】原句摘录到一个高亮分区，防止推理器只盯能对齐指南的发现而漏看可疑病灶。
注意：只做【措辞摘录与去否定】，不下任何诊断、不新增信息——仍守双盲。
"""
from typing import Any
import re

# 方案A 小模型预消化的结论 / 纯前端字段，不喂入推理
_SKIP_KEYS = {"阳性发现", "建议补充", "department_code"}
_EMPTY = (None, "", [], {})

# 客观“需进一步排查”措辞（高精度；不含 增生/增大 等常见良性词，避免淹没真正红旗）
_REDFLAG = (
    "占位", "不均质", "不均匀", "边界不清", "边界欠清", "界限不清", "形态不规则",
    "实性", "肿块", "包块", "结节", "浸润", "侵犯", "充盈缺损", "异常强化", "异常信号",
    "异常回声", "积水", "钙化", "狭窄", "梗阻", "待排", "不除外", "警惕", "恶性",
    "肉眼血尿", "潜血",
)
# 否定语境（命中红旗词但被否定的小句剔除，如“未见明显异常扩张”）
_NEG = ("未见", "未发现", "未提示", "阴性", "无异常", "无明显异常")


def _render_val(v: Any) -> str:
    if isinstance(v, list):
        return "、".join(str(x).strip() for x in v if str(x).strip())
    return str(v).strip()


def _gather_content(pkg: dict) -> str:
    """汇集患者客观内容（专科所见/佐证识别文本/各字段值），供红旗扫描；不含我加的标签。"""
    parts: list[str] = []

    def walk(v):
        if isinstance(v, dict):
            for k, sv in v.items():
                if k not in _SKIP_KEYS:
                    walk(sv)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif v not in _EMPTY:
            parts.append(str(v))

    walk({k: v for k, v in pkg.items() if k != "佐证材料"})
    for m in (pkg.get("佐证材料") or []):
        if isinstance(m, dict) and m.get("识别文本"):
            parts.append(str(m["识别文本"]))
    return "\n".join(parts)


def collect_red_flags(text: str) -> list[str]:
    """把含红旗措辞、且非否定语境的小句原文摘录出来（去重保序）。"""
    flags, seen = [], set()
    for clause in re.split(r"[。；;\n、，,]", text or ""):
        c = clause.strip()
        if not c or c in seen:
            continue
        if any(k in c for k in _REDFLAG) and not any(n in c for n in _NEG):
            seen.add(c)
            flags.append(c)
    return flags


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

    # 红旗高亮区（紧跟主诉，置顶强调）
    flags = collect_red_flags(_gather_content(pkg))
    if flags:
        head.append("【需进一步排查的客观发现（报告原文摘录，非诊断）】")
        head.extend(f"· {f}" for f in flags)

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
        evid = []
        for m in mats:
            if not isinstance(m, dict):
                continue
            name = (m.get("文件名") or "报告").strip()
            note = (m.get("备注") or "").strip()
            txt = (m.get("识别文本") or "").strip()
            label = name + (f"（{note}）" if note else "")
            if txt:
                evid.append(f"· {label}：\n{txt}")
            elif note:
                evid.append(f"· {label}")
        if evid:
            body.append("【佐证材料（报告原文）】")
            body.extend(evid)

    return "\n".join(head + body).strip()
