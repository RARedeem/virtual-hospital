# 问诊实验台 interview-lab

与生产系统**完全隔离**的沙盒，专门实验"**小模型 + 临床框架 + chat 式问诊**"，
不碰 orchestrator/postgres/authentik，纯标准库，独立端口 8010，仅共享 vh-ollama 推理。

## 启动（在你自己的终端）
```bash
python3 interview-lab/lab.py      # 监听 127.0.0.1:8010
```
浏览器开 **http://localhost:8010**。换问诊点「＋ 新问诊」。
⚠ 别在生产 /assess 跑评估时同时用（抢 GPU）。

## 实验的三个问题（对应讨论）
1. **小模型够不够**：把问诊从 llama3.3:70b 换更小的模型。下拉可随时切，看追问质量。
   约束A 合规小候选：`gemma4:e4b`(谷歌9.6G,默认) / `charlestang06/openbiollm`(医学5.7G) / `meditron:7b`。
   ⚠ Qwen/HuatuoGPT 等大陆机构模型仅供对照、**不可上线**（UI 已标「⚠大陆」）。
2. **chat 式**：当前就是纯 chat（无选项卡）。体验自然对话 vs 选项卡的取舍。
3. **临床框架**：`lab.py` 的 `FRAMEWORK` 即 system prompt 骨架（主诉→现病史→既往史→用药→家族史→
   系统回顾，按科室定制）。**在此迭代框架**，验证"框架扛结构 → 小模型够用 + 症状包天生结构化"。

## 已验证（2026-06-24）
`gemma4:e4b` + 框架：开场温和问主诉、追问一次一个且对症（尿频→问排尿不适），质量不输、更快。
→ 印证：框架是纲，小模型够用，结构化可前移进采集。

## 与生产的关系
这里只是原型验证。框架/模型定型后，再考虑移植回 `orchestrator/app/interviewer.py`（届时另议）。
