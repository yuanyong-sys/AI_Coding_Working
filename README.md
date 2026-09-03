# 无人机低空智慧调度平台 POC

当前首条可运行链路展示贵阳市观山湖区的低空运行态势：模拟无人机遥测进入 FastAPI 和 SQLite 后，由 Vue 3 态势总览通过 MapLibre 直接叠加在本地 PMTiles 底图上。

## 环境

- Node.js 24+
- pnpm 10+
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## 安装

```bash
pnpm install
uv sync --directory apps/api
```

## 启动

```bash
pnpm dev
```

该命令同时启动 API 与 Web；按 `Ctrl+C` 会一并关闭。打开 <http://127.0.0.1:5173>。Web 开发服务器将 `/api` 代理到本地 API；地图从同源的 `guanshanhu.pmtiles` 读取，不需要公网或独立瓦片服务。

提交一条模拟遥测：

```bash
curl -X POST http://127.0.0.1:8000/api/telemetry/batches \
  -H 'Content-Type: application/json' \
  -d '{"events":[{"event_id":"demo-001","drone_id":"UAV-GSH-01","longitude":106.6282,"latitude":26.6467,"altitude_m":86,"heading_deg":125,"speed_mps":12.4,"flight_state":"flying","source_time":"2026-09-03T06:32:08Z","source_type":"simulated"}]}'
```

刷新总览即可看到该无人机及“模拟数据”标识。

## 质量门禁

```bash
pnpm format:check
pnpm lint
pnpm test
pnpm test:e2e
pnpm --filter @low-altitude/web build
```

浏览器测试会自动启动临时 API 与 Web 服务，并使用本机 Chrome 验证完整链路。

## 本地地图

地图包的覆盖范围、版本、来源和重新生成方法见 `apps/web/public/maps/README.md`。该底图只用于 POC 技术验证，不得用于导航、执法或监管判断。
