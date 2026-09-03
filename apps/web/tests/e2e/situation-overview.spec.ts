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
            event_id: "e2e-map-telemetry-001",
            drone_id: "UAV-GSH-01",
            longitude: 106.6282,
            latitude: 26.6467,
            altitude_m: 86,
            heading_deg: 125,
            speed_mps: 12.4,
            flight_state: "flying",
            source_time: "2026-09-03T06:32:08Z",
            source_type: "simulated",
          },
        ],
      },
    },
  );
  expect(ingestResponse.status()).toBe(202);

  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "低空态势总览" }),
  ).toBeVisible();
  await expect(page.getByTestId("situation-map")).toHaveAttribute(
    "data-center",
    "106.6282,26.6467",
  );
  await expect(page.locator(".maplibregl-canvas")).toBeVisible();
  await expect(page.getByTestId("drone-marker-UAV-GSH-01")).toBeVisible();
  await expect(page.getByText("模拟数据", { exact: true })).toBeVisible();
  await expect(page.getByText("2026-09-03 14:32:08")).toBeVisible();

  expect(localPmtilesRequests).toContain("/maps/guanshanhu.pmtiles");
  expect(externalRequests).toEqual([]);
});
