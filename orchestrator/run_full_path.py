#!/usr/bin/env python3
"""跑整路径：忠实(parser 解析)的 extracted_zh → pipeline.run_dual → A2/B 双轨。
docker cp 进容器后：python /app/run_full_path.py /app/faithful_pkg.txt"""
import asyncio, json, os, sys
import psycopg
from app import pipeline


class _ConnAdapter:
    def __init__(self, conn): self._conn = conn
    async def fetch(self, sql, *params):
        sql = sql.replace("$1", "%s").replace("$2", "%s")
        async with self._conn.cursor() as cur:
            await cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in await cur.fetchall()]


def hr(t): print("\n" + "═" * 68 + f"\n{t}\n" + "═" * 68, flush=True)


async def main():
    zh = open(sys.argv[1], encoding="utf-8").read()
    hr("① 忠实 extracted_zh（parser 解析、报告为主）")
    print(zh, flush=True)
    hr("② run_dual 流水")
    async with await psycopg.AsyncConnection.connect(os.environ["PG_DSN"]) as conn:
        r = await pipeline.run_dual(_ConnAdapter(conn), zh,
                                    on_stage=lambda k: print(f"   · {k}", flush=True))
    hr("③ 确定性规则")
    print(json.dumps(r["rule_hits"], ensure_ascii=False, indent=2), flush=True)
    hr(f"④ 流程 A2（国内 · {r['a_model']}） 来源={r['sources_a']}")
    print(r["report_a_zh"], flush=True)
    hr(f"⑤ 流程 B（国际 · {r['b_model']}） 来源={r['sources_b']}")
    print(r["report_b_zh"], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
