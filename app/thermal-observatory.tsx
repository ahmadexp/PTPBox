"use client";

import { useState } from "react";
import { Activity, ShieldCheck, Thermometer } from "lucide-react";

type ScatterPoint = { temperature_c: number; frequency_ppb: number; elapsed_s: number };

export type ThermalNode = {
  node: string;
  status: string;
  samples?: number;
  reason?: string;
  record_span_s?: number;
  temperature?: {
    minimum_c: number; maximum_c: number; span_c: number; distinct_levels: number;
    quantised_to_whole_degrees: boolean; quantisation_sigma_c: number; mean_c: number;
  };
  frequency?: { mean_ppb: number; sigma_ppb: number; minimum_ppb: number; maximum_ppb: number };
  ols?: {
    tempco_ppb_per_c: number; frequency_at_mean_temperature_ppb: number;
    r_squared: number | null; residual_sigma_ppb: number;
    standard_error_ppb_per_c: number | null; t_statistic: number | null;
    confidence_95_ppb_per_c: [number, number] | null; note?: string;
  };
  errors_in_variables?: { deming_tempco_ppb_per_c: number | null; variance_ratio: number | null; method?: string };
  robust?: { theil_sen_tempco_ppb_per_c: number | null };
  model_selection?: { ranked_by_aic?: Array<{ model: string; aic: number; bic: number; r_squared: number | null }>; preferred?: string; note?: string };
  hysteresis?: { heating_samples: number; cooling_samples: number; heating_slope_ppb_per_c: number | null; cooling_slope_ppb_per_c: number | null; separation_ppb_per_c: number | null };
  thermal_lag?: { best_lag_samples: number | null; best_correlation: number | null; zero_lag_correlation: number | null };
  confounding?: { temperature_time_correlation: number | null; joint_fit?: { tempco_ppb_per_c: number; ageing_ppb_per_s: number; r_squared: number | null; residual_sigma_ppb: number } | null; note?: string };
  serial_correlation?: { residual_lag_one: number; effective_samples: number; note?: string };
  compensation_preview?: { raw_sigma_ppb: number; residual_sigma_ppb: number; reduction_pct: number | null };
  evidence?: { gates: Record<string, boolean>; gates_passed: number; gates_total: number; unmet: string[]; verdict: string };
  scatter?: ScatterPoint[];
};

export type ThermalPayload = {
  nodes?: Record<string, ThermalNode>;
  summary?: { analysed: number; supported: number; hottest_node: string | null; hottest_c: number | null };
  pairing?: { tolerance_s: number; paired_nodes: number; method?: string; temperature_source?: string; frequency_source?: string };
  window_s?: number;
  method?: string;
  interpretation?: string;
  live_changes?: number;
  timestamp?: number;
};

const GATE_LABELS: Record<string, string> = {
  enough_samples: "Sample count",
  enough_span: "Temperature span ≥ 5 °C",
  enough_levels: "≥ 4 distinct levels",
  not_time_confounded: "Not collinear with time",
  residuals_independent: "Residuals independent",
  effective_samples_sufficient: "Effective samples",
  slope_significant: "Slope significant",
};

function num(value: number | null | undefined, digits = 1, unit = "") {
  return value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(digits)}${unit}`;
}

/** Temperature against correction, with the fitted line and the Deming line. */
function TempcoScatter({ node }: { node: ThermalNode }) {
  const points = node.scatter ?? [];
  if (points.length < 4 || !node.ols || !node.temperature) {
    return <div className="empty-analysis">Not enough paired samples to plot</div>;
  }
  const width = 560, height = 230;
  const pad = { left: 58, right: 14, top: 14, bottom: 34 };
  const temps = points.map((p) => p.temperature_c);
  const freqs = points.map((p) => p.frequency_ppb);
  // Pad the temperature axis so quantised levels are not glued to the frame.
  const tMin = Math.min(...temps) - 0.5, tMax = Math.max(...temps) + 0.5;
  const fMin = Math.min(...freqs), fMax = Math.max(...freqs);
  const fPad = Math.max(1, (fMax - fMin) * 0.08);
  const x = (t: number) => pad.left + ((t - tMin) / Math.max(1e-9, tMax - tMin)) * (width - pad.left - pad.right);
  const y = (f: number) => pad.top + (1 - (f - (fMin - fPad)) / Math.max(1e-9, (fMax + fPad) - (fMin - fPad))) * (height - pad.top - pad.bottom);
  const elapsedMax = Math.max(...points.map((p) => p.elapsed_s), 1);

  const mean = node.temperature.mean_c;
  const intercept = node.ols.frequency_at_mean_temperature_ppb;
  const line = (slope: number) => {
    const a = { t: tMin, f: intercept + slope * (tMin - mean) };
    const b = { t: tMax, f: intercept + slope * (tMax - mean) };
    return `M${x(a.t)},${y(a.f)}L${x(b.t)},${y(b.f)}`;
  };
  const deming = node.errors_in_variables?.deming_tempco_ppb_per_c ?? null;

  return (
    <svg className="thm-scatter" viewBox={`0 0 ${width} ${height}`} role="img"
         aria-label={`Frequency correction against temperature for ${node.node}`}>
      <line className="axis" x1={pad.left} x2={width - pad.right} y1={height - pad.bottom} y2={height - pad.bottom} />
      <line className="axis" x1={pad.left} x2={pad.left} y1={pad.top} y2={height - pad.bottom} />
      {points.map((p, index) => (
        <circle key={index} cx={x(p.temperature_c)} cy={y(p.frequency_ppb)} r="2.1"
                style={{ opacity: 0.25 + 0.75 * (p.elapsed_s / elapsedMax) }}>
          <title>{`${p.temperature_c.toFixed(0)} °C · ${p.frequency_ppb.toFixed(1)} ppb · t+${p.elapsed_s.toFixed(0)} s`}</title>
        </circle>
      ))}
      <path className="fit" d={line(node.ols.tempco_ppb_per_c)} />
      {deming != null ? <path className="fit deming" d={line(deming)} /> : null}
      <text className="tick" x={pad.left - 8} y={y(fMax) + 3} textAnchor="end">{fMax.toFixed(0)}</text>
      <text className="tick" x={pad.left - 8} y={y(fMin) + 3} textAnchor="end">{fMin.toFixed(0)}</text>
      <text className="tick" x={pad.left} y={height - pad.bottom + 14} textAnchor="middle">{tMin.toFixed(1)}</text>
      <text className="tick" x={width - pad.right} y={height - pad.bottom + 14} textAnchor="middle">{tMax.toFixed(1)}</text>
      <text className="axis-title" x={pad.left + (width - pad.left - pad.right) / 2} y={height - 6} textAnchor="middle">DIE TEMPERATURE · °C</text>
      <text className="axis-title" transform={`rotate(-90 14 ${pad.top + 70})`} x="14" y={pad.top + 70}>CORRECTION · ppb</text>
      <g className="thm-legend" transform={`translate(${pad.left + 8},${pad.top + 10})`}>
        <line x1="0" x2="18" y1="0" y2="0" className="fit" /><text x="23" y="3">OLS</text>
        {deming != null ? <><line x1="60" x2="78" y1="0" y2="0" className="fit deming" /><text x="83" y="3">Deming</text></> : null}
      </g>
      <text className="thm-note" x={width - pad.right} y={pad.top + 12} textAnchor="end">opacity = time</text>
    </svg>
  );
}

/** Residual against temperature: structure here means the linear model is wrong. */
function ResidualPlot({ node }: { node: ThermalNode }) {
  const points = node.scatter ?? [];
  if (points.length < 4 || !node.ols || !node.temperature) return null;
  const width = 560, height = 130;
  const pad = { left: 58, right: 14, top: 12, bottom: 28 };
  const mean = node.temperature.mean_c;
  const intercept = node.ols.frequency_at_mean_temperature_ppb;
  const slope = node.ols.tempco_ppb_per_c;
  const residuals = points.map((p) => ({ t: p.temperature_c, r: p.frequency_ppb - (intercept + slope * (p.temperature_c - mean)) }));
  const magnitude = Math.max(1, ...residuals.map((p) => Math.abs(p.r)));
  const tMin = Math.min(...points.map((p) => p.temperature_c)) - 0.5;
  const tMax = Math.max(...points.map((p) => p.temperature_c)) + 0.5;
  const x = (t: number) => pad.left + ((t - tMin) / Math.max(1e-9, tMax - tMin)) * (width - pad.left - pad.right);
  const y = (r: number) => pad.top + (magnitude - r) / (2 * magnitude) * (height - pad.top - pad.bottom);
  return (
    <svg className="thm-residual" viewBox={`0 0 ${width} ${height}`} role="img"
         aria-label={`Fit residual against temperature for ${node.node}`}>
      <line className="zero" x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} />
      {residuals.map((p, index) => (
        <circle key={index} cx={x(p.t)} cy={y(p.r)} r="1.9"><title>{`${p.t.toFixed(0)} °C · residual ${p.r.toFixed(1)} ppb`}</title></circle>
      ))}
      <text className="tick" x={pad.left - 8} y={y(magnitude) + 3} textAnchor="end">{magnitude.toFixed(0)}</text>
      <text className="tick" x={pad.left - 8} y={y(-magnitude) + 3} textAnchor="end">{(-magnitude).toFixed(0)}</text>
      <text className="axis-title" x={pad.left + (width - pad.left - pad.right) / 2} y={height - 4} textAnchor="middle">RESIDUAL vs TEMPERATURE · ppb</text>
    </svg>
  );
}

function GateList({ node }: { node: ThermalNode }) {
  const gates = node.evidence?.gates ?? {};
  return (
    <div className="thm-gates">
      {Object.entries(gates).map(([key, value]) => (
        <div key={key} className={value ? "pass" : "fail"}>
          <i />{GATE_LABELS[key] ?? key}
        </div>
      ))}
    </div>
  );
}

export function ThermalObservatory({ thermal }: { thermal: ThermalPayload | null }) {
  const nodes = thermal?.nodes ?? {};
  const names = Object.keys(nodes);
  const [selected, setSelected] = useState<string | null>(null);
  const active = selected && nodes[selected] ? nodes[selected] : nodes[names.find((n) => nodes[n].status === "ready") ?? names[0]];

  if (!thermal) {
    return (
      <section className="panel">
        <div className="panel-heading"><div><span className="section-kicker">OSCILLATOR THERMAL RESPONSE</span><h2>Temperature coefficient</h2></div></div>
        <div className="empty-analysis">Waiting for <code>/api/thermal</code></div>
      </section>
    );
  }

  const summary = thermal.summary ?? { analysed: 0, supported: 0, hottest_node: null, hottest_c: null };

  return (
    <>
      <section className="panel">
        <div className="panel-heading">
          <div><span className="section-kicker">OSCILLATOR THERMAL RESPONSE</span><h2>Correction versus die temperature</h2></div>
          <span className={`quality-badge ${summary.supported ? "" : "warning"}`}>
            {summary.supported}/{summary.analysed} SUPPORTED
          </span>
        </div>
        <div className="thm-summary">
          <div><small>Clocks analysed</small><b>{summary.analysed}</b></div>
          <div><small>Coefficient supported</small><b>{summary.supported}</b></div>
          <div><small>Hottest adapter</small><b>{summary.hottest_node ?? "—"} {summary.hottest_c != null ? `· ${summary.hottest_c.toFixed(0)} °C` : ""}</b></div>
          <div><small>Paired window</small><b>{num(thermal.window_s, 0, " s")}</b></div>
        </div>
        <div className="dyn-evidence-note"><ShieldCheck size={14} /><span>{thermal.interpretation}</span></div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div><span className="section-kicker">PER-CLOCK ESTIMATE</span><h2>Temperature coefficient by estimator</h2></div>
          <span className="panel-meta">quantised regressor attenuates least squares</span>
        </div>
        <table className="sys-table thm-table">
          <thead><tr><th>Clock</th><th>Span</th><th>Levels</th><th>OLS ppb/°C</th><th>Deming</th><th>Theil–Sen</th><th>R²</th><th>N<sub>eff</sub></th><th>Verdict</th></tr></thead>
          <tbody>
            {names.map((name) => {
              const n = nodes[name];
              if (n.status !== "ready") {
                return <tr key={name} className="muted"><td><strong>{name}</strong></td><td colSpan={8}>{n.status}{n.reason ? ` · ${n.reason}` : ""}</td></tr>;
              }
              return (
                <tr key={name} className={active?.node === name ? "selected" : ""} onClick={() => setSelected(name)}>
                  <td><strong>{name}</strong></td>
                  <td>{num(n.temperature?.span_c, 1, " °C")}</td>
                  <td>{n.temperature?.distinct_levels ?? "—"}</td>
                  <td>{num(n.ols?.tempco_ppb_per_c)}{n.ols?.standard_error_ppb_per_c != null ? ` ±${n.ols.standard_error_ppb_per_c.toFixed(1)}` : ""}</td>
                  <td>{num(n.errors_in_variables?.deming_tempco_ppb_per_c)}</td>
                  <td>{num(n.robust?.theil_sen_tempco_ppb_per_c)}</td>
                  <td>{num(n.ols?.r_squared, 3)}</td>
                  <td>{num(n.serial_correlation?.effective_samples, 0)}</td>
                  <td><span className={`thm-verdict ${n.evidence?.verdict}`}>{n.evidence?.verdict}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {active && active.status === "ready" ? (
        <>
          <section className="panel">
            <div className="panel-heading">
              <div><span className="section-kicker">{active.node}</span><h2><Thermometer size={15} /> Correction against temperature</h2></div>
              <span className="panel-meta">{active.samples} paired samples · {num(active.record_span_s, 0, " s")}</span>
            </div>
            <TempcoScatter node={active} />
            <ResidualPlot node={active} />
            <div className="dyn-evidence-note"><Activity size={14} /><span>{active.ols?.note}</span></div>
          </section>

          <div className="sys-grid">
            <section className="panel">
              <div className="panel-heading"><div><span className="section-kicker">EVIDENCE</span><h2>Gates</h2></div>
                <span className={`quality-badge ${active.evidence?.verdict === "supported" ? "" : "warning"}`}>
                  {active.evidence?.gates_passed}/{active.evidence?.gates_total}
                </span>
              </div>
              <GateList node={active} />
              {active.evidence?.unmet?.length ? (
                <div className="sys-stat-row"><span>Unmet</span><strong>{active.evidence.unmet.map((g) => GATE_LABELS[g] ?? g).join(", ")}</strong></div>
              ) : null}
            </section>

            <section className="panel">
              <div className="panel-heading"><div><span className="section-kicker">CONFOUNDING</span><h2>Temperature or ageing?</h2></div></div>
              <div className="sys-stat-row"><span>Temperature ↔ time correlation</span><strong>{num(active.confounding?.temperature_time_correlation, 3)}</strong></div>
              <div className="sys-stat-row"><span>Joint-fit coefficient</span><strong>{num(active.confounding?.joint_fit?.tempco_ppb_per_c)} ppb/°C</strong></div>
              <div className="sys-stat-row"><span>Separated ageing</span><strong>{num(active.confounding?.joint_fit?.ageing_ppb_per_s, 4)} ppb/s</strong></div>
              <div className="sys-stat-row"><span>Joint R²</span><strong>{num(active.confounding?.joint_fit?.r_squared, 3)}</strong></div>
              <div className="dyn-evidence-note"><ShieldCheck size={14} /><span>{active.confounding?.note}</span></div>
            </section>
          </div>

          <div className="sys-grid">
            <section className="panel">
              <div className="panel-heading"><div><span className="section-kicker">HYSTERESIS</span><h2>Heating versus cooling</h2></div></div>
              <div className="sys-stat-row"><span>Heating slope</span><strong>{num(active.hysteresis?.heating_slope_ppb_per_c)} ppb/°C <small>({active.hysteresis?.heating_samples} pts)</small></strong></div>
              <div className="sys-stat-row"><span>Cooling slope</span><strong>{num(active.hysteresis?.cooling_slope_ppb_per_c)} ppb/°C <small>({active.hysteresis?.cooling_samples} pts)</small></strong></div>
              <div className="sys-stat-row"><span>Branch separation</span><strong>{num(active.hysteresis?.separation_ppb_per_c)} ppb/°C</strong></div>
              <div className="sys-stat-row"><span>Best lag</span><strong>{active.thermal_lag?.best_lag_samples ?? "—"} samples · r {num(active.thermal_lag?.best_correlation, 3)}</strong></div>
            </section>

            <section className="panel">
              <div className="panel-heading"><div><span className="section-kicker">MODEL ORDER</span><h2>AIC ranking</h2></div>
                <span className="panel-meta">preferred: {active.model_selection?.preferred ?? "—"}</span>
              </div>
              <table className="sys-table"><thead><tr><th>Model</th><th>AIC</th><th>BIC</th><th>R²</th></tr></thead>
                <tbody>{(active.model_selection?.ranked_by_aic ?? []).map((m) => (
                  <tr key={m.model}><td>{m.model}</td><td>{m.aic.toFixed(1)}</td><td>{m.bic.toFixed(1)}</td><td>{num(m.r_squared, 3)}</td></tr>
                ))}</tbody>
              </table>
              <div className="dyn-evidence-note"><ShieldCheck size={14} /><span>{active.model_selection?.note}</span></div>
            </section>
          </div>

          <section className="panel">
            <div className="panel-heading"><div><span className="section-kicker">IF COMPENSATED</span><h2>Residual after removing the fitted response</h2></div></div>
            <div className="thm-summary">
              <div><small>Raw σ</small><b>{num(active.compensation_preview?.raw_sigma_ppb)} ppb</b></div>
              <div><small>Residual σ</small><b>{num(active.compensation_preview?.residual_sigma_ppb)} ppb</b></div>
              <div><small>Reduction</small><b>{num(active.compensation_preview?.reduction_pct)} %</b></div>
              <div><small>Residual lag-1</small><b>{num(active.serial_correlation?.residual_lag_one, 3)}</b></div>
            </div>
            <div className="dyn-evidence-note"><ShieldCheck size={14} /><span>{active.serial_correlation?.note} {thermal.pairing?.method}</span></div>
          </section>
        </>
      ) : null}
    </>
  );
}
