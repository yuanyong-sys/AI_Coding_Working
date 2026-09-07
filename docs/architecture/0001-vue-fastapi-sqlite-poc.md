# ADR 0001：采用 Vue 3、FastAPI 与 SQLite 的 POC 架构

## 决策

前端使用 Vue 3、TypeScript、Vite、Pinia 和 Vue Router，后端使用 Python FastAPI、Pydantic v2、SQLAlchemy 2.0 async 与 aiosqlite。前后端按 `drone`、`mission`、`alert` 等业务域对齐目录。

经确认的态势大屏 HTML 是视觉事实来源。Vue 的 `SituationView` 通过同源 iframe 承载该文件，以避免迁移过程重新生成或改变原型；大屏仍通过 FastAPI 的 `/api/state` 使用同一 SQLite 数据。

## POC 边界

本阶段只重构已完成的可恢复演示基线和态势大屏，不实现 JWT、真实飞控、真实视频链路或生产部署机制。后续业务能力按 GitHub Tickets 独立实现。

## 后果

- 浏览器公开路由保持不变。
- SQLite 成为业务状态的唯一持久化来源。
- 原型视觉与 Vue 应用架构可以分别演进，并由视觉回归测试约束一致性。
- iframe 是 POC 阶段的显式适配边界；是否组件化迁移原型由后续独立决策处理。
