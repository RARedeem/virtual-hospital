# 文档溯源系统 — 数据结构与接口契约

> 版本：v1.0
> 日期：2026-06-16
> 配套：MODULE-DESIGN.md（模块设计）、ARCHITECTURE-PROVENANCE.md（架构）
> 定位：本文档是契约层。讲"数据长什么样、函数怎么调、传什么拿什么、错怎么报"。
>       接口签名为英文（即代码本身），正文中文。改代码前先读此文档，避免改坏血缘语义。

---

## 第一部分：数据结构契约（DB schema）

schema：`doc_provenance`（与医疗库 `knowledge_base` 物理隔离，架构第二节约束级）

### 表一：documents（文档登记）

| 字段 | 类型 | 约束 | 语义 |
|------|------|------|------|
| doc_id | uuid | PK，默认 gen_random_uuid() | 文档唯一标识 |
| filename | text | NOT NULL，UNIQUE | 文件名，如 ARCHITECTURE.md。UNIQUE 保证幂等登记 |
| created_at | timestamptz | NOT NULL，默认 now() | 首次纳入溯源的时间 |

### 表二：units（文本单元 + 版本）

每个版本一条记录。旧版永不物理删除（is_current=false 表示下线）。

| 字段 | 类型 | 约束 | 语义 |
|------|------|------|------|
| unit_id | uuid | PK，默认 gen_random_uuid() | 单元版本唯一标识（每版一条） |
| doc_id | uuid | NOT NULL，FK→documents | 所属文档 |
| lineage_id | uuid | NOT NULL | 血缘链标识。同一句的所有版本共享此值 |
| version | int | NOT NULL，默认 1 | 该单元版本号，从 1 递增 |
| content | text | NOT NULL | 文本内容 |
| unit_type | text | NOT NULL，CHECK | paragraph / list_item / heading / code_block |
| position | int | NOT NULL | 文档内顺序位置（切分时从 0 递增） |
| editor | text | NOT NULL，CHECK | user / claude / claude-code |
| parent_unit_id | uuid | FK→units，nullable | 上一版本 unit_id（血缘指针）。新增单元为 NULL |
| created_at | timestamptz | NOT NULL，默认 now() | 此版本产生时间 |
| is_current | bool | NOT NULL，默认 true | 是否为该血缘链当前版本 |
| embedding | vector(768) | nullable | nomic 向量。code_block 为 NULL |

**CHECK 约束**：
- `chk_unit_type`：unit_type ∈ {paragraph, list_item, heading, code_block}
- `chk_editor`：editor ∈ {user, claude, claude-code}

**索引**：
| 索引 | 字段 | 用途 |
|------|------|------|
| idx_units_lineage | (lineage_id, version) | 血缘链历史查询 |
| idx_units_current | (doc_id, is_current) WHERE is_current | 血缘判定取基准（部分索引） |
| idx_units_position | (doc_id, position) | 位置邻近加权 |
| idx_units_embedding | ivfflat(embedding vector_cosine_ops) lists=100 | 向量检索 |

**关键不变量（任何改动须维持）**：
1. 同一 lineage_id 下，至多一条 is_current=true。
2. version 在同一 lineage_id 下唯一且连续递增。
3. parent_unit_id 非空时，必指向同一 lineage_id 的前一 version。
4. 旧版永不物理 DELETE，只置 is_current=false。

---

## 第二部分：模块接口契约（函数签名）

签名为实际代码,逐字对齐 v1.0 实现。

### splitter.py

```python
@dataclass
class Unit:
    content: str        # 单元文本（已 strip）
    unit_type: str      # paragraph / list_item / heading / code_block
    position: int       # 文档内顺序，从 0 递增

def split_markdown(text: str) -> list[Unit]
```
**契约**：输入完整 md 文本，输出单元列表。分隔线（---/***/___）不产出单元。
空内容单元被丢弃。position 连续无空洞。
**无副作用**，纯函数，可独立测试（无 DB/Ollama 依赖）。

### embedder.py

```python
EMBED_DIM = 768

def embed_text(text: str, *, timeout: float = 120.0) -> list[float]
def embed_batch(texts: list[str]) -> list[list[float]]
```
**embed_text 契约**：
- 入参：单段文本；timeout 默认 120 秒（容冷启动）。
- 出参：恰好 768 个 float 的列表。
- 错误：重试 3 次（退避 2.0*attempt 秒）后仍失败 → 抛 `RuntimeError`。
  维度不符 768 视为失败并重试。
- 副作用：导入时设置 NO_PROXY 环境变量；调 Ollama `/api/embeddings`。

**embed_batch 契约**：逐条调 embed_text，顺序返回。任一条失败则整体抛出。

**环境依赖**：
| 变量 | 默认 | 说明 |
|------|------|------|
| OLLAMA_HOST | http://127.0.0.1:11434 | 容器内须改 Docker 内网地址 |
| PROVENANCE_EMBED_MODEL | nomic-embed-text:v1.5 | embedding 模型 |

### lineage.py

```python
THRESHOLD = 0.85
POSITION_WEIGHT = 0.0
POSITION_SPAN = 5

@dataclass
class OldUnit:
    unit_id: str
    lineage_id: str
    version: int
    position: int
    embedding: list[float]

@dataclass
class NewUnit:
    content: str
    unit_type: str
    position: int
    embedding: list[float] | None   # code_block 为 None

@dataclass
class LineageDecision:
    new_unit: NewUnit
    action: str                     # 'modify' / 'add'
    lineage_id: str | None          # modify 时为继承的 lineage
    parent_unit_id: str | None      # modify 时为旧 unit_id
    version: int
    matched_old_unit_id: str | None
    similarity: float | None

def resolve_lineage(
    new_units: list[NewUnit],
    old_units: list[OldUnit],
    *,
    threshold: float = THRESHOLD,
    position_weight: float = POSITION_WEIGHT,
) -> tuple[list[LineageDecision], list[str]]
```
**resolve_lineage 契约**：
- 入参：新版单元列表、旧版 current 单元列表；阈值与位置权重可覆盖。
- 出参：`(决策列表, 被删除旧 unit_id 列表)`。
  - 决策列表长度 = len(new_units)，每个新单元一条决策。
  - action='modify'：相似度 > threshold，继承 lineage/parent，version=旧版+1。
  - action='add'：相似度 ≤ threshold 或为 code_block，lineage/parent 为 None，version=1。
  - 删除列表：旧 current 单元中未被任何新单元匹配的 unit_id。
- **不变量**：一对一匹配——每个旧单元至多被一个新单元继承（贪心，按相似度降序）。
- **无副作用**，纯函数。code_block（embedding=None）不参与向量匹配，恒判 add。

### provenance_ingest.py

```python
VALID_EDITORS = {"user", "claude", "claude-code"}

def ingest(filepath: str, editor: str) -> dict
```
**契约**：
- 入参：md 文件路径；editor 须 ∈ VALID_EDITORS，否则抛 `ValueError`。
- 出参：统计 dict —— `{filename, editor, modify, add, delete, total_units}`。
- 副作用：单事务写 doc_provenance.documents + units。文档按 filename 幂等登记。
- 失败：事务回滚（向量化失败、DB 异常等），不留半截血缘链。

**CLI**：`python provenance_ingest.py <file> --editor={user|claude|claude-code}`

**环境依赖**：
| 变量 | 默认 | 说明 |
|------|------|------|
| DATABASE_URL | postgresql://postgres:postgres@127.0.0.1:5432/postgres | 须改 orchestrator 实际连接串 |

### query.py

```python
def history_by_lineage(lineage_id: str) -> list[dict]
def history_by_content(filename: str, needle: str) -> list[dict]
def semantic_search(filename: str | None, query_text: str, top: int = 5) -> list[dict]
```
**history_by_lineage 契约**：按 lineage_id 取全部版本，按 version 升序。
返回行含 `version, editor, created_at, is_current, unit_type, content`。

**history_by_content 契约**：先按 (filename, content ILIKE %needle%, is_current=true)
定位 lineage，再取整链历史。无匹配返回空列表。

**semantic_search 契约**：
- 入参：filename（None=全库）、查询语句、top（默认 5）。
- 出参：行含 `filename, lineage_id, version, unit_type, content, similarity`。
  similarity = 1 - 余弦距离，降序。
- 只检索 is_current=true 且 embedding 非空的单元（code_block 自动排除）。
- 副作用：调 embed_text 求查询向量。

**CLI**：
```
python query.py history --doc <file> --contains <关键词>
python query.py history --lineage <uuid>
python query.py search  --doc <file> --q <检索语句> --top <N>
```

---

## 第三部分：错误行为速查

| 场景 | 模块 | 行为 |
|------|------|------|
| editor 非法 | ingest | 抛 ValueError |
| 向量化重试耗尽 | embedder | 抛 RuntimeError，ingest 事务回滚 |
| 向量维度≠768 | embedder | 视为失败重试 |
| 摄取中途 DB 异常 | ingest | 整事务回滚，血缘链不留半截 |
| history 关键词无匹配 | query | 返回空列表（不报错） |
| search 无结果 | query | 返回空列表（不报错） |
| 文件不存在 | ingest CLI | stderr 报错，exit 1 |

---

## 第四部分：契约级隔离保证（架构第二节）

所有 SQL（DDL、INSERT、SELECT）schema 限定 `doc_provenance`。
- ingest 写入：仅 doc_provenance.documents / units。
- query 读取：FROM/JOIN 仅 doc_provenance.*，不触 knowledge_base。
- 向量空间独立：本系统 embedding 与医疗库 guideline_chunks 的 embedding 同维（768）
  但分表分 schema，检索查询永不跨库。

此隔离与约束 B（医疗指南来源隔离）同源,是硬约束,任何改动不得打破。
