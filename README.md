# 虚拟医院 — 部署骨架

私人家族健康管理系统的本地全栈部署骨架。数据不出家门，全链路约束 A/B 合规。

> **范围边界声明（2026-06-23）**：本项目与宿主机上的 Dify 部署（`/home/raredeem/dify`，
> 含 docker-nginx / docker-api / docker-weaviate 等 `docker-*` 容器）**相关度为 0**。
> 二者仅共用同一台宿主机与端口空间，无任何代码、数据、模型或架构耦合。
> 本项目的全部组件以 `vh-*` 容器前缀标识；凡 `docker-*` 前缀容器均不属于本项目，
> 排障与对账时不予计入。

## 架构

```
中文健康数据
   │
   ▼  gemma4:31b（汉译英 + 术语表注入）
英文数据 ──┐
           ▼  nomic-embed（检索国际指南）
   指南上下文
           ▼  llama4:16x17b（循证推理 + 引用溯源；2026-06-24 由 meditron 改版）
   英文评估
           ▼  gemma4:31b（英译汉）
   中文报告（含来源引用）
```

## 合规边界

| 类别 | 约束 | 实现位置 |
|---|---|---|
| 模型（约束 A） | 仅非中国大陆机构开发 | gemma4(谷歌,翻译) / llama4·llama3.3(Meta,推理) / nomic(美,检索)。meditron 已退役 |
| 指南数据源（约束 B·流程B） | 仅国际权威机构 | `knowledge_base` schema + CHECK 约束 |
| 指南数据源（约束 B·流程A） | 分流程适用，允许国内指南 | `domestic_kb` schema（无 PRC CHECK，物理/向量空间隔离） |
| 会员原始数据 | 不受约束 B 限制 | `member_data` schema（物理隔离） |

> **约束 A 两条例外**（均限非评估或单流程，边界焊死，见 ARCHITECTURE §2）：
> ① `glm-ocr`（智谱）仅用于上传报告的非评估 OCR 预处理；
> ② `bge-m3`（智源）仅用于**流程 A2 国内指南检索**（中文召回远强于英文向 nomic）。
> 二者均严禁接入翻译或流程 B 任何环节。

注：原方案中的 `bge-m3`（北京智源）已替换为 `nomic-embed-text`，理由见对话记录——管道内 query 与 corpus 均为英文，多语言 embedding 能力未被使用，替换合规且检索精度不降。

## 部署步骤

```bash
# 1. 配置密钥
cp .env.example .env
# 编辑 .env，填入强密码

# 2. 启动服务栈
docker compose up -d

# 3. 拉取并构建模型（首次，耗时取决于网络）
chmod +x setup-models.sh
./setup-models.sh

# 4. 验证
curl http://127.0.0.1:8000/health
```

## 端口（均绑定本地回环，不对外暴露）

- `8000` — 编排 API
- `5432` — PostgreSQL
- `9000/9001` — MinIO API / 控制台
- `11434` — Ollama

外部访问（家属在外）通过 Tailscale 组网，不在本骨架内，另行配置。

## 双卡资源说明

`OLLAMA_MAX_LOADED_MODELS=2` 允许 31B 翻译模型与 70B 推理模型尽量共驻。显存不足时 Ollama 自动换载，单次评估增加约 20-60 秒加载延迟，家族级使用频率下可接受。

## 指南摄取管道

将国际权威指南 PDF 摄取进知识库。

```bash
# 1. 将待摄取 PDF 放入 ./guidelines 目录
mkdir -p guidelines
cp ~/who_diabetes_2023.pdf guidelines/

# 2. 执行摄取（按需任务，跑完即退出）
docker compose run --rm ingestion ingest \
    --pdf /data/who_diabetes_2023.pdf \
    --org "WHO" \
    --title "Diagnosis and Management of Type 2 Diabetes" \
    --citation "WHO 2023 DM" \
    --version 2023-01-01
```

管道流程：Docling 结构化解析 → 按章节/标题切片（超长章节带重叠二次切分）→ nomic 向量化 → 入 `knowledge_base`。

约束 B 双重防线：应用层正则黑名单（`ingest.py`）+ 数据库 CHECK 约束（`01_schema.sql`），中英文表述的大陆机构均被拦截。

版本管理：同一 `citation_id` 再次摄取时，旧版自动标记 `is_deprecated=true`，新版生效，历史可溯源。检索仅命中未废弃来源。

## 规则引擎（确定性双轨）

评估采用双轨融合：

轨道一·确定性——`extractor.py` 从英文数据抽取结构化指标（空腹血糖、HbA1c、血压等），`rules_engine.py` 对规则库中的 JSONB 条件做硬阈值判断。结果完全确定可复现。

轨道二·概率性——RAG 检索国际指南 + Meditron 循证推理。

两轨在 `reason()` 融合：规则命中作为"既定事实"前置注入推理上下文，Meditron 在此基础上做循证解释，不得推翻硬阈值结论。例如规则引擎判定"空腹血糖 7.4 ≥ 7.0 命中糖尿病阈值 [WHO 2019]"，Meditron 负责解释临床意义与后续建议，而非重新概率推断该数值是否异常。

规则 JSONB 条件格式支持单条件、区间（between）、复合（all/any）。种子规则见 `db/init/02_rules_seed.sql`，阈值均源自 WHO/ADA/NICE。

规则与指南版本联动：规则关联的 `citation_id` 对应来源被标记废弃时，该规则自动失效，与知识库版本管理一致。

## 前端（健康仪表盘）

`frontend/index.html` — 自包含单页应用（React + Tailwind，无构建步骤），浏览器直接打开即可运行。

布局：左侧成员切换栏 + 右侧个人档案详情。生产级 UI，配色采用低饱和医用青绿为主，危险等级才用克制赭红，避免告警色滥用。

双轨评估结果视觉区分：确定性规则命中用实心圆点 + 左侧色条标识（确定结论），RAG 循证解释用空心圆点标识（参考性）。英文推理过程可折叠展开供审计溯源。

开发预览：文件内 `USE_MOCK = true` 时用内置假数据跑通全流程，无需后端。接入真实后端时改为 `false`，并确认编排服务 CORS 允许的来源端口（默认含 3000/5500）与前端托管端口一致。

部署：作为静态文件由任意静态服务器托管（如 `python -m http.server 5500`），或后续接入 Caddy/Nginx。

## 问诊交互（流程 A1 前端）

v2 流程 A1 的交互式问诊，由 llama3.3:70b 接诊，前后端均已落地（2026-06-23）。

流程：登录 → 直达「选择科室」（心血管/内分泌/泌尿/神经）→ 进入对话，首条为过渡说明
（隐私/本地模型耗时/项目特点/即将进入诊室）→ AI 逐题问诊 → 完成生成症状包并归档。

交互特性：
- **选项卡**：AI 每个问题以可点选项卡呈现。单选即点即发；多选可勾多个后点「确定」。
  首问（现有症状）强制多选；选项数不设上限；末尾「＋ 更多」可就同一问题动态追加更多选项。
  始终保留自由文本输入兜底。
- **报告上传**：问诊中可上传化验/检查/影像/处方报告（PDF/JPEG/PNG，可多选文件）。
  后端 `POST /upload` → 存 MinIO(`health-reports`) → **glm-ocr** OCR 提取文字 →
  建 `member_data.health_records`。上传不打断当前待选问题（已勾选项也保留）。
- **病史侧栏**：对话右侧实时显示该成员的历史记录。
- **症状包归档**：问诊结束，症状包打时间戳存为 `health_records(record_type='symptom_package')`，
  侧栏即时可见。

后端端点：`/interview/start`（带 member_id）、`/interview/chat`、`/interview/more`、`/upload`。

> ⚠ 约束 A 红线：报告 OCR 用 glm-ocr（GLM=北京智谱，属约束 A 黑名单），**仅限此非评估的
> 本地 OCR 预处理**，严禁接入 A1/A2/B 推理或翻译/检索任何评估环节。详见 `orchestrator/app/ocr.py`。

## 双盲评估（流程 A2 + B 背靠背）

v2 的核心。同一份 A1 症状包（含主治清洗后的在档佐证），由两条**互不知晓对方结论**的轨道独立循证推理，
呈现层并排两份结论，判断权归人、系统不仲裁。

- **流程 A2**：`llama3.3:70b` 中文原生推理 + `domestic_kb` 国内指南 RAG（bge-m3 检索）。无翻译三明治。
- **流程 B**：`gemma4` 汉译英 → nomic 检索国际指南 → `llama4:16x17b` 循证推理 → `gemma4` 英译汉。
  （2026-06-24 经三臂评测把 reasoner 由 meditron 改为 llama4：质量略胜、与 A2 的 llama3.3 架构异质→真双盲；翻译仍用定制 gemma4。）
- **确定性规则**：两轨共享的既定事实层（硬阈值判断），单算一次，置于报告顶部。
- **双盲数据流**：A2 与 B 都只消费 A1 数据，B 的输入不含 A2 的任何输出。

端点：`POST /assess`（留空 `zh_data` 则自动取该成员最新症状包）。后端编排见 `pipeline.run_dual`；
前端 `AssessmentResult` 左 A 右 B 并排 + 声明一/二。串行多模型换载，单次评估数分钟至数十分钟属正常。

> 一致性**不做相似度自动判定**（ARCHITECTURE §11 质疑6：两段自然语言无法稳定量化相似度）——
> 只并排呈现、各标来源，是否共识由人判断。

## 身份认证与授权

认证由 Authentik（OIDC 身份提供方）负责，授权逻辑落在编排服务。

职责划分：Authentik 回答"你是谁"（签发 JWT）；编排服务的 `auth.py` 回答"你能看谁"——因为"能看谁"依赖本系统 `members` 表的关系，身份提供方无从知晓。

分级权限模型：
- `admin`（本人）：可查看、评估全部家庭成员档案
- `member`（其他成员）：仅可查看、评估自己的档案

实现：`members` 表新增 `oidc_sub`（绑定登录用户）和 `role` 字段。`/members` 端点按角色过滤返回，`/assess` 端点用 `authorize_member_access` 拦截越权。所有跨成员访问写入 `access_log` 审计表。

首次配置步骤：

```bash
# 1. 生成 Authentik 密钥，填入 .env
openssl rand -base64 60   # 填入 AUTHENTIK_SECRET_KEY

# 2. 启动栈后访问 Authentik 后台完成初始化
#    http://127.0.0.1:9000/if/flow/initial-setup/
# 3. 创建 OAuth2/OIDC Provider 与 Application：
#    - Provider 类型：OAuth2/OpenID
#    - Client type：Public（前端隐式流）或 Confidential
#    - Redirect URI：前端地址
#    - 记下 issuer / jwks URL，填入 .env 的 OIDC_* 变量
# 4. 在 members 表中将各成员的 oidc_sub 绑定到对应 Authentik 用户，
#    本人 role 设为 admin，其余设为 member
```

注：前端示例用隐式流（implicit flow）演示，生产环境建议改用授权码 + PKCE（如 oidc-client-ts 库），安全性更高。

## 备份与恢复

离线硬盘加密备份，覆盖三类资产：PostgreSQL（全部 schema + Authentik 库）、MinIO 对象文件、Ollama 自定义模型。基于 restic，全程加密。

详见 `backup/README.md`。核心流程：

```bash
# 备份（插入离线硬盘后）
sudo ./backup/backup.sh /mnt/backup-disk

# 恢复
sudo ./backup/restore.sh /mnt/backup-disk list
sudo ./backup/restore.sh /mnt/backup-disk restore <snapshot-id>
```

安全设计：脚本强制校验离线硬盘已挂载，未挂载拒绝运行（避免假备份）；保留最近 7 全量 + 4 周 + 6 月；每次备份抽样校验完整性。restic 仓库密码须与硬盘分开离线保管。

## 骨架完成度

> ⚠ 范围说明：以下"已实现"指 **v1 单流程评估系统**的骨架模块，
> 该系统已跑通、质量 working well。
> **v2 双盲交叉验证架构已于 2026-06-23 全栈落地**：流程 A1 问诊、A2 国内指南推理、
> A/B 背靠背双盲、呈现层并排均已实现（见「双盲评估」节、ARCHITECTURE.md §10.1）。
> 下表的 v1 单流程模块仍有效；双盲在其上叠加，剩余工作为真实病例验证与调参。

全部规划模块已实现（v1 范围）：

| 模块 | 状态 |
|---|---|
| 部署编排（Ollama/PG/MinIO/编排服务） | ✓ |
| 翻译三明治评估管道 | ✓ |
| 指南摄取管道（约束 B 双重防线） | ✓ |
| 规则引擎（确定性双轨） | ✓ |
| 前端健康仪表盘 | ✓ |
| 身份认证与分级授权（Authentik） | ✓ |
| 离线加密备份 | ✓ |

## 已知待优化点（生产化前）

- 数据库连接统一改为连接池（asyncpg pool / psycopg_pool），替换当前每请求新建连接
- 前端 OIDC 由隐式流改为授权码 + PKCE
- 文件上传完整链路（拖拽 + 格式校验 + MinIO 直传 + 触发 OCR/摄取）
- 指标抽取增加确定性正则/表格解析作为 LLM 抽取的双保险

## 重要声明

本系统为私人健康记录与评估辅助工具，输出不构成医疗诊断，不替代专业医疗。
