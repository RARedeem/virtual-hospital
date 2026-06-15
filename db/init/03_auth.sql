-- ════════════════════════════════════════════════════════
-- 认证授权 schema 扩展
-- Authentik 负责认证（你是谁），本表负责授权映射（你能看谁）
-- ════════════════════════════════════════════════════════

-- members 表增加身份映射字段
-- oidc_sub: Authentik 签发的 JWT 中 sub claim，唯一标识登录用户
-- role: admin（管理者，本人）/ member（普通成员，仅见自己）
ALTER TABLE member_data.members
    ADD COLUMN IF NOT EXISTS oidc_sub TEXT UNIQUE,
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'member'
        CHECK (role IN ('admin', 'member'));

-- 索引：登录后按 oidc_sub 快速定位成员记录
CREATE INDEX IF NOT EXISTS idx_members_oidc_sub
    ON member_data.members(oidc_sub);

-- 审计日志：记录谁在何时访问/评估了谁的档案
-- 分级权限下，管理者可访问他人档案，留痕供事后审计
CREATE TABLE IF NOT EXISTS member_data.access_log (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    actor_sub    TEXT NOT NULL,           -- 操作者的 oidc_sub
    actor_name   TEXT,
    action       TEXT NOT NULL,           -- view_records / run_assessment
    target_member UUID REFERENCES member_data.members(id),
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_access_log_actor
    ON member_data.access_log(actor_sub, created_at);
