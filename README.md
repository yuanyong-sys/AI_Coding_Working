# 无人机低空智慧调度平台 POC

本仓库交付一个完全本地运行的演示基线。所有页面和数据均明确标识为 **POC 演示数据**，不连接真实无人机、飞控或生产系统。

## 运行

要求 Node.js 22 或更高版本，运行应用无需安装第三方依赖。完整测试使用本机 Google Chrome；其他安装位置可通过 `CHROME_PATH` 指定。

```bash
npm start
```

打开 <http://127.0.0.1:8765/>。可通过 `PORT` 修改端口，通过 `POC_DATABASE_PATH` 修改本地持久化文件位置。

## 验证

```bash
npm test
npm run typecheck
```

测试覆盖四个公开页面、统一状态接口、跨服务重启持久化，以及标准快照恢复的确认、幂等性和审计。

## HTTP 接口

- `GET /api/state`：读取无人机、任务、告警、台账和审计的统一状态。
- `GET /api/audit`：读取本地审计记录。
- `PATCH /api/{drones|tasks|alerts|ledgers}/{id}`：更新演示业务对象。
- `POST /api/demo/reset`：请求体必须包含 `{ "confirmed": true }`，恢复 `POC-DEMO-V1` 标准快照并写入审计。
