"""
指南摄取管道。

流程：PDF → Docling 结构化切片 → 向量化 → 入知识库

两个 scope：
- international（流程 B，默认）：nomic 向量化 → knowledge_base，受约束 B 黑名单校验。
- domestic（流程 A）：bge-m3 向量化 → domestic_kb，【跳过约束 B 校验】。
  约束 B 分流程适用（ARCHITECTURE §2）：流程 A 允许国内指南；bge-m3 为约束 A 例外，
  仅限流程 A 国内指南检索。详见 db/init/04_domestic_kb.sql。
"""
import json
import os
import re
import sys

from . import parser

# 跨服务统一设置（repo 根 config/，docker-compose 挂载到 /config）。设置最大化、运行模块最小化。
_CONFIG = os.environ.get("CONFIG_DIR", "/config")


def _load_config(rel: str):
    with open(os.path.join(_CONFIG, rel), encoding="utf-8") as f:
        return json.load(f)


# 约束 B 来源黑名单 → 外挂 config/constraints/constraint_b_sources.json（DB CHECK 为第二道防线）
_PRC_REGEX = re.compile(
    "|".join(_load_config("constraints/constraint_b_sources.json")["prc_org_patterns"]),
    re.IGNORECASE)

# scope 配置（表名/向量模型/维度/约束B校验，含切片入库归类）→ 外挂 config/ingestion/scopes.json
# embed_model 同名 env 可覆盖（部署灵活）
_SCOPE_CFG = {}
for _scope, _cfg in _load_config("ingestion/scopes.json").items():
    if _scope.startswith("_"):
        continue
    _SCOPE_CFG[_scope] = {
        "sources_table": _cfg["sources_table"],
        "chunks_table": _cfg["chunks_table"],
        "embed_model": os.environ.get(_cfg.get("embed_env", "")) or _cfg["embed_model"],
        "embed_dim": _cfg["embed_dim"],
        "validate_prc": _cfg["validate_prc"],
    }


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
    scope: str = "international",
) -> dict:
    """
    摄取单份指南。

    scope=international（默认）：走约束 B 校验，nomic→knowledge_base。
    scope=domestic：跳过约束 B 校验，bge-m3→domestic_kb（流程 A）。

    返回摘要 dict（来源 id、chunk 数）。
    若 citation_id 已存在，复用现有 source 行（更新元数据 + 清空旧切片），
    避免触碰 rules 外键与唯一约束；同一事务内覆盖。
    """
    cfg = _SCOPE_CFG.get(scope)
    if cfg is None:
        raise ValueError(f"未知 scope: {scope!r}（应为 international / domestic）")
    sources_table = cfg["sources_table"]
    chunks_table = cfg["chunks_table"]
    embed_model = cfg["embed_model"]
    embed_dim = cfg["embed_dim"]

    # 1. 约束 B 应用层校验（仅 international scope；domestic 分流程豁免）
    if cfg["validate_prc"]:
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
            f"SELECT id FROM {sources_table} WHERE citation_id = %s",
            (citation_id,),
        )
        existing = await cur.fetchone()
        if existing:
            source_id = existing[0]
            await cur.execute(
                f"UPDATE {sources_table} "
                "SET org = %s, title = %s, version_date = %s, is_deprecated = false "
                "WHERE id = %s",
                (org, title, version_date, source_id),
            )
            await cur.execute(
                f"DELETE FROM {chunks_table} WHERE source_id = %s",
                (source_id,),
            )
        else:
            await cur.execute(
                f"""
                INSERT INTO {sources_table}
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
                vec = await embed_fn(embed_model, txt)
            except Exception as e:
                print(f"[跳过] 切片嵌入失败 (section={section[:30]!r}): {e}", file=sys.stderr)
                skipped += 1
                continue
            if len(vec) != embed_dim:
                print(f"[跳过] 切片向量维度异常 (期望 {embed_dim}, 实得 {len(vec)}, "
                      f"section={section[:30]!r})", file=sys.stderr)
                skipped += 1
                continue
            await cur.execute(
                f"""
                INSERT INTO {chunks_table}
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
