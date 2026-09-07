import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createPocServer } from "../backend/src/server.mjs";

async function startPoc(databasePath) {
  const server = createPocServer({ databasePath });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return { baseUrl: `http://127.0.0.1:${port}`, server };
}

async function close(server) {
  server.close();
  server.closeAllConnections();
  await new Promise((resolve) => setImmediate(resolve));
}

export async function publicPagesUseSharedState() {
  const directory = await mkdtemp(path.join(tmpdir(), "drone-poc-pages-"));
  const { baseUrl, server } = await startPoc(path.join(directory, "poc.json"));

  const entry = await (await fetch(`${baseUrl}/`)).text();
  assert.match(entry, /<dialog id="reset-dialog"/);
  assert.match(entry, /确认恢复/);
  const pages = ["screen-overview", "dispatch-tasks", "alert-workbench", "stats-ledger"];
  for (const page of pages) {
    assert.match(entry, new RegExp(`${page}\\.html`));
    const response = await fetch(`${baseUrl}/${page}.html`);
    assert.equal(response.status, 200);
    const html = await response.text();
    assert.match(html, /POC 演示数据/);
    assert.match(html, /\/api\/state/);
    assert.match(html, new RegExp(`data-page="${page === "screen-overview" ? "overview" : page === "dispatch-tasks" ? "tasks" : page === "alert-workbench" ? "alerts" : "ledgers"}"`));
  }

  const state = await (await fetch(`${baseUrl}/api/state`)).json();
  assert.equal(state.snapshotVersion, "POC-DEMO-V1");
  assert.ok(state.drones.length > 0);
  assert.ok(state.tasks.length > 0);
  assert.ok(state.alerts.length > 0);
  assert.ok(state.ledgers.length > 0);

  await close(server);
  await rm(directory, { recursive: true, force: true });
}

export async function businessStateSurvivesRestart() {
  const directory = await mkdtemp(path.join(tmpdir(), "drone-poc-persist-"));
  const databasePath = path.join(directory, "poc.json");
  const first = await startPoc(databasePath);
  const changed = await fetch(`${first.baseUrl}/api/tasks/RW-20260905-012`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ status: "DISPATCHED" })
  });
  assert.equal(changed.status, 200);
  await close(first.server);

  const second = await startPoc(databasePath);
  const state = await (await fetch(`${second.baseUrl}/api/state`)).json();
  assert.equal(state.tasks.find(({ id }) => id === "RW-20260905-012").status, "DISPATCHED");
  await close(second.server);
  await rm(directory, { recursive: true, force: true });
}

export async function resetRequiresConfirmationAndIsRepeatableAndAudited() {
  const directory = await mkdtemp(path.join(tmpdir(), "drone-poc-reset-"));
  const { baseUrl, server } = await startPoc(path.join(directory, "poc.json"));
  const rejected = await fetch(`${baseUrl}/api/demo/reset`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ confirmed: false })
  });
  assert.equal(rejected.status, 409);
  assert.deepEqual(await rejected.json(), { error: "CONFIRMATION_REQUIRED" });

  const reset = () => fetch(`${baseUrl}/api/demo/reset`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ confirmed: true })
  }).then((response) => response.json());
  const first = await reset();
  const second = await reset();
  assert.deepEqual(first.businessState, second.businessState);
  assert.equal(first.snapshotVersion, "POC-DEMO-V1");
  assert.equal(second.auditEntry.action, "DEMO_RESET");
  const audit = await (await fetch(`${baseUrl}/api/audit`)).json();
  assert.equal(audit.audit.length, 2);

  const stored = JSON.parse(await readFile(path.join(directory, "poc.json"), "utf8"));
  assert.equal(stored.audit.length, 2);
  await close(server);
  await rm(directory, { recursive: true, force: true });
}
