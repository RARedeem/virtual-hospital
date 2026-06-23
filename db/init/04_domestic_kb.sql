-- ════════════════════════════════════════════════════════
-- Schema 4: domestic_kb — 国内指南知识库（流程 A2 专用）
-- ════════════════════════════════════════════════════════
-- 约束 B 分流程适用（见 ARCHITECTURE.md §2）：
--   流程 B（meditron + 国际指南）锁死国际权威来源，写入 knowledge_base，
--     受 no_prc_source CHECK 双重防线管控。
--   流程 A（llama + 国内指南）允许引入大陆权威指南（卫健委/中华医学会等），
--     用于 A2 推理，制造证据层异质性。故本 schema 与 knowledge_base 物理隔离，
--     且【不带 no_prc_source CHECK】——这是设计决策，非疏漏。
--
-- 向量空间隔离：本库用 bge-m3（北京智源，约束 A 例外，仅限流程 A 国内指南检索），
--   维度 1024，与 knowledge_base 的 nomic-embed(768) 不交叉。例外边界见 ARCHITECTURE §2。
-- ════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS domestic_kb;

CREATE TABLE domestic_kb.guideline_sources_cn (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org          TEXT NOT NULL,         -- 中华医学会 / 国家卫健委 / 中国高血压联盟 ...
    title        TEXT NOT NULL,
    citation_id  TEXT UNIQUE NOT NULL,  -- 引用标识，如 '中国高血压防治指南2024'
    version_date DATE,
    is_deprecated BOOLEAN DEFAULT false,
    ingested_at  TIMESTAMPTZ DEFAULT now()
    -- 注意：刻意不设 no_prc_source CHECK（约束 B 分流程适用，流程 A 允许国内指南）
);

CREATE TABLE domestic_kb.guideline_chunks_cn (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id   UUID REFERENCES domestic_kb.guideline_sources_cn(id) ON DELETE CASCADE,
    chunk_text  TEXT NOT NULL,          -- 中文指南片段
    section     TEXT,                   -- 所属章节
    embedding   vector(1024),           -- bge-m3 维度（约束 A 例外，仅流程 A）
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 向量检索索引（HNSW，余弦距离）
CREATE INDEX idx_chunks_cn_embedding
    ON domestic_kb.guideline_chunks_cn
    USING hnsw (embedding vector_cosine_ops);

-- ════════════════════════════════════════════════════════
-- 双盲落库：扩展 member_data.assessments 存流程 A 轨结论
--   （流程 B 沿用现有 findings_en / report_zh / cited_sources / reasoner_model）
-- ════════════════════════════════════════════════════════
ALTER TABLE member_data.assessments
    ADD COLUMN IF NOT EXISTS flow_a_zh      TEXT,    -- 流程 A2（国内指南·llama）中文结论
    ADD COLUMN IF NOT EXISTS flow_a_sources JSONB,   -- A2 引用的国内指南标识列表
    ADD COLUMN IF NOT EXISTS flow_a_model   TEXT;    -- A2 使用的推理模型版本

