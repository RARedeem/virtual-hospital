"""
指南摄取管道。

流程：PDF → Docling 结构化切片 → nomic 向量化 → 入 knowledge_base

约束 B 强制校验：摄取前在应用层拦截中国大陆机构来源，
与数据库 CHECK 约束形成双重防线。
"""
import os
import re
import sys

from . import parser

# 应用层来源黑名单（约束 B）。数据库 CHECK 约束为第二道防线。
_PRC_PATTERNS = [
    r"卫健委", r"卫生健康委", r"中华医学会", r"药监局", r"疾控中心",
    r"\bNHC\b",                        # National Health Commission
    r"chinese medical association", r"\bCMA\b",
]
_PRC_REGEX = re.compile("|".join(_PRC_PATTERNS), re.IGNORECASE)

MODEL_EMBED = os.environ.get("MODEL_EMBED", "nomic-embed-text:v1.5")


class SourceRejectedError(Exception):
    """来源违反约束 B，拒绝摄取。"""


def validate_source(org: str, title: str) -> None:
    """约束 B 校验。命中黑名单即抛错，阻断摄取。"""
    combined = f"{org} {title}"
    if _PRC_REGEX.search(combined):
        raise SourceRejectedError(
            f"来源 '{org}' 疑似中国大陆机构，违反约束 B，拒绝摄取。"
        )


async def ingest_guideline(
    conn,
    embed_fn,                          # async (model, text) -> list[float]
    pdf_path: str,
    org: str,
    title: str,
    citation_id: str,
    version_date: str,
) -> dict:
    """
    摄取单份指南。

    返回摘要 dict（来源 id、chunk 数）。
    若 citation_id 已存在，旧版（来源 + 切片）在同一事务内被删除并由新版覆盖
    （schema 对 citation_id 唯一约束，不支持多版本并存）。
    """
    # 1. 约束 B 应用层校验
    validate_source(org, title)

    # 2. 解析 + 章节切片
    chunks = parser.parse_and_chunk(pdf_path)
    if not chunks:
        raise ValueError(f"未能从 {pdf_path} 提取任何内容")

    async with conn.cursor() as cur:
        # 3-4. 覆盖式重摄：citation_id 已存在则【复用现有 source 行】——
        #      不删除该行，避免触碰 rules.clinical_rules 对 citation_id 的外键，
        #      也绕开 citation_id 唯一约束。仅更新元数据并清空旧切片；不存在则新建。
        #      与下方向量化插入同处一个事务：中途失败整体回滚，旧数据不丢。
        await cur.execute(
            "SELECT id FROM knowledge_base.guideline_sources WHERE citation_id = %s",
            (citation_id,),
        )
        existing = await cur.fetchone()
        if existing:
            source_id = existing[0]
            await cur.execute(
                "UPDATE knowledge_base.guideline_sources "
                "SET org = %s, title = %s, version_date = %s, is_deprecated = false "
                "WHERE id = %s",
                (org, title, version_date, source_id),
            )
            await cur.execute(
                "DELETE FROM knowledge_base.guideline_chunks WHERE source_id = %s",
                (source_id,),
            )
        else:
            await cur.execute(
                """
                INSERT INTO knowledge_base.guideline_sources
                    (org, title, citation_id, version_date, is_deprecated)
                VALUES (%s, %s, %s, %s, false)
                RETURNING id
                """,
                (org, title, citation_id, version_date),
            )
            source_id = (await cur.fetchone())[0]

        # 5. 逐 chunk 向量化并入库。
        #    空白块跳过；单块嵌入失败（如个别异常切片让 nomic 报错）记数并跳过，
        #    不让一块拖垮整份大指南（ADA 2663 块尤需此鲁棒性）。
        inserted = 0
        skipped = 0
        for ch in chunks:
            # 清除 NUL(0x00)：PostgreSQL text 字段拒收，某些 PDF 经 Docling 会带入
            txt = (ch.text or "").replace("\x00", "").strip()
            section = (ch.section or "").replace("\x00", "")
            if not txt:
                skipped += 1
                continue
            try:
                vec = await embed_fn(MODEL_EMBED, txt)
            except Exception as e:
                print(f"[跳过] 切片嵌入失败 (section={section[:30]!r}): {e}", file=sys.stderr)
                skipped += 1
                continue
            if len(vec) != 768:
                print(f"[跳过] 切片向量维度异常 (section={section[:30]!r})", file=sys.stderr)
                skipped += 1
                continue
            await cur.execute(
                """
                INSERT INTO knowledge_base.guideline_chunks
                    (source_id, chunk_text, section, embedding)
                VALUES (%s, %s, %s, %s)
                """,
                (source_id, txt, section, str(vec)),
            )
            inserted += 1

        await conn.commit()

    return {
        "source_id": str(source_id),
        "citation_id": citation_id,
        "chunks_ingested": inserted,
        "chunks_skipped": skipped,
    }
