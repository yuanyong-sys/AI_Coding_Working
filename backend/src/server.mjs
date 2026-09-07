import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { PocStore, businessState } from "./store.mjs";
import { pageFilenames, renderPage } from "../../frontend/page-shell.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const frontendRoot = path.join(projectRoot, "frontend");
const designRoot = path.join(projectRoot, "无人机设计方案");

const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8"
};

function sendJson(response, status, value) {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  response.end(JSON.stringify(value));
}

async function readJson(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

async function serveFile(response, filename) {
  try {
    const body = await readFile(path.join(frontendRoot, filename));
    response.writeHead(200, { "content-type": contentTypes[path.extname(filename)] ?? "application/octet-stream" });
    response.end(body);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    sendJson(response, 404, { error: "NOT_FOUND" });
  }
}

export function createPocServer({ databasePath = process.env.POC_DATABASE_PATH ?? path.join(projectRoot, "backend/data/poc.json") } = {}) {
  const store = new PocStore(databasePath);
  return createServer(async (request, response) => {
    try {
      const url = new URL(request.url, "http://localhost");
      if (request.method === "GET" && url.pathname === "/api/state") {
        return sendJson(response, 200, await store.read());
      }
      if (request.method === "GET" && url.pathname === "/api/audit") {
        return sendJson(response, 200, { audit: (await store.read()).audit });
      }
      if (request.method === "POST" && url.pathname === "/api/demo/reset") {
        const input = await readJson(request);
        if (input.confirmed !== true) return sendJson(response, 409, { error: "CONFIRMATION_REQUIRED" });
        const state = await store.reset();
        return sendJson(response, 200, { snapshotVersion: state.snapshotVersion, businessState: businessState(state), auditEntry: state.audit.at(-1) });
      }
      const entityMatch = url.pathname.match(/^\/api\/(drones|tasks|alerts|ledgers)\/([^/]+)$/);
      if (request.method === "PATCH" && entityMatch) {
        const entity = await store.updateEntity(entityMatch[1], decodeURIComponent(entityMatch[2]), await readJson(request));
        return entity ? sendJson(response, 200, entity) : sendJson(response, 404, { error: "NOT_FOUND" });
      }
      if (request.method === "GET" && url.pathname === "/") return serveFile(response, "index.html");
      const filename = url.pathname.slice(1);
      if (request.method === "GET" && filename === "screen-overview.html") {
        const body = await readFile(path.join(designRoot, filename));
        response.writeHead(200, { "content-type": contentTypes[".html"] });
        return response.end(body);
      }
      if (request.method === "GET" && pageFilenames.has(filename)) {
        response.writeHead(200, { "content-type": contentTypes[".html"] });
        return response.end(renderPage(filename));
      }
      if (request.method === "GET" && (filename === "index.html" || filename === "app.js" || filename === "styles.css")) {
        return serveFile(response, filename);
      }
      return sendJson(response, 404, { error: "NOT_FOUND" });
    } catch (error) {
      sendJson(response, 500, { error: "INTERNAL_ERROR", message: error.message });
    }
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT ?? 8765);
  createPocServer().listen(port, "127.0.0.1", () => {
    console.log(`POC server listening at http://127.0.0.1:${port}`);
  });
}
