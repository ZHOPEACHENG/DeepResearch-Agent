# Research: 深度研究平台技术决策

**Date**: 2026-06-04
**Branch**: `001-deep-research-platform`

## 1. 前端框架选型

### Decision: Vue 3 + TypeScript + Vite + Element Plus + Pinia + Vue Router

### Rationale

- **Vue 3 Composition API**: 组件逻辑复用清晰，TypeScript 支持成熟，学习曲线平缓
- **TypeScript**: 类型安全对于研究数据处理场景至关重要（Citation、RetrievalResult 等实体字段复杂）
- **Vite**: 开发服务器秒级冷启动，HMR 即时生效，构建速度显著优于 Webpack
- **Element Plus**: 成熟的 Vue 3 组件库，提供表格、表单、进度条、对话框等开箱即用的 UI 组件，适合学术工具型界面
- **Pinia**: Vue 3 官方推荐状态管理，TypeScript 原生支持，模块化 Store 天然适配用户隔离
- **Vue Router**: Vue 生态标准路由方案，路由守卫实现认证拦截

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| React + Next.js | 团队更熟悉 Vue 生态；Next.js SSR 对研究平台这类工具型 SPA 无显著优势 |
| Nuxt 3 | SSR/SSG 能力超出需求范围，增加复杂度；纯 SPA 模式下的 Nuxt 与直接使用 Vue 3 + Vite 无本质差异 |
| Ant Design Vue | 功能丰富但包体积更大；Element Plus API 更简洁 |

## 2. 后端框架选型

### Decision: FastAPI + LangGraph

### Rationale

- **FastAPI**: 原生异步支持（研究任务 I/O 密集，异步带来显著吞吐提升）；自动 OpenAPI 文档生成；Pydantic 数据验证与 TypeScript 类型系统呼应
- **LangGraph**: 专为有状态多步骤 Agent 工作流设计；原生支持条件分支（知识缺口→补充检索循环）、状态检查点（断点续传）、节点间状态传递

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| Django + DRF | ORM 强但异步支持弱；研究平台的大量 I/O 等待场景不适合同步模型 |
| Flask | 轻量但缺少 FastAPI 的自动验证、文档生成和异步原生支持 |
| LangChain LCEL | 链式调用模型对简单线性流程友好，但对含条件循环和状态检查点的研究流程支持不足 |
| 纯手写编排 | 自行实现状态管理和断点续传工作量巨大，LangGraph 已提供经过验证的抽象 |

## 3. 数据层选型

### Decision: PostgreSQL + MongoDB + Elasticsearch

### Rationale

- **PostgreSQL** (业务数据): 用户、研究任务、报告等实体间有明确关系约束；需 ACID 事务保障（如创建任务同时初始化关联的 ResearchPlan）；成熟的 ORM 生态
- **MongoDB** (研究过程): Agent 执行记录 schema 随研究流程演变（不同 Agent 输入/输出结构不同）；文档模型天然适配 JSON 结构的中间结果存储；灵活的嵌套结构无需频繁 migration
- **Elasticsearch** (全文检索): 知识库文档内容搜索和语义检索是其核心能力；支持中文分词和相关性排序；倒排索引性能远优于关系型/文档型数据库的全文索引

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| 仅 PostgreSQL | JSONB 可存灵活数据但查询和聚合性能远不及 MongoDB；全文检索 tsvector 中文分词弱，无语义搜索 |
| PostgreSQL + MongoDB | 缺少专用搜索引擎，知识库搜索功能实现复杂且性能差 |
| MongoDB 替代 Elasticsearch | MongoDB Atlas Search 有一定全文检索能力但中文支持弱，语义搜索需额外集成 |
| MinIO/S3 替代 MongoDB | 对象存储适合文件/二进制数据，不适合 Agent 执行记录这种需频繁查询和更新的结构化文档 |

## 4. AI 模型集成策略

### Decision: 通过抽象接口接入大语言模型 API，支持模型可替换

### Rationale

- 研究流程各阶段（问题拆解、知识整合、缺口识别、报告生成）均依赖 LLM
- 抽象接口解耦业务逻辑与具体模型供应商，符合宪法 V（可扩展架构）
- 支持未来切换模型或引入本地模型

### Key Design Points

- 定义 `LLMProvider` 抽象基类，统一 `chat()` 和 `embed()` 接口
- 研究各阶段的提示词模板独立管理
- 支持流式输出（SSE → 前端实时展示研究进度）

## 5. 部署策略

### Decision: Docker Compose 单机部署

### Rationale

- 面向 10-100 活跃用户的单机构/实验室场景，无需 K8s 集群
- Docker Compose 一键启动全部服务（frontend, backend, PostgreSQL, MongoDB, Elasticsearch）
- 符合宪法 VII（简洁优先）

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| Kubernetes | 单机场景下 K8s 运维成本远超收益；宪法 VII 要求避免不必要复杂架构 |
| 裸机部署 | 依赖管理复杂，环境差异导致"在我机器上能跑"问题 |
| 云托管服务 | v1 不引入云厂商绑定；自托管满足实验室内部使用需求 |

## 6. 身份认证方案

### Decision: JWT (Access Token + Refresh Token)

### Rationale

- 无状态认证，适合前后端分离架构
- 用户隔离可通过 token 中的 user_id 在中间件层统一实现
- Refresh Token 支持长时间研究任务中无缝续期

## 7. 研究流程通信模式

### Decision: REST API + SSE (Server-Sent Events) 混合

### Rationale

- CRUD 操作（任务管理、用户管理）使用标准 REST API
- 研究流程状态更新使用 SSE 实时推送到前端（任务进度、阶段切换、日志流）
- SSE 比 WebSocket 更轻量，单向推送满足需求，浏览器原生支持自动重连

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| 纯轮询 | 研究任务耗时数十分钟，高频轮询浪费资源；低频轮询状态更新不及时 |
| WebSocket | 双向通信能力超出需求；需额外处理连接管理、心跳、重连逻辑 |
