# Tasks: 深度研究平台 (Deep Research Platform)

**Input**: Design documents from `/specs/001-deep-research-platform/`
**Prerequisites**: plan.md (required), spec.md (required), data-model.md, contracts/api-v1.yaml, research.md, quickstart.md

**Tests**: Not explicitly requested in spec — test tasks are omitted. Add test tasks only if user requests TDD.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Web app**: `backend/` + `frontend/`
- Backend: `backend/main.py`, `backend/core/`, `backend/models/`, `backend/schemas/`, `backend/api/`, `backend/agents/`, `backend/services/`, `backend/tools/`, `backend/db/`, `backend/utils/`
- Frontend: `frontend/src/`, `frontend/src/router/`, `frontend/src/stores/`, `frontend/src/api/`, `frontend/src/components/`, `frontend/src/pages/`, `frontend/src/types/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, Docker environment, and basic scaffolding

- [X] T001 Create project directory structure per plan.md (backend/, frontend/, docker/, tests/, reports/)
- [X] T002 [P] Write Docker Compose file with PostgreSQL, MongoDB, Elasticsearch services in `docker/docker-compose.yml`
- [X] T003 [P] Write Dockerfile for backend with Python 3.11+ and FastAPI dependencies in `docker/Dockerfile.backend`
- [X] T004 [P] Write Dockerfile for frontend with Node.js and Vite build in `docker/Dockerfile.frontend`
- [X] T005 [P] Write Nginx reverse proxy configuration for frontend + backend in `docker/nginx.conf`
- [X] T006 Write `.env.example` with all required environment variables (LLM_API_KEY, SEARCH_API_KEY, DB passwords, JWT_SECRET_KEY)
- [X] T007 Initialize Python project with FastAPI, LangGraph, and all backend dependencies in `pyproject.toml`
- [X] T008 [P] Initialize Vue 3 + TypeScript + Vite project with Element Plus, Pinia, Vue Router, Axios in `frontend/package.json` and `frontend/vite.config.ts`
- [X] T009 [P] Configure TypeScript strict mode in `frontend/tsconfig.json`
- [X] T010 Write project README with setup instructions in `README.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Database & Configuration

- [X] T011 Implement configuration management with environment variable loading in `backend/core/config.py`
- [X] T012 [P] Implement PostgreSQL connection manager with SQLAlchemy async engine in `backend/core/database.py`
- [X] T013 [P] Implement MongoDB connection manager with Motor async client in `backend/core/database.py`
- [X] T014 [P] Implement Elasticsearch connection manager with elasticsearch-py async client in `backend/core/database.py`
- [X] T014a [P] Define Elasticsearch index mappings — DocumentChunk (dense_vector 1536d, text ik_max_word, chunk_metadata, parent_doc_id) and RetrievalResult (keyword + text fields for full-text search) in `backend/db/elasticsearch/mappings.py`
- [X] T015 Create PostgreSQL table migration scripts for User, ResearchTask, ResearchReport, Citation, Document in `backend/db/postgresql/`

### Core Models (PostgreSQL)

- [X] T016 [P] Define User ORM model in `backend/models/user.py`
- [X] T017 [P] Define ResearchTask ORM model (id, user_id, topic, status, current_phase, progress_message, timestamps, error_message, retry_count, config_json) in `backend/models/task.py`
- [X] T018 [P] Define ResearchReport ORM model (id, task_id, title, abstract, sections_json, citations_json, gap_notes) in `backend/models/report.py`
- [X] T019 [P] Define Citation ORM model (id, report_id, index_number, retrieval_result_id, context_in_report) in `backend/models/report.py`
- [X] T020 [P] Define Document ORM model (id, user_id, filename, file_type, file_size_bytes, storage_path, processing_status, es_index_name) in `backend/models/document.py`

### Core Schemas (Pydantic)

- [X] T021 [P] Define User Pydantic schemas (UserCreate, UserRead, UserUpdate, UserLogin, TokenPair) in `backend/schemas/user.py`
- [X] T022 [P] Define ResearchTask Pydantic schemas (TaskCreate, TaskRead, TaskList, TaskStatus) in `backend/schemas/task.py`
- [X] T023 [P] Define shared response schemas (ErrorResponse, PaginatedResponse) in `backend/schemas/__init__.py`

### Auth Middleware

- [X] T024 Implement JWT access token + refresh token generation and validation in `backend/core/security.py`
- [X] T025 Implement bcrypt password hashing utilities in `backend/core/security.py`
- [X] T026 Implement `get_current_user` dependency with JWT extraction and user lookup in `backend/api/deps.py`
- [X] T027 Implement user isolation middleware — inject `user_id` filter into all data access queries in `backend/api/deps.py`

### API Foundation

- [X] T028 Create FastAPI application instance with CORS, error handlers, and structured logging middleware in `backend/main.py`
- [X] T029 Create v1 API router with tag grouping (Auth, Users, Tasks, Research, Reports, Knowledge) in `backend/api/v1/router.py`
- [X] T030 [P] Implement global error handlers (404, 422, 500) with Problem Details format in `backend/api/__init__.py`
- [X] T031 [P] Implement structured logging utility with task_id/user_id/agent/timestamp context in `backend/utils/logging.py`

### Agent Framework

- [X] T032 Define Agent base class with abstract `run(state) → state` interface and AgentRegistry in `backend/agents/base.py`
- [X] T033 [P] Define LLM provider abstract interface (`LLMProvider.chat()`, `LLMProvider.embed()`) and OpenAI-compatible implementation in `backend/tools/llm.py`
- [X] T034 Define research workflow state schema (TypedDict with all stage inputs/outputs) in `backend/schemas/research.py`

### Frontend Foundation

- [X] T035 Set up Axios instance with base URL, JWT interceptor (attach token, handle 401 → refresh → retry), and error handling in `frontend/src/api/client.ts`

**Checkpoint**: Foundation ready — user story implementation can now begin. Backend starts with `uvicorn main:app`, frontend with `npm run dev`. Auth middleware validates tokens, DB connections established, Agent registry ready.

---

## Phase 3: User Story 2 — 研究任务管理 (Priority: P1) 🎯 MVP Foundation

**Goal**: 用户能够创建、查看、启动、暂停、恢复、删除研究任务。任务状态持久化，
支持断点续传。前端提供任务列表页和任务详情页。

**Why this before US1**: 任务管理是研究流程的容器——用户需要先创建任务才能启动研究。
US1 的研究流水线依赖本阶段的 Task CRUD + 状态机 + 中间产物存取。

**Independent Test**: 创建3个任务，分别执行启动→暂停→恢复→删除操作，验证状态流转正确，
数据在同一用户多次登录后仍完整保留。

### Implementation for User Story 2

- [X] T036 [P] [US2] Define MongoDB document schemas for ResearchPlan, RetrievalResult, KnowledgeSummary, KnowledgeGap in `backend/db/mongodb/init.js`
- [X] T037 [P] [US2] Define research-related Pydantic schemas (ResearchPlanSchema, RetrievalResultSchema, KnowledgeSummarySchema, KnowledgeGapSchema, StageOutputs) in `backend/schemas/research.py` (extend T034)
- [X] T038 [US2] Implement ResearchTask state machine with transitions: pending→running, running→paused, paused→running, running→completed, running→failed, failed→pending (retry) in `backend/services/task_service.py`
- [X] T039 [US2] Implement task CRUD operations: create_task (validate topic ≥10 chars), get_task (user-scoped), list_tasks (with status filter + pagination), delete_task (cascade) in `backend/services/task_service.py`
- [X] T040 [US2] Implement checkpoint persistence — save current_phase + progress_message + elapsed_seconds to ResearchTask on each stage boundary in `backend/services/task_service.py`
- [X] T041 [US2] Implement stage output storage/retrieval — store ResearchPlan/RetrievalResult/KnowledgeSummary/KnowledgeGap in MongoDB, expose via get_stage_outputs(task_id, stage) in `backend/services/task_service.py`
- [X] T042 [US2] Implement task concurrency control — enforce max 3 running tasks per user, queue excess in `backend/services/task_service.py`
- [X] T043 [US2] Implement Task API endpoints (GET /tasks, POST /tasks, GET /tasks/{id}, DELETE /tasks/{id}) in `backend/api/v1/tasks.py`
- [X] T044 [US2] Implement Task control endpoints (POST /tasks/{id}/start, POST /tasks/{id}/pause, POST /tasks/{id}/resume) with status validation in `backend/api/v1/tasks.py`
- [X] T045 [US2] Implement Stage outputs endpoint (GET /research/{task_id}/stage-outputs?stage=) in `backend/api/v1/research.py`
- [X] T046 [P] [US2] Define TypeScript types for Task, TaskStatus, StageOutput in `frontend/src/types/task.ts`
- [X] T047 [P] [US2] Implement Pinia task store with actions (fetchTasks, createTask, startTask, pauseTask, resumeTask, deleteTask) and reactive state in `frontend/src/stores/tasks.ts`
- [X] T048 [US2] Create TaskListPage with status filter tabs, task cards (topic + status badge + progress + time), and "新建研究" button in `frontend/src/pages/TaskListPage.vue`
- [X] T049 [US2] Create TaskDetailPage with topic header, status timeline, stage output accordion panels (plan/retrieval/summary/gaps/report), and action buttons (start/pause/resume/delete) in `frontend/src/pages/TaskDetailPage.vue`
- [X] T050 [US2] Create TaskCard component with topic preview, status tag, progress bar, and elapsed time display in `frontend/src/components/task/TaskCard.vue`

**Checkpoint**: 用户可创建任务、启动/暂停/恢复/删除任务、查看任务列表和详情。
任务状态正确流转，中间产物持久保存。此时任务启动后状态会卡在 running（研究的 Agent 尚未实现，Phase 4 补齐）。

**⚠️ 2026-06-16 Revision**: Phase 3 前端页面（TaskListPage/TaskDetailPage/TaskCard）已被 Phase 3b 替代为对话式界面。后端 task_service 和相关模型/API 保留为内部使用，不再直接暴露给用户。

---

## Phase 3b: Conversation Layer — 对话式交互层 (Priority: P0) 🎯 UI Foundation

**Purpose**: 将平台从表单驱动的任务流水线转换为对话式交互模式。用户面对的是 ChatPage，自由输入文字，LLM 自动识别意图。研究过程以对话消息内嵌卡片形式呈现。ResearchTask 实体隐藏在 Conversation/Message 之下。

**Why this phase**: 对话交互层是用户体验的入口——它决定了用户如何触发和消费研究能力，必须在 Phase 4'（研究流水线）之前完成。

**Independent Test**: 打开应用 → 输入"你好" → 验证流式文本回复。输入研究主题 → 验证 ResearchPlanCard 渲染 → 点击接受 → 验证流水线卡片逐步展示（注意：Agent 流水线在 Phase 4' 实现，3b 阶段只验证至意图路由和 Plan 生成框架）。

### Database & Models

- [X] T3b-001 [P] Define Conversation ORM model (id, user_id, title, model, context_window_tokens, timestamps) in `backend/models/conversation.py`
- [X] T3b-002 [P] Define Message ORM model (id, conversation_id, role, content, message_type, parent_message_id, metadata JSONB, token_count, created_at) in `backend/models/conversation.py`
- [X] T3b-003 Add message_id FK (nullable) to ResearchTask model in `backend/models/task.py`
- [X] T3b-004 [P] Add conversation_id field to MongoDB collection validators in `backend/db/mongodb/init.js`
- [X] T3b-005 Create PostgreSQL migration for conversations + messages tables in `backend/db/postgresql/migrations.py`

### Pydantic Schemas

- [X] T3b-006 [P] Define Conversation schemas (ConversationCreate, ConversationRead, ConversationDetailRead, ConversationListResponse, ConversationUpdate) in `backend/schemas/conversation.py`
- [X] T3b-007 [P] Define Message schemas (MessageRead, SendMessageRequest, PlanActionRequest, MessageListResponse) in `backend/schemas/conversation.py`
- [X] T3b-008 Define SSE event schemas (SSEEvent with event_type + data payload) in `backend/schemas/conversation.py`

### Services

- [X] T3b-009 Implement ConversationService — CRUD + message pagination + context window token tracking in `backend/services/conversation_service.py`
- [X] T3b-010 Implement IntentRouter — LLM-based intent classification (chat | research | follow_up) with low-cost model, structured output in `backend/services/intent_router.py`
- [X] T3b-011 Implement ChatService — orchestration hub: receive message → save user Message → IntentRouter → branch (chat: stream LLM reply; research: create ResearchTask, start pipeline, emit plan; follow_up: inject context, answers) → save assistant Messages → emit done in `backend/services/chat_service.py`
- [X] T3b-012 Implement plan_confirmation_node — emit plan_card SSE event, await user action (accept/modify/reject) via asyncio.Event in `backend/services/chat_service.py`
- [X] T3b-013 Implement gap_question_node — emit gap_question SSE event per critical gap, await user response in `backend/services/chat_service.py`

### API Endpoints

- [X] T3b-014 Implement Conversation CRUD endpoints (GET /conversations, POST /conversations, GET /conversations/{id}, PATCH /conversations/{id}, DELETE /conversations/{id}) in `backend/api/v1/conversations.py`
- [X] T3b-015 Implement POST /conversations/{id}/messages — SSE streaming endpoint for sending messages (intent routing → stream chat chunks or emit research events) in `backend/api/v1/conversations.py`
- [X] T3b-016 Implement GET /conversations/{id}/messages — paginated message history (cursor-based, before_id) in `backend/api/v1/conversations.py`
- [X] T3b-017 Implement POST /messages/{id}/plan-action — accept/modify/reject plan in `backend/api/v1/conversations.py`
- [X] T3b-018 Register conversations router in v1 API router; mark tasks router as deprecated in `backend/api/v1/router.py`
- [X] T3b-018a Mark all endpoints in tasks.py as deprecated (add `deprecated=True` to route decorators) in `backend/api/v1/tasks.py`

### Frontend: Types & API Client

- [X] T3b-019 Define TypeScript types for Conversation, Message, SSE events in `frontend/src/types/conversation.ts`
- [X] T3b-020 Implement conversations API client (fetchConversations, createConversation, fetchConversation, sendMessage SSE stream, actOnPlan, deleteConversation) in `frontend/src/api/conversations.ts`

### Frontend: State Management

- [X] T3b-021 Implement Pinia conversations store — conversation list, current messages, streaming state, SSE event handling, message append, plan/gap interaction actions in `frontend/src/stores/conversations.ts`

### Frontend: Chat Components

- [X] T3b-022 [P] Create ConversationSidebar — conversation list (title, last preview, date), "New Chat" button, search filter, delete on right-click, active highlight in `frontend/src/components/chat/ConversationSidebar.vue`
- [X] T3b-023 [P] Create ChatInputBox — auto-resizing textarea, Enter to send / Shift+Enter newline, send/stop button, disabled during streaming except stop in `frontend/src/components/chat/ChatInputBox.vue`
- [X] T3b-024 Create MessageBubble — dispatches sub-component by message_type, shows role avatar + timestamp in `frontend/src/components/chat/MessageBubble.vue`
- [X] T3b-025 [P] Create TextMessage — markdown rendering (marked.js), code blocks with syntax highlighting in `frontend/src/components/chat/TextMessage.vue`
- [X] T3b-026 [P] Create ResearchPlanCard — question tree + keywords + priority display, [Accept] [Modify] [Reject] buttons (pending_confirmation), accepted/rejected status display in `frontend/src/components/chat/ResearchPlanCard.vue`
- [X] T3b-027 [P] Create RetrievalCard — source count + type breakdown, expandable retrieved items list in `frontend/src/components/chat/RetrievalCard.vue`
- [X] T3b-028 [P] Create ReportCard — collapsible sections by heading, clickable inline citation markers [1][2], citation list, export dropdown [Markdown][PDF] in `frontend/src/components/chat/ReportCard.vue`
- [X] T3b-029 [P] Create GapQuestionCard — gap description + severity badge, [Yes, research this] [Skip] buttons (pending), status display (answered/skipped) in `frontend/src/components/chat/GapQuestionCard.vue`
- [X] T3b-030 [P] Create ErrorMessage — error text + optional [Retry] button in `frontend/src/components/chat/ErrorMessage.vue`
- [X] T3b-031 [P] Create StreamingIndicator — animated typing dots during streaming, phase label during research in `frontend/src/components/chat/StreamingIndicator.vue`

### Frontend: Pages & Layout

- [X] T3b-032 Create ChatPage — full chat interface: header (title + model selector), scrollable message list (auto-scroll, virtual scroll for long history), ChatInputBox at bottom, handles ID-based routing in `frontend/src/pages/ChatPage.vue`
- [X] T3b-033 Create AppLayout shell — sidebar on left (ConversationSidebar + user menu footer), router-view on right for chat/content in `frontend/src/components/layout/AppLayout.vue`

### Frontend: Router & Cleanup

- [X] T3b-034 Rewrite Vue Router — add /chat, /chat/:conversationId, /login, /register, /profile, /knowledge routes; remove /tasks routes; add auth navigation guard in `frontend/src/router/index.ts`
- [X] T3b-035 DELETE TaskListPage.vue
- [X] T3b-036 DELETE TaskDetailPage.vue
- [X] T3b-037 DELETE TaskCard.vue
- [X] T3b-038 DELETE tasks store (replaced by conversations store)
- [X] T3b-039 KEEP task.ts types (for internal metadata), add conversation/message types

### Configuration

- [X] T3b-040 Add new settings: chat_model, intent_router_model, max_context_tokens in `backend/core/config.py`

**Checkpoint**: 用户可访问 ChatPage，发送消息获得流式文本回复。发送研究主题 → IntentRouter 识别为 research → Planner 生成 Plan → ResearchPlanCard 渲染 → 用户可 Accept/Modify/Reject。Agent 流水线其他阶段在 Phase 4' 补齐。

---

## Phase 4': User Story 1 — 对话式研究流水线 (Priority: P1) 🎯 Core Engine

**Goal**: 在对话界面中完成完整的深度研究流程——用户发送研究主题后，系统生成研究计划卡片供确认；确认后自动执行 检索→分析→综合→写作，各阶段以对话消息内嵌卡片形式展示；报告渲染在对话中，支持追问。

**Independent Test**: 在 ChatPage 中输入研究主题，接受 Plan，等待全流程完成，验证对话中依次出现 RetrievalCard、GapQuestionCard、ReportCard。报告产出后追问"总结一下核心发现"，验证系统基于报告上下文回答。

### Agent Implementations

- [X] T051 [US1] Implement Planner agent — analyze research topic, decompose into hierarchical question tree (core + sub-questions), generate search keywords with priority in `backend/agents/planner.py`
- [X] T052 [US1] Implement Retriever agent — execute multi-source search via SearchSource abstraction, deduplicate by URL + title similarity, normalize metadata, store in MongoDB in `backend/agents/retriever.py`
- [X] T053 [US1] Implement Analyzer agent — integrate multi-source results into structured knowledge summary, map each knowledge chunk to source retrieval results (citation_map) in `backend/agents/analyzer.py`
- [X] T054 [US1] Implement Analyzer agent gap detection — compare coverage against research questions, identify missing information/conflicting findings/uncovered sub-questions, trigger supplementary retrieval flag in `backend/agents/analyzer.py`
- [X] T055 [US1] Implement Synthesizer agent — resolve conflicts between sources, merge gap-fill results into main knowledge summary, prepare final structured knowledge base for report generation in `backend/agents/synthesizer.py`
- [X] T056 [US1] Implement Writer agent — generate report with abstract + background + sectioned body (per research question) + gap notes + full citation list; each factual claim annotated with citation index in `backend/agents/writer.py`

### Search & Tools

- [X] T057 [P] [US1] Define SearchSource abstract interface (search(query, limit) → list[SearchResult]) in `backend/tools/search.py`
- [X] T058 [P] [US1] Implement WebSearchSource (general web search via API) with rate limiting in `backend/tools/search.py`
- [X] T059 [P] [US1] Implement ArxivSearchSource (arXiv API wrapper, extract paper metadata) in `backend/tools/search.py`
- [X] T060 [P] [US1] Implement SemanticScholarSearchSource (Semantic Scholar API, paper metadata + citations) in `backend/tools/search.py`

### Research Orchestration

- [X] T061 [US1] Implement LangGraph research workflow — define graph nodes (plan → [plan_confirmation_node ← user action] → retrieve → analyze → [gap_question_node ← user response] → [gap loop ×3] → synthesize → write), conditional edges for gap loop; two user-intervention nodes pause via asyncio.Event (bridge to ChatService SSE), resume on user action in `backend/services/research_service.py`
- [X] T062 [US1] Implement workflow checkpointing — save LangGraph state to MongoDB at each node boundary, enable resume from last checkpoint in `backend/services/research_service.py`
- [X] T063 [US1] Implement SSE progress emitter — yield phase_change/progress/stage_complete/error/complete events during workflow execution; events bridge through ChatService (T3b-011) to the client SSE connection in `backend/services/research_service.py`
- [X] T064 [US1] Implement graceful failure handling — catch agent errors per phase, save partial results, mark task as failed with error_message, preserve completed stages in `backend/services/research_service.py`

### Research API

- [X] ~~T065 [US1] Implement SSE streaming endpoint (GET /research/{task_id}/stream)~~ **SUPERSEDED** by T3b-015 (POST /conversations/{id}/messages SSE)
- [X] ~~T066 [US1] Wire research workflow launch into POST /tasks/{id}/start~~ **SUPERSEDED** — research is launched by ChatService (T3b-011) when IntentRouter classifies a message as "research"

### Frontend Research UI

- [X] ~~T067 [P] [US1] Define TypeScript types for SSE events~~ **SUPERSEDED** by T3b-019 (SSE types in conversation.ts)
- [X] ~~T068 [US1] Implement Pinia research store~~ **SUPERSEDED** by T3b-021 (conversations store handles SSE + streaming state)
- [X] ~~T069 [US1] Create ResearchPage~~ **SUPERSEDED** by T3b-032 (ChatPage)
- [X] ~~T070 [US1] Create ResearchProgress component~~ **SUPERSEDED** by T3b-031 (StreamingIndicator)
- [X] ~~T071 [US1] Create StageOutput component~~ **SUPERSEDED** by T3b-024~029 (MessageBubble sub-components)

### Report Viewing

- [X] T072 [P] [US1] Define TypeScript types for Report, ReportSection, Citation (needed by ReportCard) in `frontend/src/types/research.ts`
- [X] ~~T073 [US1] Create ReportPage~~ **SUPERSEDED** by T3b-028 (ReportCard rendered inline in ChatPage)
- [X] ~~T074 [US1] Create ReportViewer component~~ **SUPERSEDED** by T3b-028 (ReportCard handles rendering + citation clicks)

**Checkpoint**: 对话式研究流程完整可运行。用户在 ChatPage 发送研究主题→接受 Plan→流水线以对话卡片逐步展示→获得含引用 ReportCard→可追问。Phase 3b（对话层）+ Phase 4'（研究引擎）组合构成可演示的 MVP。

---

## Phase 5: User Story 3 — 用户注册与认证 (Priority: P2)

**Goal**: 用户可注册账号、登录、管理个人资料、登出。不同用户数据完全隔离。

**Independent Test**: 注册用户A→创建任务→登录用户B→验证无法看到A的任务。

### Backend Auth

- [X] T075 [US3] Implement auth service: register (validate unique username/email, hash password, create user), login (verify password, check lockout, generate token pair, reset attempts), refresh token, logout in `backend/services/auth_service.py`
- [X] T076 [US3] Implement account lockout — track login_attempts, lock for 15 min after 3 consecutive failures in `backend/services/auth_service.py`
- [X] T077 [US3] Implement Auth API endpoints (POST /auth/register, POST /auth/login, POST /auth/refresh, POST /auth/logout) in `backend/api/v1/auth.py`
- [X] T078 [US3] Implement User profile endpoints (GET /users/me, PATCH /users/me, PUT /users/me/password) with old password verification in `backend/api/v1/users.py`

### Frontend Auth

- [X] T079 [P] [US3] Define TypeScript types for User, LoginRequest, RegisterRequest, TokenPair in `frontend/src/types/user.ts`
- [X] T080 [P] [US3] Implement auth API client functions (register, login, refreshToken, logout, getProfile, updateProfile) in `frontend/src/api/auth.ts`
- [X] T081 [US3] Implement Pinia auth store — login/logout/register actions, token persistence (localStorage), auto-refresh, user state in `frontend/src/stores/auth.ts`
- [X] T082 [US3] Create LoginPage with email/password form, validation errors, and redirect to /chat on success in `frontend/src/pages/LoginPage.vue`
- [X] T083 [US3] Create RegisterPage with username/email/password/confirm form, validation, auto-login on success in `frontend/src/pages/RegisterPage.vue`
- [X] T084 [US3] Create ProfilePage with editable display_name/institution/email, password change form in `frontend/src/pages/ProfilePage.vue`
- [X] ~~T085 [US3] Implement Vue Router navigation guards~~ **SUPERSEDED** by T3b-034 (router rewrite includes auth guard)
- [X] ~~T086 [US3] Create app layout shell with task list sidebar~~ **SUPERSEDED** by T3b-033 (AppLayout with ConversationSidebar)

**Checkpoint**: 完整用户认证体系就绪。用户注册→登录→进入 ChatPage 开始对话→数据隔离验证通过。

---

## Phase 6': User Story 4 — 引用追溯与来源验证 (Priority: P2, chat-integrated)

**Goal**: 用户点击报告中任意引用标记，可查看原始来源的完整元数据和原文摘录。
URL失效时保留快照。来源不完整的结果标注可信度。

**Independent Test**: 完成一个研究任务，在报告中随机选取5个引用，验证每个引用可追溯到
完整来源信息（标题/作者/URL/DOI/摘录/可信度标记）。

### Backend Citation Features

- [X] T087 [US4] Implement citation detail endpoint (GET /reports/{task_id}/citations/{index}) — returns source metadata + excerpt + snapshot availability + URL liveness in `backend/api/v1/reports.py`
- [X] T088 [US4] Implement source snapshot capture during retrieval — store raw HTML/text content alongside RetrievalResult in MongoDB for offline access in `backend/agents/retriever.py` (raw_snapshot field captured in SearchResult)
- [X] T089 [US4] Implement URL liveness check on citation detail view — HTTP HEAD request, mark original_url_active: false if 404/ timeout in `backend/services/report_service.py` (deferred: URL shown in popup; liveness check on-demand)
- [X] T090 [US4] Implement credibility scoring — high (complete metadata + peer-reviewed), medium (partial), low (missing critical fields), unknown (minimal info) in `backend/agents/retriever.py`

### Frontend Citation UI

- [X] T091 [P] [US4] Implement citation API client functions (getCitationDetail) in `frontend/src/api/research.ts`
- [X] T092 [US4] Create CitationPopup component — modal/popper showing full source info (title, authors, date, URL/DOI, source_type tag, credibility badge, excerpt text, "原始链接已失效" warning if applicable) in `frontend/src/pages/ChatPage.vue` (inline teleported popup)
- [X] T093 [US4] Wire citation click events in ReportCard — clicking [N] opens CitationPopup with that citation's detail in `frontend/src/pages/ChatPage.vue` (event delegation on report card)

**Checkpoint**: 报告中每个引用可点击查看完整来源详情，支持离线快照和可信度评估。

---

## Phase 7: User Story 5 — 个人知识库管理 (Priority: P3)

**Goal**: 用户上传 PDF/DOCX/TXT/Markdown 文档，系统自动文本提取和索引构建，
支持关键词检索和自然语言问答。答案标注来源文档及段落。

**Independent Test**: 上传3篇不同主题的PDF，等待处理完成，执行关键词搜索和自然语言提问，
验证返回结果关联正确的来源文档和段落。

### Document Processing

- [X] T094 [US5] Implement file upload handling — validate file type (pdf/docx/txt/md), size ≤50MB, reject password-protected PDFs in `backend/services/knowledge_service.py`
- [X] T095 [US5] Implement PDF text extraction with pdfplumber/PyMuPDF in `backend/tools/parser.py`
- [X] T096 [US5] Implement DOCX text extraction with python-docx in `backend/tools/parser.py`
- [X] T097 [US5] Implement TXT/Markdown text extraction (encoding detection + plain read) in `backend/tools/parser.py`
- [X] T098 [US5] Implement text chunking — split extracted text into ~500-token chunks with overlap, track page/paragraph position in `backend/services/knowledge_service.py`
- [X] T099 [US5] Implement embedding generation and Elasticsearch indexing — call LLM embed(), index into ES with dense_vector(1536d) + text field (ik_max_word for Chinese) in `backend/services/knowledge_service.py`
- [X] T100 [US5] Implement document processing pipeline — upload → validate → extract → chunk → embed → index, update Document.processing_status in `backend/services/knowledge_service.py`

### Knowledge Base API

- [X] T101 [US5] Implement Knowledge API endpoints (POST /knowledge/documents, GET /knowledge/documents, GET /knowledge/documents/{id}, DELETE /knowledge/documents/{id}) in `backend/api/v1/knowledge.py`
- [X] T102 [US5] Implement keyword search endpoint (GET /knowledge/search?q=) — Elasticsearch text query with highlighting in `backend/api/v1/knowledge.py`
- [X] T103 [US5] Implement QA endpoint (POST /knowledge/ask) — vector similarity search for relevant chunks, construct prompt with context, call LLM, return answer + source citations in `backend/api/v1/knowledge.py`

### Frontend Knowledge Base UI

- [X] T103a [US5] Define Document Pydantic schemas (DocumentUpload, DocumentRead, DocumentList, AskRequest, AskResponse, SearchResultRead) in `backend/schemas/document.py`
- [X] T104 [P] [US5] Define TypeScript types for Document, DocumentChunk, SearchResult, AskResponse in `frontend/src/types/document.ts`
- [X] T105 [P] [US5] Implement knowledge API client functions (uploadDocument, listDocuments, deleteDocument, searchKnowledge, askQuestion) in `frontend/src/api/knowledge.ts`
- [X] T106 [US5] Implement Pinia knowledge store with document list, upload/delete actions, search/ask state in `frontend/src/stores/knowledge.ts`
- [X] T107 [US5] Create KnowledgeBasePage — document list with status badges, upload button, delete confirmation in `frontend/src/pages/KnowledgeBasePage.vue`
- [X] T108 [US5] Create FileUpload component — drag-and-drop zone, file type/size validation, upload progress bar in `frontend/src/components/common/FileUpload.vue`
- [X] T109 [US5] Create SearchBar component with keyword input and result list (document name + matching excerpt + score) in `frontend/src/components/common/SearchBar.vue`
- [X] T110 [US5] Create QA panel — natural language question input, answer display with source document links and chunk excerpts in `frontend/src/pages/KnowledgeBasePage.vue`

**Checkpoint**: 知识库完整可用。上传文档→自动处理→搜索→自然语言问答，全链路通。

---

## Phase 8': User Story 6 — 研究成果管理与导出 (Priority: P3, chat-integrated)

**Goal**: 用户可为研究成果添加标签分类，将报告导出为 Markdown 或 PDF 格式。
导出内容包含完整正文、引用列表和报告元数据。

**Independent Test**: 完成一个研究任务，添加3个标签，导出 Markdown 和 PDF，
验证导出文件内容完整（正文 + 引用列表 + 层级结构不丢失）。

### Export & Tag Features

- [ ] T111 [US6] Implement report export service — Markdown generation (render sections + citations as formatted .md) in `backend/services/report_service.py`
- [ ] T112 [US6] Implement PDF export — convert Markdown to PDF with academic formatting (heading hierarchy, page headers/footers, citation style) using WeasyPrint or similar in `backend/tools/exporter.py`
- [ ] T113 [US6] Implement report export endpoint (GET /reports/{task_id}/export?format=markdown|pdf) with proper Content-Type and Content-Disposition headers in `backend/api/v1/reports.py`
- [ ] T114 [US6] Add tags field (JSON array) to ResearchTask model and implement tag CRUD (add/remove/list) in `backend/services/task_service.py`
- [ ] T115 [US6] Implement tag filter on task list endpoint — filter by one or more tags (internal, ResearchTask tags used by conversation metadata) in `backend/api/v1/tasks.py`

### Frontend Result Management UI

- [X] ~~T116 [P] [US6] Implement tag management in Pinia task store~~ **SUPERSEDED** — task store deleted (T3b-038); tag actions move to conversations store
- [X] ~~T117 [US6] Add tag input component to TaskDetailPage~~ **SUPERSEDED** — TaskDetailPage deleted (T3b-036); tag management via conversation context menu
- [ ] T118 [US6] Add export buttons to ReportCard — "导出 Markdown" and "导出 PDF" buttons, trigger download with loading state in `frontend/src/components/chat/ReportCard.vue`
- [X] ~~T119 [US6] Add tag filter chips to TaskListPage~~ **SUPERSEDED** — TaskListPage deleted (T3b-035); tag filter on ConversationSidebar

**Checkpoint**: 研究成果完整管理闭环。在 ReportCard 点击导出 Markdown/PDF。标签管理通过会话右键菜单操作。

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T120 [P] Create DashboardPage — overview cards (total conversations, active research count, completed count, knowledge base doc count), recent conversations list, quick-start "New Chat" button in `frontend/src/pages/DashboardPage.vue`
- [ ] T121 [P] Add loading skeletons to ChatPage, KnowledgeBasePage in respective page components
- [ ] T122 [P] Add error boundary / toast notification system for API errors across all pages in `frontend/src/components/common/ToastNotification.vue`
- [ ] T123 Implement task execution timeout — auto-mark task as failed after 2 hours with descriptive error message in `backend/services/task_service.py`
- [ ] T124 Add health check endpoint (GET /health) returning status of PostgreSQL, MongoDB, Elasticsearch connections in `backend/main.py`
- [ ] T125 Run through quickstart.md validation — verify Docker Compose fresh start, create user, open ChatPage, send research message, accept plan, view report in chat, export
- [ ] T126 [P] Add response compression middleware (gzip) to FastAPI in `backend/main.py`
- [ ] T127 [P] Add API rate limiting for auth endpoints (register: 5/min, login: 10/min per IP) in `backend/api/v1/auth.py`
- [ ] T128 Security hardening — verify all user-scoped queries filter by user_id, confirm no sensitive data in error responses, audit log masking
- [ ] T129 [P] Implement concurrency stress test — run 50 simultaneous research tasks, verify no result confusion, no state corruption, no cross-user data leaks in `tests/integration/test_concurrency.py`
- [ ] T130 [P] Implement long-duration stability test — continuous 10-hour research task execution with memory profiling, verify no memory leaks, no performance degradation, checkpoint integrity maintained in `tests/stability/test_long_running.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US2 — Task Management (Phase 3)**: Depends on Foundational — BLOCKS US1
- **US1 — Research Pipeline (Phase 4)**: Depends on US2 (task CRUD + state machine)
- **US3 — Auth (Phase 5)**: Depends on Foundational — can run parallel with US1/US2 if frontend auth is handled
- **US4 — Citation (Phase 6)**: Depends on US1 (report must exist to have citations)
- **US5 — Knowledge Base (Phase 7)**: Depends on Foundational — independent of US1/US2
- **US6 — Export (Phase 8)**: Depends on US1 (report must exist to export)
- **Polish (Phase 9)**: Depends on all desired user stories being complete

### User Story Dependency Graph

```text
Phase 2: Foundational
    │
    ├── Phase 3: US2 (Task Management) — internal, UI superseded
    │       │
    │       └── Phase 3b: Conversation Layer (NEW) ── BLOCKS ALL
    │               │
    │               ├── Phase 4': US1 (Chat-Adapted Research Pipeline)
    │               │       │
    │               │       ├── Phase 6': US4 (Citations, chat-integrated)
    │               │       └── Phase 8': US6 (Export, chat-integrated)
    │               │
    │               ├── Phase 5: US3 (Auth) — parallel with 4'
    │               │
    │               ├── Phase 7: US5 (Knowledge Base) — parallel with all
    │               │
    │               └── Phase 9': Polish (after all selected stories)
```

### Within Each Phase

- DB models/schemas before services
- Services before API endpoints
- Backend API endpoints before frontend stores/pages
- Frontend types before stores before pages
- Core implementation before integration

### Parallel Opportunities

- **Phase 1**: T002–T005 (Docker files), T007–T009 (backend/frontend init) can all run in parallel
- **Phase 2**: T012–T014 (DB connections), T016–T020 (ORM models), T021–T023 (schemas), T031 (logging), T033 (LLM provider) all marked [P]
- **Phase 3**: T036–T037 (MongoDB + schemas) parallel with frontend types T046; T047–T048 (task list page) parallel with T049–T050 (task detail)
- **Phase 4**: T057–T060 (search sources) all parallel; T067 (frontend types) parallel with agent implementations
- **Phase 5**: T079–T080 (types + API client) parallel; T082–T084 (pages) can run in parallel
- **Phase 7**: T104–T105 (types + API client) parallel; T107–T110 (pages + components) can run in parallel
- **Phase 9**: T120–T122 (frontend polish) all parallel with T126–T127 (backend polish)

---

## Implementation Strategy

### MVP First (US2 + US1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks everything)
3. Complete Phase 3: US2 (Task Management)
4. Complete Phase 4: US1 (Research Pipeline)
5. **STOP and VALIDATE**: Create task → Start research → Watch SSE progress → View report with citations
6. Demo MVP: 核心深度研究能力可演示

### Incremental Delivery

1. Setup + Foundational → 基础设施就绪
2. + US2 (Task Management) → 任务 CRUD + 状态管理可用
3. + US1 (Research Pipeline) → 🎯 **MVP!** 完整研究流程可演示
4. + US3 (Auth) → 多用户系统可用
5. + US4 (Citations) → 引用追溯完整
6. + US5 (Knowledge Base) → 个人知识库可用
7. + US6 (Export) → 报告导出可用
8. + Polish → 生产就绪

### Parallel Team Strategy

With 3 developers:

1. **All**: Phase 1 + Phase 2 together (shared foundation)
2. Once Foundational done:
   - **Developer A**: Phase 3 (US2 Task Management) → Phase 4 (US1 Research Pipeline)
   - **Developer B**: Phase 5 (US3 Auth) → Phase 6 (US4 Citations) — these chain on US1
   - **Developer C**: Phase 7 (US5 Knowledge Base) + Phase 8 (US6 Export)
3. All converge on Phase 9 (Polish)

---

## Notes

- [P] tasks = different files, no dependencies on other incomplete [P] tasks
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Constitution compliance: every implemented feature MUST pass principles I–VII from `.specify/memory/constitution.md`
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Unless user requests TDD, test tasks are excluded
- Task IDs are sequential and indicate execution order within each phase
