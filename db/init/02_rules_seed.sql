-- ════════════════════════════════════════════════════════
-- 临床规则种子数据
-- 阈值均源自国际权威指南（约束 B 合规来源）
-- 注意：citation_id 需对应已摄取的 guideline_sources 记录方可关联
-- 此处为示例，实际使用前请先摄取对应指南
-- ════════════════════════════════════════════════════════

-- 先确保示例来源存在（实际应通过摄取管道写入）
INSERT INTO knowledge_base.guideline_sources (org, title, citation_id, version_date)
VALUES
    ('AHA/ACC', '2017 ACC/AHA/AAPA/ABC/ACPM/AGS/APhA/ASH/ASPC/NMA/PCNA Guideline for the Prevention, Detection, Evaluation, and Management of High Blood Pressure in Adults', 'AHA/ACC 2017 HTN', '2017-01-01'),
    ('ADA', 'Standards of Care in Diabetes 2026', 'ADA 2026', '2026-01-01')
ON CONFLICT (citation_id) DO NOTHING;

-- 糖尿病：空腹血糖阈值（WHO）
INSERT INTO rules.clinical_rules (name, metric, condition, conclusion, citation_id, severity)
VALUES
(
    'Fasting glucose - diabetes threshold',
    'fasting_glucose_mmol_l',
    '{"op": ">=", "value": 7.0}',
    'Fasting plasma glucose at or above the diabetes diagnostic threshold (7.0 mmol/L).',
    'ADA 2026',
    'high'
),
(
    'Fasting glucose - impaired (prediabetes range)',
    'fasting_glucose_mmol_l',
    '{"op": "between", "low": 6.1, "high": 6.9}',
    'Fasting plasma glucose in the impaired fasting glucose range (6.1-6.9 mmol/L).',
    'ADA 2026',
    'moderate'
),
-- 糖化血红蛋白（ADA）
(
    'HbA1c - diabetes threshold',
    'hba1c_percent',
    '{"op": ">=", "value": 6.5}',
    'HbA1c at or above the diabetes diagnostic threshold (6.5%).',
    'ADA 2026',
    'high'
),
(
    'HbA1c - prediabetes range',
    'hba1c_percent',
    '{"op": "between", "low": 5.7, "high": 6.4}',
    'HbA1c in the prediabetes range (5.7-6.4%).',
    'ADA 2026',
    'moderate'
),
-- 高血压（NICE）
(
    'Systolic BP - stage 1 hypertension',
    'systolic_bp_mmhg',
    '{"op": ">=", "value": 140}',
    'Clinic systolic blood pressure at or above 140 mmHg (stage 1 hypertension or higher).',
    'AHA/ACC 2017 HTN',
    'moderate'
),
(
    'Diastolic BP - stage 1 hypertension',
    'diastolic_bp_mmhg',
    '{"op": ">=", "value": 90}',
    'Clinic diastolic blood pressure at or above 90 mmHg (stage 1 hypertension or higher).',
    'AHA/ACC 2017 HTN',
    'moderate'
),
-- LDL 复合条件示例（任一成立即命中）
(
    'LDL cholesterol - elevated',
    'ldl_mmol_l',
    '{"op": ">", "value": 3.0}',
    'LDL cholesterol above 3.0 mmol/L; review cardiovascular risk per guideline.',
    'AHA/ACC 2017 HTN',
    'moderate'
)
ON CONFLICT DO NOTHING;
