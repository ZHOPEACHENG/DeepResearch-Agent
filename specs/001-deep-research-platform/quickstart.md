# Quickstart: 深度研究平台

**Date**: 2026-06-04
**Branch**: `001-deep-research-platform`

## 前置要求

| 软件 | 最低版本 | 用途 |
|------|---------|------|
| Docker | 24.0+ | 容器运行时 |
| Docker Compose | 2.20+ | 多容器编排 |
| Git | 2.40+ | 版本控制 |

## 快速启动

### 1. 克隆项目

```bash
git clone <repository-url>
cd DeepResearch-Agent
git checkout 001-deep-research-platform
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，填写必要配置：

```ini
# LLM API 配置（必填）
LLM_API_KEY=your-api-key-here
LLM_API_BASE=https://api.openai.com/v1
LLM_MODEL=gpt-4o
LLM_EMBED_MODEL=text-embedding-3-small
CHAT_MODEL=gpt-4o
INTENT_ROUTER_MODEL=gpt-4o
MAX_CONTEXT_TOKENS=128000

# 搜索 API 配置（必填）
SEARCH_API_KEY=your-search-api-key

# 数据库密码（可选修改）
POSTGRES_PASSWORD=research_dev
MONGO_ROOT_PASSWORD=research_dev

# JWT 密钥（生产环境务必修改）
JWT_SECRET_KEY=change-me-in-production
```

### 3. 启动服务

```bash
docker compose -f docker/docker-compose.yml up -d
```

首次启动会自动执行：
- 拉取基础镜像并构建应用镜像
- 初始化 PostgreSQL 数据库表结构
- 创建 Elasticsearch 索引
- 启动所有服务

### 4. 验证启动

```bash
# 检查服务健康状态
docker compose -f docker/docker-compose.yml ps

# 后端 API 健康检查
curl http://localhost:8000/api/v1/health

# 前端页面
# 浏览器打开 http://localhost:3000
```

预期输出：
```
backend-1      Up 30s    healthy    0.0.0.0:8000->8000/tcp
frontend-1     Up 30s    healthy    0.0.0.0:3000->3000/tcp
postgres-1     Up 30s    healthy    0.0.0.0:5432->5432/tcp
mongodb-1      Up 30s    healthy    0.0.0.0:27017->27017/tcp
elasticsearch-1 Up 30s   healthy    0.0.0.0:9200->9200/tcp
```

## 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| Frontend | 3000 | Vue 3 开发服务器 |
| Backend API | 8000 | FastAPI 服务 + Swagger UI (`/docs`) |
| PostgreSQL | 5432 | 业务数据库 |
| MongoDB | 27017 | 研究过程数据 |
| Elasticsearch | 9200 | 全文检索引擎 |

## 基本使用流程

### 1. 注册账号

打开 http://localhost:3000 ，点击"注册"并填写信息。

或通过 API：
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "researcher", "email": "researcher@example.com", "password": "secure_password_123"}'
```

### 2. 开始对话

打开 http://localhost:3000/chat，在输入框中自由输入文字。发送消息后，系统自动创建会话。

或通过 API：

```bash
# 创建新会话
curl -X POST http://localhost:8000/api/v1/conversations \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json"

# 发送消息（SSE 流式响应）
curl -N http://localhost:8000/api/v1/conversations/<conversation_id>/messages \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"content": "大语言模型在医学诊断中的应用"}'
```

### 3. 发起深度研究

在对话中发送研究主题，系统自动识别为"研究"意图，生成研究计划卡片。点击"接受"后，研究流水线启动。

### 4. 监控研究进度

在对话界面中查看实时进度（研究卡片逐步渲染），或通过 API 发送消息获取 SSE 流式响应。

### 5. 查看研究报告

研究完成后，在报告页查看完整报告及引用追溯。

### 6. 上传知识库文档

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/documents \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@/path/to/paper.pdf"
```

## 开发环境

### 后端开发

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 启动依赖服务（仅数据库）
docker compose -f docker/docker-compose.yml up -d postgres mongodb elasticsearch

# 启动开发服务器
uvicorn main:app --reload --port 8000

# 运行测试
pytest tests/ -v
```

### 前端开发

```bash
cd frontend
npm install

# 启动开发服务器
npm run dev

# 运行测试
npm run test
```

## 停止服务

```bash
# 停止所有服务
docker compose -f docker/docker-compose.yml down

# 停止并清除数据卷（谨慎！）
docker compose -f docker/docker-compose.yml down -v
```

## 常见问题

**Q: Elasticsearch 启动失败，提示 `max virtual memory areas vm.max_map_count [65530] is too low`**

```bash
# Linux 宿主机执行
sudo sysctl -w vm.max_map_count=262144

# 永久生效
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
```

**Q: 研究计划生成后流水线不继续**

检查：
1. `.env` 中的 `LLM_API_KEY` 是否已配置
2. 外部 API 是否可访问（网络连接）
3. 查看后端日志：`docker compose -f docker/docker-compose.yml logs backend`

**Q: 文档上传后一直显示 processing**

检查：
1. 文件格式是否在支持列表中（PDF/DOCX/TXT/MD）
2. PDF 是否受密码保护
3. 文件大小是否超过 50MB
4. 查看后端日志获取详细错误信息
