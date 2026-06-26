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

_ITEM = re.compile(r"(检查项目|检查部位|检查名称|项目名称)\s*[:：]\s*([^\n]+)")
_DIAG = re.compile(r"(临床诊断|送检诊断|初步诊断)\s*[:：]\s*([^\n]+)")
_DATE = re.compile(r"(报告日期|检查日期|报告时间)\s*[:：]\s*(\d{4}\D+\d{1,2}\D+\d{1,2})")
_IMPR_HDR = re.compile(r"^\s*(提示|超声提示|诊断意见|印象|结论|检查结论)\s*[:：]?\s*$")
# 抬头/落款/ID/页脚噪声（不含测值的才剥）
_NOISE = re.compile(
    r"医院|住院号|门诊号|检查号|床\s*号|病人\s*ID|样本号|条码|报告单|"
    r"送检医师|报告医师|审核医师|检查医师|记录者|医师签名|签名|核对|"
    r"第\s*\d+\s*页|联系电话|地址|结果仅供|妥善保管|遗失不补|备注\s*[:：]")
# 测值：只认影像尺寸 a×b cm / 单维 cm / 体积 ml —— 不抓散文里的 %/mg（指南正文会爆炸）
_MEASURE = re.compile(
    r"([一-龥A-Za-z（()][一-龥A-Za-z（）()/]{0,10}?)?\s*[约为]{0,2}\s*"
    r"((?:\d+(?:\.\d+)?\s*[x×*]\s*)+\d+(?:\.\d+)?\s*(?:cm|mm)"   # 尺寸 a×b(×c)
    r"|\d+(?:\.\d+)?\s*ml"                                        # 体积
    r"|\d+(?:\.\d+)?\s*cm(?![²2/]))")                            # 单维 cm


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
    """清掉行内混进的表头：取「检查所见:」之后的正文、去 IMG 占位、剥残留 header 字段。"""
    ln = re.sub(r"^.*?(?:检查所见|超声所见|影像所见)\s*[:：]\s*", "", ln)
    ln = re.sub(r"\bIMG\d+\b|\b\d{1,2}:\d{2}\b|\d{4}年\d{1,2}月\d{1,2}日", "", ln)
    ln = re.sub(r"(?:检查部位|检查项目|临床诊断|报告日期|检查日期|报告时间)\s*[:：]\s*[^\s，。；]*[;；]?", "", ln)
    return re.sub(r"\s{2,}", " ", ln).strip(" ;；·-")


def _is_impression(line):
    """无显式「提示:」头时的兜底判定：短结论行（含 提示/印象 性措辞或 ？/-- ）。"""
    s = line.strip()
    if len(s) > 26:
        return False
    return bool(re.search(r"[？?]|--|—|囊肿|增生|结节|占位|异常|改变|肿大|钙化|狭窄|积水|未见异常", s))


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
