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


# ── regedit 树格式（顶层键"症状包"）转换器 ───────────────────────────────
# 树：{症状包:[{科室:[{<科>:[<名>,{症状描述:[{<症状>:[..属性..]},{检查报告:[{<报告名>:<对象>}...]}]}]}, "<科>"]}]}
# 摊平为干净中文（与扁平 schema 同风格），临床事实保真；剔除机器抽取噪声字段，避免 repr 垃圾撑爆 embed。
_RPT_NOISE = {"关键指标", "关键键值", "重点关注", "内容摘要", "标题", "诊断相关",
              "症状体征", "用药信息", "处理建议", "检查部位", "文档类型", "机构", "页数", "检查号", "住院号"}
_BULLET_DROP = ("患者信息", "检查项目")              # 补充要点里与标题/档案重复的行
_CONCL_DROP = ("检查结论", "检查项目", "患者信息", "经腹部扫查", "CDI")  # 结论里剔除回显摘要句


def _tree_scalar(v: Any) -> str:
    if isinstance(v, list):
        return "、".join(_tree_scalar(x) for x in v if _tree_scalar(x))
    if isinstance(v, dict):
        return ""
    return str(v).strip()


def _render_report(name: str, obj: Any, out: list) -> None:
    info = obj.get("检查信息", {}) if isinstance(obj, dict) else {}
    proj = _tree_scalar(info.get("项目", "")).split("检查日期")[0].strip() or name
    diag = _tree_scalar(info.get("临床诊断", ""))
    out.append(f"  〔{proj}〕" + (f"  临床诊断：{diag}" if diag else ""))
    seen = obj.get("检查所见", {}) if isinstance(obj, dict) else {}
    for k, v in (seen.items() if isinstance(seen, dict) else []):
        if isinstance(v, str) and v.strip() and k not in _RPT_NOISE:
            out.append(f"    {k}：{v.strip()}")
    pts = seen.get("补充要点") if isinstance(seen, dict) else None
    if isinstance(pts, list):
        for p in pts:
            if isinstance(p, str) and p.strip() and not p.strip().startswith(_BULLET_DROP):
                out.append(f"    · {p.strip()}")
    concl = obj.get("超声结论") if isinstance(obj, dict) else None
    if isinstance(concl, list):
        cs = [c.strip() for c in concl if isinstance(c, str) and c.strip()]
        cs = [c for c in dict.fromkeys(cs) if len(c) <= 30 and not c.startswith(_CONCL_DROP)]
        if cs:
            out.append("    结论：" + "；".join(cs))


def _tree_to_zh(pkg: dict) -> str:
    """regedit 树格式（顶层键'症状包'）→ 干净结构化中文。"""
    sb = pkg.get("症状包")
    if isinstance(sb, list) and sb:
        sb = sb[0]
    keshi = sb.get("科室") if isinstance(sb, dict) else None
    depts, detail = [], None
    for e in (keshi or []):
        if isinstance(e, str):
            depts.append(e)
        elif isinstance(e, dict):
            for dn, dv in e.items():
                depts.append(dn)
                detail = detail or dv
    out = []
    if depts:
        out.append("【科室】" + "、".join(dict.fromkeys(depts)))
    symptoms, reports = [], []
    for item in (detail or []):
        if not isinstance(item, dict):
            continue
        for k, v in item.items():
            if k != "症状描述":
                continue
            for s in (v or []):
                if not isinstance(s, dict):
                    continue
                for sn, sv in s.items():
                    if sn == "检查报告":
                        for r in (sv or []):
                            if isinstance(r, dict):
                                for rn, ro in r.items():
                                    reports.append((rn, ro))
                    else:
                        attrs = []
                        for a in (sv or []):
                            if isinstance(a, dict):
                                for ak, av in a.items():
                                    attrs.append(f"{ak}：{_tree_scalar(av)}")
                        symptoms.append(sn + (f"（{'；'.join(attrs)}）" if attrs else ""))
    if symptoms:
        out.append("【症状】")
        out.extend(f"  {s}" for s in symptoms)
    if reports:
        out.append("【检查报告（医疗机构出具·客观为主）】")
        for rn, ro in reports:
            _render_report(rn, ro, out)
    return "\n".join(out).strip()


def to_extracted_zh(pkg: dict) -> str:
    """结构化症状包 dict → 中文文本。空值/结论字段跳过；佐证识别文本作为关键客观证据保留；
    可疑客观发现原文摘录置于主诉之后高亮。"""
    if not isinstance(pkg, dict):
        return str(pkg or "").strip()
    if "症状包" in pkg:                       # regedit 树格式 → 专用转换器，避免 repr 垃圾
        return _tree_to_zh(pkg)

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
