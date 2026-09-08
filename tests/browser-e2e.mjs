import assert from "node:assert/strict";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";

const defaultChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const visualBaselinePath = new URL("./fixtures/screen-overview-1920x1080.png", import.meta.url);

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

async function availablePort() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function waitForApi(baseUrl) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      if ((await fetch(`${baseUrl}/api/state`)).ok) return;
    } catch (_error) { /* FastAPI is still starting. */ }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("FastAPI did not become ready");
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

async function compareScreenshots(cdp, actualBase64, expectedBase64) {
  return evaluate(cdp, `(async()=>{
    const load = src => new Promise((resolve,reject) => { const image=new Image(); image.onload=()=>resolve(image); image.onerror=reject; image.src=src; });
    const [actual,expected] = await Promise.all([
      load('data:image/png;base64,${actualBase64}'), load('data:image/png;base64,${expectedBase64}')
    ]);
    if(actual.width!==expected.width || actual.height!==expected.height) return {sizeMismatch:true,actual:[actual.width,actual.height],expected:[expected.width,expected.height]};
    const canvas=document.createElement('canvas'); canvas.width=actual.width; canvas.height=actual.height;
    const context=canvas.getContext('2d',{willReadFrequently:true}); context.drawImage(actual,0,0); const a=context.getImageData(0,0,canvas.width,canvas.height).data;
    context.clearRect(0,0,canvas.width,canvas.height); context.drawImage(expected,0,0); const b=context.getImageData(0,0,canvas.width,canvas.height).data;
    let total=0,changed=0;
    for(let i=0;i<a.length;i+=4){ const delta=Math.max(Math.abs(a[i]-b[i]),Math.abs(a[i+1]-b[i+1]),Math.abs(a[i+2]-b[i+2])); total+=delta; if(delta>24) changed++; }
    return {sizeMismatch:false,meanDelta:total/(a.length/4),changedRatio:changed/(a.length/4)};
  })()`);
}

export async function browserEndToEnd() {
  const chromePath = process.env.CHROME_PATH ?? defaultChrome;
  await access(chromePath);
  const directory = await mkdtemp(path.join(tmpdir(), "drone-poc-browser-"));
  const appPort = await availablePort();
  const baseUrl = `http://127.0.0.1:${appPort}`;
  const backend = spawn(path.resolve("backend/.venv/bin/python"), ["backend/start.py"], {
    cwd: path.resolve("."), stdio: "ignore",
    env: { ...process.env, PORT: String(appPort), DRONE_POC_DATABASE_URL: `sqlite+aiosqlite:///${path.join(directory, "poc.db")}` }
  });
  const backendExited = new Promise((resolve) => backend.once("exit", resolve));
  await waitForApi(baseUrl);
  const chrome = spawn(chromePath, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${directory}`, "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });
  const chromeExited = new Promise((resolve) => chrome.once("exit", resolve));

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
      if (page === "screen-overview") {
        await waitFor(cdp, "Boolean(document.querySelector('iframe[title=\"低空态势一张图\"]'))");
        assert.match(await evaluate(cdp, "document.querySelector('iframe').getAttribute('src')"), /^\/prototype\/screen-overview\.html/);
      } else {
        await waitFor(cdp, "Boolean(document.querySelector('iframe.prototype-frame'))");
        assert.equal(await evaluate(cdp, "document.querySelector('iframe').getAttribute('src')"), `/prototype/${page}.html`);
      }
    }

    await navigate(cdp, `${baseUrl}/prototype/dispatch-tasks.html`);
    assert.deepEqual(await evaluate(cdp, "Array.from(document.querySelectorAll('[data-od-id=\"view-switch\"] button'), b => b.textContent.trim())"), ["看板", "列表", "甘特"]);
    await evaluate(cdp, "document.querySelector('#btn-new').click(); document.querySelector('#f-name').value='AC03 浏览器巡检'; document.querySelector('#f-date').value='2026-09-08'; document.querySelector('#f-start').value='15:00'; document.querySelector('#f-end').value='16:00'; document.querySelector('#btn-next').click()");
    await waitFor(cdp, "document.querySelector('.step-pane.on').dataset.step === '1'");
    await evaluate(cdp, "document.querySelector('#btn-prev').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#f-name').value"), "AC03 浏览器巡检");
    const browserValidation = await evaluate(cdp, `(async()=>await (await fetch('/api/tasks/validate',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:'AC03',date:'2026-09-08',start:'10:00',end:'09:00',droneId:'U-08',battery:100,route:'演示禁飞区航线',aiItems:[]})})).json())()`);
    assert.ok(browserValidation.blockers.some(item => item.code === "AIRSPACE_CONFLICT"));
    assert.ok(browserValidation.blockers.some(item => item.code === "TIME_INVALID"));
    assert.ok(browserValidation.blockers.some(item => item.code === "AI_REQUIRED"));
    const seededMissionStatus = await evaluate(cdp, `(async()=>{const state=await (await fetch('/api/state')).json();return state.tasks.find(t=>t.id==='RW-20260905-012')?.status})()`);
    assert.equal(seededMissionStatus, "RUNNING");
    await evaluate(cdp, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'})); document.querySelector('.k-card.clickable').click()");
    await waitFor(cdp, "document.querySelector('#monitor-mask').classList.contains('open')");
    assert.equal(await evaluate(cdp, "document.querySelector('.video-box').textContent.includes('视频流占位')"), true);
    assert.equal(await evaluate(cdp, "['mm-alt','mm-spd','mm-batt','mm-sig'].every(id => Boolean(document.getElementById(id).textContent.trim()))"), true);
    assert.equal(await evaluate(cdp, "Array.from(document.querySelectorAll('.mm-foot button')).some(b => b.textContent.includes('切换备用机'))"), false);
    await evaluate(cdp, "document.querySelector('#mm-hover-btn').click()");
    await new Promise(resolve => setTimeout(resolve, 500));
    const hoverFeedback = await evaluate(cdp, "Array.from(document.querySelectorAll('.toast'),t=>t.textContent)");
    assert.ok(hoverFeedback.some(text => text.includes('模拟悬停指令已成功执行')), JSON.stringify(hoverFeedback));
    await evaluate(cdp, "document.querySelector('#mm-fail-next').click(); document.querySelector('#mm-return-btn').click()");
    await waitFor(cdp, "Array.from(document.querySelectorAll('.toast')).some(t=>t.textContent.includes('模拟返航指令发送失败'))");
    await evaluate(cdp, "document.querySelector('#mm-link-loss-btn').click()");
    await waitFor(cdp, "document.querySelector('#mm-link-loss-btn').textContent === '完成'");
    const taskSideAlert = await evaluate(cdp, `(async()=>{const state=await (await fetch('/api/state')).json();return state.alerts.find(a=>a.missionId==='RW-20260905-012')})()`);
    assert.equal(taskSideAlert.type, "图传断链");
    await evaluate(cdp, "document.querySelector('#monitor-close').click(); document.querySelector('.k-card.clickable').click(); document.querySelector('#mm-stop-btn').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#confirm-mask').classList.contains('open')"), true);
    await evaluate(cdp, "document.querySelector('#cf-cancel').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#confirm-mask').classList.contains('open')"), false);
    await evaluate(cdp, "document.querySelector('#mm-stop-btn').click(); document.querySelector('#cf-ok').click()");
    await waitFor(cdp, "Array.from(document.querySelectorAll('.k-head')).some(h=>h.textContent.includes('已终止') && h.textContent.match(/1/))");

    // AC06: alert verification, evidence failure/retry, false-positive validation and keyboard safety.
    await fetch(`${baseUrl}/api/demo/reset`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirmed: true }) });
    await navigate(cdp, `${baseUrl}/prototype/alert-workbench.html`);
    await waitFor(cdp, "document.querySelectorAll('.alert-card').length > 0");
    await evaluate(cdp, "document.querySelector('#ev-fail-next').click()");
    await waitFor(cdp, "document.querySelector('#ev-error').classList.contains('show')");
    assert.ok((await evaluate(cdp, "document.querySelector('#ev-error-reason').textContent")).includes("暂不可用"));
    await evaluate(cdp, "document.querySelector('#ev-retry').click()");
    await waitFor(cdp, "!document.querySelector('#ev-error').classList.contains('show')");
    await evaluate(cdp, "document.querySelector('#ev-compare').click()");
    assert.equal(await evaluate(cdp, "getComputedStyle(document.querySelector('#ev-compare-scene')).display"), "block");
    assert.notEqual(await evaluate(cdp, "document.querySelector('#ev-scene').innerHTML"), await evaluate(cdp, "document.querySelector('#ev-compare-scene').innerHTML"));
    await evaluate(cdp, "document.querySelector('[data-frame=\"3\"]').click()");
    assert.notEqual(await evaluate(cdp, "document.querySelector('#ev-scene').innerHTML"), await evaluate(cdp, "document.querySelector('#ev-compare-scene').innerHTML"));
    await evaluate(cdp, "document.querySelector('#queue-search').focus(); document.querySelector('#queue-search').value='K1582'; document.querySelector('#queue-search').dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#queue-search').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))");
    assert.equal(await evaluate(cdp, "document.querySelector('.alert-card .ac-loc').textContent"), "兰海高速 K1582 都匀段");
    assert.equal(await evaluate(cdp, "document.querySelector('.st-tag').textContent"), "待核实");
    await evaluate(cdp, "document.querySelector('#queue-search').blur(); document.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))");
    await waitFor(cdp, "document.querySelector('.st-tag').textContent === '处置中'");
    const confirmedAlert = await (await fetch(`${baseUrl}/api/alerts/GJ-20260905-031`)).json();
    assert.equal(confirmedAlert.timeline.at(-1).action, "ALERT_CONFIRMED");
    assert.equal(confirmedAlert.timeline.at(-1).actor, "王警官");
    assert.ok(confirmedAlert.timeline.at(-1).occurredAt);

    await fetch(`${baseUrl}/api/demo/reset`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirmed: true }) });
    await navigate(cdp, `${baseUrl}/prototype/alert-workbench.html`);
    await waitFor(cdp, "document.querySelector('#act-false') && !document.querySelector('#act-false').disabled");
    await evaluate(cdp, "document.querySelector('#act-false').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#false-ok').disabled"), true);
    await evaluate(cdp, "document.querySelector('#false-reasons .check-row').click(); document.querySelector('#false-ok').click()");
    await waitFor(cdp, "document.querySelector('.st-tag').textContent === '误报'");
    const falseAlert = await (await fetch(`${baseUrl}/api/alerts/GJ-20260905-031`)).json();
    assert.equal(falseAlert.timeline.at(-1).action, "ALERT_MARKED_FALSE_POSITIVE");

    // AC07: transfer, escalation, resolution and dashboard drill-down close the loop.
    await fetch(`${baseUrl}/api/demo/reset`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirmed: true }) });
    await fetch(`${baseUrl}/api/alerts/GJ-20260905-031/confirm`, { method: "POST" });
    await navigate(cdp, `${baseUrl}/prototype/alert-workbench.html?alert=GJ-20260905-031`);
    await waitFor(cdp, "document.querySelector('.alert-card.sel')?.dataset.id === 'GJ-20260905-031'");
    await waitFor(cdp, "document.querySelector('#detail-grid .st-tag').textContent === '处置中'");
    await evaluate(cdp, "document.querySelector('#act-reassign').click(); document.querySelector('#reassign-ok').click()");
    await waitFor(cdp, "Array.from(document.querySelectorAll('.toast')).some(t=>t.textContent.includes('ZP-MOCK-'))");
    await evaluate(cdp, "document.querySelector('#act-escalate').click(); document.querySelector('#escalate-ok').click()");
    await waitFor(cdp, "Array.from(document.querySelectorAll('.toast')).some(t=>t.textContent.includes('SJ-MOCK-'))");
    await evaluate(cdp, "window.prompt=()=> '现场已恢复通行'; document.querySelector('#act-resolve').click()");
    await waitFor(cdp, "document.querySelector('#detail-grid .st-tag').textContent === '已办结'");
    await navigate(cdp, `${baseUrl}/prototype/screen-overview.html`);
    await waitFor(cdp, "document.querySelectorAll('#layer-alerts g').length > 0");
    const resolvedMarker = await evaluate(cdp, `(() => { const alerts=Array.from(document.querySelectorAll('#layer-alerts g')); const item=alerts.find(g=>g.getAttribute('aria-label').includes('交通事故')); return {pulse:Boolean(item.querySelector('.pulse-ring')), index:item.dataset.i}; })()`);
    assert.equal(resolvedMarker.pulse, false);
    await evaluate(cdp, `document.querySelector('#layer-alerts g[data-i="${resolvedMarker.index}"]').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
    assert.ok((await evaluate(cdp, "document.querySelector('#info-rows').textContent")).includes("已办结"));
    assert.match(await evaluate(cdp, "document.querySelector('#info-drill').getAttribute('href')"), /alert=GJ-20260905-031/);

    const injectionText = '<img id="stored-xss" src=x onerror="window.__storedXss=true">';
    await fetch(`${baseUrl}/api/tasks/RW-20260905-002`, {
      method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: injectionText })
    });
    await cdp.command("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
    await navigate(cdp, `${baseUrl}/prototype/screen-overview.html`);
    await waitFor(cdp, "document.querySelectorAll('.fleet-row').length >= 7");
    assert.equal(await evaluate(cdp, "document.querySelector('.task-name').childNodes[0].textContent"), injectionText);
    assert.equal(await evaluate(cdp, "Boolean(document.querySelector('#stored-xss')) || Boolean(window.__storedXss)"), false);
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

    const dayTrend = await evaluate(cdp, "document.querySelector('#trend-chart polyline').getAttribute('points')");
    await evaluate(cdp, "document.querySelector('[data-dim=week]').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#a1-title').textContent"), "本周巡检概览");
    assert.equal(await evaluate(cdp, "document.querySelector('#trend-title').textContent"), "近 7 周任务数 vs 告警数");
    assert.notEqual(await evaluate(cdp, "document.querySelector('#trend-chart polyline').getAttribute('points')"), dayTrend);
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

    await fetch(`${baseUrl}/api/demo/reset`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirmed: true }) });
    await navigate(cdp, `${baseUrl}/prototype/screen-overview.html?visual-test=1`);
    await waitFor(cdp, "document.querySelectorAll('.fleet-row').length === 8");
    const screenshot = await cdp.command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    const screenshotBuffer = Buffer.from(screenshot.data, "base64");
    assert.deepEqual([screenshotBuffer.readUInt32BE(16), screenshotBuffer.readUInt32BE(20)], [1920, 1080]);
    if (process.env.UPDATE_VISUAL_BASELINE === "1") {
      await mkdir(new URL("./fixtures/", import.meta.url), { recursive: true });
      await writeFile(visualBaselinePath, screenshotBuffer);
    } else {
      const baseline = await readFile(visualBaselinePath);
      const difference = await compareScreenshots(cdp, screenshot.data, baseline.toString("base64"));
      assert.equal(difference.sizeMismatch, false, JSON.stringify(difference));
      assert.ok(difference.meanDelta < 3 && difference.changedRatio < 0.08, `visual difference exceeded threshold: ${JSON.stringify(difference)}`);
    }
  } finally {
    cdp?.close();
    chrome.kill("SIGTERM");
    await Promise.race([chromeExited, new Promise((resolve) => setTimeout(resolve, 3000))]);
    backend.kill("SIGTERM");
    await Promise.race([backendExited, new Promise((resolve) => setTimeout(resolve, 3000))]);
    await rm(directory, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  }
}
