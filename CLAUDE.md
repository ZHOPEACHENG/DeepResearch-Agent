# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other information, read the current plan
at `specs/001-deep-research-platform/plan.md`.

**Tech Stack**: Vue 3 + TypeScript + Vite + Pinia + Element Plus (frontend),
FastAPI + LangGraph (backend), PostgreSQL + MongoDB + Elasticsearch (data),
Docker Compose (deployment).

**Quickstart**: See `specs/001-deep-research-platform/quickstart.md`.
<!-- SPECKIT END -->

## Spec Kit 工作流

本仓库由 **Spec Kit** 管理（`.specify/`、`specs/<feature>/`）。设计产物位于
`specs/001-deep-research-platform/`（`spec.md`、`plan.md`、`tasks.md`、
`data-model.md`、`contracts/`、`research.md`）。上方的 `<!-- SPECKIT -->` 块由
`speckit-agent-context-update` 技能托管 —— 请通过 `speckit-*` 技能编辑 Spec Kit
内容，而非手动修改。

**重要**：`plan.md` 描述的是*预期*架构。当前代码处于 Phase 3b 检查点阶段，与计划
有多处偏离 —— 见下文「计划与实现的差异」。判断当前实际存在什么时以代码为准；
判断方向意图时以计划为准。

## 常用命令

后端命令需在**仓库根目录**运行（Python 包 `deepresearch` 由根目录的 `pyproject.toml`
定义；`backend/` 是可导入包，因此应用入口是 `backend.main:app`，而非 `main:app`）。

```bash
# 后端 —— 安装（已提供 uv.lock，使用 uv 的用户可改用 `uv sync`）
pip install -e ".[dev]"

# 后端 —— 启动开发服务器（需先运行 postgres + mongo + es，见下方 Docker）
uvicorn backend.main:app --reload --port 8000

# 后端 —— lint（ruff，行长 100，py311）
ruff check backend/
ruff check backend/ --fix

# 后端 —— 测试。注意：tests/ 目前为空 —— 尚无任何测试文件。
# pytest 已配置（asyncio_mode=auto，testpaths=["tests"]），但不会收集到任何用例。
pytest -v
pytest tests/integration/test_api_auth.py::test_login -v   # 单个用例
```

```bash
# 前端（在 frontend/ 下）
npm install
npm run dev      # 开发服务器 :3000，/api 代理到 http://localhost:8000
npm run build    # 先类型检查（vue-tsc --noEmit，strict TS）再 vite build
# package.json 未定义 test/lint 脚本 —— 计划中提到的 Vitest 尚未接入。
```

```bash
# 通过 Docker Compose 启动全栈（在仓库根目录）
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps
curl http://localhost:8000/api/v1/health   # 注意：健康检查在 /api/v1/health，不是 /health
docker compose -f docker/docker-compose.yml down      # 停止
docker compose -f docker/docker-compose.yml down -v   # 停止并清除数据卷
```

端口：前端 3000 · 后端 8000（Swagger 在 `/api/v1/docs`）· postgres 5432 ·
mongo 27017 · elasticsearch 9200。必需环境变量：`LLM_API_KEY` 和
`SEARCH_API_KEY`（见 `.env.example`）。ES 需要 `ik` 分词插件（自定义
`docker/Dockerfile.elasticsearch`），Linux 宿主机需设置
`vm.max_map_count=262144`。

## 架构

### 三库分离 + 异步单例

`backend/core/database.py` 持有三个存储的模块级单例连接，每个都有
`*_connect` / `*_disconnect` / `*_health` 函数。三者均为**异步**，生命周期由
`backend/main.py` 中的 FastAPI `lifespan` 驱动：

- **PostgreSQL**（SQLAlchemy async + asyncpg）—— 业务数据：users、
  conversations、messages、research_tasks、reports、citations、documents。ORM
  模型在 `backend/models/`，全部继承 `core/database.py` 的 `Base`。
- **MongoDB**（motor）—— 研究过程数据：Agent 执行日志、中间结果、检查点。
  `backend/db/mongodb/`。
- **Elasticsearch**（elasticsearch-py async）—— 知识库全文检索。
  `backend/db/elasticsearch/mappings.py`。**非关键**：平台在无 ES 时仍可运行
  （知识库搜索会返回空，直到 ES 就绪）；索引创建失败仅记日志，不致命。

通过 `get_postgres_session()`（返回 `AsyncSession`，需作为 `async with` 上下文
管理器使用）、`get_mongo_db()`、`get_es_client()` 获取句柄。若在 `*_connect()`
之前调用会抛 `RuntimeError`。

### 迁移是幂等的启动 SQL，而非 Alembic

虽然 `alembic` 是依赖项，但迁移实际是 `backend/db/postgresql/migrations.py` 中
的**原始 `CREATE TABLE IF NOT EXISTS` SQL**，在每次应用启动时于 `lifespan` 内
运行。MongoDB 集合（`ensure_collections()`）和 ES 索引（`create_indices()`）同样
在启动时幂等创建。不存在 `alembic` 迁移链 —— 不要期待 `alembic upgrade head`。

### 以对话为中心的 SSE 流（架构核心）

产品的交互界面是对话 UI，而非任务看板。一条用户消息的流转：

1. `POST /api/v1/conversations/{id}/messages`（`backend/api/v1/conversations.py`）
   返回 `text/event-stream` 的 `StreamingResponse`。
2. `backend/services/chat_service.py::handle_message` 是一个**异步生成器**，逐个
   yield SSE 事件字典：`message_created` →
   （`chat_chunk`* | `plan_generated` | `error`）→ `done`。API 层把每个字典序列化为
   `event: <name>\ndata: <json>\n\n`。
3. **模式分支**（用户显式选择，无 LLM 分类）：请求体 `mode`（`"chat"` | `"research"`，
   默认 `"chat"`）由前端「深度研究」开关设定，经 API 透传到 `handle_message(..., mode=...)`。
   - **chat** → 加载近期历史 → 逐 token 流式输出 LLM 回复（`chat_chunk`）→ 保存
     assistant Message。
   - **research** → 创建隐藏的 `ResearchTask` → 生成计划 → 发出带 `plan_card` 消息
     的 `plan_generated` 事件 → **停止**（见下）。
4. **前端**（`frontend/src/api/conversations.ts::sendMessageStream`）使用**原生
   `fetch` + `ReadableStream`**，而非 axios —— axios 不支持流式响应。它解析
   `event:`/`data:` 行并分发到 Pinia store（`frontend/src/stores/conversations.ts`）。
   token 在流开始时读取一次；若 access token 在流中途过期，连接会中断（预期后端在
   30 分钟 access-token TTL 内完成）。

**计划动作暂停**：研究流水线通过按 `message_id` 索引的内存 `asyncio.Event` 暂停，
等待用户 accept/modify/reject（`chat_service.py` 中的 `_plan_events`/
`_plan_actions`）。`POST /conversations/messages/{id}/plan-action` 调用
`set_plan_action()`，进而唤醒 `wait_for_plan_action()`。该状态**仅在内存中** ——
后端重启即丢失，且只在单进程内有效（无法水平扩展）。

### Message 模型驱动 UI 渲染

`backend/models/conversation.py::Message` 拥有 `message_type` 枚举
（`text`、`plan_card`、`retrieval_card`、`report_card`、`citation`、`error`、
`gap_question`）和 JSONB 列 `extra`（数据库列名为 `metadata`），存放每种卡片的
类型化载荷。`parent_message_id` 提供回复线程关系。新增卡片类型 = 新增
`message_type` + 一个渲染该 `extra` 载荷的前端组件。会话列表端点内嵌关联标量子查询
以获取 `message_count` 和 `last_message_preview`（API 层用这些字段构造
`ConversationRead(**dict)` —— 保持字典形状同步）。

### 认证：JWT 轮换 + token_version 吊销

`backend/core/security.py` 签发 access（30 分钟）+ refresh（7 天）JWT。
`backend/api/deps.py::get_current_user` 解码 JWT、查 `User`，并**拒绝 `ver` 声明
早于 `user.token_version` 的 access token**。登出时递增 `token_version`，立即
吊销所有已发出的 access token（消除 30 分钟窗口）。前端
（`frontend/src/api/client.ts`）有 axios 响应拦截器：遇 401 时刷新一次、把并发请求
排队、重试，并通过 `storage` 事件跨标签页同步 token 清除/跳转。

### 用户数据隔离（双层强制）

每个查询都以注入的 `User` 的 `user_id` 为过滤条件：API 层用
`get_current_active_user`（deps），服务层用 `_get_conv_for_user` / `user_id ==`
过滤。`user_id` 在创建时设定，永不可更新。新增端点时，务必在两层都按已认证用户
的 id 过滤 —— 切勿信任客户端传入的 `user_id`。

### LLM Provider

`backend/tools/llm.py` 定义 `LLMProvider` ABC + `OpenAICompatibleProvider`
（基于 httpx，通过 `LLM_API_BASE` 兼容 OpenAI / Azure / vLLM / Ollama / LiteLLM）。
尽管 `langchain-openai` 是依赖项，运行中的代码是**直接**调用 provider —— **不**使用
LangChain。LLM 失败时最多重试 3 次，指数退避（1s→2s→4s）（规范 FR-016a）。通过
`get_llm_provider()` 单例访问；测试可用 `set_llm_provider()`。

### Agents

`backend/agents/base.py` 定义 `Agent` ABC（`async run(state) → state`）和
`AgentRegistry`。**目前只实现了 `base.py`** —— `plan.md` 中描述的
planner/retriever/analyzer/synthesizer/writer 等 Agent 及 LangGraph 工作流尚未构建。
`chat_service` 中的 research 分支是 Phase 3b 桩：生成计划卡片后在计划动作暂停处停止。
实现真正的流水线时，Agent 注册进 `AgentRegistry` 并通过共享 `state` 字典通信
（宪法 II：单一职责、基于契约的 I/O）。

## 约定

- **后端导入**以 `backend.` 为根（如 `from backend.services import chat_service`）。
  跨顶层子包不要用相对导入。
- **结构化日志**通过 `backend/utils/logging.py::get_logger` —— 发出键/值事件日志
  （`logger.info("event_name", key=val)`），而非自由格式字符串。面向用户的错误路径
  中的消息是**中文**（如 `"对话不存在"`、`"令牌无效或已过期"`）—— 新增错误时保持一致。
- **Ruff** 行长 100，目标 py311，规则 `E,F,I,N,W,UP`。
- **前端**使用路径别名 `@` → `frontend/src`；TypeScript 为 `strict`，
  `npm run build` 遇类型错误会失败。Vite 把 `/api` 代理到后端，因此开发环境下前端
  访问 `/api/v1/...` 不涉及 CORS。
- **配置**：`backend/core/config.py` 是 `pydantic-settings` 的 `Settings` 单例，读取
  `.env`（大小写不敏感，`extra="ignore"`）。新增环境变量时在此添加带默认值的字段，
  切勿直接读取 `os.environ`。
