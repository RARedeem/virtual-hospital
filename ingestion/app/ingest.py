"""
指南摄取管道。

流程：PDF → Docling 结构化切片 → nomic 向量化 → 入 knowledge_base

约束 B 强制校验：摄取前在应用层拦截中国大陆机构来源，
与数据库 CHECK 约束形成双重防线。
"""
import os
import re

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
    若 citation_id 已存在且来源版本更新，旧版自动标记 deprecated。
    """
    # 1. 约束 B 应用层校验
    validate_source(org, title)

    # 2. 解析 + 章节切片
    chunks = parser.parse_and_chunk(pdf_path)
    if not chunks:
        raise ValueError(f"未能从 {pdf_path} 提取任何内容")

    async with conn.cursor() as cur:
        # 3. 旧版处理：同 citation_id 的现存来源标记废弃（保留历史可溯源）
        await cur.execute(
            "UPDATE knowledge_base.guideline_sources "
            "SET is_deprecated = true WHERE citation_id = %s AND is_deprecated = false",
            (citation_id,),
        )

        # 4. 插入新来源记录（数据库 CHECK 约束为第二道防线）
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

        # 5. 逐 chunk 向量化并入库
        for ch in chunks:
            vec = await embed_fn(MODEL_EMBED, ch.text)
            await cur.execute(
                """
                INSERT INTO knowledge_base.guideline_chunks
                    (source_id, chunk_text, section, embedding)
                VALUES (%s, %s, %s, %s)
                """,
                (source_id, ch.text, ch.section, str(vec)),
            )

        await conn.commit()

    return {
        "source_id": str(source_id),
        "citation_id": citation_id,
        "chunks_ingested": len(chunks),
    }
