#!/usr/bin/env python3
"""薄竖切连调：方案A 结构化症状包 → 序列化(extracted_zh) → pipeline.run_dual。

直击总链路【循证推理】（流程 A2 国内指南·llama3.3 + 流程 B 国际指南·llama4），
绕过 HTTP/auth/前端/落库，专看“结构化 JSON 症状包喂给循证推理发挥如何”。

容器内运行（app 代码烤进镜像，故 docker cp 后 exec）：
    docker cp orchestrator/app/package_serializer.py vh-orchestrator:/app/app/
    docker cp orchestrator/connect_run_dual.py        vh-orchestrator:/app/
    docker exec vh-orchestrator python /app/connect_run_dual.py [pkg.json]
不带参数用内置样本；带参数读一个方案A 症状包 JSON。
"""
import asyncio
import json
import os
import sys

import psycopg
from app import pipeline
from app.package_serializer import to_extracted_zh

# 方案A 注册式表单产出的【结构化症状包】样本（原始采集 + 佐证识别文本；故意有料以试循证推理）
SAMPLE = {
    "科室": "泌尿外科",
    "主诉": "肉眼血尿1周，伴右腰胀痛",
    "现病史": {
        "起病时间": "1 周内", "诱因": "无明显诱因", "程度": "中",
        "伴随表现": ["肉眼血尿", "腰痛/肾绞痛"],
    },
    "尿液性状": {"血尿": "肉眼·无痛", "尿色": "洗肉水/血色"},
    "既往史_手术史": "胆囊切除术后；自述血压偏高未规律服药",
    "用药史": "无",
    "家族史": "父亲糖尿病",
    "全身 / 内分泌相关": {"血压": "偏高", "体重食欲": "近期乏力纳差"},
    "佐证材料": [
        {"文件名": "泌尿系超声.png",
         "识别文本": "右肾中上极见 3.5×3.0cm 实性占位，边界不清；右肾盂轻度积水；"
                     "膀胱、前列腺未见明显异常。提示：右肾占位，警惕肿瘤。"},
        {"文件名": "门诊化验.txt",
         "识别文本": "诊室血压 152/96 mmHg；空腹血糖 7.8 mmol/L；"
                     "尿潜血 +++；尿红细胞 满视野/HP；血肌酐 98 umol/L。"},
    ],
}


class _ConnAdapter:
    """适配 pipeline 期望的 .fetch（$1/$2 → %s）。同 main.py。"""
    def __init__(self, conn):
        self._conn = conn

    async def fetch(self, sql, *params):
        sql = sql.replace("$1", "%s").replace("$2", "%s")
        async with self._conn.cursor() as cur:
            await cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in await cur.fetchall()]


def _hr(t):
    print("\n" + "═" * 70 + f"\n{t}\n" + "═" * 70, flush=True)


async def main():
    pkg = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else SAMPLE
    zh = to_extracted_zh(pkg)

    _hr("① 结构化症状包 → extracted_zh（喂给循证推理的中文）")
    print(zh, flush=True)

    def on_stage(k):
        print(f"   · 阶段 {k} …", flush=True)

    _hr("② run_dual 流水（structure→译→规则→A2→B）")
    async with await psycopg.AsyncConnection.connect(os.environ["PG_DSN"]) as conn:
        r = await pipeline.run_dual(_ConnAdapter(conn), zh, on_stage=on_stage)

    _hr("③ 确定性规则命中（两轨共享既定事实）")
    print(json.dumps(r["rule_hits"], ensure_ascii=False, indent=2), flush=True)
    print("抽取指标:", json.dumps(r["extracted_metrics"], ensure_ascii=False), flush=True)

    _hr(f"④ 循证推理·流程 A2（国内指南 · {r['a_model']}） 来源={r['sources_a']}")
    print(r["report_a_zh"], flush=True)

    _hr(f"⑤ 循证推理·流程 B（国际指南 · {r['b_model']}） 来源={r['sources_b']}")
    print(r["report_b_zh"], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
