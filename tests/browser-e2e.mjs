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
      await waitFor(cdp, page === "screen-overview" ? "document.querySelectorAll('.fleet-row').length > 0" : "document.querySelectorAll('#content article').length > 0");
      assert.equal(await evaluate(cdp, "document.querySelector('.badge').textContent"), "POC 演示数据");
    }

    await cdp.command("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
    await navigate(cdp, `${baseUrl}/screen-overview.html`);
    await waitFor(cdp, "document.querySelectorAll('.fleet-row').length >= 7");
    assert.deepEqual(await evaluate(cdp, `(() => {
      const columns = Array.from(document.querySelectorAll('.col'));
      return {
        stage: [document.querySelector('#stage').offsetWidth, document.querySelector('#stage').offsetHeight],
        columns: columns.map(column => column.offsetWidth),
        mapWidth: document.querySelector('.map-wrap').offsetWidth,
        flightSpeed: document.querySelector('#stage').dataset.flightSpeed,
        idleTourMs: document.querySelector('#stage').dataset.idleTourMs,
        panelTitle: getComputedStyle(columns[0].querySelector('.panel-title')).fontSize,
        metric: getComputedStyle(columns[0].querySelector('.stat-value')).fontSize,
        primary: getComputedStyle(columns[0].querySelector('.fleet-id')).fontSize,
        auxiliary: getComputedStyle(columns[0].querySelector('.fleet-task')).fontSize
      };
    })()`), { stage: [1920, 1080], columns: [350, 350], mapWidth: 1172, flightSpeed: "0.35", idleTourMs: "60000", panelTitle: "14px", metric: "33px", primary: "12.5px", auxiliary: "11px" });

    const firstPosition = await evaluate(cdp, `(() => { const matrix=document.querySelector('#layer-drones g[data-i="3"]').transform.baseVal.consolidate().matrix; return [matrix.e,matrix.f]; })()`);
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const secondPosition = await evaluate(cdp, `(() => { const matrix=document.querySelector('#layer-drones g[data-i="3"]').transform.baseVal.consolidate().matrix; return [matrix.e,matrix.f]; })()`);
    const travelled = Math.hypot(secondPosition[0] - firstPosition[0], secondPosition[1] - firstPosition[1]);
    assert.ok(travelled > 0.5 && travelled < 10, `expected slow simulated flight, travelled ${travelled}px in one second`);

    await evaluate(cdp, "document.querySelector('[data-dim=week]').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#a1-title').textContent"), "本周巡检概览");
    await evaluate(cdp, "document.querySelector('.fleet-row').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#info-card').classList.contains('show')"), true);
    await evaluate(cdp, "document.querySelector('.layer-ctrl').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#info-card').classList.contains('show')"), true);
    await new Promise((resolve) => setTimeout(resolve, 100));
    await evaluate(cdp, "document.querySelector('#map-bg-rect').dispatchEvent(new MouseEvent('click', {bubbles:true}))");
    assert.equal(await evaluate(cdp, "document.querySelector('#info-card').classList.contains('show')"), false);
    await evaluate(cdp, "document.querySelector('[data-layer=layer-routes]').click()");
    assert.equal(await evaluate(cdp, "getComputedStyle(document.querySelector('#layer-routes')).display"), "none");
    assert.equal(await evaluate(cdp, "document.querySelector('#mute-btn').getAttribute('aria-pressed')"), "false");
    await evaluate(cdp, "document.querySelector('#mute-btn').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#mute-btn').getAttribute('aria-pressed')"), "true");
    await evaluate(cdp, `window.dispatchEvent(new CustomEvent('poc:new-emergency-alert', {detail:{
      id:'GJ-E2E-001', type:'测试紧急告警', loc:'测试路段', time:'15:00', x:520, y:430
    }}))`);
    assert.equal(await evaluate(cdp, "document.querySelector('.alert-item .alert-type').textContent"), "测试紧急告警");
    assert.equal(await evaluate(cdp, "document.querySelector('#info-card').classList.contains('show')"), true);
    assert.equal(await evaluate(cdp, "document.querySelector('#tour-lock').getAttribute('aria-pressed')"), "false");
    await evaluate(cdp, "document.querySelector('#tour-lock').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#tour-lock').getAttribute('aria-pressed')"), "true");
    assert.equal(await evaluate(cdp, "document.querySelector('#stage').dataset.tourState"), "locked");
  } finally {
    cdp?.close();
    chrome.kill("SIGTERM");
    server.close();
    server.closeAllConnections();
    await rm(directory, { recursive: true, force: true });
  }
}
