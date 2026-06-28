#!/usr/bin/env python3
"""
问诊实验台（interview-lab）—— 与生产系统完全隔离的沙盒。

目的：实验"小模型 + 临床框架 + 结构化采集"。交互界面是【会员注册式表单】：
整张病史表一次性呈现，患者自由选序填写；AI 只做【动态追问】。
症状包天生就是 JSON —— 表单状态即症状包，最终产物也是结构化 JSON。

★ 临床内容一律【外挂 JSON 数据集】驱动（datasets/*.json），lab.py 不硬编码任何
  科室字段。新增/修改专科 = 改 JSON，零改代码。每次请求实时读盘 → 改完即生效。

不依赖 orchestrator/postgres/authentik，纯标准库，独立端口 8010，
只共享 vh-ollama 做推理（localhost:11434，仅推理不改状态）。
⚠ 别在生产 /assess 跑评估时同时用本台（抢 GPU）。

启动：
    python3 interview-lab/lab.py
然后浏览器开 http://localhost:8010
"""
import base64, glob, json, mimetypes, os, re, subprocess, tempfile, urllib.request, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 约束A：中国大陆机构模型黑名单 → 外挂 repo 根 config/（跨服务统一设置，宿主直读，仍纯标准库）
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config",
                       "constraints", "constraint_a_models.json"), encoding="utf-8") as _f:
    BANNED = re.compile("|".join(json.load(_f)["banned_name_patterns"]), re.I)

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
PORT = int(os.environ.get("LAB_PORT", "8010"))
HERE = os.path.dirname(os.path.abspath(__file__))
DATASETS = os.path.join(HERE, "datasets")   # 外挂数据集目录（症状结构的唯一来源）
UPLOADS = os.environ.get("LAB_UPLOADS", os.path.join(HERE, "uploads"))  # 佐证落盘(含PHI,已gitignore)；selftest 覆写到临时目录
MAX_UPLOAD = 25 * 1024 * 1024               # 单份上限 25MB
os.makedirs(UPLOADS, exist_ok=True)

# 佐证材料 OCR：复用 vh-ollama 的 glm-ocr（非评估用途，不破坏隔离）。仅识别图片，PDF/文档不栅格化。
OCR_MODEL = os.environ.get("OCR_MODEL", "glm-ocr:latest")
OCR_PROMPT = "逐字提取图中全部文字，保留数字与单位，按行输出，只输出一次，不要解释。"
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
TXT_EXT = (".txt", ".md", ".csv", ".log")
PDF_JSON_PROMPT = """你是一位中文文档补充助手。下面已经有规则引擎抽取出的结构化结果，请仅做补充，不要推翻已有结构。

要求：
1. 忠实于原文，不要编造。
2. 只输出以下 JSON：
{
  "标题": "可选",
  "机构": "可选",
  "文档类型": "可选",
  "要点": ["要点1", "要点2"],
  "补充字段": {"键": "值"}
}
3. 若无法判断，返回空字符串/空数组/空对象。

当前文件：__NAME__
识别状态：__STATUS__

规则结构（JSON）：
__BASE__

原文片段：
__TEXT__
"""

ULTRASOUND_STRUCT_PROMPT = """你是医学影像报告结构化器。请把下面的超声报告整理为严格 JSON，且只输出 JSON，不要解释。

目标结构：
{
    "患者信息": {
        "姓名": "",
        "性别": "",
        "年龄": 0,
        "检查号": "",
        "科室": "",
        "住院号": ""
    },
    "检查信息": {
        "项目": "",
        "检查日期": "",
        "报告日期": "",
        "检查部位": [],
        "临床诊断": ""
    },
    "检查所见": {},
    "超声结论": []
}

规则：
1) 忠实原文，不编造。
2) 年龄必须是数字；缺失时填 0。
3) 检查部位/超声结论 必须是数组。
4) 检查所见 尽量按器官分层（如 肝/胆囊/胰腺/脾/CDFI）。
5) 缺失项留空字符串、空数组或空对象。

原文：
__TEXT__
"""

_RE_KV_LINE = re.compile(r"^\s*([^:：\s][^:：]{0,40}?)\s*[:：]\s*(.+?)\s*$")
_RE_DATE = re.compile(r"\b(?:19|20)\d{2}[-/.年](?:0?[1-9]|1[0-2])[-/.月](?:0?[1-9]|[12]\d|3[01])(?:日)?\b")
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_URL = re.compile(r"https?://[^\s]+")
_RE_PHONE = re.compile(r"(?:\+?86[-\s]?)?1[3-9]\d{9}\b")
_RE_MEASUREMENT = re.compile(
    r"([\u4e00-\u9fa5A-Za-z]{1,24})\s*[:：]?\s*(-?\d+(?:\.\d+)?)\s*"
    r"(mmHg|bpm|次/分|℃|%|kg|g|mg|mmol/L|mg/dL|cm|mm|U/L|IU/L|μmol/L|umol/L)?"
)
_RE_SECTION = re.compile(r"^\s*(?:第[一二三四五六七八九十\d]+[章节部分项]|[一二三四五六七八九十]+[、.．]|\d+(?:\.\d+){0,3}[、.．]?|[（(][一二三四五六七八九十\d]+[)）])")

_MEDICAL_KEYWORDS = [
    "主诉", "现病史", "既往史", "过敏", "体温", "血压", "脉搏", "呼吸", "血氧", "心率",
    "诊断", "印象", "症状", "体征", "阳性", "阴性", "异常", "检验", "化验", "影像", "超声",
    "ct", "mri", "x线", "病理", "用药", "剂量", "复查", "随访", "治疗", "处置", "手术",
    "hemoglobin", "platelet", "glucose", "creatinine", "diagnosis", "impression", "assessment",
]

_NOISE_LINE_PATTERNS = [
    r"^\s*page\s*\d+\s*$",
    r"^\s*第\s*\d+\s*页\s*$",
    r"^\s*contents\s*$",
    r"^\s*table\s+of\s+contents\s*$",
    r"copyright|all rights reserved|doi:|isbn|issn|guideline office",
    r"www\.|http://|https://",
    r"^\s*references?\s*$",
    r"^\s*acknowledg(e)?ments?\s*$",
]

_RISK_TERMS = [
    "危急", "高危", "重度", "急性", "恶化", "恶性", "梗死", "出血", "休克", "昏迷",
    "感染", "败血", "坏死", "栓塞", "穿孔", "肿瘤", "癌", "阳性", "异常", "中毒",
    "critical", "urgent", "acute", "severe", "malignant", "positive", "abnormal",
]

_ABNORMAL_MARKERS = ["↑", "↓", "异常", "阳性", "high", "low", "critical", "abnormal", "++", "+"]


# ── 数据集加载（每次实时读盘 → 改 JSON 即生效，无需重启）──
def _load(name):
    with open(os.path.join(DATASETS, name), encoding="utf-8") as f:
        return json.load(f)


def list_depts():
    """从 datasets/ 发现科室（文件名即 code，_ 开头为公共件跳过），按 order 排序。"""
    out = []
    for fn in sorted(os.listdir(DATASETS)):
        if fn.endswith(".json") and not fn.startswith("_"):
            d = _load(fn)
            out.append({"code": d.get("code", fn[:-5]), "name": d.get("name", fn[:-5]),
                        "order": d.get("order", 99)})
    return sorted(out, key=lambda x: (x["order"], x["code"]))


def dept_name(code):
    for d in list_depts():
        if d["code"] == code:
            return d["name"]
    return "全科"


def schema_for(dept_code):
    """组装表单 schema：通用骨架(_base.json) + 该科外挂数据集。代码只管流程，字段全来自 JSON。"""
    base = _load("_base.json")
    try:
        d = _load(f"{dept_code}.json")
    except FileNotFoundError:
        d = _load("urology.json")
    hpi_fields = list(base["common_hpi"]) + [
        {"key": "伴随症状", "label": "伴随表现（可多选）", "type": "multi", "options": d.get("assoc", [])}]
    dept_sections = [{"key": f"sp{i}", "title": s["title"], "fields": s["fields"]}
                     for i, s in enumerate(d.get("sections", []))]
    sections = [
        {"key": "chief", "title": base["chief"]["title"], "fields": [base["chief"]["field"]]},
        {"key": "hpi", "title": "现病史", "fields": hpi_fields},
    ] + base["history_sections"] + dept_sections
    return {"department": d.get("name", "全科"), "department_code": d.get("code", dept_code),
            "sections": sections}


# ── AI 提示词 ──
AUGMENT_PROMPT = """你是一位经验丰富的{dept}主治医师，正在审阅一份正在填写中的病史采集表。
患者主诉：{chief}
当前已填内容（JSON）：
{filled}

请基于主诉与已填内容做【动态追问】，只补充最关键、尚未覆盖的点。严格只输出一个 JSON 对象，禁止任何解释文字、禁止 markdown 代码块，结构如下：
{{
  "add_options": {{"伴随症状": ["新增候选1", "新增候选2"]}},   // 给已有多选字段追加更贴合本主诉的候选，没有则空对象
  "new_fields": [{{"section": "hpi", "key": "唯一键", "label": "字段名", "type": "single", "options": ["a","b"]}}],  // 需要新增的追问字段，section ∈ hpi/sp0../pmh，type ∈ text/single/multi，没有则空数组
  "notes": ["一句话提醒患者注意补充的要点"]   // ≤3 条，没有则空数组
}}
每个新增 label/选项≤8字。只问与本主诉直接相关的。"""

PACKAGE_PROMPT = """你是一位{dept}主治医师。下面是患者填写完成的结构化病史表（JSON）：
{filled}

请据此生成最终【症状包】。严格只输出一个 JSON 对象，禁止任何解释文字、禁止 markdown 代码块，结构如下：
{{
  "科室": "{dept}",
  "主诉": "一句话主诉",
  "现病史": {{ ... 把已填的现病史要素逐条规整，缺失项省略 ... }},
  "既往史_手术史": "...",
  "用药史": "...",
  "家族史": "...",
  "专科所见": {{ ... 把已填的各专科领域要点逐条规整 ... }},
  "阳性发现": ["按临床意义排序的关键阳性点"],
  "建议补充": ["仍缺失、建议进一步问清的点，≤3条"]
}}
忠实于患者所填，不杜撰；不做诊断、不开药。"""


def ollama_chat(model, messages, fmt=None):
    payload = {"model": model, "messages": messages, "stream": False}
    if fmt:
        payload["format"] = fmt   # "json" → 让 ollama 强制 JSON 输出
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def clean_ocr(t):
    """glm-ocr 小模型常把同一段循环重复输出，这里去围栏 + 首个非空行复现即截断。"""
    t = re.sub(r"```[a-zA-Z]*", "", (t or "").strip())
    lines = [ln.rstrip() for ln in t.splitlines()]
    first = next((ln for ln in lines if ln.strip()), None)
    if first:
        idx = [i for i, ln in enumerate(lines) if ln.strip() == first.strip()]
        if len(idx) >= 2:
            lines = lines[:idx[1]]
    out = []
    for ln in lines:                       # 折叠多余空行
        if not ln.strip() and (not out or not out[-1]):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def ollama_ocr(b64):
    payload = {"model": OCR_MODEL, "prompt": OCR_PROMPT, "images": [b64], "stream": False,
               "options": {"num_predict": 700, "stop": ["```"]}}   # stop 截断循环复读
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return clean_ocr(json.load(r).get("response", ""))


def extract_pdf(path, max_pages=8):
    """PDF 抽文本：先取文本层(pdftotext)，是扫描件则栅格化(pdftoppm)逐页过 glm-ocr。
    用 poppler CLI（subprocess，仍属标准库导入），不引第三方 Python 依赖。"""
    # 1) 文本层（数字版报告，秒出且无损）
    try:
        out = subprocess.run(["pdftotext", "-layout", path, "-"],
                             capture_output=True, timeout=30).stdout.decode("utf-8", "ignore").strip()
        if len(re.sub(r"\s", "", out)) >= 20:
            return out, "pdf-text"
    except Exception:
        pass
    # 2) 扫描件：栅格化 → 逐页 OCR
    try:
        with tempfile.TemporaryDirectory() as td:
            pre = os.path.join(td, "p")
            subprocess.run(["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", str(max_pages), path, pre],
                           capture_output=True, timeout=180)
            texts = []
            for i, pg in enumerate(sorted(glob.glob(pre + "*.png")), 1):
                with open(pg, "rb") as f:
                    t = ollama_ocr(base64.b64encode(f.read()).decode())
                if t:
                    texts.append(f"【第{i}页】\n{t}")
            joined = "\n\n".join(texts).strip()
            return joined, ("pdf-ocr" if joined else "empty")
    except Exception as e:
        return f"[PDF解析失败] {e}", "error"


def ollama_models():
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=15) as r:
        out = []
        for m in json.load(r).get("models", []):
            n = m["name"]
            out.append({"name": n, "gb": round(m.get("size", 0) / 1e9, 1), "ok": not BANNED.search(n)})
        return sorted(out, key=lambda x: x["gb"])


def extract_json(text):
    """从模型输出里抠出第一个完整 JSON 对象，容忍代码块/前后赘述。"""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e > s:
        try:
            return json.loads(text[s:e + 1])
        except Exception:
            return None
    return None


def trim_filled(filled, per_file=1200, max_files=6):
    """喂模型前给佐证识别文本瘦身：单份截断 + 限份数，防小模型上下文溢出 → JSON 失败。
    只影响传给模型的副本；前端右栏展示/下载仍是全文。"""
    try:
        f = json.loads(json.dumps(filled, ensure_ascii=False))   # 深拷贝
    except Exception:
        return filled
    mats = f.get("佐证材料")
    if isinstance(mats, list):
        f["佐证材料"] = mats[:max_files]
        for m in f["佐证材料"]:
            if isinstance(m, dict):
                t = m.get("识别文本")
                if isinstance(t, str) and len(t) > per_file:
                    m["识别文本"] = t[:per_file] + "…（余略）"
    return f


def trim_text(text, limit=8000):
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…（余略）"


def _blank_ultrasound_schema():
    return {
        "患者信息": {
            "姓名": "",
            "性别": "",
            "年龄": 0,
            "检查号": "",
            "科室": "",
            "住院号": "",
        },
        "检查信息": {
            "项目": "",
            "检查日期": "",
            "报告日期": "",
            "检查部位": [],
            "临床诊断": "",
        },
        "检查所见": {},
        "超声结论": [],
    }


def _text_value_by_labels(text, labels):
    for lab in labels:
        m = re.search(rf"(?:^|\n)\s*{re.escape(lab)}\s*[:：]\s*(.+)", text or "", flags=re.I)
        if m:
            return m.group(1).strip()
    return ""


def _split_items(raw):
    if not raw:
        return []
    parts = re.split(r"[、，,;/；\n]+", raw)
    out = [p.strip() for p in parts if p.strip()]
    return _uniq_keep_order(out)


def _extract_conclusion_from_text(text):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out = []
    in_conclusion = False
    for ln in lines:
        if re.search(r"结论|超声提示|印象", ln):
            in_conclusion = True
            tail = re.sub(r"^.*?(结论|超声提示|印象)\s*[:：]?", "", ln).strip()
            if tail:
                out.extend(_split_items(tail))
            continue
        if in_conclusion:
            if re.match(r"^[A-Za-z\u4e00-\u9fa5].*[:：]", ln) and not re.match(r"^[\d一二三四五六七八九十]+[、.．]", ln):
                break
            out.extend(_split_items(re.sub(r"^[\-•·\d一二三四五六七八九十]+[、.．)]?", "", ln).strip()))
    return _uniq_keep_order(out)


def _basic_ultrasound_fallback(name, extracted):
    s = _blank_ultrasound_schema()
    s["患者信息"]["姓名"] = _text_value_by_labels(extracted, ["姓名", "患者姓名"])
    s["患者信息"]["性别"] = _text_value_by_labels(extracted, ["性别"])
    age_raw = _text_value_by_labels(extracted, ["年龄"])
    m_age = re.search(r"(\d{1,3})", age_raw)
    s["患者信息"]["年龄"] = int(m_age.group(1)) if m_age else 0
    s["患者信息"]["检查号"] = _text_value_by_labels(extracted, ["检查号", "检查编号", "检查ID", "检查号ID"]) or name
    s["患者信息"]["科室"] = _text_value_by_labels(extracted, ["科室", "送检科室"])
    s["患者信息"]["住院号"] = _text_value_by_labels(extracted, ["住院号", "病案号"])

    s["检查信息"]["项目"] = _text_value_by_labels(extracted, ["检查项目", "项目", "检查名称"])
    s["检查信息"]["检查日期"] = _text_value_by_labels(extracted, ["检查日期", "检查时间"]) or _text_value_by_labels(extracted, ["日期"])
    s["检查信息"]["报告日期"] = _text_value_by_labels(extracted, ["报告日期", "报告时间"])
    s["检查信息"]["临床诊断"] = _text_value_by_labels(extracted, ["临床诊断", "诊断"])
    sites_raw = _text_value_by_labels(extracted, ["检查部位", "部位", "检查范围"])
    s["检查信息"]["检查部位"] = _split_items(sites_raw)

    s["超声结论"] = _extract_conclusion_from_text(extracted)
    s["检查所见"] = {"原文摘录": trim_text(extracted, 2400)}
    return s


def _normalize_ultrasound_schema(candidate, name, extracted):
    base = _blank_ultrasound_schema()
    if isinstance(candidate, dict):
        top_aliases = {
            "患者信息": ["患者信息", "patient_info"],
            "检查信息": ["检查信息", "examination_info"],
        }
        field_aliases = {
            "患者信息": {
                "姓名": ["姓名", "name"],
                "性别": ["性别", "gender"],
                "年龄": ["年龄", "age"],
                "检查号": ["检查号", "examination_number"],
                "科室": ["科室", "department"],
                "住院号": ["住院号", "inpatient_number"],
            },
            "检查信息": {
                "项目": ["项目", "project"],
                "检查日期": ["检查日期", "examination_date"],
                "报告日期": ["报告日期", "report_date"],
                "检查部位": ["检查部位", "examination_sites"],
                "临床诊断": ["临床诊断", "clinical_diagnosis"],
            },
        }

        for top_cn, top_keys in top_aliases.items():
            src = None
            for k in top_keys:
                if isinstance(candidate.get(k), dict):
                    src = candidate[k]
                    break
            if not src:
                continue
            for field_cn, aliases in field_aliases[top_cn].items():
                for a in aliases:
                    if a in src:
                        base[top_cn][field_cn] = src[a]
                        break

        findings = candidate.get("检查所见")
        if not isinstance(findings, dict):
            findings = candidate.get("findings")
        if isinstance(findings, dict):
            base["检查所见"] = findings

        conclusion = candidate.get("超声结论")
        if conclusion is None:
            conclusion = candidate.get("conclusion")
        if isinstance(conclusion, list):
            base["超声结论"] = [str(x).strip() for x in conclusion if str(x).strip()]

    # hard type correction
    try:
        base["患者信息"]["年龄"] = int(base["患者信息"].get("年龄") or 0)
    except Exception:
        m = re.search(r"(\d{1,3})", str(base["患者信息"].get("年龄", "")))
        base["患者信息"]["年龄"] = int(m.group(1)) if m else 0

    if not isinstance(base["检查信息"].get("检查部位"), list):
        base["检查信息"]["检查部位"] = _split_items(str(base["检查信息"].get("检查部位", "")))
    if not isinstance(base.get("超声结论"), list):
        base["超声结论"] = _split_items(str(base.get("超声结论", "")))

    # fill critical blanks from regex fallback
    fb = _basic_ultrasound_fallback(name, extracted)
    for top in ["患者信息", "检查信息"]:
        for k, v in base[top].items():
            empty = (v == "" or v == 0 or v == [] or v == {})
            if empty and fb[top].get(k) not in ("", 0, [], {}):
                base[top][k] = fb[top][k]

    if not base["超声结论"]:
        base["超声结论"] = fb["超声结论"]
    if not base["检查所见"]:
        base["检查所见"] = fb["检查所见"]
    return base


def _is_ultrasound_like(name, extracted):
    probe = f"{name}\n{extracted}".lower()
    keys = ["超声", "彩色多普勒", "cdfi", "超声提示", "肝", "胆囊", "胰腺", "脾"]
    hits = sum(1 for k in keys if k.lower() in probe)
    return hits >= 3


def _uniq_keep_order(items):
    seen = set()
    out = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _is_noise_line(line):
    s = (line or "").strip()
    if not s:
        return True
    if len(s) <= 1:
        return True
    if len(re.sub(r"[\W_]+", "", s, flags=re.UNICODE)) <= 1:
        return True
    for p in _NOISE_LINE_PATTERNS:
        if re.search(p, s, flags=re.I):
            return True
    return False


def _medical_relevance_score(line):
    s = (line or "").strip()
    if not s:
        return 0
    score = 0
    lower = s.lower()
    if any(k in s or k in lower for k in _MEDICAL_KEYWORDS):
        score += 2
    if _RE_MEASUREMENT.search(s):
        score += 2
    if _RE_KV_LINE.match(s):
        score += 1
    if any(flag in s for flag in ["↑", "↓", "+", "-", "阳性", "阴性", "异常"]):
        score += 1
    if re.search(r"\b\d+(?:\.\d+)?\s*(mmHg|bpm|%|℃|kg|g|mg|mmol/L|mg/dL|cm|mm)\b", s, flags=re.I):
        score += 2
    return score


def _filter_medical_lines(lines, max_lines=500):
    scored = []
    for ln in lines:
        s = ln.strip()
        if _is_noise_line(s):
            continue
        score = _medical_relevance_score(s)
        if score <= 0:
            continue
        scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = _uniq_keep_order([s for _, s in scored])
    return selected[:max_lines]


def _is_medical_kv(kv):
    k = str(kv.get("key", "")).strip()
    v = str(kv.get("value", "")).strip()
    probe = f"{k} {v}"
    if any(w in probe.lower() or w in probe for w in _MEDICAL_KEYWORDS):
        return True
    if _RE_MEASUREMENT.search(probe):
        return True
    if re.search(r"\b\d+(?:\.\d+)?\b", probe):
        return True
    return False


def _extract_medical_useful(lines, key_values, measurements):
    diagnosis = []
    symptoms = []
    meds = []
    plans = []

    for ln in lines:
        s = ln.strip()
        lo = s.lower()
        if any(x in s for x in ["诊断", "印象"]) or "diagnosis" in lo or "impression" in lo:
            diagnosis.append(s)
        if any(x in s for x in ["痛", "发热", "咳", "乏力", "头晕", "恶心", "呕吐", "胸闷", "气短", "出血"]) or "symptom" in lo:
            symptoms.append(s)
        if any(x in s for x in ["用药", "药物", "mg", "剂量", "bid", "qd", "tid", "po", "iv"]):
            meds.append(s)
        if any(x in s for x in ["建议", "复查", "随访", "评估", "治疗", "处置", "plan", "follow-up"]):
            plans.append(s)

    useful = {
        "诊断相关": _uniq_keep_order(diagnosis)[:30],
        "症状体征": _uniq_keep_order(symptoms)[:40],
        "用药信息": _uniq_keep_order(meds)[:30],
        "处理建议": _uniq_keep_order(plans)[:30],
        "关键键值": _uniq_keep_order([kv for kv in key_values if _is_medical_kv(kv)])[:120],
        "关键指标": _uniq_keep_order(measurements)[:150],
    }
    return useful


def _is_abnormal_measurement(item):
    if not isinstance(item, dict):
        return False
    text = f"{item.get('name', '')} {item.get('value', '')} {item.get('unit', '')}".lower()
    return any(m.lower() in text for m in _ABNORMAL_MARKERS)


def _priority_profile(doc_type):
    base = {
        "risk_term": 3,
        "abnormal_marker": 2,
        "measurement": 2,
        "diagnosis_hint": 2,
        "plan_hint": 1,
        "kind_bonus": {
            "诊断线索": 2,
            "症状体征": 1,
            "处置建议": 0,
            "关键键值": 0,
            "关键指标": 0,
        },
    }
    if doc_type == "检验报告":
        base["measurement"] = 4
        base["abnormal_marker"] = 3
        base["kind_bonus"]["关键指标"] = 2
    elif doc_type == "影像报告":
        base["diagnosis_hint"] = 3
        base["risk_term"] = 4
        base["kind_bonus"]["诊断线索"] = 3
    elif doc_type == "病理报告":
        base["risk_term"] = 4
        base["diagnosis_hint"] = 3
        base["kind_bonus"]["诊断线索"] = 3
    elif doc_type == "出院记录":
        base["plan_hint"] = 2
        base["kind_bonus"]["处置建议"] = 1
    return base


def _score_priority_text(text, profile):
    s = str(text or "").strip()
    if not s:
        return 0, []
    score = 0
    reasons = []
    lo = s.lower()
    for t in _RISK_TERMS:
        if t.lower() in lo:
            score += profile["risk_term"]
            reasons.append(f"风险词:{t}")
    for m in _ABNORMAL_MARKERS:
        if m.lower() in lo:
            score += profile["abnormal_marker"]
            reasons.append(f"异常标记:{m}")
    if _RE_MEASUREMENT.search(s):
        score += profile["measurement"]
        reasons.append("检验指标")
    if any(k in lo for k in ["诊断", "impression", "diagnosis", "评估", "assessment"]):
        score += profile["diagnosis_hint"]
        reasons.append("诊断线索")
    if any(k in lo for k in ["建议", "复查", "随访", "治疗", "处置", "plan", "follow-up"]):
        score += profile["plan_hint"]
        reasons.append("处置建议")
    return score, _uniq_keep_order(reasons)


def _build_priority_findings(medical_useful, doc_type):
    profile = _priority_profile(doc_type)
    candidates = []

    for text in medical_useful.get("诊断相关", []):
        score, reasons = _score_priority_text(text, profile)
        bonus = profile["kind_bonus"].get("诊断线索", 0)
        candidates.append({"kind": "诊断线索", "text": text, "score": score + bonus,
                           "triggers": reasons + ["类别加权:诊断线索"] if bonus else reasons})
    for text in medical_useful.get("症状体征", []):
        score, reasons = _score_priority_text(text, profile)
        bonus = profile["kind_bonus"].get("症状体征", 0)
        candidates.append({"kind": "症状体征", "text": text, "score": score + bonus,
                           "triggers": reasons + ["类别加权:症状体征"] if bonus else reasons})
    for text in medical_useful.get("处理建议", []):
        score, reasons = _score_priority_text(text, profile)
        bonus = profile["kind_bonus"].get("处置建议", 0)
        candidates.append({"kind": "处置建议", "text": text, "score": score + bonus,
                           "triggers": reasons + ["类别加权:处置建议"] if bonus else reasons})

    for kv in medical_useful.get("关键键值", []):
        txt = f"{kv.get('key', '')}: {kv.get('value', '')}".strip()
        score, reasons = _score_priority_text(txt, profile)
        bonus = profile["kind_bonus"].get("关键键值", 0)
        candidates.append({"kind": "关键键值", "text": txt, "score": score + bonus,
                           "triggers": reasons + ["类别加权:关键键值"] if bonus else reasons})

    for m in medical_useful.get("关键指标", []):
        txt = f"{m.get('name', '')}: {m.get('value', '')}{m.get('unit', '')}".strip()
        score, reasons = _score_priority_text(txt, profile)
        bonus = profile["kind_bonus"].get("关键指标", 0)
        if _is_abnormal_measurement(m):
            score += 3
            reasons = reasons + ["异常指标加权"]
        candidates.append({"kind": "关键指标", "text": txt, "score": score + bonus,
                           "triggers": reasons + ["类别加权:关键指标"] if bonus else reasons})

    candidates = [c for c in candidates if c["score"] > 0 and c["text"]]
    candidates.sort(key=lambda x: x["score"], reverse=True)

    dedup = []
    seen = set()
    for c in candidates:
        key = c["text"]
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)

    high_risk = [c for c in dedup if c["score"] >= 6][:30]
    suspected_dx = [c["text"] for c in dedup if c["kind"] == "诊断线索" and c["score"] >= 4][:20]
    abnormal_indicators = [c for c in dedup if c["kind"] == "关键指标" and c["score"] >= 4][:40]

    return {
        "doc_type_profile": doc_type,
        "high_risk": high_risk,
        "suspected_diagnosis_clues": suspected_dx,
        "abnormal_indicators": abnormal_indicators,
    }


def _split_pages(text):
    t = (text or "").strip()
    if not t:
        return []

    pages = []
    current_no = 1
    buf = []
    for line in t.splitlines():
        m = re.match(r"^【第(\d+)页】\s*$", line.strip())
        if m:
            if buf:
                pages.append({"page": current_no, "text": "\n".join(buf).strip()})
            current_no = int(m.group(1))
            buf = []
            continue
        buf.append(line)
    if buf:
        pages.append({"page": current_no, "text": "\n".join(buf).strip()})
    return [p for p in pages if p["text"]]


def _pick_title(lines):
    for ln in lines[:20]:
        s = ln.strip()
        if not s:
            continue
        if len(s) <= 80 and not _RE_KV_LINE.match(s):
            return s
    return ""


def _extract_key_values(lines):
    kvs = []
    for ln in lines:
        m = _RE_KV_LINE.match(ln)
        if not m:
            continue
        k = m.group(1).strip()
        v = m.group(2).strip()
        if len(k) < 1 or len(v) < 1:
            continue
        kvs.append({"key": k, "value": v})
    return _uniq_keep_order(kvs)


def _extract_measurements(text):
    out = []
    for m in _RE_MEASUREMENT.finditer(text or ""):
        name = m.group(1).strip()
        value = m.group(2).strip()
        unit = (m.group(3) or "").strip()
        if len(name) < 1:
            continue
        out.append({"name": name, "value": value, "unit": unit})
    return _uniq_keep_order(out)


def _extract_entities(text):
    t = text or ""
    return {
        "dates": _uniq_keep_order(_RE_DATE.findall(t)),
        "emails": _uniq_keep_order(_RE_EMAIL.findall(t)),
        "urls": _uniq_keep_order(_RE_URL.findall(t)),
        "phones": _uniq_keep_order(_RE_PHONE.findall(t)),
    }


def _extract_sections(lines):
    sections = []
    current = {"title": "正文", "content": []}

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if _RE_SECTION.match(s):
            if current["content"]:
                sections.append(current)
            current = {"title": s[:60], "content": []}
            continue
        current["content"].append(s)

    if current["content"]:
        sections.append(current)

    out = []
    for i, sec in enumerate(sections, 1):
        text = "\n".join(sec["content"]).strip()
        out.append({
            "id": f"S{i}",
            "title": sec["title"],
            "content": trim_text(text, 1200),
            "line_count": len(sec["content"]),
        })
    return out


def _guess_doc_type(name, text):
    s = f"{name}\n{text}".lower()
    if "检验" in s or "化验" in s or "laboratory" in s:
        return "检验报告"
    if "影像" in s or "ct" in s or "mri" in s or "超声" in s:
        return "影像报告"
    if "出院" in s:
        return "出院记录"
    if "病理" in s:
        return "病理报告"
    return "未分类医疗文档"


def _structure_pdf_json(name, status, extracted):
    pages = _split_pages(extracted)
    if not pages and extracted.strip():
        pages = [{"page": 1, "text": extracted.strip()}]

    all_lines = []
    page_items = []
    for p in pages:
        lines = [ln.rstrip() for ln in p["text"].splitlines() if ln.strip()]
        med_lines = _filter_medical_lines(lines, max_lines=180)
        all_lines.extend(lines)
        kvs = [kv for kv in _extract_key_values(med_lines) if _is_medical_kv(kv)]
        meas = _extract_measurements("\n".join(med_lines))
        page_items.append({
            "page_no": p["page"],
            "text": trim_text(p["text"], 1800),
            "medical_text": trim_text("\n".join(med_lines), 1800),
            "line_count": len(lines),
            "medical_line_count": len(med_lines),
            "key_values": kvs,
            "measurements": meas,
        })

    full_text = "\n".join(p["text"] for p in pages) if pages else extracted
    medical_lines = _filter_medical_lines(all_lines, max_lines=600)
    medical_text = "\n".join(medical_lines)
    entities = _extract_entities(full_text)
    all_kv = _uniq_keep_order([kv for pg in page_items for kv in pg["key_values"]])
    all_meas = _uniq_keep_order([m for pg in page_items for m in pg["measurements"]])
    sections = _extract_sections(medical_lines)[:180]
    medical_useful = _extract_medical_useful(medical_lines, all_kv, all_meas)
    doc_type = _guess_doc_type(name, full_text)
    priority_findings = _build_priority_findings(medical_useful, doc_type)

    return {
        "meta": {
            "file_name": name,
            "source": "pdf",
            "ocr_status": status,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "page_count": len(page_items),
            "text_length": len(full_text),
            "medical_text_length": len(medical_text),
            "medical_line_count": len(medical_lines),
        },
        "document": {
            "title": _pick_title(medical_lines or all_lines),
            "doc_type": doc_type,
            "summary": trim_text(medical_text or full_text, 900),
        },
        "entities": entities,
        "key_values": all_kv,
        "measurements": all_meas,
        "medical_useful": medical_useful,
        "priority_findings": priority_findings,
        "sections": sections,
        "pages": page_items,
    }


def _merge_ai_enrichment(base, ai_data):
    if not isinstance(ai_data, dict):
        return base

    base = json.loads(json.dumps(base, ensure_ascii=False))
    doc = base.get("document", {})
    if ai_data.get("标题") and not doc.get("title"):
        doc["title"] = str(ai_data.get("标题"))
    if ai_data.get("文档类型"):
        doc["doc_type"] = str(ai_data.get("文档类型"))
    if ai_data.get("机构"):
        doc["organization"] = str(ai_data.get("机构"))
    base["document"] = doc

    points = ai_data.get("要点") if isinstance(ai_data.get("要点"), list) else []
    base["insights"] = {
        "key_points": _uniq_keep_order([str(x).strip() for x in points if str(x).strip()]),
        "extra_fields": ai_data.get("补充字段") if isinstance(ai_data.get("补充字段"), dict) else {},
    }
    return base


def _to_chinese_report_schema(name, extracted, merged):
    base = merged if isinstance(merged, dict) else {}
    doc = base.get("document", {}) if isinstance(base.get("document"), dict) else {}
    meta = base.get("meta", {}) if isinstance(base.get("meta"), dict) else {}
    useful = base.get("medical_useful", {}) if isinstance(base.get("medical_useful"), dict) else {}
    priority = base.get("priority_findings", {}) if isinstance(base.get("priority_findings"), dict) else {}
    insights = base.get("insights", {}) if isinstance(base.get("insights"), dict) else {}

    fb = _basic_ultrasound_fallback(name, extracted)

    key_points = insights.get("key_points") if isinstance(insights.get("key_points"), list) else []
    high_risk = priority.get("high_risk") if isinstance(priority.get("high_risk"), list) else []
    dx_clues = priority.get("suspected_diagnosis_clues") if isinstance(priority.get("suspected_diagnosis_clues"), list) else []

    conclusion = _uniq_keep_order(
        _extract_conclusion_from_text(extracted)
        + [str(item.get("text", "")).strip() for item in high_risk if isinstance(item, dict)]
        + [str(item).strip() for item in dx_clues]
        + [str(item).strip() for item in key_points]
    )[:12]

    report_title = str(doc.get("title") or "").strip()
    report_type = str(doc.get("doc_type") or "").strip()
    organization = str(doc.get("organization") or "").strip()
    summary = str(doc.get("summary") or "").strip()

    project = fb["检查信息"].get("项目") or report_title or report_type

    return {
        "患者信息": fb["患者信息"],
        "检查信息": {
            "项目": project,
            "检查日期": fb["检查信息"].get("检查日期", ""),
            "报告日期": fb["检查信息"].get("报告日期", ""),
            "检查部位": fb["检查信息"].get("检查部位", []),
            "临床诊断": fb["检查信息"].get("临床诊断", ""),
            "文档类型": report_type,
            "机构": organization,
            "页数": meta.get("page_count", 0),
        },
        "检查所见": {
            "标题": report_title,
            "内容摘要": summary,
            "诊断相关": useful.get("诊断相关", []),
            "症状体征": useful.get("症状体征", []),
            "用药信息": useful.get("用药信息", []),
            "处理建议": useful.get("处理建议", []),
            "关键键值": useful.get("关键键值", []),
            "关键指标": useful.get("关键指标", []),
            "重点关注": high_risk,
            "补充要点": key_points,
        },
        "超声结论": conclusion,
    }


def _ai_json(model, prompt):
    """跑一次 AI，强制 JSON，返回 (data|None, stats, raw)。"""
    resp = ollama_chat(model, [{"role": "user", "content": prompt}], fmt="json")
    content = resp.get("message", {}).get("content", "")
    ec, ed = resp.get("eval_count", 0), resp.get("eval_duration", 0) or 0
    stats = {"tok": ec, "tps": round(ec / (ed / 1e9), 1) if ed else 0,
             "total_s": round(resp.get("total_duration", 0) / 1e9, 1)}
    return extract_json(content), stats, content


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")   # 禁缓存：防 lab.html 版本错配
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            with open(os.path.join(HERE, "lab.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/depts":
            try:
                self._send(200, json.dumps({"depts": list_depts()}, ensure_ascii=False))
            except Exception as e:
                self._send(500, json.dumps({"error": f"数据集加载失败: {e}"}, ensure_ascii=False))
        elif self.path.startswith("/schema"):
            m = re.search(r"[?&]dept=([a-z]+)", self.path)
            dept = m.group(1) if m else "urology"
            try:
                self._send(200, json.dumps(schema_for(dept), ensure_ascii=False))
            except Exception as e:
                self._send(500, json.dumps({"error": f"数据集加载失败: {e}"}, ensure_ascii=False))
        elif self.path == "/models":
            try:
                self._send(200, json.dumps({"models": ollama_models()}, ensure_ascii=False))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
        elif self.path.startswith("/uploads/"):
            fn = os.path.basename(self.path[len("/uploads/"):])   # basename 防目录穿越
            p = os.path.join(UPLOADS, fn)
            if fn and os.path.isfile(p):
                ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
                with open(p, "rb") as f:
                    self._send(200, f.read(), ctype)
            else:
                self._send(404, json.dumps({"error": "not found"}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_OPTIONS(self):
        self._send(204, b"")

    def _upload(self):
        """佐证材料落盘：收 {name, mime, data(base64)}，存为 <uuid><ext>，回 {id,name,size}。"""
        try:
            req = self._body()
            raw = base64.b64decode(req.get("data", "") or "")
            if not raw:
                return self._send(200, json.dumps({"error": "空文件"}, ensure_ascii=False))
            if len(raw) > MAX_UPLOAD:
                return self._send(200, json.dumps({"error": "文件过大(>25MB)"}, ensure_ascii=False))
            name = (req.get("name") or "file").strip()
            ext = os.path.splitext(name)[1][:10]
            fid = uuid.uuid4().hex[:12] + ext
            with open(os.path.join(UPLOADS, fid), "wb") as f:
                f.write(raw)
            # 抽取文本：图片走 glm-ocr，纯文本直接解码，其它（PDF/文档）暂不处理
            mime = (req.get("mime") or "").lower()
            el = ext.lower()
            ocr, status = "", "skipped"
            try:
                if mime.startswith("image/") or el in IMG_EXT:
                    ocr = ollama_ocr(req.get("data", "")); status = "ok" if ocr else "empty"
                elif el == ".pdf" or mime == "application/pdf":
                    ocr, status = extract_pdf(os.path.join(UPLOADS, fid))
                elif mime.startswith("text/") or el in TXT_EXT:
                    ocr = raw.decode("utf-8", "ignore").strip(); status = "text"
            except Exception as e:
                ocr, status = f"[OCR失败] {e}", "error"
            self._send(200, json.dumps({"id": fid, "name": name, "size": len(raw),
                                        "ocr": ocr[:8000], "ocr_status": status}, ensure_ascii=False))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))

    def _pdf_json(self):
        try:
            req = self._body()
            raw = base64.b64decode(req.get("data", "") or "")
            if not raw:
                return self._send(200, json.dumps({"error": "空文件"}, ensure_ascii=False))
            if len(raw) > MAX_UPLOAD:
                return self._send(200, json.dumps({"error": "文件过大(>25MB)"}, ensure_ascii=False))

            name = (req.get("name") or "file.pdf").strip()
            model = (req.get("model") or "gemma4:e4b").strip()
            if not name.lower().endswith(".pdf"):
                name += ".pdf"

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(raw)
                tmp_path = tmp.name

            try:
                extracted, status = extract_pdf(tmp_path)
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

            if _is_ultrasound_like(name, extracted):
                prompt = ULTRASOUND_STRUCT_PROMPT.replace("__TEXT__", trim_text(extracted, 7000))
                ai_data, stats, raw_content = _ai_json(model, prompt)
                data = _normalize_ultrasound_schema(ai_data, name, extracted)
            else:
                base_struct = _structure_pdf_json(name, status, extracted)
                prompt = (
                    PDF_JSON_PROMPT
                    .replace("__NAME__", name)
                    .replace("__STATUS__", status)
                    .replace("__BASE__", trim_text(json.dumps(base_struct, ensure_ascii=False), 4000))
                    .replace("__TEXT__", trim_text(extracted, 5000))
                )
                ai_data, stats, raw_content = _ai_json(model, prompt)
                merged = _merge_ai_enrichment(base_struct, ai_data)
                data = _to_chinese_report_schema(name, extracted, merged)

            self._send(200, json.dumps({
                "name": name,
                "ocr_status": status,
                "ocr": trim_text(extracted, 5000),
                "json": data,
                "raw_ai": trim_text(raw_content, 1200),
                "stats": stats,
            }, ensure_ascii=False))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))

    def do_POST(self):
        if self.path == "/upload":
            return self._upload()
        if self.path == "/pdf-json":
            return self._pdf_json()
        if self.path not in ("/augment", "/package"):
            return self._send(404, json.dumps({"error": "not found"}))
        req = self._body()
        model = req.get("model") or "gemma4:e4b"
        dept = dept_name(req.get("department"))
        filled = json.dumps(trim_filled(req.get("filled", {})), ensure_ascii=False, indent=2)
        chief = req.get("chief") or "（未填）"
        prompt = (AUGMENT_PROMPT if self.path == "/augment" else PACKAGE_PROMPT).format(
            dept=dept, chief=chief, filled=filled)
        try:
            data, stats, raw = _ai_json(model, prompt)
            if data is None:
                return self._send(200, json.dumps(
                    {"error": "模型未返回有效JSON", "raw": raw[:500], "stats": stats}, ensure_ascii=False))
            key = "augment" if self.path == "/augment" else "package"
            self._send(200, json.dumps({key: data, "stats": stats}, ensure_ascii=False))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}))


if __name__ == "__main__":
    print(f"问诊实验台（数据集驱动·注册式表单） → http://localhost:{PORT}  (ollama={OLLAMA})")
    print(f"  数据集目录: {DATASETS}  科室: {[d['code'] for d in list_depts()]}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
