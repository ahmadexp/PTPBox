import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the PTPBox product shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>PTPBox — Precision Time Lab<\/title>/i);
  assert.match(html, /Cascade overview/);
  assert.match(html, /Seven-stage clock cascade/);
  assert.match(html, /NIC clocks relative to BC1/);
  assert.match(html, /measurement mode pending/);
  assert.match(html, /Apply settings/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Starter Project/i);
});

test("ships the live-agent and standalone-host surfaces", async () => {
  const [page, layout, agent, packageJson, standalone] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../agent/ptpbox_agent.py", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../standalone/index.html", import.meta.url), "utf8"),
  ]);

  assert.match(page, /\/api\/status/);
  assert.match(page, /\/api\/telemetry/);
  assert.match(page, /read-only kernel cross timestamps/);
  assert.match(page, /Kernel cross timestamps place every PHC at a common epoch/i);
  assert.match(page, /Analytics/);
  assert.match(page, /Multi-pendulum/);
  assert.match(page, /Cascade phase pendulum/);
  assert.match(page, /Automatic equilibrium zeroing/);
  assert.match(page, /not a simulated gravity model/i);
  assert.match(page, /Covariance lab/);
  assert.match(page, /Cross-hop covariance matrix/);
  assert.match(page, /Rolling pair matrix/);
  assert.match(page, /Eigen spectrum/);
  assert.match(page, /Pendulum zeroing does not enter this path/);
  assert.match(page, /State-space atlas/);
  assert.match(page, /Principal state plane/);
  assert.match(page, /Poincaré map/);
  assert.match(page, /Eigenvalues through time/);
  assert.match(page, /not, by itself, evidence of a periodic orbit/i);
  assert.match(page, /Experiments/);
  assert.match(page, /Interfaces/);
  assert.match(page, /Configuration/);
  assert.match(page, /\/api\/servo\/control/);
  assert.match(page, /Linear regression/);
  assert.match(page, /Enter holdover/);
  assert.match(page, /Notification center/);
  assert.match(page, /Mark all read/);
  assert.match(page, /Open full event log/);
  assert.match(page, /Search pages, clocks, measurements, or controls/);
  assert.match(page, /PRECISION OBSERVATORY COMMAND/);
  assert.match(page, /Synchronization frequency/);
  assert.match(page, /nearest valid rate/);
  assert.match(page, /logSyncInterval/);
  assert.match(layout, /PTPBox — Precision Time Lab/);
  assert.match(agent, /\/api\/telemetry/);
  assert.match(agent, /\/api\/config\/apply/);
  assert.match(agent, /\/api\/servo\/control/);
  assert.match(agent, /PTP_SYS_OFFSET_EXTENDED/);
  assert.match(packageJson, /build:standalone/);
  assert.match(standalone, /PTPBox — Precision Time Lab/);
});
