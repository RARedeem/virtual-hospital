-- ════════════════════════════════════════════════════════
-- 虚拟医院数据库初始化
-- 设计原则：会员原始数据 与 指南知识库 物理隔离（不同 schema）
-- 仅在应用推理层交汇，确保审计时可独立溯源
-- ════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ────────────────────────────────────────────────────────
-- Schema 1: member_data — 会员原始健康数据
-- 来源可含中国大陆实体医院（个人医疗事实，不受约束 B 限制）
-- ────────────────────────────────────────────────────────
CREATE SCHEMA member_data;

CREATE TABLE member_data.members (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    full_name   TEXT NOT NULL,
    birth_date  DATE,
    sex         TEXT,                  -- 男 / 女（循证推理按性别分层之基础）
    relation    TEXT,                  -- 本人 / 配偶 / 子女 / 父母
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE member_data.health_records (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    member_id    UUID REFERENCES member_data.members(id) ON DELETE CASCADE,
    record_type  TEXT NOT NULL,        -- prescription / lab_report / imaging / checkup
    source_org   TEXT,                 -- 出具机构（可为中国大陆医院）
    record_date  DATE,
    raw_file_key TEXT,                 -- MinIO 对象键
    extracted_zh TEXT,                 -- OCR/解析后的中文原文
    translated_en TEXT,               -- 汉译英中间结果（留存供审计）
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- 症状包与佐证文件的关联表（需求：编辑页面追加/删除文件）
CREATE TABLE member_data.package_evidence (
    package_id   UUID REFERENCES member_data.health_records(id) ON DELETE CASCADE,
    evidence_id  UUID REFERENCES member_data.health_records(id) ON DELETE CASCADE,
    curator_notes TEXT,                -- llama3.3 清洗摘要或患者备注
    created_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (package_id, evidence_id)
);

-- 上传文件待审核表：上传后先由llama3.3清洗，患者决定accept/reject后才入package_evidence
CREATE TABLE member_data.curation_review_pending (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    upload_id    UUID NOT NULL,         -- 上传到 health_records 的 id
    package_id   UUID REFERENCES member_data.health_records(id) ON DELETE CASCADE,
    curator_notes TEXT NOT NULL,        -- llama3.3 清洗摘要
    raw_extracted_zh TEXT,             -- 上传文件的原始OCR
    accepted     BOOLEAN DEFAULT NULL,  -- NULL=待审 TRUE=接受 FALSE=拒绝
    reviewed_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE member_data.assessments (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    member_id     UUID REFERENCES member_data.members(id) ON DELETE CASCADE,
    record_id     UUID REFERENCES member_data.health_records(id),
    findings_en   TEXT,                -- 英文推理中间结果（溯源留存）
    report_zh     TEXT,                -- 最终中文报告
    risk_level    TEXT,
    cited_sources JSONB,               -- 引用的指南标识列表
    reasoner_model TEXT,               -- 记录使用的推理模型版本
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- ────────────────────────────────────────────────────────
-- Schema 2: knowledge_base — 国际权威指南知识库
-- 白名单来源（受约束 B 管控），中国大陆机构一律排除
-- ────────────────────────────────────────────────────────
CREATE SCHEMA knowledge_base;

CREATE TABLE knowledge_base.guideline_sources (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org         TEXT NOT NULL,         -- WHO / NICE / ADA / AHA ...
    title       TEXT NOT NULL,
    citation_id TEXT UNIQUE NOT NULL,  -- 引用标识，如 'NICE NG28'
    version_date DATE,
    is_deprecated BOOLEAN DEFAULT false,  -- 旧版软删除标记
    ingested_at TIMESTAMPTZ DEFAULT now(),
    -- 约束 B 强制校验：来源机构不得为中国大陆机构
    CONSTRAINT no_prc_source CHECK (
        org NOT ILIKE '%卫健委%' AND
        org NOT ILIKE '%中华医学会%' AND
        org NOT ILIKE '%药监局%'
    )
);

CREATE TABLE knowledge_base.guideline_chunks (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id   UUID REFERENCES knowledge_base.guideline_sources(id) ON DELETE CASCADE,
    chunk_text  TEXT NOT NULL,         -- 英文指南片段
    section     TEXT,                  -- 所属章节
    embedding   vector(768),           -- nomic-embed-text v1.5 维度
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 向量检索索引（HNSW，余弦距离）
CREATE INDEX idx_chunks_embedding
    ON knowledge_base.guideline_chunks
    USING hnsw (embedding vector_cosine_ops);

-- ────────────────────────────────────────────────────────
-- Schema 3: rules — 确定性临床规则引擎库
-- ────────────────────────────────────────────────────────
CREATE SCHEMA rules;

CREATE TABLE rules.clinical_rules (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    metric      TEXT NOT NULL,         -- 如 'fasting_glucose_mmol_l'
    condition   JSONB NOT NULL,        -- 如 {"op": ">=", "value": 7.0}
    conclusion  TEXT NOT NULL,         -- 命中后的结论
    citation_id TEXT REFERENCES knowledge_base.guideline_sources(citation_id),
    severity    TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
