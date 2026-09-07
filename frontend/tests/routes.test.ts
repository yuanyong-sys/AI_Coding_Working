import { describe, expect, it } from "vitest";

import { platformRoutes } from "../src/router/routes";

describe("platform routes", () => {
  it("exposes the four agreed public pages", () => {
    expect(platformRoutes.map(({ path }) => path)).toEqual([
      "/screen-overview.html",
      "/dispatch-tasks.html",
      "/alert-workbench.html",
      "/stats-ledger.html"
    ]);
  });
});
