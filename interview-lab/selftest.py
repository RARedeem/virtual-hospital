#!/usr/bin/env python3
"""
interview-lab 端到端自测 / 回归。

完全自管：自己挑一个【空闲端口】起一份 lab.py 实例、跑完用例后按 PID 关掉，
**绝不 pkill**（不会误杀你手动跑着的 8010 实例），佐证落盘也改到临时目录（不污染 uploads/）。
改完代码 `python3 interview-lab/selftest.py` 一键回归。

覆盖：trim_filled 瘦身(单测) · /depts 科室发现 · /schema 卡片选项 ·
/upload(文本/PDF/图片三路抽取) · /augment 动态追问 · /package 症状包 · 超大佐证不溢出。
依赖缺失(ollama / gemma4:e4b / glm-ocr / poppler / PIL)时，对应用例标 SKIP，不算失败。
退出码：有 FAIL=1，否则 0。
"""
import base64, json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
REASON_MODEL = os.environ.get("SELFTEST_MODEL", "gemma4:e4b")

results = []   # (name, status, detail)  status ∈ PASS/FAIL/SKIP


def rec(name, status, detail=""):
    results.append((name, status, detail))
    tag = {"PASS": "✓ PASS", "FAIL": "✗ FAIL", "SKIP": "– SKIP"}[status]
    print(f"  [{tag}] {name}" + (f"   ·   {detail}" if detail else ""))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def get_json(base, path, timeout=20):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read())


def post_json(base, path, body, timeout=200):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ollama_models():
    try:
        d = json.load(urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5))
        return {m["name"] for m in d.get("models", [])}
    except Exception:
        return set()


def minimal_pdf(lines):
    """纯标准库手搓最小文本层 PDF（内置 Helvetica，ASCII 即可被 pdftotext 抽取）。"""
    ops = ("BT /F1 14 Tf 50 760 Td 16 TL\n" + "".join(f"({l}) Tj T*\n" for l in lines) + "ET").encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"\nendstream"]
    pdf = b"%PDF-1.4\n"; off = []
    for i, o in enumerate(objs, 1):
        off.append(len(pdf)); pdf += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(pdf); pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in off:
        pdf += b"%010d 00000 n \n" % o
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, x)
    return pdf


def make_image():
    """带文字的 PNG bytes（用于 glm-ocr 路径）；PIL 不可用则 None。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io
    except Exception:
        return None
    img = Image.new("RGB", (520, 150), "white"); d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except Exception:
        f = ImageFont.load_default()
    y = 18
    for ln in ["Ultrasound Report", "Right kidney: 3.5 cm solid mass", "Impression: renal mass, suspicious"]:
        d.text((20, y), ln, fill="black", font=f); y += 38
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()


def b64(raw):
    return base64.b64encode(raw).decode()


def main():
    print("interview-lab 自测 ─────────────────────────────")
    models = ollama_models()
    has_reason = REASON_MODEL in models
    has_ocr = any(m.startswith("glm-ocr") for m in models)
    has_poppler = bool(shutil.which("pdftotext"))
    try:
        sys.path.insert(0, HERE); import lab
        has_lab = True
    except Exception as e:
        has_lab, lab = False, None
        print("  (warn) import lab 失败:", e)
    print(f"环境: ollama模型={len(models)}  {REASON_MODEL}={'有' if has_reason else '无'}  "
          f"glm-ocr={'有' if has_ocr else '无'}  poppler={'有' if has_poppler else '无'}")
    print("- 用例 ------------------------------------------")

    # ① 单测 trim_filled（不依赖服务/模型）——佐证瘦身回归
    if has_lab:
        big = {"主诉": "x", "佐证材料": [{"文件名": f"g{i}.pdf", "识别文本": "长" * 9000} for i in range(4)]}
        t = lab.trim_filled(big)
        lens = [len(m["识别文本"]) for m in t["佐证材料"]]
        ok = len(t["佐证材料"]) <= 6 and all(n <= 1300 for n in lens)
        rec("trim_filled 瘦身(单测)", "PASS" if ok else "FAIL", f"份数{len(t['佐证材料'])} 各长{lens}")
    else:
        rec("trim_filled 瘦身(单测)", "SKIP", "import lab 失败")

    # 起独立实例（空闲端口 + 临时 uploads 目录）
    port = int(os.environ.get("SELFTEST_PORT") or free_port())
    base = f"http://127.0.0.1:{port}"
    up_dir = tempfile.mkdtemp(prefix="selftest_uploads_")
    env = dict(os.environ, LAB_PORT=str(port), LAB_UPLOADS=up_dir)
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "lab.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        ready = False
        for _ in range(40):
            try:
                get_json(base, "/depts", timeout=2); ready = True; break
            except Exception:
                time.sleep(0.25)
        if not ready:
            rec("服务启动", "FAIL", f"{base} 未就绪")
            return
        rec("服务启动", "PASS", f"独立实例 {base} (pid {proc.pid})")

        # ② /depts
        depts = get_json(base, "/depts")["depts"]
        rec("科室发现 /depts", "PASS" if depts else "FAIL", " / ".join(d["name"] for d in depts))

        # ③ /schema 卡片选项（带 desc）
        sch = get_json(base, "/schema?dept=urology")
        carded = any(isinstance(o, dict) and o.get("desc")
                     for sec in sch["sections"] for f in sec["fields"] for o in (f.get("options") or []))
        rec("卡片选项(带desc) /schema", "PASS" if carded else "FAIL",
            "urology 有带解释选项" if carded else "未发现 desc 选项")

        # ④ /upload 文本
        r = post_json(base, "/upload", {"name": "lab.txt", "mime": "text/plain",
                                        "data": b64("尿潜血 +++\n尿红细胞 满视野".encode())})
        ok = r.get("ocr_status") == "text" and "尿潜血" in r.get("ocr", "")
        rec("佐证·文本 /upload", "PASS" if ok else "FAIL", f"status={r.get('ocr_status')}")

        # ⑤ /upload PDF 文本层
        if has_poppler:
            pdf = minimal_pdf(["CT Report", "Right renal mass 3.5 cm", "Impression: r/o RCC"])
            r = post_json(base, "/upload", {"name": "ct.pdf", "mime": "application/pdf", "data": b64(pdf)})
            ok = r.get("ocr_status") == "pdf-text" and "renal" in r.get("ocr", "").lower()
            rec("佐证·PDF文本层 /upload", "PASS" if ok else "FAIL", f"status={r.get('ocr_status')}")
        else:
            rec("佐证·PDF文本层 /upload", "SKIP", "poppler(pdftotext) 不可用")

        # ⑥ /upload 图片 glm-ocr
        img = make_image()
        if img and has_ocr:
            r = post_json(base, "/upload", {"name": "us.png", "mime": "image/png", "data": b64(img)})
            ok = r.get("ocr_status") == "ok" and len(r.get("ocr", "").strip()) > 0
            rec("佐证·图片 glm-ocr /upload", "PASS" if ok else "FAIL",
                f"status={r.get('ocr_status')} chars={len(r.get('ocr', ''))}")
        else:
            rec("佐证·图片 glm-ocr /upload", "SKIP", "PIL 不可用" if not img else "glm-ocr 不在")

        # filled（含卡片选项值 + 佐证）
        chief = "肉眼血尿1周，伴右腰胀痛"
        filled = {"科室": "泌尿外科", "主诉": chief,
                  "现病史": {"起病时间": "1 周内", "程度": "中", "伴随表现（可多选）": ["肉眼血尿"]},
                  "尿液性状": {"血尿": "肉眼·无痛", "尿色": "洗肉水/血色"},
                  "全身 / 内分泌相关": {"血压": "偏高"},
                  "佐证材料": [{"文件名": "化验单.txt", "识别文本": "尿潜血+++；尿红细胞满视野"}]}

        if has_reason:
            # ⑦ /augment
            a = post_json(base, "/augment", {"model": REASON_MODEL, "department": "urology",
                                             "chief": chief, "filled": filled})
            aug = a.get("augment")
            ok = isinstance(aug, dict) and any(k in aug for k in ("add_options", "new_fields", "notes"))
            rec("动态追问 /augment", "PASS" if ok else "FAIL",
                f"{a.get('stats', {}).get('total_s', '?')}s" + ("" if ok else f" err={a.get('error')}"))

            # ⑧ /package
            p = post_json(base, "/package", {"model": REASON_MODEL, "department": "urology",
                                             "chief": chief, "filled": filled})
            pkg = p.get("package")
            ok = isinstance(pkg, dict) and ("阳性发现" in pkg or "主诉" in pkg)
            rec("生成症状包 /package", "PASS" if ok else "FAIL",
                f"{p.get('stats', {}).get('total_s', '?')}s" + ("" if ok else f" err={p.get('error')}"))

            # ⑨ 超大佐证不溢出（trim 集成回归）
            heavy = json.loads(json.dumps(filled))
            heavy["佐证材料"] = [{"文件名": f"指南{i}.pdf", "识别文本": "阈值用药分级" * 2200} for i in range(3)]
            p2 = post_json(base, "/package", {"model": REASON_MODEL, "department": "urology",
                                              "chief": chief, "filled": heavy})
            ok = isinstance(p2.get("package"), dict)
            rec("超大佐证不溢出 /package", "PASS" if ok else "FAIL",
                "瘦身后仍合法JSON" if ok else f"err={p2.get('error')}")
        else:
            for n in ("动态追问 /augment", "生成症状包 /package", "超大佐证不溢出 /package"):
                rec(n, "SKIP", f"{REASON_MODEL} 不在")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(up_dir, ignore_errors=True)

    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    n_skip = sum(1 for _, s, _ in results if s == "SKIP")
    print("─────────────────────────────────────────────")
    print(f"汇总: {n_pass} PASS · {n_fail} FAIL · {n_skip} SKIP")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
