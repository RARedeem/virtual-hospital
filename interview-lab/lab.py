#!/usr/bin/env python3
"""
问诊实验台（interview-lab）—— 与生产系统完全隔离的沙盒。

目的：专门实验"小模型 + 临床框架 + chat 式问诊"，不碰生产
（不依赖 orchestrator/postgres/authentik，纯标准库，独立端口 8010）。

只共享 vh-ollama 做推理（localhost:11434，仅推理不改状态）。
⚠ 别在生产 /assess 跑评估时同时用本台（抢 GPU）。

启动：
    python3 interview-lab/lab.py
然后浏览器开 http://localhost:8010
"""
import json, os, re, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 约束A：中国大陆机构模型（仅供实验对照，不可上线）
BANNED = re.compile(r"qwen|huatuo|deepseek|glm|chatglm|baichuan|internlm|\byi\b|bge|ernie", re.I)

# 选项卡模式追加指令：让模型在问题后给可点选项（hybrid：chat + chips）
OPTIONS_INSTRUCTION = (
    "\n\n【选项卡模式】每次提问后必须另起一行，用 `【选项】选项1 | 选项2 | 选项3` 格式，"
    "给出 3-6 个该问题最常见的简短备选答案（每个≤8字），供患者快速点选；患者也可自由作答。"
    "输出【问诊小结】时不给选项。")

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
PORT = int(os.environ.get("LAB_PORT", "8010"))
HERE = os.path.dirname(os.path.abspath(__file__))

# ── 临床问诊框架（system prompt 第一版，可在此迭代）──
# 框架承载"该问什么、什么顺序"，让小模型只需把对话填自然 → 小模型够用、信息不漏。
FRAMEWORK = """你是一位经验丰富的全科主治医师，正在用中文为患者做【问诊/病史采集】。
当前科室：{dept}。

请遵循临床问诊骨架，循序渐进采集信息：
1. 主诉——先弄清最困扰患者的症状（一句话）。
2. 现病史——围绕主诉追问：起病时间、诱因、部位、性质、程度、加重/缓解因素、伴随症状、演变。
3. 既往史与手术史。
4. 用药史。
5. 家族史。
6. 系统回顾——针对【{dept}】重点追问相关系统（如泌尿问排尿/夜尿/性功能；神经问震颤/步态/记忆；
   心血管问胸闷/心悸/活动耐量；内分泌问多饮多尿/体重/怕热怕冷）。

硬规则：
- 每轮【只问一个】最关键的问题，语言通俗（患者不是医生），态度温和。
- 必须【根据患者上一轮回答】动态决定下一问，不要机械背模板。
- 不做诊断、不开药——你只负责把信息问全问清。
- 信息基本采全后，输出以【问诊小结】开头的结构化分节摘要：
  主诉 / 现病史 / 既往史·手术史 / 用药 / 家族史 / 阳性发现，每节逐条。

现在开始：先用一句话向患者问好并询问主诉。"""

DEPTS = {"cardiology":"心血管内科","endocrinology":"内分泌科","urology":"泌尿外科","neurology":"神经内科"}


def ollama_chat(model, messages):
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)   # 返回完整响应（含 message + 计时/token 统计）


def ollama_models():
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=15) as r:
        out = []
        for m in json.load(r).get("models", []):
            n = m["name"]
            out.append({"name": n, "gb": round(m.get("size", 0) / 1e9, 1), "ok": not BANNED.search(n)})
        return sorted(out, key=lambda x: x["gb"])   # 小模型在前


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a): pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            with open(os.path.join(HERE, "lab.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/models":
            try: self._send(200, json.dumps({"models": ollama_models()}))
            except Exception as e: self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/chat":
            return self._send(404, json.dumps({"error": "not found"}))
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        model = req.get("model") or "gemma4:e4b"   # 默认小模型（谷歌，约束A合规）
        dept = DEPTS.get(req.get("department"), "全科")
        mode = req.get("mode", "chat")              # chat | cards
        history = req.get("messages", [])           # [{role, content}]
        sysprompt = FRAMEWORK.format(dept=dept) + (OPTIONS_INSTRUCTION if mode == "cards" else "")
        sys = {"role": "system", "content": sysprompt}
        try:
            resp = ollama_chat(model, [sys] + history)
            content = resp.get("message", {}).get("content", "").strip()
            ec, ed = resp.get("eval_count", 0), resp.get("eval_duration", 0) or 0
            stats = {"tok": ec, "tps": round(ec / (ed / 1e9), 1) if ed else 0,
                     "total_s": round(resp.get("total_duration", 0) / 1e9, 1)}
            self._send(200, json.dumps({"reply": content, "stats": stats}))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}))


if __name__ == "__main__":
    print(f"问诊实验台 → http://localhost:{PORT}  (ollama={OLLAMA})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
