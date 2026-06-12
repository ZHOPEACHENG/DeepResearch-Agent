# Data Model: 深度研究平台

**Date**: 2026-06-04
**Branch**: `001-deep-research-platform`

## Entity-Relationship Overview

```text
User (1) ────────< (N) ResearchTask
User (1) ────────< (N) Document

ResearchTask (1) ──< (1) ResearchPlan
ResearchTask (1) ──< (N) RetrievalResult
ResearchTask (1) ──< (N) KnowledgeSummary
ResearchTask (1) ──< (N) KnowledgeGap
ResearchTask (1) ──< (1) ResearchReport

ResearchReport (1) ──< (N) Citation
Citation (N) ────> (1) RetrievalResult

Document (1) ────< (N) DocumentChunk
```

## Storage Strategy

| Entity | Storage | Reason |
|--------|---------|--------|
| User | PostgreSQL | 结构化数据，关系约束，ACID |
| ResearchTask | PostgreSQL | 任务状态流转需事务保障 |
| ResearchPlan | MongoDB | JSON 层级结构（问题拆解树） |
| RetrievalResult | MongoDB | 灵活 schema（不同来源字段各异） |
| KnowledgeSummary | MongoDB | 文档型内容 + 引用映射 |
| KnowledgeGap | MongoDB | 嵌套的缺口描述信息 |
| ResearchReport | PostgreSQL | 结构化报告元数据 |
| Citation | PostgreSQL | 关系型引用映射 |
| Document | PostgreSQL | 结构化文件元数据 |
| DocumentChunk | Elasticsearch | 全文索引单元 |

## Entity Definitions

### User (PostgreSQL)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, default uuid4 | 用户唯一标识 |
| username | VARCHAR(50) | UNIQUE, NOT NULL | 用户名 |
| email | VARCHAR(255) | UNIQUE, NOT NULL | 邮箱地址 |
| password_hash | VARCHAR(255) | NOT NULL | bcrypt 哈希后的密码 |
| display_name | VARCHAR(100) | nullable | 显示名称 |
| institution | VARCHAR(200) | nullable | 所属机构 |
| is_active | BOOLEAN | default true | 账号启用状态 |
| locked_until | TIMESTAMP | nullable | 账号锁定到期时间 |
| login_attempts | INTEGER | default 0 | 连续登录失败次数 |
| created_at | TIMESTAMP | default now() | 注册时间 |
| updated_at | TIMESTAMP | auto-update | 最后更新时间 |

### ResearchTask (PostgreSQL)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK | 任务唯一标识 |
| user_id | UUID | FK → User.id, NOT NULL | 所属用户 |
| topic | TEXT | NOT NULL, min_length=10 | 研究主题描述 |
| status | VARCHAR(20) | NOT NULL, default 'pending' | 状态: pending / running / paused / completed / failed |
| current_phase | VARCHAR(30) | nullable | 当前阶段: planning / retrieving / analyzing / synthesizing / reporting |
| progress_message | TEXT | nullable | 阶段进度可读描述 |
| started_at | TIMESTAMP | nullable | 开始执行时间 |
| completed_at | TIMESTAMP | nullable | 完成时间 |
| elapsed_seconds | INTEGER | default 0 | 已用时间（秒） |
| error_message | TEXT | nullable | 失败原因 |
| retry_count | INTEGER | default 0 | 重试次数 |
| config_json | JSONB | default '{}' | 任务配置（检索来源、最大迭代轮次等） |
| created_at | TIMESTAMP | default now() | 创建时间 |
| updated_at | TIMESTAMP | auto-update | 最后更新时间 |

**State Machine**:
```text
pending ──> running ──> completed
  │           │
  │           ├──> failed ──> pending (retry)
  │           │
  │           └──> paused ──> running (resume)
  │                          │
  │                          └──> failed
  └── (delete from pending)
```

### ResearchPlan (MongoDB)

```json
{
  "_id": "ObjectId",
  "task_id": "UUID (关联 ResearchTask.id)",
  "research_questions": [
    {
      "id": "q1",
      "question": "核心研究问题",
      "sub_questions": [
        { "id": "q1.1", "question": "子问题", "priority": 1 }
      ]
    }
  ],
  "search_keywords": [
    { "keyword": "LLM medical diagnosis", "language": "en", "priority": 1 }
  ],
  "expected_sources": ["web", "arxiv", "semantic_scholar"],
  "generated_at": "ISODate"
}
```

### RetrievalResult (MongoDB)

```json
{
  "_id": "ObjectId",
  "task_id": "UUID",
  "round": 1,
  "source_type": "arxiv | semantic_scholar | web | knowledge_base",
  "title": "Paper or Page Title",
  "abstract": "Snippet or abstract text",
  "url": "https://...",
  "doi": "10.xxx/yyyy (nullable)",
  "authors": ["Author Name"],
  "published_date": "2024-01-15 (nullable)",
  "raw_snapshot": "Full text snapshot at retrieval time (nullable)",
  "credibility": "high | medium | low | unknown",
  "retrieved_at": "ISODate"
}
```

**credibility 判定**:
- `high`: 完整元数据 + 同行评议来源
- `medium`: 部分元数据缺失或非正式来源
- `low`: 关键元数据缺失，来源未验证
- `unknown`: 来源信息严重不完整（对应 FR-024）

### KnowledgeSummary (MongoDB)

```json
{
  "_id": "ObjectId",
  "task_id": "UUID",
  "phase": "initial_synthesis | gap_fill_round_1 | gap_fill_round_2 | gap_fill_round_3",
  "content": "Markdown 格式的结构化总结文本",
  "citation_map": {
    "chunk_1": ["retrieval_result_id_1", "retrieval_result_id_3"],
    "chunk_2": ["retrieval_result_id_2"]
  },
  "generated_at": "ISODate"
}
```

### KnowledgeGap (MongoDB)

```json
{
  "_id": "ObjectId",
  "task_id": "UUID",
  "description": "缺口描述：该子问题缺少2024年之后的实证研究数据",
  "related_question_id": "q1.2",
  "triggered_retrieval": true,
  "retrieval_round": 2,
  "retrieval_status": "pending | running | completed | failed",
  "identified_at": "ISODate"
}
```

### ResearchReport (PostgreSQL)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK | 报告唯一标识 |
| task_id | UUID | FK → ResearchTask.id, UNIQUE | 所属任务（一对一） |
| title | VARCHAR(500) | NOT NULL | 报告标题 |
| abstract | TEXT | NOT NULL | 摘要 |
| sections_json | JSONB | NOT NULL | 分节正文（含内联引用标记） |
| citations_json | JSONB | NOT NULL | 引用列表汇总 |
| gap_notes | TEXT | nullable | 知识缺口说明 |
| export_format_log | JSONB | default '[]' | 导出记录 [{format: "pdf", exported_at: "..."}] |
| created_at | TIMESTAMP | default now() | 创建时间 |
| updated_at | TIMESTAMP | auto-update | 最后更新时间 |

### Citation (PostgreSQL)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK | 引用唯一标识 |
| report_id | UUID | FK → ResearchReport.id | 所属报告 |
| index_number | INTEGER | NOT NULL | 引用序号 [1], [2], ... |
| retrieval_result_id | VARCHAR(24) | NOT NULL | 关联 MongoDB 中 RetrievalResult 的 _id（24字符 hex 字符串） |
| context_in_report | TEXT | nullable | 引用在报告中的上下文位置 |

### Document (PostgreSQL)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK | 文档唯一标识 |
| user_id | UUID | FK → User.id | 所属用户 |
| filename | VARCHAR(500) | NOT NULL | 原始文件名 |
| file_type | VARCHAR(10) | NOT NULL | pdf / docx / txt / md |
| file_size_bytes | BIGINT | NOT NULL | 文件大小 |
| storage_path | VARCHAR(1000) | NOT NULL | 文件存储路径 |
| processing_status | VARCHAR(20) | default 'pending' | pending / processing / completed / failed |
| processing_error | TEXT | nullable | 处理失败原因 |
| es_index_name | VARCHAR(100) | nullable | ES 索引名称 |
| created_at | TIMESTAMP | default now() | 上传时间 |
| updated_at | TIMESTAMP | auto-update | 最后更新时间 |
| processed_at | TIMESTAMP | nullable | 处理完成时间 |

### DocumentChunk (Elasticsearch)

```json
{
  "document_id": "UUID",
  "user_id": "UUID",
  "chunk_index": 0,
  "text": "Chunk text content...",
  "page_number": 3,
  "paragraph_range": [10, 15],
  "embedding": [0.123, -0.456, ...]
}
```

## Index Strategy

### PostgreSQL Indexes
- `User`: email (unique), username (unique)
- `ResearchTask`: (user_id, status), (user_id, updated_at DESC)
- `ResearchReport`: task_id (unique)
- `Citation`: (report_id, index_number)

### MongoDB Indexes
- `ResearchPlan`: task_id (unique)
- `RetrievalResult`: (task_id, round)
- `KnowledgeSummary`: (task_id, phase)
- `KnowledgeGap`: (task_id, retrieval_status)

### Elasticsearch Indexes
- `DocumentChunk`: dense_vector(embedding, 1536d), text (analyzed with ik_max_word for Chinese)
