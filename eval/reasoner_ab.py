"""
Reasoner 对照评测：流程 B 的 reasoner 在 meditron:70b ↔ llama3.3:70b 间换，其余全控变量。

目的：验证"llama3.3 的严谨度/医学专业性是否明显弱于 meditron"这一假设（2026-06-24 用户提出）。
方法：同病例、同国际证据(knowledge_base/nomic)、同 few-shot prompt，仅换 reasoner 模型。
  复用生产 pipeline 的 translate/retrieve/rules 函数；reason 的 prompt 原样搬来、参数化模型。
  绝不改动生产 pipeline.py 的焊死路线——这是离线实验。

运行（容器内，绕 auth）：
  docker exec -i vh-orchestrator python - < eval/reasoner_ab.py
输出：逐例 meditron vs llama3.3 的中文结论并排 + 计时，另存 JSON 供盲评打分。
"""
import asyncio, os, time, json
import psycopg
from app import pipeline
from app import ollama_client as oc
from app import rules_engine, extractor
from app.main import _ConnAdapter

PG_DSN = os.environ["PG_DSN"]
OUT_JSON = "/tmp/reasoner_ab.json"

# 三臂候选 reasoner（key→ollama 模型名）。llama4:16x17b=67G＞48G显存，会 CPU offload，
# 本评测正是要实测它的质量与延迟代价（ARCHITECTURE §6.4 红线）。
MODELS = {
    "meditron": "meditron:70b",
    "llama3.3": "llama3.3:70b",
    "llama4":   "llama4:16x17b",
}
WARM = 600  # keep_alive 秒：让模型在跑完一批病例期间常驻，省重载

# 4 例：机械阈值 / 专科对标 / 标准推理 / 边界陷阱
CASES = {
    "代谢综合征(机械阈值)":
        "体检发现：空腹血糖 6.1 mmol/L，糖化血红蛋白 6.9%，诊室血压 148/109 mmHg，甘油三酯 2.71 mmol/L。无明显不适。",
    "前列腺增生(专科对标)":
        "主诉尿频、尿急、尿流不畅、夜尿增多 3 月。超声：前列腺横径 4.9cm、上下径 4.0cm、体积约 40.8ml，内腺增大。",
    "帕金森(标准推理)":
        "62 岁男性，右手静止性震颤 1 年，渐出现运动迟缓、写字变小、起步困难、面部表情减少；查体右上肢肌强直（齿轮样），单侧起病。",
    "边界陷阱(白大衣高血压?)":
        "28 岁男性，公司体检单次诊室血压 150/95 mmHg，平素无症状，近一周熬夜赶项目、大量咖啡、自述紧张。其余指标正常，无家族史。",
}


def build_messages(patient_en, guidelines):
    """与生产 pipeline.reason() 完全一致的 prompt（搬来以便换模型，不动生产代码）。"""
    evidence_str = "\n".join(
        f"- [{g['citation_id']}] {g['chunk_text'][:pipeline.CONTEXT_CHUNK_CHARS]}"
        for g in guidelines
    ) or "-（无检索证据）"
    sys_prompt = (
        "You are an Evidence-Based Medicine Expert. You must strictly output the "
        "3-part structured assessment as shown in the example. Assess ONLY findings "
        "explicitly present in the patient case; never invent values or guideline content."
    )
    shot_user = (
        "CLINICAL EVIDENCE:\n- Stage 2 Hypertension is defined as BP >= 140/90.\n"
        "- If BP >= 130/80 and high CVD risk, start medication.\n\n"
        "PATIENT CASE: The patient has a resting blood pressure of 145/95 mmHg.\n\nASSESSMENT:")
    shot_asst = (
        "1. CLINICAL IMPRESSION: Stage 2 Hypertension.\n"
        "2. GUIDELINE ALIGNMENT: Based on the provided evidence, the patient's BP "
        "(145/95) strictly meets the criteria for Stage 2 Hypertension (>= 140/90).\n"
        "3. RECOMMENDED ACTION: Initiate pharmacological treatment and schedule a follow-up.")
    actual_user = f"CLINICAL EVIDENCE:\n{evidence_str}\n\nPATIENT CASE: {patient_en}\n\nASSESSMENT:"
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": shot_user},
        {"role": "assistant", "content": shot_asst},
        {"role": "user", "content": actual_user},
    ]


async def main():
    results = {}
    async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
        ad = _ConnAdapter(conn)
        active_rules = await pipeline.fetch_active_rules(ad)

        # Phase 0：控变量——逐例 translate(gemma4) + retrieve(nomic/国际) + rules，两 reasoner 共用
        print("=== Phase 0：翻译 + 国际检索 + 规则（两轨共用证据）===", flush=True)
        for name, zh in CASES.items():
            en = await pipeline.translate_to_en(zh)
            gl = await pipeline.retrieve_guidelines(ad, en)
            metrics = await extractor.extract_metrics(en)
            hits = rules_engine.evaluate_rules(metrics, active_rules)
            results[name] = {"zh": zh, "en": en, "guidelines": gl,
                             "sources": [g["citation_id"] for g in gl],
                             "rules": [h.conclusion for h in hits]}
            print(f"  [{name}] 证据来源={results[name]['sources']} 规则命中={len(hits)}", flush=True)

        # Phase 1：逐 reasoner warm-batch 跑完所有例（一个模型常驻跑完一批再换下一个，省重载）
        for key, model in MODELS.items():
            print(f"\n=== reasoner = {key} ({model})（常驻 warm）===", flush=True)
            for name in CASES:
                t = time.time()
                msgs = build_messages(results[name]["en"], results[name]["guidelines"])
                out = await oc.chat(model, msgs, options=pipeline.REASONING_OPTIONS, keep_alive=WARM)
                results[name][f"{key}_en"] = out
                results[name][f"{key}_s"] = round(time.time() - t, 1)
                print(f"  [{name}] {results[name][f'{key}_s']}s", flush=True)
            await oc.generate(model, "x", keep_alive=0)  # 卸载该模型腾显存给下一个

        # Phase 2：各 reasoner 的英文结论回译中文（gemma4）
        print(f"\n=== 英译汉（gemma4）===", flush=True)
        for name in CASES:
            for key in MODELS:
                results[name][f"{key}_zh"] = await pipeline.translate_to_zh(results[name][f"{key}_en"])

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 并排打印（三臂）
    print(f"\n\n############ 三臂对照：{' vs '.join(MODELS)} ############", flush=True)
    for name, r in results.items():
        print(f"\n{'='*70}\n■ 病例：{name}")
        print(f"  证据来源：{r['sources']}  |  规则命中：{r['rules']}")
        for key in MODELS:
            print(f"\n— {key} ({MODELS[key]}，{r[f'{key}_s']}s）—\n{r[f'{key}_zh']}")
    print("\n=== 各模型总耗时 ===")
    for key in MODELS:
        print(f"  {key}: {round(sum(r[f'{key}_s'] for r in results.values()),1)}s")
    print(f"\nJSON 已存 {OUT_JSON}")

asyncio.run(main())
