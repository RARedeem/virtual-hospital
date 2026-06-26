"""医疗报告解析 —— OCR 文本【确定性】解析成干净结构化 JSON。

设计原则（连调实证：小模型「规整」会丢尺寸、抹平结构、埋掉放射科印象）：
- 源报告本就是结构化的（检查项目/临床诊断/所见/提示/测值），用规则解析，【不经 LLM 改写】
  → 零失真、零幻觉、零丢尺寸，「提示/印象」逐字保真。
- 净化：内容指纹去重（同报告多传只留一份）、剥抬头/ID/日期/页码噪声。
- 无法识别结构的报告：保留原文，绝不丢信息。
纯标准库（re/hashlib），可独立测试。
"""
import hashlib
import re

from . import settings

# 报告格式 profile 外挂（设置最大化）：代码只留通用解析机制，字段标记/噪声/单位/印象判定全来自配置。
_P = settings.load("report_formats/default.json")
_FM = _P["field_markers"]


def _alt(items):
    return "|".join(items)


_ITEM = re.compile(rf"({_alt(_FM['item'])})\s*[:：]\s*([^\n]+)")
_DIAG = re.compile(rf"({_alt(_FM['diag'])})\s*[:：]\s*([^\n]+)")
_DATE = re.compile(rf"({_alt(_FM['date'])})\s*[:：]\s*(\d{{4}}\D+\d{{1,2}}\D+\d{{1,2}})")
_IMPR_HDR = re.compile(rf"^\s*({_alt(_FM['impression_header'])})\s*[:：]?\s*$")
_NOISE = re.compile(_alt(_P["noise"]))
_SU, _VU = _alt(_P["size_units"]), _alt(_P["volume_units"])
# 测值：只认影像尺寸 a×b / 单维 / 体积 —— 不抓散文里的 %/mg（指南正文会爆炸）
_MEASURE = re.compile(
    rf"([一-龥A-Za-z（()][一-龥A-Za-z（）()/]{{0,10}}?)?\s*[约为]{{0,2}}\s*"
    rf"((?:\d+(?:\.\d+)?\s*[x×*]\s*)+\d+(?:\.\d+)?\s*(?:{_SU})"
    rf"|\d+(?:\.\d+)?\s*(?:{_VU})"
    rf"|\d+(?:\.\d+)?\s*(?:{_SU})(?![²2/]))")
_FINDINGS_LABELS = _alt(_P["findings_labels"])
_SCRUB_HDR = _alt(_FM["item"] + _FM["date"] + _FM["diag"])
_IMPR_MARKERS = re.compile(_alt(_P["impression_markers"]))
_IMPR_MAXLEN = _P.get("impression_max_len", 26)


def report_fingerprint(text: str) -> str:
    """内容指纹（归一空白后 md5）：同报告多次上传只留一份。"""
    return hashlib.md5(re.sub(r"\s+", "", text or "").encode("utf-8")).hexdigest()


def dedupe_reports(texts):
    seen, out = set(), []
    for t in texts:
        if not (t or "").strip():
            continue
        fp = report_fingerprint(t)
        if fp not in seen:
            seen.add(fp)
            out.append(t)
    return out


def _norm_date(s):
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else (s or "").strip()


def _scrub(ln):
    """清掉行内混进的表头：取「检查所见:」之后的正文、去 IMG/时间占位、剥残留 header 字段。"""
    ln = re.sub(rf"^.*?(?:{_FINDINGS_LABELS})\s*[:：]\s*", "", ln)
    ln = re.sub(r"\bIMG\d+\b|\b\d{1,2}:\d{2}\b|\d{4}年\d{1,2}月\d{1,2}日", "", ln)
    ln = re.sub(rf"(?:{_SCRUB_HDR})\s*[:：]\s*[^\s，。；]*[;；]?", "", ln)
    return re.sub(r"\s{2,}", " ", ln).strip(" ;；·-")


def _is_impression(line):
    """无显式「提示:」头时的兜底判定：短结论行（含印象性措辞，markers/阈值来自 profile）。"""
    s = line.strip()
    return len(s) <= _IMPR_MAXLEN and bool(_IMPR_MARKERS.search(s))


def parse_report(text):
    """OCR 报告文本 → 结构化 dict：{检查项目, 临床诊断, 报告日期, 所见[], 提示[], 测值{}}。
    确定性解析、不丢信息、提示保真；完全无结构则回退保留原文。"""
    item = diag = date = ""
    findings, impression, measures = [], [], {}
    in_impr = False
    body_lines = []  # (text, is_descriptive)

    for raw in (text or "").splitlines():
        ln = re.sub(r"\s{2,}", " ", raw.strip())
        if not ln:
            continue
        if _DATE.search(ln) and not date:
            date = _norm_date(_DATE.search(ln).group(2))
        m = _ITEM.search(ln)
        if m and not item:
            item = re.sub(r"\s*(报告日期|检查日期).*$", "", m.group(2)).strip(" ;；")
        m = _DIAG.search(ln)
        if m and not diag:
            diag = re.sub(r"\s*(报告日期|检查日期).*$", "", m.group(2)).strip()
        if _IMPR_HDR.match(ln):
            in_impr = True
            continue
        # 测值抽取（所见/提示都抽，一项不丢）
        for mm in _MEASURE.finditer(ln):
            key = (mm.group(1) or "").strip(" ：:约为") or "测值"
            measures.setdefault(key, mm.group(2).replace(" ", ""))
        if _NOISE.search(ln) and not _MEASURE.search(ln):
            continue
        body = _scrub(ln)
        if not body:
            continue
        (impression if in_impr else body_lines).append(body)

    # 无显式「提示」头：把末尾连续的短结论行划为提示，其余为所见
    if not impression and body_lines:
        cut = len(body_lines)
        while cut > 0 and _is_impression(body_lines[cut - 1]):
            cut -= 1
        findings, impression = body_lines[:cut], body_lines[cut:]
    else:
        findings = body_lines

    out = {"检查项目": item, "临床诊断": diag, "报告日期": date,
           "所见": findings, "提示": impression, "测值": measures}
    if not (item or diag or impression or findings):
        out = {"原文": (text or "").strip()}
    return out


def report_to_zh(rep):
    """结构化报告 → 喂下游的中文：提示/印象置顶加权（机构结论是高价值信号），测值齐全。"""
    if rep.get("原文"):
        return rep["原文"]
    head = rep.get("检查项目") or "检查报告"
    parts = [f"【{head}】"]
    if rep.get("提示"):
        parts.append("提示（机构结论）：" + "；".join(rep["提示"]))
    if rep.get("测值"):
        parts.append("测值：" + "；".join(f"{k}={v}" for k, v in rep["测值"].items()))
    if rep.get("所见"):
        parts.append("所见：" + " ".join(rep["所见"]))
    return "\n".join(parts)


def reports_to_zh(texts):
    """多份报告原文 → 去重 → 逐份解析 → 拼接成下游可消费的中文（提示加权）。"""
    return "\n\n".join(report_to_zh(parse_report(t)) for t in dedupe_reports(texts))
