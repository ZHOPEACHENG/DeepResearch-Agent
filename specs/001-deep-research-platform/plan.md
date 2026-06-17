# Implementation Plan: 深度研究平台 (Deep Research Platform)

**Branch**: `001-deep-research-platform` | **Date**: 2026-06-16 (revised) | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-deep-research-platform/spec.md`

## Summary

构建一个面向学术研究场景的 Deep Research Platform，采用前后端分离架构。
用户输入研究主题后，系统通过基于 LangGraph 的多智能体协作流水线，
自动完成研究问题拆解、研究计划生成、多源资料检索、知识整合、知识缺口识别、
补充研究和研究报告生成。平台支持研究任务的完整生命周期管理（创建、执行、
暂停、恢复、历史查看），提供个人知识库构建能力，以及研究人员账号管理。

技术路线：前端基于 Vue 3 + TypeScript + Vite + Element Plus 构建 SPA；
后端基于 FastAPI + LangGraph 实现多智能体研究编排；
PostgreSQL 存储业务数据，MongoDB 存储研究过程与 Agent 执行记录，
Elasticsearch 提供全文检索能力；整体通过 Docker Compose 容器化部署。

## Technical Context

**Language/Version**: Python 3.11+ (backend), TypeScript 5.x (frontend)

**Primary Dependencies**: FastAPI, LangGraph, Vue 3, Vite, Element Plus, Pinia, Vue Router, Axios

**Storage**: PostgreSQL (业务数据: 用户、任务、报告), MongoDB (研究过程: 中间结果、Agent执行记录、检查点), Elasticsearch (全文检索: 知识库文档索引、检索结果搜索)

**Testing**: pytest (backend), Vitest (frontend)

**Target Platform**: Linux server (Docker/Docker Compose 部署), 现代浏览器 (Chrome/Firefox/Edge/Safari 最近2个主版本)

**Project Type**: Web application (前后端分离)

**Performance Goals**:
- 用户操作响应时间 < 3秒 (正常负载)
- 端到端研究任务 < 30分钟 (中等复杂度)
- 知识库问答 < 5秒返回
- 支持50个研究任务并发

**Constraints**:
- 单个研究任务最大执行时间: 2小时
- 用户同时运行任务上限: 3个（超出排队）
- 文档上传大小限制: 50MB
- 补充研究最大迭代轮次: 3轮
- 研究主题最少输入: 10个字符

**Scale/Scope**:
- 10-100 活跃用户 (单机构/实验室规模)
- 10 个核心实体 (User, ResearchTask, ResearchPlan, RetrievalResult, KnowledgeSummary, KnowledgeGap, ResearchReport, Citation, Document, DocumentChunk)
- 6 个用户故事 (P1: 研究流程 + 任务管理; P2: 用户认证 + 引用追溯; P3: 知识库 + 成果管理)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Research Credibility First ✅

| Gate | Status | Evidence |
|------|--------|----------|
| 每个事实性断言附带引用来源 | ✅ | RetrievalResult + Citation 实体建模，FR-010 要求结论→来源追溯 |
| 结论可追溯到原始资料 | ✅ | FR-021/FR-022 定义完整元数据记录和引用汇总；US4 专用于引用追溯 |
| AI生成与原始内容明确区分 | ✅ | FR-010 要求引用标记；US4 场景覆盖来源内容摘录展示 |

### II. Single Responsibility Agents ✅

| Gate | Status | Evidence |
|------|--------|----------|
| 智能体职责独立拆分 | ✅ | LangGraph 编排 Planner → Retriever → Analyzer → Synthesizer → ReportWriter 独立 Agent |
| 智能体可独立替换 | ✅ | Agent 间通过契约通信，各 Agent 定义标准 I/O schema |
| 新增智能体不破坏现有结构 | ✅ | Agent Registry 模式，编排层与 Agent 实现解耦 |

### III. Data Persistence & Recoverability ✅

| Gate | Status | Evidence |
|------|--------|----------|
| 状态跟踪 | ✅ | ResearchTask 状态机: 待开始/进行中/已暂停/已完成/失败；FR-014 实时状态展示 |
| 中间结果保存 | ✅ | MongoDB 存储所有阶段中间产物；FR-012 要求持久保存全部中间产物 |
| 中断恢复 | ✅ | FR-013 断点续传；US2 场景覆盖恢复流程；每个阶段完成写入检查点 |

### IV. User Data Isolation ✅

| Gate | Status | Evidence |
|------|--------|----------|
| 用户数据严格隔离 | ✅ | FR-020 强制隔离；所有查询以 user_id 为过滤前缀 |
| API + 业务逻辑双层校验 | ✅ | 中间件层 JWT 认证 + 服务层 user_id 注入 |
| Ownership 不可修改 | ✅ | user_id 在创建时设定，禁止更新 |

### V. Extensible Architecture ✅

| Gate | Status | Evidence |
|------|--------|----------|
| 搜索源可扩展 | ✅ | SearchSource 抽象接口，多来源接入（web + arXiv + Semantic Scholar） |
| 知识库可扩展 | ✅ | Document 实体设计适配多格式（PDF/DOCX/TXT/Markdown） |
| 接口向后兼容 | ✅ | API 版本化前缀 `/api/v1/` |

### VI. Observability & Auditability ✅

| Gate | Status | Evidence |
|------|--------|----------|
| 执行轨迹记录 | ✅ | MongoDB 存储 Agent 执行记录（输入/输出/耗时/状态） |
| 决策溯源 | ✅ | KnowledgeGap 记录缺口识别原因；RetrievalResult 记录检索时间戳和来源 |
| 结构化日志 | ✅ | 按 task_id/user_id/agent_name/timestamp 维度记录 |

### VII. Simplicity First ✅

| Gate | Status | Evidence |
|------|--------|----------|
| 核心工作流优先 | ✅ | P1 聚焦完整研究流程 + 任务管理（US1 + US2） |
| 无微服务拆分 | ✅ | Docker Compose 单机部署；模块化 Monolith 而非分布式 |
| 主分支可运行 | ✅ | 每个 Phase 以可运行的 Checkpoint 结束 |

### Complexity Justification

| Item | Justification | Simpler Alternative Rejected |
|------|--------------|------------------------------|
| 三个数据库 (PostgreSQL + MongoDB + Elasticsearch) | PostgreSQL: 结构化业务数据（用户、任务）需事务和关系约束；MongoDB: 研究过程文档（中间结果、Agent日志）schema 灵活多变，文档模型自然适配；Elasticsearch: 知识库全文检索和语义搜索是其核心能力，非关系型/文档型数据库可替代 | 单 PostgreSQL 方案无法高效支持全文检索和灵活 schema；PostgreSQL + MongoDB 双库方案缺少专用搜索引擎，知识库搜索体验差 |
| LangGraph 编排框架 | 研究流程需支持条件分支（缺口→补充检索循环）、状态检查点（断点续传）、Agent 间状态传递，LangGraph 原生支持这些模式 | 硬编码 if/else 编排逻辑无法支持断点续传和动态流程调整 |

## Project Structure

### Documentation (this feature)

```text
specs/001-deep-research-platform/
├── plan.md              # This file
├── research.md          # Phase 0 output - 技术决策研究
├── data-model.md        # Phase 1 output - 数据模型设计
├── quickstart.md        # Phase 1 output - 快速启动指南
├── contracts/           # Phase 1 output - API 契约
│   └── api-v1.yaml      # OpenAPI 3.0 REST API 定义
└── tasks.md             # Phase 2 output (/speckit-tasks command)
```

### Source Code (repository root)

```text
backend/
├── __init__.py
├── main.py                    # FastAPI 应用入口
├── core/
│   ├── __init__.py
│   ├── config.py              # 配置管理（环境变量）
│   ├── security.py            # JWT 认证、密码哈希
│   └── database.py            # PostgreSQL/MongoDB/ES 连接管理
├── models/
│   ├── __init__.py
│   ├── user.py                # User ORM 模型
│   ├── conversation.py        # Conversation + Message ORM 模型（对话交互层）
│   ├── task.py                # ResearchTask ORM 模型（内部实体）
│   ├── report.py              # ResearchReport ORM 模型
│   └── document.py            # Document ORM 模型
├── schemas/
│   ├── __init__.py
│   ├── user.py                # Pydantic 请求/响应 schema
│   ├── conversation.py        # Conversation/Message/SSE 事件 schemas
│   ├── task.py
│   ├── research.py            # 研究流程相关 schema
│   └── document.py
├── api/
│   ├── __init__.py
│   ├── deps.py                # 依赖注入（get_current_user 等）
│   └── v1/
│       ├── __init__.py
│       ├── router.py          # v1 路由汇总
│       ├── auth.py            # 注册、登录、登出
│       ├── users.py           # 用户资料管理
│       ├── conversations.py   # 会话管理 + 消息发送（SSE 流式响应）
│       ├── tasks.py           # 研究任务 CRUD（内部保留，已 deprecated）
│       ├── research.py        # 研究流程 WebSocket/SSE 端点
│       ├── reports.py         # 报告查看 + 导出
│       └── knowledge.py       # 知识库上传、搜索、问答
├── agents/
│   ├── __init__.py
│   ├── base.py                # Agent 基类 + 注册表
│   ├── planner.py             # 研究规划 Agent
│   ├── retriever.py           # 资料检索 Agent
│   ├── analyzer.py            # 知识整合 Agent
│   ├── synthesizer.py         # 综合合成 Agent
│   └── writer.py              # 报告生成 Agent
├── services/
│   ├── __init__.py
│   ├── auth_service.py        # 认证业务逻辑
│   ├── conversation_service.py # 会话 CRUD + 上下文窗口管理
│   ├── intent_router.py       # LLM 意图分类（chat/research/follow_up）
│   ├── chat_service.py        # 对话编排中枢（意图路由→聊天/流水线→SSE→写Message）
│   ├── task_service.py        # 任务管理业务逻辑（内部使用）
│   ├── research_service.py    # 研究编排（LangGraph workflow）
│   ├── report_service.py      # 报告生成与导出
│   └── knowledge_service.py   # 知识库管理
├── tools/
│   ├── __init__.py
│   ├── search.py              # 搜索源抽象 + 实现（web, arXiv, Semantic Scholar）
│   ├── parser.py              # 文档解析（PDF, DOCX, TXT, MD）
│   └── exporter.py            # 报告导出（Markdown, PDF）
├── db/
│   ├── __init__.py
│   ├── postgresql/            # PostgreSQL 迁移脚本
│   └── mongodb/               # MongoDB 初始化脚本
└── utils/
    ├── __init__.py
    ├── logging.py             # 结构化日志工具
    └── helpers.py             # 通用工具函数

frontend/
├── index.html
├── vite.config.ts
├── tsconfig.json
├── package.json
├── src/
│   ├── main.ts                # Vue 应用入口
│   ├── App.vue                # 根组件
│   ├── router/
│   │   └── index.ts           # Vue Router 路由配置
│   ├── stores/
│   │   ├── auth.ts            # Pinia 认证状态
│   │   ├── conversations.ts   # 会话 + 消息 + SSE 流式状态
│   │   └── knowledge.ts       # 知识库状态
│   ├── api/
│   │   ├── client.ts          # Axios 实例 + 拦截器
│   │   ├── auth.ts            # 认证 API 调用
│   │   ├── conversations.ts   # 会话/消息 API + SSE ReadableStream 解析
│   │   ├── research.ts        # 研究流程 API 调用
│   │   └── knowledge.ts       # 知识库 API 调用
│   ├── components/
│   │   ├── layout/            # 布局组件（AppLayout, Header）
│   │   ├── chat/              # 对话组件（MessageBubble, ChatInputBox, ConversationSidebar,
│   │   │                      #   TextMessage, ResearchPlanCard, RetrievalCard, ReportCard,
│   │   │                      #   GapQuestionCard, ErrorMessage, StreamingIndicator）
│   │   ├── research/          # 研究流程组件（CitationPopup 等）
│   │   ├── report/            # 报告组件（ExportButton）
│   │   └── common/            # 通用组件（FileUpload, SearchBar）
│   ├── pages/
│   │   ├── ChatPage.vue       # 主对话页面（会话消息列表 + 输入框）
│   │   ├── LoginPage.vue
│   │   ├── RegisterPage.vue
│   │   ├── KnowledgeBasePage.vue
│   │   └── ProfilePage.vue
│   └── types/
│       ├── user.ts            # TypeScript 类型定义
│       ├── conversation.ts    # Conversation/Message/SSE 事件类型
│       ├── task.ts            # Task 类型（内部 metadata）
│       ├── research.ts
│       └── document.ts

docker/
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
└── nginx.conf

tests/
├── unit/
│   ├── test_agents.py
│   ├── test_services.py
│   └── test_models.py
└── integration/
    ├── test_api_auth.py
    ├── test_api_tasks.py
    └── test_research_flow.py

reports/                      # 运行时生成的报告文件目录
```

**Structure Decision**: 选择 Option 2 Web application 结构。前端 (`frontend/`) 和后端 (`backend/`) 各自独立，通过 REST API 通信。后端按 FastAPI 标准分层（models/schemas/api/services/agents）；前端按 Vue 3 生态标准分层（router/stores/api/components/pages）。多数据库连接管理集中在 `backend/core/database.py`。

## Complexity Tracking

> 以下为宪法检查中识别并已论证的复杂度项

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| 三个数据库 (PostgreSQL + MongoDB + Elasticsearch) | (1) PostgreSQL: 用户/任务/报告等业务数据需 ACID 事务和关系约束; (2) MongoDB: 研究过程数据（Agent 日志、中间结果、检查点）schema 随研究流程演变，文档模型天然适配; (3) Elasticsearch: 知识库全文检索和语义搜索是知识库功能的核心，专用搜索引擎不可替代 | 单 PostgreSQL 方案: JSONB 列可存灵活数据但索引和查询性能远不及 MongoDB 和 ES；全文检索 tsvector 功能弱于 ES 且无法做语义搜索。双库方案 (PG + Mongo) 缺少搜索引擎，知识库搜索体验显著下降 |
| LangGraph 编排框架 | 研究流程包含条件循环（缺口→补充检索，最大3轮）、状态检查点（断点续传）、多 Agent 状态传递，这些是 LangGraph 原生能力 | 手动 if/else 编排：无法支持断点续传（需自行实现状态序列化/反序列化），流程变更需修改编排代码，缺乏可视化调试能力 |
