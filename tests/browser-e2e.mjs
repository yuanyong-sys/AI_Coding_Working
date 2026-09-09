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

async function tabTo(cdp, selector, limit = 120) {
  await evaluate(cdp, "document.body.focus()");
  for (let index = 0; index < limit; index += 1) {
    await cdp.command("Input.dispatchKeyEvent", {type:"keyDown",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
    await cdp.command("Input.dispatchKeyEvent", {type:"keyUp",key:"Tab",code:"Tab",windowsVirtualKeyCode:9});
    if (await evaluate(cdp, `document.activeElement.matches(${JSON.stringify(selector)})`)) return;
  }
  throw new Error(`Keyboard focus did not reach ${selector}`);
}

async function pressEnter(cdp) {
  await cdp.command("Input.dispatchKeyEvent", {type:"keyDown",key:"Enter",code:"Enter",text:"\r",unmodifiedText:"\r",windowsVirtualKeyCode:13});
  await cdp.command("Input.dispatchKeyEvent", {type:"keyUp",key:"Enter",code:"Enter",windowsVirtualKeyCode:13});
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
      "/screen-overview.html", "/dispatch-tasks.html", "/alert-workbench.html", "/stats-ledger.html",
      "/resource-management.html", "/system-management.html"
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
        await waitFor(cdp, "Boolean(document.querySelector('iframe').contentDocument?.querySelector('[data-od-id=back-to-index]'))");
        assert.deepEqual(await evaluate(cdp, `(() => {
          const link = document.querySelector('iframe').contentDocument.querySelector('[data-od-id=back-to-index]');
          return [link.getAttribute('href'), link.getAttribute('target')];
        })()`), ["/", "_top"]);
        await evaluate(cdp, "document.querySelector('iframe').contentDocument.querySelector('[data-od-id=back-to-index]').click()");
        await waitFor(cdp, "location.pathname === '/' && Boolean(document.querySelector('main[aria-label=\"业务页面\"]'))");
      } else {
        await waitFor(cdp, "Boolean(document.querySelector('iframe.prototype-frame'))");
        assert.equal(await evaluate(cdp, "document.querySelector('iframe').getAttribute('src')"), `/prototype/${page}.html`);
      }
    }
    for (const page of ["dispatch-tasks", "alert-workbench", "stats-ledger"]) {
      await navigate(cdp, `${baseUrl}/prototype/${page}.html`);
      assert.deepEqual(await evaluate(cdp, "['resource','system'].map(name=>{const link=document.querySelector(`[data-od-id=nav-${name}]`);return [link.getAttribute('href'),link.getAttribute('target')]})"), [["/resource-management.html","_top"],["/system-management.html","_top"]]);
    }

    // Issue #10: resource and system administration prototypes are first-class routes.
    for (const page of ["resource-management", "system-management", "system-org", "system-roles", "system-config", "system-logs"]) {
      await navigate(cdp, `${baseUrl}/${page}.html`);
      await waitFor(cdp, "Boolean(document.querySelector('iframe.prototype-frame'))");
      assert.equal(await evaluate(cdp, "document.querySelector('iframe').getAttribute('src')"), `/prototype/${page}.html`);
      await waitFor(cdp, "Boolean(document.querySelector('iframe').contentDocument?.querySelector('[data-od-id=side-nav]'))");
    }

    // Issue #10: the agreed browser seam exercises each resource-management flow.
    await navigate(cdp, `${baseUrl}/prototype/resource-management.html`);
    assert.deepEqual(await evaluate(cdp, "Array.from(document.querySelectorAll('[data-od-id=resource-tabs] button'),button=>button.textContent.trim())"), ["无人机机队","机场机巢","飞手与排班"]);
    assert.deepEqual(await evaluate(cdp, "Array.from(document.querySelectorAll('[data-od-id=side-nav] a[href]'),a=>a.target).filter(Boolean)"), ["_top","_top","_top","_top","_top","_top","_top"]);
    await evaluate(cdp, "document.querySelector('#f-search').value='不存在的无人机'; document.querySelector('#f-search').dispatchEvent(new Event('input',{bubbles:true}))");
    assert.equal(await evaluate(cdp, "Boolean(document.querySelector('[data-od-id=fleet-empty]'))"), true);
    await evaluate(cdp, "document.querySelector('#f-search').value='U-01'; document.querySelector('#f-search').dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('[data-act=detail]').click()");
    assert.match(await evaluate(cdp, "document.querySelector('#drawer-title').textContent"), /U-01/);
    await evaluate(cdp, "document.querySelector('#drawer-close').click(); document.querySelector('[data-act=maint]').click(); document.querySelector('#m-date').value=''; document.querySelector('#maint-submit').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#maint-error').hidden"), false);
    await evaluate(cdp, "document.querySelector('#m-date').value='2026-09-09'; document.querySelector('#maint-submit').click(); document.querySelector('[data-tab=dock]').click(); document.querySelector('[data-dock]').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#dock-modal').classList.contains('open')"), true);
    await evaluate(cdp, "document.querySelector('#dock-close').click(); document.querySelector('[data-tab=pilot]').click(); document.querySelector('[data-pilot]').click()");
    assert.match(await evaluate(cdp, "document.querySelector('#drawer-title').textContent"), /飞手档案/);
    await evaluate(cdp, "document.querySelector('#drawer-close').click(); document.querySelector('#btn-schedule-edit').click()");
    assert.match(await evaluate(cdp, "document.querySelector('#toast-box .toast:last-child').textContent"), /演示环境暂不支持修改排班/);

    // Issue #10: system overview, staff, roles, configuration and audit logs remain demonstrable.
    await navigate(cdp, `${baseUrl}/prototype/system-management.html`);
    assert.deepEqual(await evaluate(cdp, "Array.from(document.querySelectorAll('[data-od-id=module-nav-grid] a'),a=>a.getAttribute('href'))"), ["/system-org.html","/system-roles.html","/system-config.html","/system-logs.html"]);
    await navigate(cdp, `${baseUrl}/prototype/system-org.html`);
    await evaluate(cdp, "document.querySelector('[data-unit=dy]').click(); document.querySelector('#s-role').value='调度员'; document.querySelector('#s-role').dispatchEvent(new Event('change',{bubbles:true})); document.querySelector('#s-status').value='在岗'; document.querySelector('#s-status').dispatchEvent(new Event('change',{bubbles:true}))");
    assert.ok(await evaluate(cdp, "Array.from(document.querySelectorAll('#staff-tbody tr')).every(row=>row.textContent.includes('都匀')&&row.textContent.includes('调度员')&&row.textContent.includes('在岗'))"));
    await evaluate(cdp, "document.querySelector('[data-unit=qn]').click(); document.querySelector('#s-role').value='全部'; document.querySelector('#s-role').dispatchEvent(new Event('change',{bubbles:true})); document.querySelector('#s-status').value='全部'; document.querySelector('#s-status').dispatchEvent(new Event('change',{bubbles:true}))");
    await evaluate(cdp, "document.querySelector('#btn-add-staff').click(); document.querySelector('#staff-submit').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#fw-name').classList.contains('has-err')"), true);
    await evaluate(cdp, "document.querySelector('#f-name').value='演示人员'; document.querySelector('#f-no').value='399999'; document.querySelector('#f-phone').value='13900000000'; document.querySelector('#staff-submit').click()");
    assert.ok(await evaluate(cdp, "Array.from(document.querySelectorAll('#staff-tbody tr')).some(row=>row.textContent.includes('演示人员'))"));
    await evaluate(cdp, "Array.from(document.querySelectorAll('#staff-tbody tr')).find(row=>row.textContent.includes('演示人员')).querySelector('[data-act=edit]').click(); document.querySelector('#f-name').value='演示人员甲'; document.querySelector('#staff-submit').click()");
    assert.ok(await evaluate(cdp, "Array.from(document.querySelectorAll('#staff-tbody tr')).some(row=>row.textContent.includes('演示人员甲'))"));
    await evaluate(cdp, "Array.from(document.querySelectorAll('#staff-tbody tr')).find(row=>row.textContent.includes('演示人员甲')).querySelector('[data-act=disable]').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#confirm-modal').classList.contains('open')"), true);
    await evaluate(cdp, "document.querySelector('#confirm-ok').click(); document.querySelector('#s-search').value='演示人员甲'; document.querySelector('#s-search').dispatchEvent(new Event('input',{bubbles:true}))");
    assert.match(await evaluate(cdp, "document.querySelector('#staff-tbody').textContent"), /演示人员甲.*停用/s);
    await navigate(cdp, `${baseUrl}/prototype/system-roles.html`);
    await evaluate(cdp, "document.querySelector('.btn-edit-perm').click(); document.querySelector('.perm-cb').click(); window.confirm=()=>false; document.querySelector('#drawer-close').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#perm-drawer').classList.contains('open')"), true);
    await evaluate(cdp, "document.querySelector('#drawer-cancel').click(); document.querySelector('.btn-edit-perm').click(); document.querySelector('#btn-clear-all').click(); document.querySelector('#btn-clear-all').click(); document.querySelector('#drawer-save').click()");
    assert.match(await evaluate(cdp, "document.querySelector('#toast-box .toast:last-child').textContent"), /权限点不能为 0/);
    await evaluate(cdp, "document.querySelector('#btn-check-all').click(); document.querySelector('#drawer-save').click()");
    assert.match(await evaluate(cdp, "document.querySelector('#toast-box .toast:last-child').textContent"), /权限已保存/);
    await navigate(cdp, `${baseUrl}/prototype/system-config.html`);
    assert.equal(await evaluate(cdp, "document.querySelector('#cfg-amap-key').type"), "password");
    assert.ok(await evaluate(cdp, "document.querySelector('#cfg-amap-key').value.includes('PLACEHOLDER')"));
    await evaluate(cdp, "document.querySelector('#cfg-amap-key').value='x'; document.querySelector('#btn-save').click()");
    assert.match(await evaluate(cdp, "document.querySelector('#toast-box .toast:last-child').textContent"), /校验失败/);
    await evaluate(cdp, "document.querySelector('#btn-restore').click(); document.querySelector('#restore-confirm').click()");
    assert.match(await evaluate(cdp, "document.querySelector('#toast-box .toast:last-child').textContent"), /已恢复为默认配置/);
    await evaluate(cdp, "document.querySelector('#cfg-zoom').value='11'; document.querySelector('#cfg-zoom').dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#btn-save').click()");
    assert.match(await evaluate(cdp, "document.querySelector('#toast-box .toast:last-child').textContent"), /配置已保存/);
    await navigate(cdp, `${baseUrl}/prototype/system-logs.html`);
    await evaluate(cdp, "document.querySelector('#f-module').value='资源管理'; document.querySelector('#f-module').dispatchEvent(new Event('change',{bubbles:true})); document.querySelector('[data-xp]').click()");
    assert.equal(await evaluate(cdp, "Boolean(document.querySelector('.detail-row'))"), true);
    await evaluate(cdp, "document.querySelector('#btn-export-csv').click()");
    assert.match(await evaluate(cdp, "document.querySelector('#toast-box .toast:last-child').textContent"), /已导出.*CSV/);
    await evaluate(cdp, "document.querySelector('#btn-reset').click(); document.querySelector('[data-range=\"30d\"]').click(); document.querySelector('[data-pg=\"2\"]').click()");
    assert.match(await evaluate(cdp, "document.querySelector('.pg-info').textContent"), /第 2\//);
    assert.ok(await evaluate(cdp, "document.body.textContent.includes('U-04')"));

    for (const [page, regions] of [
      ["resource-management", [".sidenav", ".topbar", ".content"]],
      ["system-management", [".sidenav", ".topbar", ".mod-grid"]],
      ["system-org", [".sidenav", ".topbar", ".org-layout"]],
      ["system-roles", [".sidenav", ".topbar", ".role-grid"]],
      ["system-config", [".sidenav", ".topbar", ".cfg-grid"]],
      ["system-logs", [".sidenav", ".topbar", ".table-wrap"]]
    ]) {
      await cdp.command("Emulation.setDeviceMetricsOverride", {width:1440,height:900,deviceScaleFactor:1,mobile:false});
      await navigate(cdp, `${baseUrl}/prototype/${page}.html`);
      assert.equal(await evaluate(cdp, "document.querySelector('.nav-logo').getAttribute('href')"), "/");
      assert.equal(await evaluate(cdp, "document.querySelector('.nav-logo').target"), "_top");
      assert.match(await evaluate(cdp, "document.querySelector('.nav-item.active').textContent.trim()"), page === "resource-management" ? /资源管理/ : /系统管理/);
      const layout = await evaluate(cdp, `(() => ({overflow:document.documentElement.scrollWidth-innerWidth,regions:${JSON.stringify(regions)}.map(selector=>{const r=document.querySelector(selector)?.getBoundingClientRect();return Boolean(r&&r.width>40&&r.height>20&&r.right>0&&r.left<innerWidth&&r.bottom>0&&r.top<innerHeight)})}))()`);
      assert.ok(layout.overflow <= 1, `${page} has ${layout.overflow}px horizontal overflow at 1440x900`);
      assert.ok(layout.regions.every(Boolean), `${page} has a hidden core region at 1440x900`);
      const screenshot = await cdp.command("Page.captureScreenshot", {format:"png",captureBeyondViewport:false});
      const pixels = Buffer.from(screenshot.data, "base64");
      assert.deepEqual([pixels.readUInt32BE(16),pixels.readUInt32BE(20)], [1440,900]);
    }

    await navigate(cdp, `${baseUrl}/prototype/resource-management.html`);
    const resourceVocabulary = await evaluate(cdp, "document.body.textContent");
    await navigate(cdp, `${baseUrl}/prototype/system-logs.html`);
    const logVocabulary = await evaluate(cdp, "document.body.textContent");
    assert.ok(resourceVocabulary.includes("U-04") && logVocabulary.includes("U-04"));
    assert.ok(resourceVocabulary.includes("兰海高速都匀段") && logVocabulary.includes("兰海高速都匀段"));

    // AC10: externally controlled failures expose reason, last-valid information and a retry action.
    await fetch(`${baseUrl}/api/demo/control`, { method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify({advanceMinutes:3,failures:["data"]}) });
    await navigate(cdp, `${baseUrl}/prototype/screen-overview.html`);
    await waitFor(cdp, "Boolean(document.querySelector('#data-failure'))");
    assert.match(await evaluate(cdp, "document.querySelector('#data-failure').textContent"), /演示数据服务加载失败.*最后有效信息.*重试数据/);
    assert.ok(await evaluate(cdp, "document.querySelectorAll('.fleet-row').length > 0"));
    await evaluate(cdp, "document.querySelector('#data-retry').click()");
    await waitFor(cdp, "!document.querySelector('#data-failure')");

    await fetch(`${baseUrl}/api/demo/control`, { method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify({failures:["map"]}) });
    await navigate(cdp, `${baseUrl}/prototype/screen-overview.html`);
    await waitFor(cdp, "Boolean(document.querySelector('#map-failure'))");
    assert.match(await evaluate(cdp, "document.querySelector('#map-failure').textContent"), /地图底图服务不可用.*最后有效信息.*重试地图/);
    assert.ok(await evaluate(cdp, "document.querySelectorAll('#layer-drones g').length > 0"));
    await evaluate(cdp, "document.querySelector('#map-retry').click()");
    await waitFor(cdp, "!document.querySelector('#map-failure')");

    await fetch(`${baseUrl}/api/demo/control`, { method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify({failures:["media"]}) });
    await navigate(cdp, `${baseUrl}/prototype/alert-workbench.html`);
    await waitFor(cdp, "document.querySelectorAll('.alert-card').length > 0");
    await evaluate(cdp, "document.querySelector('.alert-card').click()");
    await waitFor(cdp, "document.querySelector('#ev-error').classList.contains('show')");
    assert.match(await evaluate(cdp, "document.querySelector('#ev-error').textContent"), /视频证据流已中断.*最后有效信息.*重试加载/);
    await evaluate(cdp, "document.querySelector('#ev-retry').click()");
    await waitFor(cdp, "!document.querySelector('#ev-error').classList.contains('show')");

    await fetch(`${baseUrl}/api/demo/control`, { method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify({failures:["control"]}) });
    await navigate(cdp, `${baseUrl}/prototype/dispatch-tasks.html`);
    await evaluate(cdp, "document.querySelector('.k-card.clickable').click()");
    await waitFor(cdp, "document.querySelector('#monitor-mask').classList.contains('open')");
    await evaluate(cdp, "document.querySelector('#mm-hover-btn').click()");
    await waitFor(cdp, "document.querySelector('#mm-hover-btn').textContent.includes('失败·重试')");
    assert.match((await evaluate(cdp, "Array.from(document.querySelectorAll('.toast')).at(-1).textContent")), /响应超时.*最后有效信息.*点击重试/);
    await evaluate(cdp, "document.querySelector('#mm-hover-btn').click()");
    await waitFor(cdp, "document.querySelector('#mm-hover-btn').textContent.includes('成功')");

    // AC07: advancing the public simulation clock deterministically raises and displays the overdue reminder.
    await fetch(`${baseUrl}/api/demo/reset`, {method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({confirmed:true})});
    await fetch(`${baseUrl}/api/demo/control`, {method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({advanceMinutes:6})});
    await navigate(cdp, `${baseUrl}/prototype/alert-workbench.html`);
    await waitFor(cdp, "document.querySelector('.ac-esc')?.textContent.includes('超时未核实')");
    assert.equal(await evaluate(cdp, "document.querySelector('.alert-card').classList.contains('urgent-open')"), true);
    const reminderAudit = await (await fetch(`${baseUrl}/api/audit`)).json();
    assert.ok(reminderAudit.audit.some(item=>item.action==='ALERT_EMERGENCY_REMINDER'));

    // AC11: a browser-visible mutation survives reload; repeated restore is identical and audited.
    await fetch(`${baseUrl}/api/tasks/RW-20260905-002`, {method:"PATCH",headers:{"content-type":"application/json"},body:JSON.stringify({name:"刷新后仍保留的任务"})});
    await navigate(cdp, `${baseUrl}/prototype/screen-overview.html`);
    await waitFor(cdp, "Array.from(document.querySelectorAll('.task-name')).some(n=>n.textContent.includes('刷新后仍保留的任务'))");
    await navigate(cdp, `${baseUrl}/prototype/screen-overview.html`);
    await waitFor(cdp, "Array.from(document.querySelectorAll('.task-name')).some(n=>n.textContent.includes('刷新后仍保留的任务'))");
    const restoredOnce = await (await fetch(`${baseUrl}/api/demo/reset`, {method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({confirmed:true})})).json();
    const restoredTwice = await (await fetch(`${baseUrl}/api/demo/reset`, {method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({confirmed:true})})).json();
    assert.deepEqual(restoredOnce.businessState, restoredTwice.businessState);
    const restoreAudit = await (await fetch(`${baseUrl}/api/audit`)).json();
    assert.deepEqual(restoreAudit.audit.slice(-2).map(item=>item.action), ["DEMO_RESET","DEMO_RESET"]);

    // AC12: every page is reached by real Tab navigation, shows focus and renders at each target viewport.
    const viewportPages = [
      [1920,1080,"screen-overview","#mute-btn",[".topbar",".map-wrap",".col"]],
      [1440,900,"dispatch-tasks","#btn-new",[".topbar","#stats-strip","#kanban-board"]],
      [1440,900,"alert-workbench","#queue-search",[".topbar",".queue",".evidence",".disp"]],
      [1440,900,"stats-ledger","#btn-query",[".topbar",".filter-bar",".content"]],
      [1366,768,"dispatch-tasks","#btn-new",[".topbar","#stats-strip","#kanban-board"]],
      [1366,768,"alert-workbench","#queue-search",[".topbar",".queue",".evidence",".disp"]],
      [1366,768,"stats-ledger","#btn-query",[".topbar",".filter-bar",".content"]]
    ];
    for (const [width,height,page,targetSelector,regions] of viewportPages) {
      await cdp.command("Emulation.setDeviceMetricsOverride", {width,height,deviceScaleFactor:1,mobile:false});
      await navigate(cdp, `${baseUrl}/prototype/${page}.html`);
      await tabTo(cdp,targetSelector);
      const focusStyle=await evaluate(cdp, "({outline:getComputedStyle(document.activeElement).outlineStyle,shadow:getComputedStyle(document.activeElement).boxShadow,tag:document.activeElement.outerHTML.slice(0,120)})");
      assert.ok(focusStyle.outline!=="none" || focusStyle.shadow!=="none", `${page} ${width}x${height} has no visible focus: ${JSON.stringify(focusStyle)}`);
      assert.deepEqual(await evaluate(cdp, "[innerWidth,innerHeight]"), [width,height]);
      const viewportShot=await cdp.command("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});
      const viewportBuffer=Buffer.from(viewportShot.data,"base64");
      assert.deepEqual([viewportBuffer.readUInt32BE(16),viewportBuffer.readUInt32BE(20)],[width,height]);
      const layout=await evaluate(cdp, `(() => ({
        horizontalOverflow:document.documentElement.scrollWidth-innerWidth,
        regions:${JSON.stringify(regions)}.map(selector=>{const node=document.querySelector(selector),r=node?.getBoundingClientRect();return {selector,exists:Boolean(node),width:r?.width||0,height:r?.height||0,visible:Boolean(r&&r.right>0&&r.left<innerWidth&&r.bottom>0&&r.top<innerHeight)};})
      }))()`);
      assert.ok(layout.horizontalOverflow<=1, `${page} ${width}x${height} horizontal overflow: ${layout.horizontalOverflow}px`);
      assert.ok(layout.regions.every(region=>region.exists&&region.visible&&region.width>40&&region.height>20), `${page} ${width}x${height} hidden/collapsed core region: ${JSON.stringify(layout.regions)}`);
      if(page==='dispatch-tasks'){ await pressEnter(cdp); await waitFor(cdp,"document.querySelector('#drawer-mask').classList.contains('open')"); }
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
    assert.equal(await evaluate(cdp, "!/(人民路|中山路|解放路|城北物流园)/.test(document.querySelector('#kanban-board').textContent)"), true);
    assert.equal(await evaluate(cdp, "/(惠水|都匀|贵定|福泉|独山|荔波)/.test(document.querySelector('#kanban-board').textContent)"), true);
    assert.deepEqual(await evaluate(cdp, "Array.from(document.querySelectorAll('#f-route option')).slice(1,-1).map(option=>option.textContent)"), ["贵北高速惠水段","惠水服务区","都匀互通","福泉贵定高架","独山森林高速","荔波喀斯特公路"]);
    await evaluate(cdp, "document.querySelector('[data-view=list]').click()");
    assert.equal(await evaluate(cdp, `(()=>{const codes=['RW-20260905-012','RW-20260905-022','RW-20260905-015','RW-20260905-013','RW-20260905-024','RW-20260905-014'];const positions=codes.map(code=>{document.querySelector('[data-act=detail][data-code="'+code+'"]').click();const position=document.querySelector('#mm-live-image').style.backgroundPosition;document.querySelector('#monitor-close').click();return position});return new Set(positions).size})()`), 6);
    await evaluate(cdp, "document.querySelector('[data-view=kanban]').click()");
    await evaluate(cdp, "document.querySelector('.k-card.drillable:not(.clickable)').click()");
    await waitFor(cdp, "document.querySelector('#monitor-mask').classList.contains('open')");
    assert.equal(await evaluate(cdp, "document.querySelector('#mm-title').textContent"), "任务详情");
    assert.equal(await evaluate(cdp, "document.querySelector('#mm-controls').hidden"), true);
    assert.equal(await evaluate(cdp, "document.querySelector('#mm-video').hidden"), true);
    assert.equal(await evaluate(cdp, "document.querySelector('#mm-telemetry').hidden"), true);
    assert.equal(await evaluate(cdp, "document.querySelector('#mm-trajectory').hidden"), true);
    assert.equal(await evaluate(cdp, "document.querySelector('#mm-body').classList.contains('details-only')"), true);
    await evaluate(cdp, "document.querySelector('#monitor-close').click()");
    assert.equal(await evaluate(cdp, "Array.from(document.querySelectorAll('.k-card.drillable')).every(card=>card.tabIndex===0&&card.getAttribute('role')==='button')"), true);
    await evaluate(cdp, "(()=>{const card=document.querySelector('.k-card.drillable');card.focus();card.dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true}))})()");
    await waitFor(cdp, "document.querySelector('#monitor-mask').classList.contains('open')");
    await evaluate(cdp, "document.querySelector('#monitor-close').click()");
    await evaluate(cdp, "(()=>{const card=document.querySelectorAll('.k-card.drillable')[1];card.focus();card.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))})()");
    await waitFor(cdp, "document.querySelector('#monitor-mask').classList.contains('open')");
    await evaluate(cdp, "document.querySelector('#monitor-close').click()");
    await evaluate(cdp, "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'})); document.querySelector('.k-card.clickable').click()");
    await waitFor(cdp, "document.querySelector('#monitor-mask').classList.contains('open')");
    assert.match(await evaluate(cdp, "getComputedStyle(document.querySelector('#mm-live-image')).backgroundImage"), /road-regions-v1\.png/);
    assert.equal(await evaluate(cdp, "document.querySelector('#mm-telemetry').hidden || document.querySelector('#mm-trajectory').hidden"), false);
    await cdp.command("Emulation.setEmulatedMedia", {features:[{name:"prefers-reduced-motion",value:"reduce"}]});
    assert.equal(await evaluate(cdp, "getComputedStyle(document.querySelector('#mm-live-image')).animationName"), "none");
    await cdp.command("Emulation.setEmulatedMedia", {features:[]});
    assert.equal(await evaluate(cdp, "document.querySelector('#mm-live-image').getAttribute('aria-label').endsWith('无人机巡检实景图像')"), true);
    const firstRoadPosition=await evaluate(cdp, "document.querySelector('#mm-live-image').style.backgroundPosition");
    await evaluate(cdp, "document.querySelector('#monitor-close').click(); document.querySelectorAll('.k-card.clickable')[1].click()");
    assert.notEqual(await evaluate(cdp, "document.querySelector('#mm-live-image').style.backgroundPosition"), firstRoadPosition);
    await evaluate(cdp, "document.querySelector('#monitor-close').click(); document.querySelector('.k-card.clickable').click()");
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
    const taskSideAlert = await evaluate(cdp, `(async()=>{const state=await (await fetch('/api/state')).json();return state.alerts.find(a=>a.id==='GJ-AUTO-RW-20260905-012')})()`);
    assert.equal(taskSideAlert.type, "图传断链");
    await evaluate(cdp, "document.querySelector('#monitor-close').click(); document.querySelector('.k-card.clickable').click(); document.querySelector('#mm-stop-btn').click()");
    const terminatedTaskCode = await evaluate(cdp, "document.querySelector('#mm-stop-btn').dataset.code");
    assert.equal(await evaluate(cdp, "document.querySelector('#confirm-mask').classList.contains('open')"), true);
    await evaluate(cdp, "document.querySelector('#cf-cancel').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#confirm-mask').classList.contains('open')"), false);
    await evaluate(cdp, "document.querySelector('#mm-stop-btn').click(); document.querySelector('#cf-ok').click()");
    await waitFor(cdp, "Array.from(document.querySelectorAll('.k-head')).some(h=>h.textContent.includes('已终止') && h.textContent.match(/1/))");
    const terminationAudit = await (await fetch(`${baseUrl}/api/audit`)).json();
    assert.ok(terminationAudit.audit.some(item=>item.subjectId===terminatedTaskCode&&item.action==='MISSION_DEMO_DATA_ARCHIVED'&&item.result==='SUCCESS'));

    const regionalPages = [];
    for (const page of ["screen-overview", "dispatch-tasks", "alert-workbench", "stats-ledger"]) {
      await navigate(cdp, `${baseUrl}/prototype/${page}.html`);
      regionalPages.push(await evaluate(cdp, "document.body.textContent"));
    }
    const regionalText = regionalPages.join(" ");
    for (const place of ["惠水", "都匀", "贵定", "福泉", "独山", "荔波"]) assert.ok(regionalText.includes(place), place);
    assert.doesNotMatch(regionalText, /人民路|中山路|解放路|城北物流园/);

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
    assert.match(await evaluate(cdp, "getComputedStyle(document.querySelector('#ev-scene .ev-photo-img')).backgroundImage"), /event-scenes-v1\.png/);
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
    await evaluate(cdp, "document.querySelector('.alert-item[data-i=\"1\"]').click()");
    assert.deepEqual(await evaluate(cdp, `(() => ({
      list: Array.from(document.querySelectorAll('.alert-item.selected'), item => item.dataset.i),
      map: Array.from(document.querySelectorAll('#layer-alerts g.selected'), marker => marker.dataset.i),
      pressed: document.querySelector('.alert-item[data-i="1"]').getAttribute('aria-pressed')
    }))()`), { list:["1"], map:["1"], pressed:"true" });
    const resolvedMarker = await evaluate(cdp, `(() => { const alerts=Array.from(document.querySelectorAll('#layer-alerts g')); const item=alerts.find(g=>g.getAttribute('aria-label').includes('交通事故')); return {pulse:Boolean(item.querySelector('.pulse-ring')), index:item.dataset.i}; })()`);
    assert.equal(resolvedMarker.pulse, false);
    await evaluate(cdp, `document.querySelector('#layer-alerts g[data-i="${resolvedMarker.index}"]').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
    assert.ok((await evaluate(cdp, "document.querySelector('#info-rows').textContent")).includes("已办结"));
    assert.match(await evaluate(cdp, "document.querySelector('#info-drill').getAttribute('href')"), /alert=GJ-20260905-031/);

    // AC08: one filter expression drives metrics, charts, drill-down and ledger detail.
    await navigate(cdp, `${baseUrl}/prototype/stats-ledger.html`);
    await waitFor(cdp, "document.querySelectorAll('#ledger-tbody tr.row').length > 0");
    assert.deepEqual(await evaluate(cdp, "Array.from(document.querySelectorAll('#f-district option')).slice(1).map(option => option.value)"), ["都匀城区段", "福泉贵定段", "独山荔波段"]);
    assert.deepEqual(await evaluate(cdp, "Array.from(new Set(Array.from(document.querySelectorAll('#ledger-tbody tr.row td:nth-child(4)'), cell => cell.textContent))).sort()"), ["独山荔波段", "福泉贵定段", "都匀城区段"]);
    await evaluate(cdp, "document.querySelector('#f-district').value='都匀城区段'; document.querySelector('#btn-query').click()");
    await waitFor(cdp, "document.querySelector('#filter-capsules').textContent.includes('辖区：都匀城区段')");
    assert.equal(await evaluate(cdp, "document.querySelector('.stat-card .sc-value').textContent.trim()"), "7项");
    assert.deepEqual(await evaluate(cdp, "Array.from(document.querySelectorAll('.stat-card'), card => [card.tagName, card.dataset.drill])"), [
      ["BUTTON", "tasks"], ["BUTTON", "flights"], ["BUTTON", "km"], ["BUTTON", "clues"], ["BUTTON", "rate"]
    ]);
    await evaluate(cdp, "document.querySelector('.stat-card[data-drill=\"km\"]').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('[data-arr=\"km\"]').textContent"), "↓");
    assert.ok((await evaluate(cdp, "document.querySelector('.toast:last-child').textContent")).includes("巡查里程"));
    assert.equal(await evaluate(cdp, "document.querySelector('#ledger-count').textContent"), "共 7 条台账 · 10 条/页");
    await evaluate(cdp, "document.querySelector('#filter-capsules button[data-k=\"district\"]').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#ledger-count').textContent"), "共 15 条台账 · 10 条/页");
    await evaluate(cdp, "document.querySelector('#trend-svg [data-date]').dispatchEvent(new MouseEvent('click',{bubbles:true}))");
    assert.ok((await evaluate(cdp, "document.querySelector('#filter-capsules').textContent")).includes("起始："));
    await evaluate(cdp, "document.querySelector('#ledger-tbody [data-act=\"view\"]').click()");
    assert.equal(await evaluate(cdp, "Boolean(document.querySelector('.expand-row'))"), true);
    assert.equal(await evaluate(cdp, "Boolean(document.querySelector('.expand-row a[href*=\"alert-workbench\"]'))"), true);
    assert.match(await evaluate(cdp, "document.querySelector('.expand-row a[href*=\"alert-workbench\"]').getAttribute('href')"), /type=/);
    await evaluate(cdp, "document.querySelector('th[data-sort=\"km\"]').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('[data-arr=\"km\"]').classList.contains('on')"), true);
    await evaluate(cdp, "document.querySelector('#duration-svg [data-duration-bin]').dispatchEvent(new MouseEvent('click',{bubbles:true}))");
    assert.ok((await evaluate(cdp, "document.querySelector('#filter-capsules').textContent")).includes("处置时长："));
    await evaluate(cdp, "document.querySelector('#f-date-from').value='2025-01-01'; document.querySelector('#f-date-to').value='2025-01-02'; document.querySelector('#btn-query').click()");
    await waitFor(cdp, "document.querySelector('#empty-state').classList.contains('show')");
    assert.equal(await evaluate(cdp, "document.querySelector('#btn-adjust').textContent"), "调整筛选条件");
    await evaluate(cdp, "document.querySelector('#btn-reset').click(); document.querySelector('#btn-export').click(); document.querySelector('#tpl-grid [data-tpl=\"周报\"]').click(); document.querySelector('#export-format').value='pdf'; document.querySelector('#export-format').dispatchEvent(new Event('change',{bubbles:true}))");
    assert.ok((await evaluate(cdp, "document.querySelector('#export-preview').textContent")).includes("PDF"));
    await evaluate(cdp, "document.querySelector('#export-fail').checked=true; document.querySelector('#export-generate').click()");
    await waitFor(cdp, "Array.from(document.querySelectorAll('.toast')).some(t=>t.textContent.includes('报表生成失败'))");
    assert.equal(await evaluate(cdp, "document.querySelector('#export-generate').textContent"), "重新生成");
    await evaluate(cdp, "document.querySelector('#export-fail').checked=false; document.querySelector('#export-generate').click()");
    await waitFor(cdp, "Array.from(document.querySelectorAll('.toast')).some(t=>t.textContent.includes('生成成功'))");
    const exportAudit = await (await fetch(`${baseUrl}/api/audit`)).json();
    assert.ok(exportAudit.audit.some(item=>item.action==='REPORT_EXPORTED' && item.result==='SUCCESS'));

    const injectionText = '<img id="stored-xss" src=x onerror="window.__storedXss=true">';
    await fetch(`${baseUrl}/api/tasks/RW-20260905-002`, {
      method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: injectionText })
    });
    await cdp.command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    await navigate(cdp, `${baseUrl}/prototype/screen-overview.html`);
    assert.deepEqual(await evaluate(cdp, "Array.from(document.querySelectorAll('.col'),column=>Math.round(column.getBoundingClientRect().width))"), [350,350]);
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
    await evaluate(cdp, "document.querySelector('.fleet-row[data-i=\"3\"]').click()");
    assert.equal(await evaluate(cdp, "document.querySelector('#info-card').classList.contains('show')"), true);
    assert.ok(await evaluate(cdp, `(() => {
      const card = document.querySelector('#info-card').getBoundingClientRect();
      const map = document.querySelector('.map-wrap').getBoundingClientRect();
      return card.right <= map.left;
    })()`), 'expected the drone/alert detail card to stay left of the map');
    assert.ok(await evaluate(cdp, `(() => {
      const card = document.querySelector('#info-card').getBoundingClientRect();
      const fleet = document.querySelector('[data-od-id="panel-fleet"]').getBoundingClientRect();
      return card.bottom <= fleet.top;
    })()`), 'expected the map detail card not to cover fleet status');
    await waitFor(cdp, "document.querySelector('#info-thumb video')?.readyState >= 2");
    assert.match(await evaluate(cdp, "document.querySelector('#info-thumb video').getAttribute('aria-label')"), /告警回传/);
    assert.equal(await evaluate(cdp, "document.querySelector('#info-thumb video').muted && document.querySelector('#info-thumb video').loop"), true);
    assert.match(await evaluate(cdp, "document.querySelector('#info-thumb video source').getAttribute('src')"), /demo-aerial-1/);
    assert.match(await evaluate(cdp, "document.querySelector('#info-thumb').textContent"), /POC 模拟/);
    assert.ok(await evaluate(cdp, `(() => {
      const media = document.querySelector('#info-thumb').getBoundingClientRect();
      return Math.abs(media.width / media.height - 16 / 9) < .03;
    })()`), 'expected the return-video viewport to remain 16:9');
    const videoVisibility = await evaluate(cdp, `(() => {
      const video = document.querySelector('#info-thumb video');
      const canvas = document.createElement('canvas');
      canvas.width = 160;
      canvas.height = 90;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let luminance = 0;
      let visiblePixels = 0;
      for (let index = 0; index < pixels.length; index += 4) {
        const value = pixels[index] * .2126 + pixels[index + 1] * .7152 + pixels[index + 2] * .0722;
        luminance += value;
        if (value >= 45) visiblePixels += 1;
      }
      const count = pixels.length / 4;
      return { meanLuminance: luminance / count, visibleRatio: visiblePixels / count };
    })()`);
    assert.ok(videoVisibility.meanLuminance >= 45 && videoVisibility.visibleRatio >= .35,
      `expected a clearly visible return-video frame, got ${JSON.stringify(videoVisibility)}`);
    for (const index of [1,2,3,4,5,6]) {
      const eventType = await evaluate(cdp, `document.querySelector('.alert-item[data-i="${index}"] .alert-type').textContent`);
      await evaluate(cdp, `document.querySelector('.alert-item[data-i="${index}"]').click()`);
      assert.equal(await evaluate(cdp, "document.querySelector('#info-thumb .event-photo')?.getAttribute('aria-label')"), `${eventType}实景演示图像`);
      assert.match(await evaluate(cdp, "getComputedStyle(document.querySelector('#info-thumb .event-photo')).backgroundImage"), /event-scenes-v1\.png/);
      assert.equal(await evaluate(cdp, `new Promise(resolve=>{const image=new Image();image.onload=()=>resolve(image.naturalWidth>0&&image.naturalHeight>0);image.onerror=()=>resolve(false);image.src='/prototype-assets/alerts/event-scenes-v1.png?v=photo-1'})`), true);
      assert.match(await evaluate(cdp, "document.querySelector('#info-thumb').textContent"), new RegExp(eventType));
    }
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
