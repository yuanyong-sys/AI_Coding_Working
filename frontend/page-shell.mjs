const pages = {
  "screen-overview.html": { title: "态势一张图", collection: "overview" },
  "dispatch-tasks.html": { title: "任务调度", collection: "tasks" },
  "alert-workbench.html": { title: "告警线索处置", collection: "alerts" },
  "stats-ledger.html": { title: "巡检台账统计", collection: "ledgers" }
};

export function renderPage(filename) {
  const page = pages[filename];
  if (!page) return null;
  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>${page.title}</title>
    <link rel="stylesheet" href="/styles.css">
  </head>
  <body data-page="${page.collection}" data-api="/api/state">
    <header><a href="/">返回</a><span class="badge">POC 演示数据</span><h1>${page.title}</h1></header>
    <main id="content" class="dashboard"></main>
    <script type="module" src="/app.js"></script>
  </body>
</html>`;
}

export const pageFilenames = new Set(Object.keys(pages));
