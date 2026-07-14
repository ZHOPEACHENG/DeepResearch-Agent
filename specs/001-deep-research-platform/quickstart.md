# Quickstart: 深度研究平台

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
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，**至少填写以下必填项**：

```ini
# LLM API 密钥（至少填一个）
DEEPSEEK_API_KEY=sk-your-deepseek-key
# OPENAI_API_KEY=sk-your-openai-key

# LLM API 地址（可选，留空用官方地址）
# DeepSeek 直连: https://api.deepseek.com/v1
# DashScope 代理: https://dashscope.aliyuncs.com/compatible-mode/v1
BASE_URL=

# Embedding API（如与 LLM 共用则留空）
# EMBEDDING_BASE_URL=
# EMBEDDING_API_KEY=

# 搜索 API（必填）
SEARCH_API_KEY=tvly-your-tavily-key
SEARCH_PROVIDER=tavily

# JWT 密钥（生产环境务必修改）
JWT_SECRET_KEY=change-me-in-production
```

### 3. 启动服务

```bash
docker compose -f docker/docker-compose.yml up -d
```

首次启动会自动：
- 拉取基础镜像并构建应用镜像
- 初始化 PostgreSQL 数据库表结构
- 创建 Elasticsearch 索引（含 IK 中文分词）
- 启动所有服务

### 4. 验证启动

```bash
# 检查服务健康状态
docker compose -f docker/docker-compose.yml ps

# 后端健康检查
curl http://localhost:8000/health

# 前端页面 → 浏览器打开 http://localhost:3000
```

预期输出：
```
NAME                       STATUS
deepresearch-postgres      Up (healthy)
deepresearch-mongodb       Up (healthy)
deepresearch-elasticsearch Up (healthy)
deepresearch-backend       Up
deepresearch-frontend      Up
```

## 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| Frontend | 3000 | Vue 3 前端（Nginx） |
| Backend API | 8000 | FastAPI + Swagger UI (`/docs`) |
| PostgreSQL | 5432 | 业务数据库 |
| MongoDB | 27017 | 研究过程数据 |
| Elasticsearch | 9200 | 全文检索引擎 |

## 基本使用流程

### 1. 注册账号

打开 http://localhost:3000 → 注册，填写信息。

或通过 API：
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "researcher", "email": "researcher@example.com", "password": "securePassword1"}'
```

### 2. 仪表盘

登录后进入仪表盘，查看总对话数、已生成报告、知识库文档统计。

### 3. 开始对话

打开 http://localhost:3000/chat，输入框中自由输入文字。

使用开关：
- **知识库**：开启后，对话和研究都会从个人知识库检索相关内容辅助回答
- **深度研究**：开启后，系统生成研究计划 → 检索 → 分析 → 生成含引用的研究报告

或通过 API：

```bash
# 创建对话
curl -X POST http://localhost:8000/api/v1/conversations \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json"

# 发送消息（SSE 流式，开启知识库 + 深度研究）
curl -N http://localhost:8000/api/v1/conversations/<conv_id>/messages \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"content":"固态电池量产技术路线对比","mode":"research","use_knowledge":true}'
```

### 4. 研究报告导出

报告卡片上点击「导出 Markdown」或「导出 PDF」即可下载。

### 5. 知识库管理

- 上传 PDF/DOCX/TXT/MD 到知识库（右上角菜单 → 知识库管理）
- 文档自动完成文本提取、分块、向量化、ES 索引
- 支持混合检索（BM25 + 向量）和自然语言问答

```bash
# API 上传
curl -X POST http://localhost:8000/api/v1/knowledge/documents \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@/path/to/paper.pdf"
```

## 开发环境

### 后端开发

```bash
# 安装依赖
pip install -e ".[dev]"

# 启动依赖服务
docker compose -f docker/docker-compose.yml up -d postgres mongodb elasticsearch

# 启动开发服务器（仓库根目录）
uvicorn backend.main:app --reload --port 8000
```

### 前端开发

```bash
cd frontend
npm install
npm run dev       # :3000，/api 代理到 :8000
```

## 停止服务

```bash
docker compose -f docker/docker-compose.yml down           # 停止
docker compose -f docker/docker-compose.yml down -v        # 停止 + 清除数据卷
```

## 常见问题

**Q: Elasticsearch 启动失败，`max virtual memory areas vm.max_map_count too low`**

```bash
# Linux 宿主机
sudo sysctl -w vm.max_map_count=262144
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
```

**Q: 研究计划生成后流水线不继续**

检查：
1. `.env` 中 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` 是否已配置
2. `BASE_URL` 是否正确（DeepSeek 直连留空）
3. 查看后端日志：`docker compose -f docker/docker-compose.yml logs backend`

**Q: 知识库搜索/问答无结果**

检查：
1. Elasticsearch 是否正常运行
2. 是否已上传文档且处理状态为"已完成"
3. IK 分词插件是否安装成功

**Q: 文档上传后一直显示 processing**

检查：
1. 文件格式是否支持（PDF/DOCX/TXT/MD）
2. PDF 是否受密码保护
3. 文件大小是否超过 50MB（`MAX_UPLOAD_SIZE_MB`）
