# 后续清单 — 系统已跑通后的收尾与优化

---
## ★ 权威状态摘要（2026-06-16 校准，冲突以本节为准）★
本文档为增量追加，早期章节的乐观结论可能被后续章节推翻。
当任何记述冲突时，**以本摘要 + 时间最新章节为准**：

- **系统范围**：**v2 双盲交叉验证已全栈落地并端到端实测**（2026-06-24）。v1 单流程 = 流程 B 的底子。
  （早期章节"v2未实现"为旧状态，作废。详见下方 2026-06-24 阶段收尾 + ARCHITECTURE.md。）
- **B 轨 reasoner = llama4:16x17b**（2026-06-24 三臂评测裁定，由 meditron 改版；meditron 已退役）。
  翻译仍用定制 gemma4:31b（translator-zh-en/en-zh）。早期 gemma2:27b 为**旧值作废**。
- **G1（Meditron 结构化输出）**：历史问题，meditron 已退役，不再相关。
- **EAU 知识库切片数**：**474**（Docling 重摄取后）。早期 314 为 pdftotext 旧值，作废。
- **文档纪律**：今后重大状态变更，须回头改旧章节或更新本摘要，不可只追加。

### ▶ 2026-06-24 阶段收尾（v2 双盲精修 + reasoner 改版 + UI 重构）
本阶段（13 笔提交）成果，以此为当前权威实况：
- **reasoner 改版**：三臂对照评测（`eval/reasoner_ab.py`：meditron/llama3.3/llama4 同条件）→ meditron 幻觉/过诊出局，**B 轨改 llama4**（与 A2 的 llama3.3 架构异质 MoE vs dense = 真双盲）。翻译保留 gemma4（实测 llama3.3 翻译吐繁体+加戏）。见记忆 [[reasoner-meditron-vs-llama]] / [[rigid-pipeline-route]]（路线已改版）。
- **症状包结构化**：`run_dual` 最前加 `structure_symptom_package`(llama3.3)，乱麻→结构化转诊摘要再喂推理（解耦理解格式/医学推理）。⚠ 确定性规则改从**原始数据**抽取（结构化曾致命中 5→0，已修）。
- **国内库五科齐全**：`domestic_kb` = 高血压2024/糖尿病2020上下/帕金森第五版/BPH共识2025/慢性肾脏病2026（bge-m3 1024维，约束A例外）。
- **前端**：深色 IDE 主题；评估等待→左栏实时阶段进度+计时；结论逐段流式(规则→A→B淡入)；报告结构化(标签块)+A/B双色；症状包下拉文字框(≤1/3屏)；结论占满全宽；模型标签后端动态驱动。
- **合规上云**：全历史脱敏推送 GitHub 私有库 RARedeem/virtual-hospital（仅 master、PHI零残留、版权PDF退本地）。
- **拓扑**：SYSTEM-TOPOLOGY.html 按实况重绘。
- **本地安全网**：tag `backup-pre-scrub`(原始PHI历史) / `pre-reasoner-swap-20260624`(换llama4前)，仅本地、绝不推送。
- **待办**：同型半胱氨酸确定性规则仍缺（见 I 节）；llama4 单次 /assess ~14min（批处理可容忍）。

### ▶ 2026-06-23 对账校准（活动状态实测，优先级最高）
对真实容器/库做了一次"文档 vs 实况"对账，以下以**实测**为准，覆盖早期章节：

- **真实库命名（写脚本必看）**：DB 用户 `vhadmin`（非旧文档/sbx.txt 的 `yangrenming`），
  库 `virtual_hospital`。表名为 `knowledge_base.guideline_sources`、
  `knowledge_base.guideline_chunks`（外键 `source_id`，**无 citation_id 列**）、
  独立 schema `rules.clinical_rules`、`member_data.members`（成员姓名列是 `full_name`，非 `name`）。
  早期文档/sbx.txt 里的 `knowledge_base.sources`、`knowledge_base.rules`、`name` 均为**错误命名，作废**。
- **血脂指南已摄取**：`ACC/AHA 2026 Dyslipidemia`（org=ACC/AHA，**435 切片**，未 deprecated）已入库。
  下方 A3 的 `PENDING-LIPID` 占位**已不存在**，LDL 规则已对齐到真实来源。工作区两个血脂 PDF
  （md5 相同，同一文件两命名）是已用完的摄取源，可归档/删除。
- **甘油三酯规则已补**：`triglycerides_mmol_l → ACC/AHA 2026 Dyslipidemia` 已建（A3 末尾标的 gap 部分关闭）。
  **同型半胱氨酸(homocysteine) 仍无确定性规则** → 见下方「I. 待办·挂起」。

### ▶ 2026-06-23 双盲交叉验证全栈落地（v2 核心）
- **流程 A2 已建**：`pipeline.reason_a2`（llama3.3:70b 中文原生，无翻译三明治）+ 国内指南库
  `domestic_kb`（bge-m3 检索）。国内指南已摄取：高血压2024、糖尿病2020上/下。
- **双盲编排 `pipeline.run_dual`**：A2 与 B 各自独立、只吃 A1 症状包+在档佐证，**B 输入不含 A2 结论**；
  确定性规则两轨共享、单算一次。`/assess` 并排返回两份结论（留空 zh_data 自动取最新症状包——
  **关闭了"症状包尚未喂 A2/B"的 gap**）。前端左 A 右 B 并排 + 声明一/二。
- **约束 A 例外·bge-m3**：用户裁定为流程 A 破例（中文检索远强于 nomic）。边界焊死：仅流程 A2 国内指南检索，
  流程 B 仍 nomic、meditron 路线不动。详见 README/ARCHITECTURE §2、记忆 [[dual-blind-a2]]。
- **约束 B 分流程**：国内指南入 `domestic_kb`（无 no_prc_source CHECK），与 knowledge_base 物理+向量空间隔离。
- 一致性不做相似度自动判定（遵质疑6，判断权归人）。流程 B 刚性路线（gemma4→meditron→gemma4）未动。

### ⏸ I. 待办·挂起（2026-06-23 立项，暂不推进）
**同型半胱氨酸(homocysteine) 确定性规则缺失。** 成员A该项 21.2 μmol/L 异常，
目前仅 RAG 轨覆盖，命中不保证。补齐需两步（受约束 B 限制）：
1. 摄取一份含 homocysteine 阈值的**国际权威指南**来源（约束 B：仅 WHO/AHA/ESC/ADA 等国际机构，
   禁大陆机构）。候选方向：心血管/营养学领域含 Hcy 风险分层阈值的国际指南，需先调研确定具体来源。
2. 在 `rules.clinical_rules` 新建 `homocysteine_umol_l` 规则，`citation_id` 对齐第1步摄取的来源。
**状态：已挂起**，待"主治医生↔患者交互"主线完成后再回。
- **当前规则×来源全貌（8 条，全对齐真实来源，无幽灵 id）**：
  fasting_glucose/hba1c→ADA 2026；systolic/diastolic_bp→AHA/ACC 2017 HTN；
  ldl/triglycerides→ACC/AHA 2026 Dyslipidemia。
- **知识库切片实测**：ADA 2026=2663 / EAU 2026 LUTS=474 / ACC/AHA Dyslipidemia=435 /
  AHA/ACC 2017 HTN=341 / MDS=60。
- **文档溯源子系统未在本库落地**：commit 7d9ec78 称"5文件入库"，但实测 `doc_provenance` schema
  **0 张表**。INDEX.md 的"待 Claude Code 落地"在本库**仍未完成**，commit message 高估了状态。
- **v2 流程 A1（问诊）已部分落地**：`orchestrator/app/interviewer.py` 已存在并在 `main.py` 挂载
  `/interview/start`、`/interview/chat` 端点。ARCHITECTURE.md 10.1 仍标"待实现"，已同步更正。
- **约束 A 治理项（2026-06-23 已裁定：保留待用）**：vh-ollama 内存在 `glm-ocr`（GLM=北京智谱，
  属约束 A 黑名单机构）。全代码/配置 grep **无任何引用**，故运行管道未违反约束 A。
  用户裁定**保留待用**。⚠ 红线：约束 A 锁的是"全链路评估管道所用模型"——glm-ocr 仅可用于
  **非评估链路**（如本地 OCR 预处理上传文件），**严禁接入 A1/A2/B 推理或翻译/检索任何评估环节**，
  否则即违反约束 A。日后若 B4 文件上传 OCR 落地用到它，须在此明确记录其边界。
- **Dify 无关声明**：宿主机另有 Dify 栈（`docker-*` 容器），与本项目相关度为 0，见 README 范围边界声明。
---

# 后续清单（原始内容如下，部分早期结论已被上方摘要校准）

系统已完整跑通（认证→授权→双轨评估→中文报告，2026-06-14 验证；此处指 v1 单流程）。
以下均为非阻塞项，系统当前可正常使用。按优先级排列，有空时按图索骥即可。

---

## A. 收尾项（建议尽早，关系安全与整洁）

### A1. 关闭测试旁路 DEV_BYPASS_AUTH 【已完成 2026-06-20】
为本地测试管道时，.env 里加了 DEV_BYPASS_AUTH=1 绕过 JWT 校验。
认证现已完整跑通，应关闭它，否则 /assess 等端点存在无鉴权调用的风险。
```bash
# 编辑 .env，删除 DEV_BYPASS_AUTH（或设为 0）
docker compose up -d orchestrator
# 验证：未登录直接 curl，应返回 401
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/assess -X POST
```
预期返回 401。若仍 200，说明旁路没关干净。

**完成记录（2026-06-20）**：勘查发现 .env 里本就无 DEV_BYPASS_AUTH 行，
运行容器内该变量已为空（非"1"），旁路实际已关闭。验证 /assess、/members、
/me 未鉴权均返回 **401**，确认无残留旁路代码路径。
（回归项：携带有效 token 调 /me 返回 200 需浏览器登录后手动确认，未在本次 CLI 验证。）

### A2. 用独立账号替代 akadmin
当前用 Authentik 管理员账号 akadmin 登录并绑定到本人成员记录。
akadmin 应仅用于管理 Authentik 本身，不宜等同于一个家庭成员。
- 在 Authentik (localhost:9100) → Directory → Users 建一个代表本人的普通用户
- 取该用户的 sub（hashed_user_id，64 位 hex），重新绑定：
  UPDATE member_data.members SET oidc_sub='<新用户sub>'
  WHERE id='<MEMBER_UUID>';
- 注意：Authentik 默认 sub 不是 User.uuid，而是 token 里的 hashed 值，
  以浏览器登录后解码 token 的 sub claim 为准（见下方"已知坑"）。

### A3. 清理空的种子指南来源 【已完成 2026-06-20】
db/init/02_rules_seed.sql 预置了三条 citation_id（ADA 2024 / NICE NG136 /
WHO 2019 DM），但没有对应指南入库，切片数为 0，是"幽灵来源"。
规则目前关联这些 id 仍能命中（因为命中只看 metric 阈值），但长期是脏数据。
可选清理：把规则的 citation_id 对齐到实际摄取的来源
（血糖→ADA 2026，血压→AHA/ACC 2017 HTN），并删除三条空 source 记录。

**完成记录（2026-06-20，单事务改库 + 硬闸自检）**：
改库前已做全量离线备份（restic 快照 e17caf5b，check 10% 抽样 no errors）。
单事务对齐 + 清理：
- 血糖(fasting_glucose) + HbA1c 4 条规则 → **ADA 2026**（2663 切片真实来源）
- 收缩压 + 舒张压 2 条 → **AHA/ACC 2017 HTN**（341 切片真实来源）
- LDL 1 条 → **PENDING-LIPID**（诚实占位 source，org='PENDING'，0 切片，
  待日后摄取血脂指南 ESC/EAS 等再对齐；占位 source 保留使该规则不被级联失效）
- 删除三个幽灵 source：ADA 2024 / WHO 2019 DM / NICE NG136
验证（成员A HbA1c 6.9 / 空腹血糖 6.1 / 血压 148/109 / LDL 3.4）：确定性轨
命中 5 条，来源标注全为真实入库来源 + PENDING-LIPID 占位，无任何幽灵 id。

**⚠ 勘查发现的 gap（未在本次范围，待裁定）**：规则库无任何覆盖
**甘油三酯(triglycerides)** 与 **同型半胱氨酸(homocysteine)** 的确定性规则，
而成员A这两项异常（甘油三酯 2.71 / 同型半胱氨酸 21.2）。当前只能靠 RAG 轨
（Meditron + 检索）处理，命中不保证。如需纳入确定性轨，需补对症国际指南来源
+ 新建规则（注意约束 B）。

---

## B. 优化项（日后增强，不急）

### B1. 验证 pdftotext 的切片质量
摄取时为绕开 HuggingFace 连接问题，把 Docling 换成了 pdftotext。
pdftotext 只抽纯文本，丢失表格结构。指南里诊断阈值常以表格呈现，
可能被抽成错乱文字，影响 RAG 检索质量。
验证方法：检索一个已知指标（如糖尿病诊断阈值），人眼看检出的指南片段
是否完整通顺。若破碎，考虑给 Docling 配 HF_ENDPOINT=https://hf-mirror.com
国内镜像换回来。

### B2. 借鉴旧管道（~/ebm-ai-pipeline）的两处技巧
旧的已跑通管道有两处实现更稳健，可移植：
- few-shot 示例：Meditron 推理时给一个"输入→输出"样板，输出格式更稳定。
- NO_PROXY 设置：os.environ["NO_PROXY"]="localhost,127.0.0.1,11434"，
  确保调本地 Ollama 不走代理（本机有 VPN/代理，值得加这道防护）。
- 可改用 ollama 官方 Python 客户端 client.chat() 替代裸 httpx 调 /api/generate。

### B3. 数据库连接池
orchestrator 当前每个请求新建 DB 连接（auth.py、main.py、pipeline 均如此）。
家用负载可用，但生产化前应统一引入连接池（asyncpg pool 或 psycopg_pool）。

### B4. 前端文件上传链路【已立项】
当前前端只能查阅已入库记录、运行评估，没有完整的文件上传。
日后补：拖拽上传体检报告 PDF → MinIO 存储 → OCR/解析 → 入 member_data。
后端需新增上传端点（当前只有 /assess、/members、/me）。

**落地拆解：**

**后端（orchestrator）**

1. `POST /upload`（新端点）
   - 接收 multipart/form-data（字段：`file` + `member_id`）
   - 鉴权：`get_principal()` 从 JWT 提取 sub，校验调用方有权操作该 member
   - 格式校验：仅允许 `application/pdf`、`image/jpeg`、`image/png`；文件大小上限 20MB
   - 存储：写入 MinIO bucket `health-reports`，对象路径 `{member_id}/{timestamp}_{filename}`
   - 触发解析：上传成功后异步调用 OCR/解析管道，结果写入 `member_data.health_records`
   - 返回：`{ "object_key": "...", "status": "queued" }`

2. MinIO（已部署，见 docker-compose.yml `vh-minio`）
   - bucket `health-reports` 尚未创建 → 首次部署时 `mc mb` 初始化，可加进 start.sh 自检步骤
   - MinIO 端点：`vh-minio:9000`（容器内网）/ 控制台 `localhost:9001`

3. OCR 解析管道（异步 worker）
   - 从 MinIO 取文件 → Docling 解析提取结构化文本
   - 提取 record_type / 日期 / 来源机构 / 数值字段
   - 写入 `member_data.health_records`（record_type 按内容判断：'imaging'/'lab'）
   - 复用 ingestion 镜像中的 Docling 依赖，无需新镜像

**前端（frontend/index.html）**

4. 上传入口：在 `MemberDetail` 的 `profile` 视图下方加"上传体检报告"区域
   - 支持点击选择文件（`<input type="file" accept=".pdf,.jpg,.jpeg,.png">`）
   - 支持拖拽（onDragOver / onDrop 事件）
   - 选中后显示文件名 + 大小预览，点"确认上传"发送

5. 上传请求：`POST /upload` with `FormData`，headers 加 `...(await authHeaders())`
   （不设 Content-Type，浏览器自动补 multipart boundary）

6. 上传状态：进行中显示 spinner；成功提示"上传成功，报告处理中"；
   失败显示具体错误（格式不支持 / 超 20MB / 网络错误）

**约束**

- 鉴权必须走 `get_principal()` + OIDC JWT，不得依赖 DEV_BYPASS_AUTH；member 只能传自己档案
- 格式约束：服务端 MIME 二次校验，不信客户端传的 Content-Type
- MinIO bucket 需初始化（start.sh 自检或首次手动 `mc mb`）
- 会员上传的实体医院报告（含大陆医院）属个人医疗事实，不受约束 B 限制；文件落 MinIO 不出本机

**优先级说明**

前置依赖：F5 科室选择 / F6 问诊流程验证通过后再推进 B4；
B4 不阻塞任何当前已上线功能，属增量扩展。

### B5. OIDC 改授权码+PKCE 已完成
（记录：前端最初用隐式流，Authentik 2024.10 不支持，已改授权码+PKCE。
此项已做，无需再动，仅作存档。）

---

## 已知坑备忘（重启或排障时回看）

1. **容器走 IPv6 失败**：本机 IPv6 不可达，容器默认优先 IPv6 导致拉取/连接失败。
   已给 ollama、ingestion 服务加 dns: [1.1.1.1, 8.8.8.8] 解决。

2. **OIDC sub 不是 UUID**：Authentik 默认 sub 是 hashed_user_id（64 位 hex），
   不是 User.uuid。绑定 oidc_sub 必须用 token 里的真实 sub。
   解码方法（浏览器控制台）：
   Object.keys(sessionStorage).forEach(k=>{try{let p=JSON.parse(atob(sessionStorage[k].split('.')[1]));if(p.sub)console.log(k,p.sub)}catch(e){}})
   本人当前绑定的 sub: d26bcf182626eb336e16d30609988a2358170b2efe6119487412a2c12ced8961

3. **OIDC 内外两套地址**：浏览器侧用 localhost:9100，容器内验签用
   authentik-server:9000。OIDC_ISSUER 用外网地址（匹配 token 的 iss claim），
   OIDC_JWKS_URL 用内网地址（容器拉公钥）。二者独立，不可混用。

4. **Authentik 端口**：与 MinIO 抢 9000，已改为宿主机 9100:9000。

5. **GPU 透传**：系统重启后容器可能丢 GPU，报
   "invalid value for main_gpu: 0 (available devices: 0)"。
   排查：宿主机 nvidia-smi 看驱动，docker exec vh-ollama nvidia-smi 看容器。
   修复：确认 NVIDIA Container Toolkit 就绪，必要时重建 ollama 容器挂回 GPU。
   重启后启动顺序见 README。

6. **重启后启动**：
   cd ~/virtual-hospital && docker compose up -d
   等约 40s 待 healthy，再 cd frontend && python3 -m http.server 5500
   浏览器 localhost:5500（首次或换账号用隐身窗口避免旧 token 缓存）。

---

## 当前已确认可用的关键参数

- 成员：成员A（本人，role=admin），member_id=<MEMBER_UUID>
- OIDC Client ID: FqESExIdzWZZgkwh55IHiABpaSmasxfjAq1ztBVB
- 知识库：ADA 2026(2663切片) / AHA-ACC 2017 HTN(341) / EAU 2026 LUTS(474, Docling重摄取后) / MDS(60)
- 模型：gemma4:31b（翻译，2026-06-14升级）, meditron:70b, nomic-embed-text:v1.5,
  + translator-zh-en, translator-en-zh, reasoner-meditron
- 访问：前端 localhost:5500 / API localhost:8000 / Authentik localhost:9100 / MinIO localhost:9001

---

## C. 实测发现与优先改进项（2026-06-14 用北医三院泌尿超声实测）

### C0. 实测结论
用成员A本人 2026-05-25 北医三院前列腺超声（前列腺增生，体积40.8ml）测试：
- ✓ RAG 对症检索成功：引用来源全部为 EAU 2026 LUTS，未串味到糖尿病/高血压指南。
  证明向量检索能按病种精准匹配对应国际专科指南。
- ✗ Meditron 输出跑偏：报告讲的是指南里有什么（膀胱壁厚度、膀胱出口梗阻），
  而非紧扣患者实际的"前列腺增生40.8ml"。还出现占位符"[数值]毫米"
  （患者输入根本没有膀胱壁厚度，模型顺着检索到的指南段落硬编了一段）。
根因：reason() 的 prompt 只要求"用指南做循证评估"，未约束模型
"只解读患者实际拥有的数据、不得引入患者数据中不存在的指标"。

### C1.（高优先）给 Meditron 加 few-shot + 紧扣数据约束
这是当前最该先做的改进，直接压制"编造患者没有的指标"问题。
借鉴旧管道 ~/ebm-ai-pipeline 的 few-shot 写法。

具体改 orchestrator/app/pipeline.py 的 reason() 函数，prompt 部分：

1. 在 prompt 开头加强约束规则（替换/补强当前那句 "Provide an evidence-based assessment..."）：
```
CRITICAL RULES:
- Assess ONLY findings explicitly present in PATIENT DATA below.
- Do NOT introduce, infer, or fabricate any measurement not given
  (e.g. if bladder wall thickness is not in PATIENT DATA, never mention it).
- NEVER output placeholders like [value], [数值], [X]. If a value is absent, omit that topic.
- For each patient finding, state: the finding, then what the cited guideline says about it.
- Cite only the provided guideline sources.
```

2. 加一个 few-shot 样板（在真实 PATIENT DATA 之前，给一组输入→输出示范）。
   样板要示范"只讲患者有的、不编没有的"。例如：
```
EXAMPLE
GUIDELINE CONTEXT: [EAU LUTS] Prostate volume >30 mL indicates benign enlargement...
PATIENT DATA: Prostate volume 40.8 mL, benign prostatic hyperplasia.
ASSESSMENT:
- FINDING: Prostate volume 40.8 mL with benign prostatic hyperplasia.
- GUIDELINE: Per EAU LUTS, a prostate volume above 30 mL is consistent with
  benign prostatic enlargement; 40.8 mL falls in this range.
- NOTE: No bladder or flow data provided; further urodynamic assessment
  would require additional tests not in this record.
END EXAMPLE
```
   注意样板里主动示范了"没有的数据就明说没有"，而不是硬编。

3. 实现方式可参考旧管道：用 system + user(shot) + assistant(shot) + user(actual)
   的多轮消息结构（ollama client.chat），比单 prompt 更能稳定输出格式。
   若保持当前 httpx /api/generate 单 prompt 方式，则把 few-shot 写进同一段 prompt。

验证：改完用同一份前列腺超声重测，检查输出是否：
不再出现 [数值] 占位符；紧扣 40.8ml 做解读；不再凭空讲膀胱壁厚度。

### C2.（中优先）验证 EAU 指南切片质量（关联 B1）
Meditron 抓到"膀胱壁厚度"泛段而非"前列腺体积分级"精准段，
部分原因可能是 pdftotext 把 EAU 指南的分级标准表格抽乱了。
验证：直接查 knowledge_base.guideline_chunks 里 EAU 来源、含 "prostate volume"
的切片，人眼看是否完整通顺。若破碎，按 B1 换 Docling + hf-mirror 重新摄取 EAU。

### C3.（按需）扩展知识库覆盖
本次三份报告还涉及肾囊肿、肝囊肿，库里无对症指南（EAU LUTS 只覆盖下尿路）。
日后可补：KDIGO（肾脏病）等国际指南，覆盖肾囊肿/肾功能场景。
摄取命令见 README，注意仍须符合约束 B（国际机构）。

### C4.（数据卫生）本次插入的测试记录
为测试插入了一条 health_records（2026-05-25 前列腺超声）。
这是真实数据可保留。若日后要把三份报告完整入档，另两份（腹部超声、
泌尿系超声）也应作为独立 health_records 插入，record_type='imaging'，
source_org='北京大学第三医院'。注意：右肾中上极"异常回声，边界不清"
是报告医生用问号标注的发现，属个人医疗事实可入档，但 AI 评估仅供参考，
该发现应以泌尿外科医生当面解读为准。

---

## D. 模型升级路径（2026-06 调研，基于当前真实信息）

> 说明：Gemma 4 于 2026-04-02 由 Google DeepMind 发布，晚于本助手 2026-01 知识
> 截止，故初版 TODO 未涵盖。经实时检索确认以下信息真实有效。全部为 Google 模型，
> 符合约束 A（非中国大陆机构）。

### D1.（可选升级）翻译模型 gemma2:27b → gemma4:31b
- Gemma 4 发布 2026-04-02，Apache 2.0 许可（比 Gemma2/3 自定义许可更宽松）。
- 尺寸：E2B、E4B、26B(MoE,3.8B激活)、31B Dense。31B 是稠密旗舰版。
- 能力：256K 上下文、原生多模态(文/图/音)、140+ 语言、强逻辑与 agentic。
  对中英医学翻译这一环，多语言能力较 gemma2:27b 预期有明显提升。
- 安装：ollama pull gemma4:31b （需 Ollama 版本支持，先确认 ollama 已升级到位）。
- 约束 A：Google 开发，合规。
- 升级注意事项（重要）：
  1. 自定义翻译模型 translator-zh-en / translator-en-zh 当前基于 gemma2:27b 构建，
     换基座必须重建这两个模型：改 Modelfile 的 FROM 行为 gemma4:31b，重新 ollama create。
  2. 31B Dense 显存占用比 27B 略高，换前确认双卡显存够（与 meditron:70b 并存时尤其注意）。
  3. 升级后必须重测翻译质量：中文体检数据→英文，术语准确性对比 gemma2 版本。
  4. 若显存吃紧，可考虑 26B MoE 版本（激活参数仅约 3.8B，显存/算力成本更低，
     质量接近）——但 MoE 与稠密模型行为不同，需实测。

### D2.（更对口）专用翻译变体 TranslateGemma
- Google 随 Gemma 4 发布了 TranslateGemma，专做翻译，有 4B / 12B / 27B 尺寸。
- 对"中英医学翻译"这个具体环节，可能比通用 gemma 更对口、更省显存。
- 建议：D1 与 D2 二选一或对比测试。若翻译是瓶颈，优先试 TranslateGemma:27b
  作为 translator 基座，可能用更小体积拿到更好翻译质量。
- 约束 A：Google 开发，合规。

### D3.（推理模型备选，谨慎评估）MedGemma
- Google 发布了 MedGemma（医疗专用 Gemma），及 MedGemma 1.5（4B）。
- 理论上比通用 meditron 更贴合医疗，但：
  - 当前推理用 meditron:70b（EPFL 瑞士），已符合约束 A 且体量大、能力强。
  - MedGemma 现有尺寸偏小（4B/27B），70B meditron 在循证推理深度上可能仍占优。
  - 不建议贸然替换 meditron。可作为"轻量备选"了解，或日后做 A/B 对比。
- 约束 A：Google 开发，合规。

### 升级优先级判断
- 当前不急。先完成 C 节（EAU 检索 REFERENCES 过滤 + few-shot），
  那是评估不对症的真正根因，与翻译模型无关。
- C 节解决后，若仍觉翻译质量是短板，再按 D1/D2 升级翻译模型。
- 任何模型变更后，都要重跑前列腺(40.8ml)标准用例回归验证，确认无退化。

---

## C 节补录：评估质量问题最终解决状态（2026-06-14）

### 根因排查全链路（存档）
从"评估不对症"到最终修复，共穿越四层障碍：

| 层次 | 根因 | 修复 |
|------|------|------|
| 第1层 | pdftotext 抽取 EAU 指南，章节全归 Preamble、文本乱空格 | 换回 Docling，正确识别章节结构 |
| 第2层 | Docling v2 标题类型是 SectionHeaderItem 而非 HeadingItem | parser.py 修正 API 调用 |
| 第3层 | Docling 把引用列表里的 URL 误识别为章节标题 | parser.py 过滤 http 开头的假章节 |
| 第4层 | "7. REFERENCES" 文献条目词频高，向量相似度反超临床正文，检索 top-5 全被占满 | pipeline.py retrieve_guidelines WHERE 加正则过滤非正文章节 |

### 第4层修复后的验证结果（2026-06-14 通过）
- 检索章节：`4.6.1 前列腺特异性抗原和前列腺体积预测`、`4.13.1 前列腺构型/膀胱内前列腺突出`
  → 真实临床正文，不再是文献目录
- 内容对症：指南直接提及前列腺体积 30mL/40mL/50mL 阈值，PSA 预测标准，膀胱内突出分级
  → 与患者数据（40.8ml）直接挂钩
- 不再出现：编造指标（占位符）、泛泛复述、反问用户、全是 REFERENCES 条目
- 系统状态：**评估可用**。从"broken"到"working"的跨越已完成。

### 仍存在的两个打磨点（good → better，不阻塞使用）

【⚠ 此结论后被推翻又最终解决，完整时间线见下方 H 节】
【2026-06-14 当时判断】打磨点 1：Meditron 结论扣患者不够紧（C1）
- 现象：能引用对症指南段落，但缺少"所以您的40.8ml意味着……，建议……"的临门一脚。
  倾向于陈述指南内容，而非将指南与患者数据结合后下结论。
- 根因：reason() prompt 未约束"先引指南、再扣患者下结论"的输出结构。
- 修复：当时以为 few-shot 三段结构生效。**但此结论后被证伪**——见 F/G/H 节：
  few-shot 复杂 prompt 反而导致 Meditron 漂移，最终用「简化 prompt」（H 节）才真正解决。
  **G1 终态：已解决（2026-06-15，方向B简化prompt），few-shot 路线被弃用。**

**打磨点 2：检索结果有重复 chunk（小）**
- 现象：同一段指南内容（如 4.6.1）在输出里出现两次。
- 根因：top-k 检索命中了重复或高度相似的 chunk，未去重。
- 修复：在 retrieve_guidelines 结果里按 chunk_text 或 section 去重，
  或降低 top_k 同时加多样性筛选。优先级：低。

### 下一步建议
1. C1 few-shot（让 Meditron 结论更扣患者）——最能提升报告可读性
2. D1/D2 翻译模型升级（gemma4:31b 或 TranslateGemma）——提升翻译质量
3. 前端加自由文本输入框——方便快速测各种输入
以上三件均已在 C1/D 节有详细方案，按优先级依序推进即可。

---

## E. 工具分工原则（2026-06-14 确立）

**Claude Code（本地 llama4:16x17b 驱动）**
- 负责：执行、调试、改文件、跑命令、迭代排错
- 不消耗 Anthropic 云端额度
- 能力边界：够用于代码执行和文件操作，复杂推理场景可能有限

**Claude（Anthropic 云端）**
- 负责：架构判断、约束裁定（A/B）、根因分析、设计决策、文档
- 额度专用于判断和决策，不与 Claude Code 共享
- 原则：Claude Code 不消耗 Claude 的流量资源

**操作方式**
- Claude Code 启动：ollama launch claude --model llama4（本地离线）
- 复杂问题 → 这个对话（Claude）
- 执行任务 → Claude Code（本地）

---

## F. 当日收尾状态（2026-06-14 晚）

### 今日已完成且验证通过
| 组件 | 状态 |
|------|------|
| Docling 摄取 + SectionHeaderItem + URL-section 过滤 | ✓ |
| 知识库 474 个 EAU 临床 chunks，0 Preamble | ✓ |
| pipeline section filter（排除 REFERENCES 等非正文章节） | ✓ |
| 翻译模型升级 gemma2:27b → gemma4:31b（两个 translator 重建） | ✓ |
| 中文报告输出恢复（gemma4 翻译质量明显优于 gemma2） | ✓ |
| few-shot 示例数据隔离（不再污染真实患者数据） | ✓ |
| ollama_client 空响应重试 + 异常（解决模型冷启动返回空） | ✓ |
| Claude Code 改用本地 llama4 驱动（下载中，接通后不耗云端额度） | 进行中 |

### 唯一未解决：G1 — Meditron 结构化输出不稳定（高优先，留待下次）

**现象**：Meditron 70B 在结构化 prompt 下输出漂移，在四种失败间跳：
空响应 / 复述指南方法论 / 回显 prompt / 三段内容雷同。
最近一次输出了 EAU "1.4 Evidence strength" 证据分级方法论原文，
完全没碰患者的 40.8ml / IPSS 12。

**两种根因假说**：
- Claude Code 判断：gemma2/meditron 热切换的稳定性问题。
- 本对话（Claude）判断：更可能是 Meditron 70B 本身不擅长遵循复杂结构化
  指令——prompt 越复杂（few-shot+三段+多条规则）越漂移，是"指令过载"。
  Meditron 是医学文献微调模型，强于消化文本，弱于听从格式指令。

**下次优先试（方向 B，最简，验证根因）**：
大幅简化给 Meditron 的 prompt——去掉 few-shot、去掉三段结构、去掉多条
CRITICAL RULES，只留一句类似：
"Based on the guideline context below, assess the patient's findings.
 Cite specific thresholds or recommendations from the guideline.
 Be concise and specific to this patient."
若简化后反而稳定对症 → 证实"指令过载"，问题解决。

**若方向 B 不够（方向 A，治本，架构调整）**：
推理拆两步——
1. Meditron 只做"消化指南+患者数据→输出自由文本分析"（不要求格式）
2. gemma4:31b 把该分析整理成 FINDING/GUIDELINE/CONCLUSION 三段中文
让擅长指令遵循的 gemma4 管格式，让 Meditron 管医学理解，解耦"理解"与"格式化"。

**其他可试**：top_k 3 降噪、温度调整、换 reasoner-meditron 自定义模型的 system prompt。

### 注意：这是 good→better 的最后一块
系统当前可用：认证、授权、双轨评估、检索对症（指南背景已能命中
EAU 临床章节）、中文输出均正常。G1 是让 Meditron 的"结论段"更规整，
不影响系统基本可用性。不必当晚救火，静心单独处理效果更好。

---

## G. 显存管理教训 + G1 再确认（2026-06-14 最末）

### 【重要教训】OLLAMA_KEEP_ALIVE 必须为 0（多模型串行管道）
**事故**：docker-compose.yml 第 19 行原为 OLLAMA_KEEP_ALIVE=24h（骨架默认值，
意为模型常驻显存 24 小时减少换载延迟）。这在单模型场景是优化，但本系统是
多模型串行管道（translator-gemma4 19GB → meditron 38GB → translator 19GB），
模型用完不释放会累积占满显存，导致后续模型加载失败 → /assess 连续返回 500。

**根因**：用户早已交代 keep_alive=0（用完即释放），但该要求未落实到配置文件，
24h 默认值一直留着，直到显存累积撑爆才暴露。

**修复**：OLLAMA_KEEP_ALIVE=24h → 0，重启 ollama。500 立即消失，评估恢复。

**机制教训（写给未来）**：关键运行参数（keep_alive、超时、显存相关）交代后
必须当场核实落地到具体文件第几行，不能假设默认值已改。这类"交代了但没落实、
默认值没复核"的缝隙，是单点疏漏的高发区——正是 HANDOFF/TODO 这类文件化
机制要兜住的。下次设此类参数，加一句"已写入 X 文件第 N 行"确认。

**权衡说明**：keep_alive=0 代价是每次评估模型需重新换载（gemma4↔meditron
切换有加载延迟），单次评估变慢。但这是用速度换显存安全，避免 OOM，
家用频率完全可接受。这是设计取舍，非缺陷。

### 【显存红线】llama4:16x17b 暂不加载驱动 Claude Code
用户决定：在 llama4(67GB) 与评估管道的显存关系彻底论证清楚前，
不贸然加载 llama4 驱动 Claude Code。理由充分——任何常驻显存的大模型都
可能挤爆评估管道（keep_alive 事故已证明显存累积的危害）。
先把 virtual-hospital 显存账算明白，再谈 llama4 离线驱动。
Claude Code 暂继续用云端，但仍遵守"不与 Claude 抢额度"原则（见 E 节）。

### G1 再确认：Meditron 结构化输出问题（根因已明）
keep_alive 修复后显存稳定，但 Meditron 的指令遵循问题独立浮现：
最新一次评估输出了 prompt 回显（"请按照上述结构提供评估……"被原样吐出）
+ translator 口头禅泄漏（"如果您有其他需要翻译的文本"）。

**这证实了本对话的判断（而非热切换说）**：Meditron 70B 本身扛不住复杂
结构化 prompt——显存已稳定，漂移依旧，说明根因在模型指令遵循能力，
不在环境稳定性。

**下次第一件事（方向 B，最简验证）**：大幅简化给 Meditron 的 prompt，
去掉 few-shot、三段结构、多条 CRITICAL RULES，只留一句简单指令。
若简化后反而对症 → 根因坐实，问题解。详见 F 节 G1。

### 今日最终状态
系统可用且显存稳定：认证授权、双轨评估、检索对症（命中 EAU 临床章节）、
中文输出（gemma4:31b）、500 已消除。唯一剩 G1（Meditron 结论段规整），
方向明确、不影响基本可用。今日收尾。

---

## H. G1 已解决 + Claude Code 运行纪律落地（2026-06-14 深夜）

### 【已解决】G1：Meditron 结构化输出 — "指令过载"假说证实
**方向 B（简化 prompt）一步到位。**

修改：reason() 的 prompt 从复杂（few-shot + CRITICAL RULES + 三段结构 +
隔离标记）简化为一句话：
"Based on the guideline context below, assess this patient's findings.
 Cite specific thresholds or recommendations from the guideline.
 Be concise and specific to this patient's actual data.
 Do not mention any findings not present in the patient data."
（并删除了 139-141 行旧 prompt 残留）

**验证结果（前列腺超声 40.8ml 实测，通过）**：
- FINDING：紧扣 40.8ml / 3.5x2.6cm / 无膀胱突出 / 无钙化，无编造
- GUIDELINE：精准引用 EAU 三个章节 [4.10.2.a 前列腺大小与治疗选择]
  [4.4.1 TRUS 比指检更准，尤其 >30mL] [4.13.1 IPP I级 0-4.9mm]
- CONCLUSION：40.8ml(>30ml) → 手术/药物(5-ARIs)参考 + IPP 非高等级
  （之前一直缺的"临门一脚"出现了）
- 三段内容各不相同，不再漂移

**根因定论**：Meditron 70B 是医学微调模型，capacity 应用于医学推理，
不擅长遵循复杂格式指令。prompt 越复杂越崩（空响应/回显/复述/雷同），
只告诉它"做什么"、不规定"怎么格式化"，它反而自组织出干净结构。
**教训：对专业微调模型，少即是多。指令做减法，不做加法。**

备份：pipeline.py.bak（复杂版）保留，供日后对比。

### 【已落地】Claude Code 运行纪律
新增 claude-code.sh 智能启动器：
- 启动时读 nvidia-smi 检测 GPU 显存占用
- 占用 >5000MB（评估管道在跑）→ 自动回退云端 claude（不争显存）
- GPU 空闲 → 用本地模型（默认可配，零云端消耗）
- 实测验证：评估运行期间启动 ./claude-code.sh → 自动切云端 →
  评估不受影响。生产优先、Code 谦让的纪律生效。

### 【已修复】端口隔离
删除 docker-compose.yml 中 vh-ollama 的 127.0.0.1:11434:11434 映射
（及随后产生的空 ports: 键）。宿主机 ollama（驱动 Claude Code）走宿主机
11434，vh-ollama（驱动评估管道）走 Docker 内网，两个 ollama 实例共存不撞端口。

### 系统当前完整状态（全部已验证）
| 组件 | 状态 |
|------|------|
| 认证授权（Authentik OIDC + PKCE） | ✓ |
| 知识库（Docling + 章节识别 + REFERENCES 过滤） | ✓ |
| 翻译（gemma4:31b，中文通顺） | ✓ |
| 推理（Meditron 简化 prompt，稳定对症） | ✓ |
| 规则引擎（双轨，确定性阈值命中） | ✓ |
| 显存管理（keep_alive=0） | ✓ |
| Claude Code 运行纪律（GPU 检测 + 自动回退） | ✓ |
| 端口隔离（宿主机/容器 ollama 共存） | ✓ |

**v1 单流程评估系统：已完整跑通、质量达标（working well）。**
下一阶段：v2 双盲交叉验证架构的实现（见 ARCHITECTURE.md）。

---

## H. G1 已解决 + Claude Code 运行纪律落地（2026-06-15）

### G1 解决：Meditron 结构化输出（"指令过载"假说证实）

**方向 B 一步到位。** 简化 reason() 的 prompt 后，Meditron 稳定对症。

**对照实验结论**：
| Prompt 复杂度 | Meditron 行为 |
|--------------|--------------|
| 复杂（few-shot + CRITICAL RULES + 三段结构 + 隔离标记） | 漂移：空响应 / prompt 回显 / 方法论复述 / 三段雷同 |
| 简化（一句话：基于指南评估患者、引用具体阈值、紧扣实际数据） | 稳定：三段结构自然涌现、精准引用 EAU 章节号、结论扣患者 |

**最终 prompt（保留这个，不要再加复杂约束）**：
```
Based on the guideline context below, assess this patient's findings.
Cite specific thresholds or recommendations from the guideline.
Be concise and specific to this patient's actual data.
Do not mention any findings not present in the patient data.
```

**验证报告（前列腺 40.8ml）质量**：
- FINDING：紧扣 40.8ml/3.5x2.6cm/无膀胱突出/无钙化，无编造
- GUIDELINE：引用 EAU 三个具体章节 (4.10.2.a / 4.4.1 / 4.13.1)，含 >30mL 阈值
- CONCLUSION：40.8ml(>30ml) 关联手术/药物(5-ARIs)选择，临门一脚到位

**核心教训（写给未来的 prompt 设计）**：
Meditron 70B 是医学微调模型，capacity 应用于医学推理，不是"听话照格式填空"。
越逼它遵循复杂格式它越崩；只告诉它"做什么"、不规定"怎么格式化"，
它反而自组织出清晰结构。**对专业微调模型：轻指令 > 重指令。**
（这与通用模型相反——通用模型吃 few-shot，专业模型被 few-shot 拖垮。）

旧复杂版 prompt 已备份至 orchestrator/app/pipeline.py.bak，留作对照。

### Claude Code 运行纪律落地（claude-code.sh）

**已实现并验证**：评估管道运行期间启动 ./claude-code.sh，脚本检测到 GPU
被占（显存 > 5000MB 阈值）→ 自动回退云端 API → 评估不受影响。
生产优先、Code 谦让的纪律生效。

**脚本逻辑**：
- GPU 空闲（< 5000MB）→ 本地模型（ollama launch claude，零云端消耗）
- GPU 被占（> 5000MB）→ 云端 claude（不争显存）
- 默认本地模型 gemma4:31b，可用 CLAUDE_CODE_MODEL 环境变量覆盖
- 阈值依据：keep_alive=0 下空闲约 0-500MB，评估时 meditron 占 ~38000MB

**端口隔离（已修）**：删除 docker-compose.yml 中 vh-ollama 的
127.0.0.1:11434:11434 映射（含空 ports: 键）。宿主机 ollama 占 11434 供
Claude Code，容器 vh-ollama 走 Docker 内网供 orchestrator，两者不冲突。

**操作纪律（人为遵守）**：评估和 Claude Code 不强求同时跑。70B 本地模型
（llama3.3/llama4）显存占用大，与评估管道并存风险高；31B 较安全。

### 三脚本启停体系（已成型）
```
./start.sh        启动生产环境（容器栈 + 前端 + 自检）
./claude-code.sh  启动 Claude Code（智能选本地/云端）
./stop.sh         停止一切（数据保留）
```

### 系统当前完整状态（2026-06-15）
| 组件 | 状态 |
|------|------|
| 认证授权 Authentik OIDC + PKCE | ✓ |
| 知识库 Docling + 章节识别 + REFERENCES 过滤 | ✓ |
| 翻译 gemma4:31b（中文输出通顺） | ✓ |
| 推理 Meditron 简化 prompt（稳定对症） | ✓ |
| 规则引擎双轨（确定性阈值命中） | ✓ |
| 显存管理 keep_alive=0 | ✓ |
| Claude Code 运行纪律（GPU 检测 + 自动回退） | ✓ |
| 端口隔离（双 ollama 共存） | ✓ |

v1 单流程评估系统：完整可用，质量达到"working well"。
下一阶段：v2 双盲交叉验证架构（见 ARCHITECTURE.md），按需推进。

---

## J. 主治医生↔患者交互（流程 A1 前端 + 报告上传 OCR）已实现（2026-06-23）

v2 流程 A1 的问诊交互前后端落地，并把原 B4（文件上传）一并完成。

### 后端（orchestrator）
- `interviewer.py`：llama3.3:70b 结构化问诊。每轮输出 JSON `{type:question, question, options[], multi, allow_free_text}`，
  收尾输出 `{type:summary,...}`。首问强制 `multi=true`（症状清单）；选项不设上限；去掉"其他"兜底。
- 端点：`/interview/start`（带 member_id）、`/interview/chat`、`/interview/more`（"更多"：同一问题追加选项，不推进）、
  `/upload`（multipart→MinIO→glm-ocr OCR→建 health_records）。
- 症状包打时间戳归档为 `health_records(record_type='symptom_package', source_org='AI问诊')`。
- 新模块 `storage.py`（MinIO，桶 `health-reports` 自建）、`ocr.py`（glm-ocr，PDF 经 poppler 栅格化；
  尾部重复用 `stop=["```"]` 截断）。`ollama_client.generate` 增 images/options 支持。
- Dockerfile 加 poppler-utils + pdf2image + Pillow + python-multipart，已重建。

### 前端（frontend/index.html）
- 登录后直达"选择科室"；点科室→对话首条为过渡说明（隐私/本地模型耗时/项目特点/即将进入诊室）→直接问诊。
- 选项卡：单选即点即发；多选勾多个→"确定"；末尾"＋更多"动态追加选项；自由文本兜底。
- 右侧病史侧栏（复用 `/members/{id}/records`）。报告上传条（可多文件，类型可选）。
- 待选问题抽成独立 `pending` 状态，与消息流解耦——**上传不打断待选**（已勾选项也保留）。

### 验证（后端逐项实测通过）
选项卡结构化输出、首问多选、"更多"追加不重复（15→24项主题不变）、glm-ocr 图片&PDF OCR、
症状包入档、MinIO 自建桶存储——均通过。前端待浏览器登录态终验。

### B4 状态更新
原「B4 前端文件上传链路」**已实现**（随本次 A1 前端一并落地）。OCR 用 glm-ocr，
严格限定非评估链路（约束 A 红线，见 ocr.py 注释）。

### ⚠ 顺带修复：.env PG_USER 潜伏漂移
`.env` 原 `PG_USER=yangrenming`（过时占位）与真实 DB 角色 `vhadmin` 不符，
`docker compose up` 重建容器后 orchestrator 连不上库。已改 `.env` `PG_USER=vhadmin`
（密码不变、TCP 验证通过）。.env 被 gitignore，不入库；此处留记录。

### 待补 / 已知小瑕疵
- 症状包目前只归档，尚未喂给 A2/B 推理（属 v2 后续）。
- "更多"追加偶尔广撒到跨科症状；如需收紧到当前科室可调 `more_options` prompt。
- 问诊选项偶发夹带英文词（llama 漏译），非阻塞。
- 流程 A1「略过 Authentik 授权页直达选科室」：需在 Authentik 后台把 provider 的
  Authorization flow 改为 implicit-consent（浏览器人工操作，非代码）。

### 顺带修复：评估报告展示层两处 bug（2026-06-23）
纯前端 `frontend/index.html`，不碰后端/推理路线/数据层：
- **来源标签重复**：`risk_sources` 同一来源（如 EAU 2026 LUTS）渲染多次。渲染前 `[...new Set(...)]` 去重。
  （注意：这是展示层去重，与 I/H 节检索侧 `retrieve_guidelines` 的 chunk 去重是两回事。）
- **LaTeX 源码漏出**：meditron/gemma 在 `report_zh` 里输出 `$\alpha$`/`$5\alpha$` 等数学符号，前端未渲染、原样吐出。
  新增轻量 `cleanInlineMath()`（不引 KaTeX），渲染前把 `$5\alpha$`→`5α`、`$\alpha$`→`α`（含 β），并清掉孤立 `$`。

### 同型半胱氨酸规则（见 I 节）仍挂起。
