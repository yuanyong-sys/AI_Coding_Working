const status = document.querySelector("#message");
const content = document.querySelector("#content");

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

async function loadPage() {
  if (!content) return;
  const response = await fetch("/api/state", { cache: "no-store" });
  if (!response.ok) throw new Error("演示数据加载失败");
  const state = await response.json();
  const pageCollections = { overview: "drones", tasks: "tasks", alerts: "alerts", ledgers: "ledgers" };
  const collection = pageCollections[document.body.dataset.page];
  content.innerHTML = state[collection].map((item) => `<article><h2>${escapeHtml(item.name ?? item.type ?? item.id)}</h2><pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre></article>`).join("");
}

const resetDialog = document.querySelector("#reset-dialog");
document.querySelector("#reset")?.addEventListener("click", () => resetDialog.showModal());
document.querySelector("#confirm-reset")?.addEventListener("click", async () => {
  const response = await fetch("/api/demo/reset", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ confirmed: true }) });
  const result = await response.json();
  status.textContent = response.ok ? `已恢复 ${result.snapshotVersion}，审计记录 ${result.auditEntry.id}` : "恢复失败";
});

loadPage().catch((error) => { if (content) content.innerHTML = `<p class="error">${escapeHtml(error.message)}，请刷新重试。</p>`; });
