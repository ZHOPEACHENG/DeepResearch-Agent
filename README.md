# Deep Research Platform

面向学术研究场景的自动化深度研究平台。

用户输入研究主题后，系统自动完成研究问题拆解、多源资料检索、知识整合、
知识缺口识别和完整研究报告生成，帮助研究人员高效完成文献调研。

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | Vue 3 + TypeScript + Vite + Element Plus + Pinia |
| 后端 | FastAPI + LangGraph |
| 业务数据 | PostgreSQL |
| 研究过程 | MongoDB |
| 全文检索 | Elasticsearch |
| 部署 | Docker Compose |

## 快速启动

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY 和 SEARCH_API_KEY

# 2. 启动所有服务
docker compose -f docker/docker-compose.yml up -d

# 3. 访问
# 前端: http://localhost:3000
# API 文档: http://localhost:8000/docs
```

详细说明见 [Quickstart Guide](specs/001-deep-research-platform/quickstart.md)。

## 项目结构

```
backend/          FastAPI 后端服务
frontend/         Vue 3 前端应用
docker/           Docker 部署编排
specs/            设计文档和任务规划
tests/            自动化测试
```

## 开发

```bash
# 后端
cd backend
pip install -e ".[dev]"
uvicorn backend.main:app --reload

# 前端
cd frontend
npm install
npm run dev
```
