"""
指南摄取命令行入口。

用法：
  python -m app.cli ingest \
      --pdf /data/who_diabetes_2023.pdf \
      --org "WHO" \
      --title "Diagnosis and Management of Type 2 Diabetes" \
      --citation "WHO 2023 DM" \
      --version 2023-01-01
"""
import argparse
import asyncio
import os
import sys

import psycopg

from . import ingest
from . import ollama_client as oc

PG_DSN = os.environ["PG_DSN"]


async def _run(args) -> None:
    async with await psycopg.AsyncConnection.connect(PG_DSN) as conn:
        try:
            summary = await ingest.ingest_guideline(
                conn=conn,
                embed_fn=oc.embed,
                pdf_path=args.pdf,
                org=args.org,
                title=args.title,
                citation_id=args.citation,
                version_date=args.version,
                scope=args.scope,
            )
        except ingest.SourceRejectedError as e:
            print(f"[拒绝] {e}", file=sys.stderr)
            sys.exit(2)
    print(f"[完成] 来源 {summary['citation_id']}，"
          f"切片 {summary['chunks_ingested']} 个"
          f"（跳过 {summary.get('chunks_skipped', 0)} 个），id={summary['source_id']}")


def main() -> None:
    p = argparse.ArgumentParser(description="国际指南摄取管道")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="摄取一份指南 PDF")
    ing.add_argument("--pdf", required=True, help="PDF 文件路径")
    ing.add_argument("--org", required=True, help="发布机构，如 WHO / NICE")
    ing.add_argument("--title", required=True, help="指南标题")
    ing.add_argument("--citation", required=True, help="引用标识，如 'NICE NG28'")
    ing.add_argument("--version", required=True, help="版本日期 YYYY-MM-DD")
    ing.add_argument("--scope", default="international",
                     choices=["international", "domestic"],
                     help="international=国际指南→knowledge_base(nomic)；"
                          "domestic=国内指南→domestic_kb(bge-m3，流程A，跳约束B校验)")

    args = p.parse_args()
    if args.cmd == "ingest":
        asyncio.run(_run(args))


if __name__ == "__main__":
    main()
