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
  assert.match(page, /Attractor Observatory/);
  assert.match(page, /Candidate attractor geometry/);
  assert.match(page, /False nearest neighbors/);
  assert.match(page, /Return map/);
  assert.match(page, /Poincaré section/);
  assert.match(page, /Eigenvalues through time/);
  assert.match(page, /no single plot is treated as proof of deterministic chaos/i);
  assert.match(page, /Experiments/);
  assert.match(page, /Interfaces/);
  assert.match(page, /Configuration/);
  assert.match(page, /\/api\/servo\/control/);
  assert.match(page, /Linear regression/);
  assert.match(page, /Kalman · phase \+ frequency/);
  assert.match(page, /two-state estimator with bounded PHC frequency control/i);
  assert.match(page, /Enter holdover/);
  assert.match(page, /Notification center/);
  assert.match(page, /Mark all read/);
  assert.match(page, /Open full event log/);
  assert.match(page, /Search pages, clocks, measurements, or controls/);
  assert.match(page, /PRECISION OBSERVATORY COMMAND/);
  assert.match(page, /Synchronization frequency/);
  assert.match(page, /nearest valid rate/);
  assert.match(page, /logSyncInterval/);
  assert.match(page, /\/api\/phc/);
  assert.match(page, /PHC sampling/);
  assert.match(page, /sampling follows the applied Sync cadence/);
  assert.match(page, /Metrology workbench/);
  assert.match(page, /Clock stability atlas/);
  assert.match(page, /Fractional-frequency deviations/);
  assert.match(page, /Time-error statistics/);
  assert.match(page, /PDEV/);
  assert.match(page, /TOTDEV/);
  assert.match(page, /TIE RMS/);
  assert.match(page, /effective τ = 0\.75mτ₀/);
  assert.match(page, /Confidence is intentionally omitted/);
  assert.match(page, /Path microscope/);
  assert.match(page, /Intelligence/);
  assert.match(page, /Holdover chamber/);
  assert.match(page, /Synchronize → release → observe → recover/);
  assert.match(page, /\/api\/holdover/);
  assert.match(page, /no smoothing · every visible point is measured/i);
  assert.match(page, /Resilience/);
  assert.match(page, /Adaptive Kalman/);
  assert.match(page, /INTERACTING MULTIPLE MODEL/);
  assert.match(page, /BAYESIAN ROOT-CAUSE WATCH/);
  assert.match(page, /COMMON-EDGE PHC COMPARISON/);
  assert.match(page, /Bode · Nyquist · sampled-data stability/);
  assert.match(page, /Jury \/ Schur conditions/);
  assert.match(page, /Routh–Hurwitz array/);
  assert.match(page, /Formal Nyquist encirclement requires the identified open-loop transfer/i);
  assert.match(page, /geometry only · not a margin/i);
  assert.match(page, /Replay bifurcation map/);
  assert.match(page, /settled extrema from bounded offline PI replay/i);
  assert.match(page, /true physical bifurcation claim requires a controlled hardware sweep/i);
  assert.match(page, /PI baseline is not live/i);
  assert.match(page, /NO LIVE CHANGES/);
  assert.match(page, /Fractal analysis/);
  assert.match(page, /CORRELATION DIMENSION/);
  assert.match(page, /HIGUCHI GRAPH DIMENSION/);
  assert.match(page, /MULTIFRACTAL SCALING/);
  assert.match(page, /None alone proves chaos, self-similarity, or a strange attractor/i);
  assert.match(page, /IEEE 802\.1AS gPTP/);
  assert.match(page, /PTP message authentication/);
  assert.match(page, /Inject bounded fault/);
  assert.match(page, /\/api\/research/);
  assert.match(page, /\/api\/experiments/);
  assert.match(page, /\/api\/fault\/control/);
  assert.match(layout, /PTPBox — Precision Time Lab/);
  assert.match(agent, /\/api\/telemetry/);
  assert.match(agent, /\/api\/config\/apply/);
  assert.match(agent, /\/api\/servo\/control/);
  assert.match(agent, /kalman/);
  assert.match(agent, /PTP_SYS_OFFSET_EXTENDED/);
  assert.match(agent, /\/api\/research/);
  assert.match(agent, /\/api\/path-events/);
  assert.match(agent, /\/api\/experiments/);
  assert.match(agent, /\/api\/holdover/);
  assert.match(agent, /\/api\/fault\/control/);
  assert.match(packageJson, /build:standalone/);
  assert.match(standalone, /PTPBox — Precision Time Lab/);
});
