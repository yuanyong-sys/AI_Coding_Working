import { describe, expect, it } from "vitest";

import { platformRoutes } from "../src/router/routes";

describe("platform routes", () => {
  it("exposes the six top-level POC modules including resource and system management", () => {
    expect(platformRoutes.map(({ path }) => path)).toEqual([
      "/screen-overview.html",
      "/dispatch-tasks.html",
      "/alert-workbench.html",
      "/stats-ledger.html",
      "/resource-management.html",
      "/system-management.html"
    ]);
  });
});
