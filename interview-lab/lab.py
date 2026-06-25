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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 约束A：中国大陆机构模型（仅供实验对照，不可上线）
BANNED = re.compile(r"qwen|huatuo|deepseek|glm|chatglm|baichuan|internlm|\byi\b|bge|ernie", re.I)

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

    def do_POST(self):
        if self.path == "/upload":
            return self._upload()
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
