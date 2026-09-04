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
LOW_ALTITUDE_DEMO_PASSWORD='请替换为本地演示密码' \
LOW_ALTITUDE_INFERENCE_TOKEN='请替换为本地推理令牌' \
pnpm dev
```

该命令同时启动 API 与 Web；按 `Ctrl+C` 会一并关闭。打开 <http://127.0.0.1:5173>。Web 开发服务器将 `/api` 代理到本地 API；地图从同源的 `guanshanhu.pmtiles` 读取，不需要公网或独立瓦片服务。

平台会初始化三类本地演示账号，共用由 `LOW_ALTITUDE_DEMO_PASSWORD` 注入的密码：

- `situation-viewer`：态势查看者，可查看运行态势和数据智能入口
- `clue-reviewer`：线索研判员，可查看运行态势和 AI异常线索研判入口
- `spatial-admin`：空间管理员，可查看运行态势和空间规则入口

密码不会写入源码，数据库中仅保存加盐哈希。若未设置环境变量，API 会在启动日志中生成并显示本次初始化密码。登录会话有效期为 8 小时，仅通过 HttpOnly、SameSite=Strict Cookie 保存。

## 固定视频推理

保持平台运行，在另一个终端使用与平台相同的本地推理令牌执行：

```bash
LOW_ALTITUDE_INFERENCE_TOKEN='请替换为本地推理令牌' pnpm infer:demo
```

该命令启动独立推理进程，读取仓库内固定预录视频、通过 FFmpeg 抽取关键帧，并以确定性暗区模型生成一条“疑似烟火”AI异常线索。截图作为研判材料写入 `apps/api/var/clue-materials/frames/`，业务数据库只保存受控相对引用；结构化推理结果同时保存在 `apps/api/var/inference-results/latest.json`。使用 `clue-reviewer` 登录后，可在“AI异常线索研判”专题视图查看类型、置信度、来源时间、位置、评测来源和模型版本。

运行推理需要本机可执行的 `ffmpeg`。推理结果仅为待复核线索，不代表确认事件。

提交一条模拟遥测：

```bash
curl -X POST http://127.0.0.1:8000/api/telemetry/batches \
  -H 'Content-Type: application/json' \
  -d '{"events":[{"event_id":"demo-001","sortie_id":"GSH-DEMO-SORTIE-001","drone_id":"UAV-GSH-01","longitude":106.6282,"latitude":26.6467,"altitude_m":86,"heading_deg":125,"speed_mps":12.4,"flight_state":"flying","source_time":"2026-09-03T06:32:08Z","source_type":"simulated"}]}'
```

登录后，总览会自动刷新并显示该无人机及“模拟数据”标识。

运行可重复的正常飞行场景：

```bash
pnpm simulate
```

模拟器按固定来源时间提交 20 架已接入无人机，其中主无人机形成 6 个连续遥测点。重复执行使用相同事件标识，平台按幂等成功处理且不会产生重复航迹；总览通过可恢复的 WebSocket 增量链路持续展示航迹、飞行状态、核心指标和数据时效。

空间管理员登录后可从“空间规则”入口编辑禁飞区或电子围栏草稿，设置 WGS-84 水平范围、高度和有效期，再发布不可变版本。当前生效版本会以不同颜色叠加在地图上。所有登录用户均可从“航前规则校验”提交带高度和时间的计划航线；结果只表达规则判断，并在地图定位命中位置，不代表审批或飞行许可。

飞行中的无人机持续 2 秒进入禁飞区或超出电子围栏后生成越界告警，持续恢复合规 2 秒后结束；水平边界 5 米范围内作为缓冲区，不累计触发或恢复时长，遥测间隔超过 5 秒会中断连续性。可通过 `LOW_ALTITUDE_INCURSION_DURATION_SECONDS`、`LOW_ALTITUDE_INCURSION_MAX_GAP_SECONDS` 和 `LOW_ALTITUDE_INCURSION_BOUNDARY_BUFFER_M` 调整阈值。越界告警保留命中时的规则版本快照，仅表达空间规则命中事实，不作违规认定，也不输出飞行控制指令。

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
