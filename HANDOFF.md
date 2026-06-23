# HANDOFF — 交接说明（供 Claude Code 接手）

本文件是项目从对话式架构设计阶段移交到环境调试阶段的交接记录。
接手前请先通读本目录的 README.md 了解系统全貌。

> **状态更新（2026-06-23）**：本文件是 v1 环境调试阶段的**历史交接快照**。其下"当前状态 / 当前卡点 /
> 待完成三件事"多数已解决——4 份国际指南已摄取、成员A已入库、Authentik 已配置（认证闭环见
> HANDOFF-2-AUTH.md）、Docling 重摄取已修复、规则 citation_id 已对齐真实来源。**当前权威状态以
> TODO-NEXT.md「★ 权威状态摘要」为准**（含 2026-06-23 对账校准）。本文件保留作历史记录，下方术语已校正，
> 但具体卡点不再逐项维护。

## 系统定位

私人家族健康档案 + AI 循证健康评估系统，本地部署，数据不出家门。
对标三甲全科能力，但无医生角色、无诊疗行为、无商业用途，仅供本人及家属使用。

## 两条硬约束（必须严格遵守，不可违反）

- **约束 A**：全链路模型只能用非中国大陆机构开发的。当前用 gemma4(谷歌)、
  meditron(EPFL 瑞士)、nomic-embed(美)。禁止 Qwen/DeepSeek/GLM/BGE 等。
  例外：`glm-ocr`（GLM=北京智谱）仅用于**非评估链路**的本地 OCR 预处理（上传报告识字），
  不属"全链路评估模型"，严禁接入推理/翻译/检索任何评估环节。详见 TODO-NEXT I 节、README、
  `orchestrator/app/ocr.py`。
- **约束 B**：知识库指南来源只能是国际权威机构（WHO/ADA/AHA/NICE/EAU/MDS 等），
  禁止任何中国大陆机构（卫健委/中华医学会/药监局等）发布的指南。
  注意：会员上传的实体医院检验报告（含大陆医院出具的）属个人医疗事实，
  不受约束 B 限制，可正常入库。

应用层与数据库层均有约束 B 的双重防线（ingest.py 正则黑名单 + 01_schema.sql 的
CHECK 约束）。遇到与约束 A/B 冲突的情况必须停下来确认，不可自行放宽。

## 评估管道（翻译三明治）

```
中文数据
  →[gemma4 汉译英 + 术语表注入]
  →[nomic 向量检索国际指南]
  →[meditron 循证推理 + 引用溯源]
  →[gemma4 英译汉]
  →中文报告
```

另有确定性规则引擎作为双轨之一（orchestrator/app/rules_engine.py + extractor.py）。
规则命中作为既定事实注入推理上下文，meditron 不得推翻硬阈值结论。
英文中间结果（translated_en / findings_en）全程落库供审计。

## 当前状态

- 7 个容器已起：ollama / postgres / minio / orchestrator /
  authentik-server / authentik-worker / authentik-redis
- 模型已加载：gemma4:31b, meditron:70b, nomic-embed-text:v1.5,
  及三个自定义模型 translator-zh-en / translator-en-zh / reasoner-meditron
- **vh-authentik-server 状态是 unhealthy，待排查**
- 网络问题根因记录：容器默认走 IPv6 但本机 IPv6 不可达。已给 ollama 服务
  加 dns: [1.1.1.1, 8.8.8.8] 解决了模型拉取。**ingestion 服务尚未加 dns。**

## 当前卡点

ingestion 容器摄取指南时，Docling 要从 HuggingFace 下载 layout 模型，
报 `[Errno 101] Network is unreachable`。
- 原因一：ingestion 服务没配 dns（ollama 配了，ingestion 没配）。
  修法：在 docker-compose.yml 的 ingestion 服务下加 dns: [1.1.1.1, 8.8.8.8]。
- 原因二（很可能）：HuggingFace 在国内连通性差，即使 DNS 正常也可能超时。
  备选修法：给 ingestion 容器加环境变量 HF_ENDPOINT=https://hf-mirror.com
  使用国内镜像；或预先下载 Docling layout 模型挂载进容器离线使用。
  注意 hf-mirror.com 是镜像服务非模型开发方，不触发约束 A（约束 A 限制的是
  模型开发机构，gemma/meditron/nomic 本身合规，镜像源仅是下载通道）。

## 待完成的三件事（按序）

### 1. 摄取 4 份国际指南到知识库（符合约束 B）

```
guidelines/CARDIO/AHA_ACC_Hypertension_Guidelines.pdf   org=AHA/ACC
guidelines/METABOLISM/ADA_Standards_of_Care_2026.pdf    org=ADA
guidelines/NEURO/mds_parkinson_criteria.pdf             org=MDS
guidelines/UROLOGY/eau_urology_guidelines.pdf           org=EAU
```

命令格式（详见 README 的"指南摄取管道"节）：

```bash
docker compose run --rm ingestion ingest \
  --pdf /data/METABOLISM/ADA_Standards_of_Care_2026.pdf \
  --org "ADA" --title "ADA Standards of Care in Diabetes 2026" \
  --citation "ADA 2026" --version 2026-01-01
```

每条成功输出 `[完成] 来源 xxx，切片 N 个`。

### 2. 插入第一个家庭成员记录

成员信息（来自一份真实体检报告，属会员原始数据，不受约束 B 限制）：
- 姓名：成员A，男，（年龄/出生年已脱敏），role=admin
- 已知异常指标：糖化血红蛋白 6.9%、空腹血糖 6.1 mmol/L、血压 148/109 mmHg、
  甘油三酯 2.71 mmol/L、同型半胱氨酸 21.2 μmol/L、GGT 65 U/L、
  甲状腺结节 TI-RADS 3 类、双侧颈动脉斑块、轻度脂肪肝、
  既往胆囊切除 + 右肾囊肿术后

插入 member_data.members 表。oidc_sub 暂可留空，待配 Authentik 后绑定。

**重要：规则 citation_id 对齐**
db/init/02_rules_seed.sql 里的规则关联的 citation_id 是 WHO 2019 DM / ADA 2024 /
NICE NG136，与实际摄取的 AHA/ADA 2026/MDS/EAU 不完全匹配。需把规则的 citation_id
对齐到实际摄取的来源，否则确定性规则那一轨会命中为空。
例如血糖规则可对齐到 "ADA 2026"，血压规则对齐到 "AHA/ACC 2017 HTN"。

### 3. 配置 Authentik

先修 unhealthy（查 docker compose logs authentik-server），再创建 OIDC
application/provider，绑定成员的 oidc_sub。前端 frontend/index.html 把
USE_MOCK 改为 false 接真实后端，并确认 OIDC 配置中的 authority/clientId 与
Authentik 实际创建的一致。

## 验证目标

最终能跑通：录入成员A的体检数据 → 评估管道输出中文报告（含国际指南引用）
+ 规则引擎命中（如血糖、血压阈值）。

**建议先做一个绕过 Authentik 的内部测试验证管道**（直接调用 orchestrator
的 pipeline，或临时在 /assess 去掉 Depends(auth.get_principal)），确认翻译
三明治 + 双轨评估能跑通，再回头配认证。Authentik 是访问控制，不应成为验证
核心管道的前置阻塞。

## 已知待优化点（生产化前，非阻塞）

- 数据库连接统一改为连接池（asyncpg pool / psycopg_pool），替换当前每请求新建连接
- 前端 OIDC 由隐式流改为授权码 + PKCE
- 文件上传完整链路（拖拽 + 格式校验 + MinIO 直传 + 触发 OCR/摄取）
- 指标抽取增加确定性正则/表格解析作为 LLM 抽取的双保险

## 分工说明

环境调试（DNS、镜像、容器健康、摄取重试、Authentik 配置）由 Claude Code 在
本地直接执行。架构决策、约束判断、设计权衡仍可回到原对话讨论。
