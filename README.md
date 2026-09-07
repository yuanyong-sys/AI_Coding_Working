# 无人机低空智慧调度平台 POC

本仓库采用 Vue 3 + FastAPI + SQLAlchemy 2.0 + SQLite 架构，交付完全本地运行的警务无人机演示平台。所有页面和数据均明确标识为 **POC 演示数据**，不连接真实无人机、飞控、视频或生产系统。

## 工程结构

- `frontend/src/domains/`：Vue 业务域页面；Pinia 和 API 适配位于 `stores/`、`shared/api/`。
- `backend/app/domains/`：FastAPI 业务域、Pydantic schema 与 SQLAlchemy 模型。
- `无人机设计方案/screen-overview.html`：经确认的原始态势大屏视觉资产，由 Vue 路由同源承载，不重新生成。
- `tests/`：真实 Chrome 端到端和 1920×1080 视觉回归。

## 安装与运行

要求 Node.js 18+、Python 3.11+、uv 和 Google Chrome。

```bash
cd backend && uv sync
cd ../frontend && npm install
cd .. && npm start
```

打开 <http://127.0.0.1:8766/>。可通过 `PORT` 修改端口，通过 `DRONE_POC_DATABASE_URL` 修改 SQLite URL。

开发时可分别运行 `cd backend && uv run uvicorn app.main:app --reload --port 8000` 和 `cd frontend && npm run dev`。

## 验证

```bash
npm test
npm run typecheck
```

完整测试覆盖 FastAPI/SQLite 持久化与恢复、Vue 路由和构建、真实 Chrome 页面交互，以及态势大屏视觉基线。

## POC HTTP 接口

- `GET /api/state`：读取无人机、任务、告警、台账和审计的统一状态。
- `GET /api/audit`：读取本地审计记录。
- `PATCH /api/tasks/{id}`：更新演示任务状态。
- `POST /api/demo/reset`：请求体必须包含 `{ "confirmed": true }`，恢复 `POC-DEMO-V1` 标准快照并写入审计。

JWT、真实飞控命令、WebRTC/RTMP 视频链路和生产部署安全机制不属于当前 POC。
