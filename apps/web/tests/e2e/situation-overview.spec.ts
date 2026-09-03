import { expect, test } from "@playwright/test";

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
