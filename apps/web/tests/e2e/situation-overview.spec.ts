import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

test("模拟无人机显示在观山湖本地态势总览", async ({ page, request }) => {
  const localPmtilesRequests: string[] = [];
  const externalRequests: string[] = [];

  page.on("request", (outboundRequest) => {
    const url = new URL(outboundRequest.url());
    if (url.pathname.endsWith(".pmtiles"))
      localPmtilesRequests.push(url.pathname);
    if (
      ["http:", "https:"].includes(url.protocol) &&
      !["127.0.0.1", "localhost"].includes(url.hostname)
    ) {
      externalRequests.push(outboundRequest.url());
    }
  });

  const ingestResponse = await request.post(
    "http://127.0.0.1:8000/api/telemetry/batches",
    {
      data: {
        events: [
          {
            event_id: "e2e-track-001",
            drone_id: "UAV-GSH-TRACK-01",
            longitude: 106.6282,
            latitude: 26.6467,
            altitude_m: 86,
            heading_deg: 125,
            speed_mps: 12.4,
            flight_state: "flying",
            source_time: "2026-09-02T06:32:08Z",
            source_type: "simulated",
            sortie_id: "GSH-E2E-SORTIE-001",
          },
        ],
      },
    },
  );
  expect(ingestResponse.status()).toBe(202);

  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "进入低空智慧调度平台" }),
  ).toBeVisible();
  await page.getByLabel("账号").fill("situation-viewer");
  await page.getByLabel("密码").fill("local-e2e-password");
  await page.getByRole("button", { name: "登录" }).click();

  await expect(
    page.getByRole("heading", { name: "低空态势总览" }),
  ).toBeVisible();
  await expect(page.getByRole("navigation")).toContainText("数据智能");
  await expect(page.getByRole("navigation")).not.toContainText("空间规则");
  await expect(page.getByTestId("situation-map")).toHaveAttribute(
    "data-center",
    "106.6282,26.6467",
  );
  await expect(page.locator(".maplibregl-canvas")).toBeVisible();
  const droneMarker = page.getByTestId("drone-marker-UAV-GSH-TRACK-01");
  await expect(droneMarker).toBeVisible();
  await expect(droneMarker).toHaveAttribute(
    "data-position",
    "106.6282,26.6467",
  );
  await expect(droneMarker).toContainText("飞行中");

  const moveResponse = await request.post(
    "http://127.0.0.1:8000/api/telemetry/batches",
    {
      data: {
        events: [
          {
            event_id: "e2e-track-002",
            drone_id: "UAV-GSH-TRACK-01",
            longitude: 106.6382,
            latitude: 26.6567,
            altitude_m: 96,
            heading_deg: 140,
            speed_mps: 13.4,
            flight_state: "flying",
            source_time: "2026-09-02T06:32:18Z",
            source_type: "simulated",
            sortie_id: "GSH-E2E-SORTIE-001",
          },
        ],
      },
    },
  );
  expect(moveResponse.status()).toBe(202);
  await expect(droneMarker).toHaveAttribute(
    "data-position",
    "106.6382,26.6567",
  );
  await expect(page.getByTestId("situation-map")).toHaveAttribute(
    "data-track-points",
    "2",
  );
  await expect(page.getByText("航迹 · 2 个遥测点")).toBeVisible();
  await expect(page.getByText("模拟数据", { exact: true })).toBeVisible();
  await expect(page.getByText("2026-09-02 14:32:18")).toBeVisible();

  expect(localPmtilesRequests).toContain("/maps/guanshanhu.pmtiles");
  expect(externalRequests).toEqual([]);
});

test("按角色能力显示功能入口并可退出登录", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("账号").fill("spatial-admin");
  await page.getByLabel("密码").fill("local-e2e-password");
  await page.getByRole("button", { name: "登录" }).click();

  await expect(page.getByRole("navigation")).toContainText("运行态势");
  await expect(page.getByRole("navigation")).toContainText("空间规则");
  await expect(page.getByRole("navigation")).not.toContainText(
    "AI异常线索研判",
  );
  await expect(page.getByText("空间管理员", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(
    page.getByRole("heading", { name: "进入低空智慧调度平台" }),
  ).toBeVisible();
});

test("空间管理员发布空间规则并在地图查看生效版本", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("账号").fill("spatial-admin");
  await page.getByLabel("密码").fill("local-e2e-password");
  await page.getByRole("button", { name: "登录" }).click();
  await page.getByRole("button", { name: "空间规则", exact: true }).click();

  await expect(
    page.getByRole("heading", { name: "空间规则草稿" }),
  ).toBeVisible();
  await page.getByLabel("规则名称").fill("观山湖测试禁飞区");
  await page.getByLabel("最低高度（米）").fill("60");
  await page.getByLabel("最高高度（米）").fill("180");
  await page.getByLabel("生效时间").fill("2026-09-03T00:00");
  await page.getByLabel("失效时间").fill("2027-09-30T00:00");
  await page.getByRole("button", { name: "发布版本" }).click();

  await expect(page.getByText("已发布 v1")).toBeVisible();
  await expect(page.getByTestId("situation-map")).toHaveAttribute(
    "data-spatial-rule-count",
    "1",
  );
  await expect(page.getByText("禁飞区 · 观山湖测试禁飞区")).toBeVisible();
  await expect(page.getByText("版本 v1 · WGS84")).toBeVisible();
});

test("用户提交计划航线并在地图定位航前规则校验结果", async ({
  page,
  request,
}) => {
  await request.post("http://127.0.0.1:8000/api/auth/login", {
    data: { username: "spatial-admin", password: "local-e2e-password" },
  });
  await request.post("http://127.0.0.1:8000/api/spatial-rules/drafts", {
    data: {
      rule_id: "E2E-NFZ-PREFLIGHT",
      name: "航前测试禁飞区",
      rule_type: "no_fly_zone",
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [106.61, 26.63],
            [106.64, 26.63],
            [106.64, 26.66],
            [106.61, 26.66],
            [106.61, 26.63],
          ],
        ],
      },
      min_altitude_m: 60,
      max_altitude_m: 180,
      valid_from: "2026-09-03T00:00:00Z",
      valid_to: "2027-09-30T00:00:00Z",
      source: "Playwright",
    },
  });
  await request.post(
    "http://127.0.0.1:8000/api/spatial-rules/drafts/E2E-NFZ-PREFLIGHT/publish",
  );

  await page.goto("/");
  await page.getByLabel("账号").fill("situation-viewer");
  await page.getByLabel("密码").fill("local-e2e-password");
  await page.getByRole("button", { name: "登录" }).click();
  await page.getByRole("button", { name: "航前规则校验" }).click();
  await page.getByRole("button", { name: "校验计划航线" }).click();

  await expect(page.getByText("进入禁飞区", { exact: true })).toBeVisible();
  await expect(page.getByText("命中版本 E2E-NFZ-PREFLIGHT v1")).toBeVisible();
  await expect(page.getByTestId("preflight-result-position")).toHaveAttribute(
    "data-position",
    "106.61,26.645",
  );
  await expect(page.getByText("规则判断，不代表审批或飞行许可")).toBeVisible();
});

test("越界告警专题视图联动无人机与空间规则", async ({ page, request }) => {
  await request.post("http://127.0.0.1:8000/api/auth/login", {
    data: { username: "spatial-admin", password: "local-e2e-password" },
  });
  const now = Date.now();
  const ruleId = "E2E-NFZ-INCURSION";
  await request.post("http://127.0.0.1:8000/api/spatial-rules/drafts", {
    data: {
      rule_id: ruleId,
      name: "越界告警测试禁飞区",
      rule_type: "no_fly_zone",
      geometry: {
        type: "Polygon",
        coordinates: [
          [
            [106.66, 26.67],
            [106.68, 26.67],
            [106.68, 26.69],
            [106.66, 26.69],
            [106.66, 26.67],
          ],
        ],
      },
      min_altitude_m: 60,
      max_altitude_m: 180,
      valid_from: new Date(now - 60_000).toISOString(),
      valid_to: new Date(now + 60_000).toISOString(),
      source: "Playwright 越界告警",
    },
  });
  await request.post(
    `http://127.0.0.1:8000/api/spatial-rules/drafts/${ruleId}/publish`,
  );
  for (const [index, longitude] of [106.67, 106.671, 106.672].entries()) {
    await request.post("http://127.0.0.1:8000/api/telemetry/batches", {
      data: {
        events: [
          {
            event_id: `e2e-incursion-${index}`,
            sortie_id: "E2E-INCURSION-SORTIE",
            drone_id: "UAV-GSH-ALERT",
            longitude,
            latitude: 26.68,
            altitude_m: 100,
            heading_deg: 90,
            speed_mps: 8,
            flight_state: "flying",
            source_time: new Date(now + index * 1000).toISOString(),
            source_type: "simulated",
          },
        ],
      },
    });
  }

  await page.goto("/");
  await page.getByLabel("账号").fill("situation-viewer");
  await page.getByLabel("密码").fill("local-e2e-password");
  await page.getByRole("button", { name: "登录" }).click();
  await page.getByRole("button", { name: "越界告警", exact: true }).click();
  await expect(page.getByText("UAV-GSH-ALERT", { exact: true })).toBeVisible();
  await page.getByText("UAV-GSH-ALERT", { exact: true }).click();
  await expect(page.getByTestId("incursion-alert-position")).toBeVisible();
  await expect(page.getByText(`${ruleId} · v1`)).toBeVisible();
  await expect(
    page.getByText("规则命中提示，不代表违规认定，不输出飞行控制指令。"),
  ).toBeVisible();
});

test("线索研判员查看 AI异常线索并联动地图位置", async ({ page, request }) => {
  const materialRoot = join("/private/tmp", "low-altitude-e2e-clue-materials");
  await mkdir(join(materialRoot, "frames"), { recursive: true });
  await writeFile(join(materialRoot, "frames/e2e-fire.jpg"), "local-frame");
  const accepted = await request.post(
    "http://127.0.0.1:8000/api/inference/results",
    {
      headers: { "X-Inference-Token": "local-e2e-inference-token" },
      data: {
        result_id: "e2e-fire-clue",
        anomaly_type: "suspected_fire",
        confidence: 0.87,
        source_time: "2026-09-03T06:30:05Z",
        location: { longitude: 106.6282, latitude: 26.6467 },
        material_reference: "frames/e2e-fire.jpg",
        model_version: "deterministic-dark-region-v1",
        source_type: "evaluation",
      },
    },
  );
  expect(accepted.status(), await accepted.text()).toBe(202);

  await page.goto("/");
  await page.getByLabel("账号").fill("clue-reviewer");
  await page.getByLabel("密码").fill("local-e2e-password");
  await page.getByRole("button", { name: "登录" }).click();
  await page.getByRole("button", { name: "AI异常线索研判" }).click();

  await expect(page.getByRole("heading", { name: "AI异常线索" })).toBeVisible();
  await expect(page.getByText("推理降级", { exact: true })).toBeVisible();
  await expect(page.getByText("推理进程不可达", { exact: true })).toBeVisible();
  await expect(
    page.getByText("降级期间不生成伪线索，既有线索仍可查看和研判。"),
  ).toBeVisible();
  await expect(page.getByText("疑似烟火", { exact: true })).toBeVisible();
  await expect(page.getByText("置信度 87.0%")).toBeVisible();
  const clueCard = page.getByTestId("ai-clue-card");
  await expect(clueCard.getByText("评测来源", { exact: true })).toBeVisible();
  await expect(page.getByText("2026-09-03 14:30:05")).toBeVisible();
  await page.getByRole("button", { name: /疑似烟火/ }).click();
  await expect(page.getByTestId("ai-clue-position")).toHaveAttribute(
    "data-position",
    "106.6282,26.6467",
  );
  await expect(page.getByTestId("ai-clue-position")).toContainText(
    "疑似烟火 · 87.0%",
  );
  await expect(page.getByTestId("ai-clue-position")).toContainText("评测来源");
  await expect(page.getByAltText("疑似烟火研判材料")).toHaveAttribute(
    "src",
    "/api/ai-clues/e2e-fire-clue/material",
  );
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await expect(page.getByText("研判结果 · 确认")).toBeVisible();
  await expect(page.getByText(/确认 · clue-reviewer ·/)).toBeVisible();
  await page.getByRole("button", { name: "误报", exact: true }).click();
  await expect(page.getByText("研判结果 · 误报")).toBeVisible();
  await expect(page.getByText(/误报 · clue-reviewer ·/)).toBeVisible();
});
