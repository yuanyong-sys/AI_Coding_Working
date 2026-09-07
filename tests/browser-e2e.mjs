import assert from "node:assert/strict";
import { access, mkdtemp, readFile, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";

import { createPocServer } from "../backend/src/server.mjs";

const defaultChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

async function waitForDevToolsPort(directory) {
  const filename = path.join(directory, "DevToolsActivePort");
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const [port] = (await readFile(filename, "utf8")).trim().split("\n");
      return Number(port);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error("Chrome DevTools did not become ready");
}

async function connectCdp(webSocketUrl) {
  const socket = new WebSocket(webSocketUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(String(event.data));
    if (!message.id) return;
    const request = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) request.reject(new Error(message.error.message));
    else request.resolve(message.result);
  });
  return {
    close: () => socket.close(),
    command(method, params = {}) {
      const id = nextId++;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    }
  };
}

async function evaluate(cdp, expression) {
  const result = await cdp.command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}

async function navigate(cdp, url) {
  await cdp.command("Page.navigate", { url });
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await evaluate(cdp, "document.readyState === 'complete'")) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`Page did not load: ${url}`);
}

async function waitFor(cdp, expression) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await evaluate(cdp, expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`Browser condition timed out: ${expression}`);
}

export async function browserEndToEnd() {
  const chromePath = process.env.CHROME_PATH ?? defaultChrome;
  await access(chromePath);
  const directory = await mkdtemp(path.join(tmpdir(), "drone-poc-browser-"));
  const server = createPocServer({ databasePath: path.join(directory, "poc.json") });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  const chrome = spawn(chromePath, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${directory}`, "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });

  let cdp;
  try {
    const port = await waitForDevToolsPort(directory);
    const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
    cdp = await connectCdp(target.webSocketDebuggerUrl);
    await cdp.command("Page.enable");
    await cdp.command("Runtime.enable");

    await navigate(cdp, `${baseUrl}/`);
    assert.deepEqual(await evaluate(cdp, "Array.from(document.querySelectorAll('main a'), a => a.getAttribute('href'))"), [
      "/screen-overview.html", "/dispatch-tasks.html", "/alert-workbench.html", "/stats-ledger.html"
    ]);
    await evaluate(cdp, "document.querySelector('#reset').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#reset-dialog').open"), true);
    await evaluate(cdp, "document.querySelector('#reset-dialog button[value=cancel]').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#reset-dialog').open"), false);

    for (const page of ["screen-overview", "dispatch-tasks", "alert-workbench", "stats-ledger"]) {
      await navigate(cdp, `${baseUrl}/${page}.html`);
      await waitFor(cdp, "document.querySelectorAll('#content article').length > 0");
      assert.equal(await evaluate(cdp, "document.querySelector('.badge').textContent"), "POC 演示数据");
    }
  } finally {
    cdp?.close();
    chrome.kill("SIGTERM");
    server.close();
    server.closeAllConnections();
    await rm(directory, { recursive: true, force: true });
  }
}
