"use client";

import {
  Activity,
  AlertTriangle,
  Check,
  FlaskConical,
  Gauge,
  Network,
  Orbit,
  Play,
  ShieldCheck,
  Square,
  Waves,
} from "lucide-react";
import { useMemo, useState } from "react";

type Status = { status?: string; samples?: number; method?: string; interpretation?: string };
type CurvePoint = { tau_s: number; value: number; pairs: number };

export type IdentificationState = {
  enabled?: boolean;
  target?: string;
  servo?: string;
  amplitude_ppb?: number;
  frequencies_hz?: number[];
  offset_limit_ns?: number;
  started_at?: number;
  expires_at?: number;
  duration_s?: number;
  reason?: string;
};

export type DynamicsPayload = {
  transfer_noise?: Status & {
    qualified_residual?: boolean;
    provenance?: string;
    ftu?: CurvePoint[];
    adevs?: CurvePoint[];
    tierms?: CurvePoint[];
  };
  dynamic_stability?: Status & {
    window_s?: number;
    times_s?: number[];
    taus_s?: number[];
    cells?: Array<{
      time_s: number;
      tau_s: number;
      adev?: number;
      mdev?: number;
      ftu?: number;
      adevs_ns?: number;
      pairs: number;
    }>;
  };
  spectral_cascade?: Status & {
    channels?: string[];
    segments?: number;
    median_adjacent_coherence?: number | null;
    formal_string_stability?: boolean;
    points?: Array<{
      frequency_hz: number;
      total_power_db: number;
      dominant_share: number;
      hops: Array<{
        id: string;
        psd_db_ns2_hz: number;
        gain_db: number;
        cumulative_gain_db: number;
        coherence: number;
        phase_deg: number;
      }>;
      mode: Array<{ id: string; magnitude: number; phase_deg: number }>;
    }>;
  };
  multiresolution_modes?: Status & {
    provenance?: string;
    bands?: Array<{
      label: string;
      minimum_hz: number;
      maximum_hz: number;
      energy_share: number;
      dominant_share: number;
      loadings: Array<{ id: string; magnitude: number; phase_deg: number }>;
    }>;
  };
  hybrid_servo?: Status & {
    states?: Array<{
      state: string;
      samples: number;
      share: number;
      offset_rms_ns: number;
      correction_rms_ppb: number;
      median_dwell_samples: number;
      local_pole: number | null;
    }>;
    transitions?: Array<{ source: string; target: string; count: number; probability: number }>;
    timeline?: Array<{ observed_at: number; state: string; offset_ns: number; correction_ppb: number }>;
  };
  estimator_consistency?: Status & {
    points?: Array<{ index: number; observed_at: number; innovation_ns: number; nis: number; accepted: boolean }>;
    mean_nis?: number;
    within_95_pct?: number;
    lag_one_autocorrelation?: number;
    acceptance_pct?: number;
    consistent?: boolean;
  };
  identifiability?: Status & {
    normalized_information_eigenvalues?: number[];
    condition_number?: number | null;
    rank?: number;
    parameter_count?: number;
    input_sigma_ppb?: number;
    persistently_exciting?: boolean;
  };
  active_identification?: Status & {
    active?: boolean;
    target?: string | null;
    reason?: string;
    points?: Array<{
      frequency_hz: number;
      plant_magnitude_db: number;
      plant_phase_deg: number;
      loop_magnitude_db: number;
      loop_phase_deg: number;
      loop_real: number;
      loop_imag: number;
      sensitivity_db: number;
      complementary_sensitivity_db: number;
      control_sensitivity_db: number;
      coherence_excitation_output: number;
      coherence_excitation_input: number;
      relative_plant_scatter: number;
      balanced_disk_delta: number;
      iqc_lower_distance: number;
    }>;
    reliable_bins?: number;
    excitation_rms_ppb?: number;
    disk_margin?: {
      balanced_alpha?: number | null;
      gain_lower?: number | null;
      gain_upper?: number | null;
      phase_deg?: number | null;
      qualified_bins?: number;
    };
    iqc_envelope?: { robustly_separated?: boolean; minimum_distance?: number | null; model?: string };
  };
  timing_oam?: Status & {
    nodes?: Array<{
      id: string;
      samples: number;
      cte_ns: number;
      dte_rms_ns: number;
      peak_to_peak_ns: number;
      max_abs_te_ns: number;
      p95_abs_te_ns: number;
    }>;
    accumulation?: Array<{ id: string; hop_cte_ns: number; accumulated_cte_ns: number; hop_p2p_ns: number }>;
    thresholds?: Array<{ label: string; limit_ns: number; violations: string[]; pass: boolean; provenance: string }>;
  };
  holdover_risk?: Status & {
    initial_phase_ns?: number;
    frequency_ppb?: number;
    drift_ppb_s?: number;
    calibration?: string;
    forecast?: Array<{
      horizon_s: number;
      expected_ns: number;
      sigma_ns: number;
      lower_95_ns: number;
      upper_95_ns: number;
    }>;
    thresholds?: Array<{
      limit_ns: number;
      first_5pct_horizon_s: number | null;
      risks: Array<{ horizon_s: number; probability: number }>;
    }>;
  };
  clock_decomposition?: Status & {
    eligible?: boolean;
    eligibility_reason?: string;
    fit_residual_ppb2?: number;
    clocks?: Array<{ id: string; variance_ppb2: number; sigma_ppb: number }>;
  };
  path_regimes?: Status & {
    calibrated_asymmetry?: boolean;
    median_round_trip_ns?: number;
    median_directional_imbalance_ns?: number;
    regimes?: Array<{ name: string; samples: number; share: number }>;
    timeline?: Array<{
      node: string;
      observed_at: number;
      forward_ns: number;
      reverse_ns: number;
      round_trip_ns: number;
      imbalance_ns: number;
      regime?: string;
    }>;
  };
  nonlinear?: {
    bicoherence?: Status & {
      strongest?: { f1_hz: number; f2_hz: number; sum_hz: number; bicoherence: number } | null;
      screening_floor?: number;
      couplings?: Array<{ f1_hz: number; f2_hz: number; sum_hz: number; bicoherence: number }>;
    };
    topology?: Status & {
      max_beta1?: number;
      curve?: Array<{ radius_sigma: number; beta0: number; beta1: number; edges: number; triangles: number }>;
    };
    directed_dependence?: Status & {
      causal?: boolean;
      links?: Array<{ source: string; target: string; score: number; variance_reduction_pct: number }>;
    };
    multiscale_entropy?: Status & {
      points?: Array<{ scale_samples: number; entropy: number; matches: number }>;
    };
  };
};

type Props = {
  dynamics?: DynamicsPayload;
  nodes: Array<{ id: string; role: string; servoType?: string | null; servoEnabled?: boolean }>;
  connection: string;
  analysisMode: string;
  identification?: IdentificationState | null;
  identificationBusy: boolean;
  controlIdentification: (request: {
    target: string;
    enabled: boolean;
    amplitude_ppb?: number;
    duration_s?: number;
    offset_limit_ns?: number;
    frequencies_hz?: number[];
  }) => void;
};

const WIDTH = 560;
const HEIGHT = 172;
const PAD = { left: 48, right: 12, top: 14, bottom: 28 };

function finite(values: Array<number | undefined | null>) {
  return values.filter((value): value is number => typeof value === "number" && Number.isFinite(value));
}

function ns(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "—";
  const absolute = Math.abs(value);
  if (absolute >= 1_000_000) return `${(value / 1_000_000).toFixed(2)} ms`;
  if (absolute >= 1_000) return `${(value / 1_000).toFixed(2)} µs`;
  return `${value.toFixed(absolute < 10 ? 2 : 1)} ns`;
}

function compact(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 1000 || (Math.abs(value) > 0 && Math.abs(value) < 0.01)) return value.toExponential(2);
  return value.toFixed(2);
}

function statusClass(status?: string) {
  return status === "ready" ? "" : status === "gated" || status === "low-evidence" ? "warning" : "pending";
}

function EmptyAnalysis({ analysis, message }: { analysis?: Status; message: string }) {
  return (
    <div className="dyn-empty">
      <Activity size={20} />
      <strong>{message}</strong>
      <span>{analysis?.samples ?? 0} qualified samples · {analysis?.status ?? "waiting"}</span>
    </div>
  );
}

function LogCurve({
  points,
  label,
  units,
  color = "cyan",
}: {
  points: CurvePoint[];
  label: string;
  units: string;
  color?: "cyan" | "violet" | "mint" | "amber";
}) {
  if (points.length < 2) return <EmptyAnalysis analysis={{ samples: points.length }} message={`Collecting ${label}`} />;
  const xs = points.map((point) => Math.log10(point.tau_s));
  const ys = points.map((point) => Math.log10(Math.max(Number.MIN_VALUE, point.value)));
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);
  const x = (value: number) => PAD.left + ((Math.log10(value) - xMin) / Math.max(1e-9, xMax - xMin)) * (WIDTH - PAD.left - PAD.right);
  const y = (value: number) => PAD.top + (1 - (Math.log10(Math.max(Number.MIN_VALUE, value)) - yMin) / Math.max(1e-9, yMax - yMin)) * (HEIGHT - PAD.top - PAD.bottom);
  const path = points.map((point, index) => `${index ? "L" : "M"}${x(point.tau_s).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ");
  return (
    <svg className={`dyn-line-chart ${color}`} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${label} versus averaging time`}>
      {[0, .5, 1].map((fraction) => {
        const yy = PAD.top + fraction * (HEIGHT - PAD.top - PAD.bottom);
        const exponent = yMax - fraction * (yMax - yMin);
        return <g key={fraction}><line x1={PAD.left} x2={WIDTH - PAD.right} y1={yy} y2={yy} /><text x={PAD.left - 7} y={yy + 3} textAnchor="end">10^{exponent.toFixed(1)}</text></g>;
      })}
      {points.map((point) => <line className="vertical" key={point.tau_s} x1={x(point.tau_s)} x2={x(point.tau_s)} y1={PAD.top} y2={HEIGHT - PAD.bottom} />)}
      <path d={path} />
      {points.map((point) => <circle key={point.tau_s} cx={x(point.tau_s)} cy={y(point.value)} r="2.2"><title>{`τ ${point.tau_s}s · ${point.value.toExponential(3)} ${units} · ${point.pairs} pairs`}</title></circle>)}
      <text className="axis-title" x={PAD.left + (WIDTH - PAD.left - PAD.right) / 2} y={HEIGHT - 4} textAnchor="middle">AVERAGING TIME τ · s</text>
      <text className="axis-title" transform={`translate(10 ${PAD.top + (HEIGHT - PAD.top - PAD.bottom) / 2}) rotate(-90)`} textAnchor="middle">{label.toUpperCase()} · {units}</text>
    </svg>
  );
}

function DynamicHeatmaps({ analysis, transferQualified }: { analysis?: DynamicsPayload["dynamic_stability"]; transferQualified?: boolean }) {
  const cells = analysis?.cells ?? [];
  const times = analysis?.times_s ?? [];
  const taus = analysis?.taus_s ?? [];
  if (!cells.length || !times.length || !taus.length) return <EmptyAnalysis analysis={analysis} message="Building sliding stability windows" />;
  const metrics = [
    ["adev", "CLOCK ADEV", "fractional"],
    ["mdev", "CLOCK MDEV", "fractional"],
    ["ftu", transferQualified ? "TRANSFER FTU" : "COMPOSITE FTU", "fractional"],
    ["adevs_ns", transferQualified ? "TRANSFER ADEVS" : "COMPOSITE ADEVS", "ns"],
  ] as const;
  return (
    <div className="dyn-heatmap-grid">
      {metrics.map(([metric, label, unit]) => {
        const values = finite(cells.map((cell) => cell[metric]));
        const logs = values.map((value) => Math.log10(Math.max(Number.MIN_VALUE, value)));
        const minimum = Math.min(...logs);
        const maximum = Math.max(...logs);
        return (
          <div className="dyn-heatmap" key={metric}>
            <div><strong>{label}</strong><span>{unit} · {analysis?.window_s?.toFixed(0)} s window</span></div>
            <div className="dyn-heatmap-body" style={{ gridTemplateColumns: `repeat(${times.length}, minmax(3px, 1fr))`, gridTemplateRows: `repeat(${taus.length}, 14px)` }}>
              {[...taus].reverse().flatMap((tau) => times.map((time) => {
                const cell = cells.find((item) => item.tau_s === tau && item.time_s === time);
                const value = cell?.[metric];
                const intensity = value == null ? 0 : .08 + .92 * ((Math.log10(Math.max(Number.MIN_VALUE, value)) - minimum) / Math.max(1e-12, maximum - minimum));
                return <i key={`${tau}-${time}`} style={{ opacity: intensity }}><span>{value == null ? "unavailable" : `${label}: ${value.toExponential(3)} ${unit}; τ ${tau}s; t ${time.toFixed(1)}s`}</span></i>;
              }))}
            </div>
            <div className="dyn-heatmap-axis"><span>{times[0]?.toFixed(0)} s</span><span>TIME →</span><span>now</span></div>
          </div>
        );
      })}
    </div>
  );
}

function CascadeHeatmap({ analysis }: { analysis?: DynamicsPayload["spectral_cascade"] }) {
  const points = analysis?.points ?? [];
  const channels = analysis?.channels ?? [];
  if (!points.length || !channels.length) return <EmptyAnalysis analysis={analysis} message="Estimating cross-spectral cascade modes" />;
  const gains = finite(points.flatMap((point) => point.hops.map((hop) => hop.cumulative_gain_db)));
  const scale = Math.max(3, ...gains.map(Math.abs));
  return (
    <div className="dyn-cascade-spectrum">
      <div className="dyn-spectrum-labels">{channels.map((channel) => <span key={channel}>{channel}</span>)}</div>
      <div className="dyn-spectrum-grid" style={{ gridTemplateColumns: `repeat(${points.length}, minmax(5px, 1fr))`, gridTemplateRows: `repeat(${channels.length}, 27px)` }}>
        {channels.flatMap((channel) => points.map((point) => {
          const hop = point.hops.find((item) => item.id === channel);
          const gain = hop?.cumulative_gain_db ?? 0;
          const positive = gain >= 0;
          return <i className={positive ? "amplify" : "attenuate"} key={`${channel}-${point.frequency_hz}`} style={{ opacity: .1 + .9 * Math.min(1, Math.abs(gain) / scale) }}><span>{`${channel} · ${point.frequency_hz.toFixed(4)} Hz · cumulative ${gain.toFixed(2)} dB · coherence ${(hop?.coherence ?? 0).toFixed(2)} · phase ${(hop?.phase_deg ?? 0).toFixed(1)}°`}</span></i>;
        }))}
      </div>
      <div className="dyn-spectrum-axis"><span>{points[0].frequency_hz.toFixed(4)} Hz</span><span>FREQUENCY → · CYAN AMPLIFIES · VIOLET ATTENUATES</span><span>{points.at(-1)?.frequency_hz.toFixed(3)} Hz</span></div>
    </div>
  );
}

function CoherentModes({ analysis }: { analysis?: DynamicsPayload["multiresolution_modes"] }) {
  const bands = analysis?.bands ?? [];
  if (!bands.length) return <EmptyAnalysis analysis={analysis} message="Separating coherent timescales" />;
  return (
    <div className="dyn-mode-bands">
      {bands.map((band) => {
        const maximum = Math.max(...band.loadings.map((loading) => loading.magnitude), 1e-9);
        return (
          <div key={band.label}>
            <header><strong>{band.label}</strong><span>{band.minimum_hz.toFixed(4)}–{band.maximum_hz.toFixed(3)} Hz</span><em>{(band.energy_share * 100).toFixed(1)}% energy</em></header>
            <div>{band.loadings.map((loading) => <span key={loading.id}><b>{loading.id}</b><i style={{ width: `${100 * loading.magnitude / maximum}%` }} /><small>{loading.phase_deg.toFixed(0)}°</small></span>)}</div>
          </div>
        );
      })}
    </div>
  );
}

function EstimatorPlot({ analysis }: { analysis?: DynamicsPayload["estimator_consistency"] }) {
  const points = analysis?.points ?? [];
  if (points.length < 2) return <EmptyAnalysis analysis={analysis} message="Collecting Kalman innovations" />;
  const width = 560;
  const height = 154;
  const plotHeight = 112;
  const maximum = Math.max(4.5, ...points.map((point) => Math.min(20, point.nis)));
  const x = (index: number) => 35 + index / Math.max(1, points.length - 1) * 510;
  const y = (value: number) => 10 + (1 - Math.min(maximum, value) / maximum) * plotHeight;
  const path = points.map((point, index) => `${index ? "L" : "M"}${x(index)},${y(point.nis)}`).join(" ");
  return (
    <svg className="dyn-nis-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Normalized innovation squared consistency timeline">
      <line x1="35" x2="545" y1={y(3.841)} y2={y(3.841)} className="limit" />
      <text x="540" y={y(3.841) - 4} textAnchor="end">χ² 95% · 3.84</text>
      <path d={path} />
      {points.map((point, index) => <circle className={point.accepted ? "" : "rejected"} key={`${point.observed_at}-${index}`} cx={x(index)} cy={y(point.nis)} r="2"><title>{`NIS ${point.nis.toFixed(2)} · innovation ${ns(point.innovation_ns)} · ${point.accepted ? "accepted" : "rejected"}`}</title></circle>)}
      <text className="axis-title" x="290" y="149" textAnchor="middle">ESTIMATOR UPDATE</text>
    </svg>
  );
}

function HoldoverFan({ analysis }: { analysis?: DynamicsPayload["holdover_risk"] }) {
  const forecast = analysis?.forecast ?? [];
  if (forecast.length < 2) return <EmptyAnalysis analysis={analysis} message="Learning holdover state and noise" />;
  const width = 560;
  const height = 190;
  const pad = { left: 48, right: 12, top: 15, bottom: 29 };
  const values = forecast.flatMap((point) => [point.lower_95_ns, point.upper_95_ns, point.expected_ns]);
  const magnitude = Math.max(1, ...values.map(Math.abs));
  const x = (value: number) => pad.left + value / Math.max(1, forecast.at(-1)?.horizon_s ?? 1) * (width - pad.left - pad.right);
  const y = (value: number) => pad.top + (magnitude - value) / (2 * magnitude) * (height - pad.top - pad.bottom);
  const upper = forecast.map((point, index) => `${index ? "L" : "M"}${x(point.horizon_s)},${y(point.upper_95_ns)}`).join(" ");
  const lower = [...forecast].reverse().map((point) => `L${x(point.horizon_s)},${y(point.lower_95_ns)}`).join(" ");
  const center = forecast.map((point, index) => `${index ? "L" : "M"}${x(point.horizon_s)},${y(point.expected_ns)}`).join(" ");
  return (
    <svg className="dyn-holdover-fan" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Holdover forecast and 95 percent uncertainty tube">
      <line x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} />
      <path className="fan" d={`${upper}${lower}Z`} />
      <path className="center" d={center} />
      {forecast.map((point) => <circle key={point.horizon_s} cx={x(point.horizon_s)} cy={y(point.expected_ns)} r="2"><title>{`${point.horizon_s}s · expected ${ns(point.expected_ns)} · 95% ${ns(point.lower_95_ns)} to ${ns(point.upper_95_ns)}`}</title></circle>)}
      <text x={pad.left - 7} y={y(magnitude) + 3} textAnchor="end">{ns(magnitude)}</text>
      <text x={pad.left - 7} y={y(0) + 3} textAnchor="end">0 ns</text>
      <text x={pad.left - 7} y={y(-magnitude) + 3} textAnchor="end">{ns(-magnitude)}</text>
      <text className="axis-title" x={pad.left + (width - pad.left - pad.right) / 2} y={height - 4} textAnchor="middle">HOLDOVER HORIZON · s</text>
    </svg>
  );
}

function RobustLoopPlots({ analysis }: { analysis?: DynamicsPayload["active_identification"] }) {
  const points = analysis?.points ?? [];
  if (points.length < 2) return <EmptyAnalysis analysis={analysis} message={analysis?.reason ?? "Run bounded identification to unlock S / T / KS and margins"} />;
  const width = 560;
  const height = 194;
  const pad = { left: 45, right: 10, top: 14, bottom: 28 };
  const xValues = points.map((point) => Math.log10(point.frequency_hz));
  const yValues = points.flatMap((point) => [point.sensitivity_db, point.complementary_sensitivity_db, point.control_sensitivity_db]);
  const xMin = Math.min(...xValues);
  const xMax = Math.max(...xValues);
  const yMin = Math.floor(Math.min(...yValues, -6) / 10) * 10;
  const yMax = Math.ceil(Math.max(...yValues, 6) / 10) * 10;
  const x = (value: number) => pad.left + (Math.log10(value) - xMin) / Math.max(1e-9, xMax - xMin) * (width - pad.left - pad.right);
  const y = (value: number) => pad.top + (yMax - value) / Math.max(1e-9, yMax - yMin) * (height - pad.top - pad.bottom);
  const line = (key: "sensitivity_db" | "complementary_sensitivity_db" | "control_sensitivity_db") => points.map((point, index) => `${index ? "L" : "M"}${x(point.frequency_hz)},${y(point[key])}`).join(" ");
  return (
    <div className="dyn-loop-plots">
      <svg className="dyn-loop-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Identified sensitivity, complementary sensitivity and control sensitivity">
        {[yMin, 0, yMax].map((value) => <g key={value}><line x1={pad.left} x2={width - pad.right} y1={y(value)} y2={y(value)} /><text x={pad.left - 7} y={y(value) + 3} textAnchor="end">{value} dB</text></g>)}
        <path className="s" d={line("sensitivity_db")} />
        <path className="t" d={line("complementary_sensitivity_db")} />
        <path className="ks" d={line("control_sensitivity_db")} />
        <text className="direct-label s" x={width - 66} y="19">S</text><text className="direct-label t" x={width - 46} y="19">T</text><text className="direct-label ks" x={width - 24} y="19">KS</text>
        <text className="axis-title" x={pad.left + (width - pad.left - pad.right) / 2} y={height - 4} textAnchor="middle">FREQUENCY · Hz</text>
      </svg>
      <svg className="dyn-nyquist-mini" viewBox="0 0 250 194" role="img" aria-label="Identified loop Nyquist geometry with uncertainty-qualified points">
        <line x1="15" x2="238" y1="97" y2="97" /><line x1="125" x2="125" y1="10" y2="182" />
        <circle className="critical" cx="74" cy="97" r="4" /><text x="68" y="89">−1</text>
        {(() => {
          const magnitude = Math.max(1, ...points.flatMap((point) => [Math.abs(point.loop_real), Math.abs(point.loop_imag)]));
          const xx = (value: number) => 125 + value / magnitude * 51;
          const yy = (value: number) => 97 - value / magnitude * 51;
          const path = points.map((point, index) => `${index ? "L" : "M"}${xx(point.loop_real)},${yy(point.loop_imag)}`).join(" ");
          return <><path d={path} />{points.map((point) => <circle className={point.coherence_excitation_output >= .55 ? "qualified" : ""} key={point.frequency_hz} cx={xx(point.loop_real)} cy={yy(point.loop_imag)} r="2"><title>{`${point.frequency_hz.toFixed(4)} Hz · L ${point.loop_magnitude_db.toFixed(2)} dB / ${point.loop_phase_deg.toFixed(1)}° · coherence ${point.coherence_excitation_output.toFixed(2)}`}</title></circle>)}</>;
        })()}
        <text className="axis-title" x="125" y="190" textAnchor="middle">NYQUIST · IDENTIFIED L(jω)</text>
      </svg>
    </div>
  );
}

function ResearchDiagnostics({ dynamics }: { dynamics?: DynamicsPayload }) {
  const bicoherence = dynamics?.nonlinear?.bicoherence;
  const topology = dynamics?.nonlinear?.topology;
  const directed = dynamics?.nonlinear?.directed_dependence;
  const entropy = dynamics?.nonlinear?.multiscale_entropy;
  const topologicalCurve = topology?.curve ?? [];
  const entropyPoints = entropy?.points ?? [];
  const links = directed?.links ?? [];
  const ids = [...new Set(links.flatMap((link) => [link.source, link.target]))];
  const maxLink = Math.max(...links.map((link) => link.score), 1e-9);
  return (
    <div className="dyn-research-grid">
      <article>
        <header><span>HIGHER-ORDER SPECTRUM</span><strong>Quadratic coupling</strong><em className={statusClass(bicoherence?.status)}>{bicoherence?.status ?? "waiting"}</em></header>
        {bicoherence?.strongest ? <div className="dyn-primary-finding"><Waves size={19} /><strong>{bicoherence.strongest.bicoherence.toFixed(3)}</strong><span>{bicoherence.strongest.f1_hz.toFixed(3)} + {bicoherence.strongest.f2_hz.toFixed(3)} → {bicoherence.strongest.sum_hz.toFixed(3)} Hz</span><small>screening floor {compact(bicoherence.screening_floor)}</small></div> : <EmptyAnalysis analysis={bicoherence} message="Learning bicoherence" />}
      </article>
      <article>
        <header><span>TOPOLOGICAL DYNAMICS</span><strong>Betti curves</strong><em className={statusClass(topology?.status)}>{topology?.status ?? "waiting"}</em></header>
        {topologicalCurve.length ? <div className="dyn-betti">{topologicalCurve.map((point) => <span key={point.radius_sigma}><i className="beta0" style={{ height: `${Math.min(100, point.beta0 * 6)}%` }} /><i className="beta1" style={{ height: `${Math.min(100, point.beta1 * 10)}%` }}><b>{point.beta1}</b></i><small>{point.radius_sigma.toFixed(2)}σ</small></span>)}</div> : <EmptyAnalysis analysis={topology} message="Building delay-complex topology" />}
      </article>
      <article>
        <header><span>PREDICTIVE DIRECTION</span><strong>Lag dependence</strong><em className={statusClass(directed?.status)}>{directed?.status ?? "waiting"}</em></header>
        {links.length ? <div className="dyn-directed-matrix" style={{ gridTemplateColumns: `42px repeat(${ids.length}, 1fr)` }}><i />{ids.map((id) => <b key={`h-${id}`}>{id}</b>)}{ids.flatMap((target) => [<b key={`r-${target}`}>{target}</b>, ...ids.map((source) => {
          const link = links.find((item) => item.source === source && item.target === target);
          const opacity = source === target ? 0 : .08 + .92 * (link?.score ?? 0) / maxLink;
          return <i key={`${source}-${target}`} className={source === target ? "diagonal" : ""} style={{ opacity }}><span>{source === target ? "self" : `${source} predicts ${target}: ${link?.variance_reduction_pct.toFixed(1) ?? 0}% variance reduction; not causal`}</span></i>;
        })])}</div> : <EmptyAnalysis analysis={directed} message="Learning predictive links" />}
      </article>
      <article>
        <header><span>MULTISCALE COMPLEXITY</span><strong>Sample entropy</strong><em className={statusClass(entropy?.status)}>{entropy?.status ?? "waiting"}</em></header>
        {entropyPoints.length ? <div className="dyn-entropy-bars">{entropyPoints.map((point) => <span key={point.scale_samples}><i style={{ height: `${Math.min(100, Math.max(4, point.entropy * 42))}%` }} /><b>{point.entropy.toFixed(2)}</b><small>{point.scale_samples}×</small></span>)}</div> : <EmptyAnalysis analysis={entropy} message="Learning scale-dependent entropy" />}
      </article>
    </div>
  );
}

export function CascadeDynamicsObservatory({
  dynamics,
  nodes,
  connection,
  analysisMode,
  identification,
  identificationBusy,
  controlIdentification,
}: Props) {
  const eligibleNodes = nodes.filter((node) => node.role !== "Grandmaster");
  const identificationNodes = eligibleNodes.filter((node) => (
    node.servoEnabled !== false
    && ["kalman", "adaptive-kalman", "imm"].includes(node.servoType ?? "")
  ));
  const defaultTarget = identification?.target && identificationNodes.some((node) => node.id === identification.target)
    ? identification.target
    : identificationNodes.at(-1)?.id ?? "";
  const [target, setTarget] = useState(defaultTarget);
  const [amplitude, setAmplitude] = useState(25);
  const [duration, setDuration] = useState(180);
  const [offsetLimit, setOffsetLimit] = useState(5_000);
  const [frequencies, setFrequencies] = useState("0.01, 0.025, 0.05, 0.1");
  const selectedTarget = identification?.enabled && identification.target
    ? identification.target
    : identificationNodes.some((node) => node.id === target)
      ? target
      : identificationNodes.at(-1)?.id ?? "";
  const parsedFrequencies = useMemo(() => frequencies.split(",").map((value) => Number(value.trim())).filter((value) => Number.isFinite(value) && value > 0), [frequencies]);
  const active = Boolean(identification?.enabled);
  const transfer = dynamics?.transfer_noise;
  const estimator = dynamics?.estimator_consistency;
  const identifiability = dynamics?.identifiability;
  const activeId = dynamics?.active_identification;
  const oam = dynamics?.timing_oam;
  const risk = dynamics?.holdover_risk;
  const clockDecomposition = dynamics?.clock_decomposition;
  const pathRegimes = dynamics?.path_regimes;
  const hybrid = dynamics?.hybrid_servo;
  const maxClockSigma = Math.max(...(clockDecomposition?.clocks ?? []).map((clock) => clock.sigma_ppb), 1e-9);

  return (
    <div className="dyn-layout">
      <section className="instrument-panel dyn-hero">
        <div>
          <span className="section-kicker">CASCADE DYNAMICS OBSERVATORY</span>
          <h2>Clock, transfer, servo, and spatial dynamics</h2>
          <p>Every result is tied to raw PHC, LinuxPTP, path, or controlled-excitation evidence. Formal loop claims remain locked until the independent instrument passes coherence and identifiability gates.</p>
          <div className="research-tags"><span>{analysisMode === "simulation" ? "MODEL PREVIEW" : `${connection.toUpperCase()} ANALYSIS`}</span><span>{dynamics?.spectral_cascade?.segments ?? 0} SPECTRAL WINDOWS</span><span>{transfer?.qualified_residual ? "INDEPENDENT TRANSFER RESIDUAL" : "CLOCK + TRANSFER COMPOSITE"}</span><span>{activeId?.target ? `${activeId.target} · ` : ""}{activeId?.reliable_bins ?? 0} IDENTIFIED BINS</span></div>
        </div>
        <div className={`dyn-ident-control ${active ? "active" : ""}`}>
          <header><div><FlaskConical size={16} /><span><small>BOUNDED MULTISINE</small><strong>{active ? `${identification?.target} · ACTIVE` : "Controlled identification"}</strong></span></div><em>{active ? "LIVE" : "ARMED SAFE"}</em></header>
          <div className="dyn-ident-fields">
            <label><span>Clock</span><select value={selectedTarget} disabled={active || identificationBusy || !identificationNodes.length} onChange={(event) => setTarget(event.target.value)}>{identificationNodes.length ? identificationNodes.map((node) => <option key={node.id} value={node.id}>{node.id} · {node.servoType?.toUpperCase()}</option>) : <option value="">Apply a Kalman servo first</option>}</select></label>
            <label><span>Peak correction</span><div><input type="number" min="0.1" max="500" value={amplitude} disabled={active || identificationBusy} onChange={(event) => setAmplitude(Number(event.target.value))} /><em>ppb</em></div></label>
            <label><span>Duration</span><div><input type="number" min="30" max="900" value={duration} disabled={active || identificationBusy} onChange={(event) => setDuration(Number(event.target.value))} /><em>s</em></div></label>
            <label><span>Offset abort</span><div><input type="number" min="100" max="100000" value={offsetLimit} disabled={active || identificationBusy} onChange={(event) => setOffsetLimit(Number(event.target.value))} /><em>ns</em></div></label>
            <label className="wide"><span>Excitation frequencies · Hz</span><input value={frequencies} disabled={active || identificationBusy} onChange={(event) => setFrequencies(event.target.value)} /></label>
          </div>
          <button type="button" className={active ? "danger-action" : "primary-action"} disabled={identificationBusy || (!active && (!selectedTarget || !parsedFrequencies.length))} onClick={() => controlIdentification(active ? { target: identification?.target ?? selectedTarget, enabled: false } : { target: selectedTarget, enabled: true, amplitude_ppb: amplitude, duration_s: duration, offset_limit_ns: offsetLimit, frequencies_hz: parsedFrequencies })}>{active ? <Square size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}{identificationBusy ? "Transitioning…" : active ? "Stop excitation" : "Start safe identification"}</button>
          <small>{active ? `Auto-stop ${identification?.expires_at ? new Date(identification.expires_at * 1000).toLocaleTimeString() : "armed"} · ${identification?.reason ?? "offset guard active"}` : "Requires an active Kalman-family servo. Monitoring and automatic abort remain live."}</small>
        </div>
      </section>

      <section className="instrument-panel dyn-wide">
        <div className="panel-heading"><div><span className="section-kicker">NONSTATIONARY METROLOGY</span><h2>Dynamic clock and transfer stability</h2></div><span className={`quality-badge ${statusClass(dynamics?.dynamic_stability?.status)}`}>{dynamics?.dynamic_stability?.status?.toUpperCase() ?? "WAITING"}</span></div>
        <DynamicHeatmaps analysis={dynamics?.dynamic_stability} transferQualified={transfer?.qualified_residual} />
      </section>

      <section className="instrument-panel dyn-wide">
        <div className="panel-heading"><div><span className="section-kicker">SPATIAL FREQUENCY RESPONSE</span><h2>Cascade amplification and coherence</h2></div><span className="panel-meta">{dynamics?.spectral_cascade?.median_adjacent_coherence == null ? "coherence learning" : `median coherence ${dynamics.spectral_cascade.median_adjacent_coherence.toFixed(2)}`}</span></div>
        <CascadeHeatmap analysis={dynamics?.spectral_cascade} />
        <div className="dyn-evidence-note"><Network size={14} /><span>Passive gain is a cascade-amplification screen. The UI deliberately does not call it formal string stability until an independent input is present and frequency-bin coherence passes.</span></div>
      </section>

      <section className="instrument-panel">
        <div className="panel-heading"><div><span className="section-kicker">{transfer?.qualified_residual ? "TRANSFER-NOISE WORKBENCH" : "END-TO-END COMPOSITE"}</span><h2>FTU · first-difference frequency uncertainty</h2></div><span className={`quality-badge ${transfer?.qualified_residual ? "" : "warning"}`}>{transfer?.qualified_residual ? "INDEPENDENT RESIDUAL" : "CLOCK + PATH"}</span></div>
        <LogCurve points={transfer?.ftu ?? []} label="FTU" units="fractional" color="cyan" />
        <div className="dyn-evidence-note"><ShieldCheck size={14} /><span>{transfer?.provenance ?? "Waiting for a qualified transfer residual."}</span></div>
      </section>

      <section className="instrument-panel">
        <div className="panel-heading"><div><span className="section-kicker">RESIDUAL NOISE TYPE</span><h2>ADEVS · drift-sensitive transfer statistic</h2></div><span className={`quality-badge ${statusClass(transfer?.status)}`}>{transfer?.status?.toUpperCase() ?? "WAITING"}</span></div>
        <LogCurve points={transfer?.adevs ?? []} label="ADEVS" units="ns" color="violet" />
        <div className="dyn-evidence-note"><Waves size={14} /><span>{transfer?.qualified_residual ? "ADEVS resolves transfer-noise slopes and sees linear drift; TIE RMS remains the time-dispersion statistic." : "ADEVS sees drift in the end-to-end PHC difference, but this record cannot separate oscillator behavior from the network path."}</span></div>
      </section>

      <section className="instrument-panel dyn-wide">
        <div className="panel-heading"><div><span className="section-kicker">MULTIRESOLUTION COHERENT MODES</span><h2>Which timescales live on which hops</h2></div><span className={`quality-badge ${statusClass(dynamics?.multiresolution_modes?.status)}`}>{dynamics?.multiresolution_modes?.status?.toUpperCase() ?? "WAITING"}</span></div>
        <CoherentModes analysis={dynamics?.multiresolution_modes} />
        <div className="dyn-evidence-note"><Orbit size={14} /><span>{dynamics?.multiresolution_modes?.provenance ?? "Log-frequency coherent modes will appear after enough aligned hop data is available."}</span></div>
      </section>

      <section className="instrument-panel">
        <div className="panel-heading"><div><span className="section-kicker">HYBRID SERVO SYSTEM</span><h2>Acquisition, lock, holdover, recovery</h2></div><span className={`quality-badge ${statusClass(hybrid?.status)}`}>{hybrid?.status?.toUpperCase() ?? "WAITING"}</span></div>
        {(hybrid?.states ?? []).length ? <div className="dyn-hybrid">
          <div className="dyn-mode-ribbon">{(hybrid?.timeline ?? []).map((point, index) => <i className={point.state} key={`${point.observed_at}-${index}`}><span>{`${point.state} · ${ns(point.offset_ns)} · ${point.correction_ppb.toFixed(2)} ppb`}</span></i>)}</div>
          <div className="dyn-state-table">{hybrid?.states?.map((state) => <div key={state.state}><i className={state.state} /><strong>{state.state}</strong><span>{(state.share * 100).toFixed(1)}%</span><span>{ns(state.offset_rms_ns)} RMS</span><span>pole {compact(state.local_pole)}</span></div>)}</div>
        </div> : <EmptyAnalysis analysis={hybrid} message="Learning servo modes" />}
      </section>

      <section className="instrument-panel">
        <div className="panel-heading"><div><span className="section-kicker">ESTIMATOR CONSISTENCY</span><h2>Innovation energy and whiteness</h2></div><span className={`quality-badge ${estimator?.consistent ? "" : statusClass(estimator?.status)}`}>{estimator?.consistent ? "CONSISTENT" : estimator?.status?.toUpperCase() ?? "WAITING"}</span></div>
        <EstimatorPlot analysis={estimator} />
        <div className="dyn-metric-strip"><div><span>Mean NIS</span><strong>{compact(estimator?.mean_nis)}</strong></div><div><span>Inside 95%</span><strong>{estimator?.within_95_pct == null ? "—" : `${estimator.within_95_pct.toFixed(1)}%`}</strong></div><div><span>Lag-1 ρ</span><strong>{compact(estimator?.lag_one_autocorrelation)}</strong></div><div><span>Accepted</span><strong>{estimator?.acceptance_pct == null ? "—" : `${estimator.acceptance_pct.toFixed(1)}%`}</strong></div></div>
      </section>

      <section className="instrument-panel dyn-wide">
        <div className="panel-heading"><div><span className="section-kicker">ACTIVE MULTIRATE IDENTIFICATION</span><h2>S / T / KS, Nyquist, disk margin, and uncertainty envelope</h2></div><span className={`quality-badge ${statusClass(activeId?.status)}`}>{activeId?.status?.toUpperCase() ?? "GATED"}</span></div>
        <RobustLoopPlots analysis={activeId} />
        <div className="dyn-loop-ledger">
          <div><span>Reliable bins</span><strong>{activeId?.reliable_bins ?? 0}</strong><small>coherence-gated</small></div>
          <div><span>Balanced disk α</span><strong>{compact(activeId?.disk_margin?.balanced_alpha)}</strong><small>{activeId?.disk_margin?.gain_upper == null ? "gain interval learning" : `${compact(activeId.disk_margin.gain_lower)}×–${compact(activeId.disk_margin.gain_upper)}×`}</small></div>
          <div><span>Disk phase</span><strong>{activeId?.disk_margin?.phase_deg == null ? "—" : `${activeId.disk_margin.phase_deg.toFixed(1)}°`}</strong><small>qualified bins only</small></div>
          <div><span>IQC envelope</span><strong>{activeId?.iqc_envelope?.robustly_separated ? "SEPARATED" : "UNPROVEN"}</strong><small>{activeId?.iqc_envelope?.model ?? "needs active data"}</small></div>
          <div><span>Information rank</span><strong>{identifiability?.rank ?? 0}/{identifiability?.parameter_count ?? 5}</strong><small>κ {compact(identifiability?.condition_number)}</small></div>
        </div>
      </section>

      <section className="instrument-panel">
        <div className="panel-heading"><div><span className="section-kicker">HOLDOVER REACHABILITY</span><h2>Forecast tube and time-to-mask risk</h2></div><span className={`quality-badge ${risk?.calibration === "validated" ? "" : "warning"}`}>{risk?.calibration === "validated" ? "CALIBRATED" : "BACKTEST NEEDED"}</span></div>
        <HoldoverFan analysis={risk} />
        <div className="dyn-risk-thresholds">{risk?.thresholds?.map((threshold) => <div key={threshold.limit_ns}><span>±{ns(threshold.limit_ns)}</span><strong>{threshold.first_5pct_horizon_s == null ? "> scan" : `${threshold.first_5pct_horizon_s}s`}</strong><small>first ≥5% violation risk</small></div>)}</div>
      </section>

      <section className="instrument-panel">
        <div className="panel-heading"><div><span className="section-kicker">INDIVIDUAL CLOCK SEPARATION</span><h2>N-cornered instability screen</h2></div><span className={`quality-badge ${clockDecomposition?.eligible ? "" : "warning"}`}>{clockDecomposition?.eligible ? "INDEPENDENT" : "GATED"}</span></div>
        {(clockDecomposition?.clocks ?? []).length ? <div className={`dyn-clock-bars ${clockDecomposition?.eligible ? "" : "gated"}`}>{clockDecomposition?.clocks?.map((clock) => <div key={clock.id}><strong>{clock.id}</strong><i><b style={{ width: `${100 * clock.sigma_ppb / maxClockSigma}%` }} /></i><span>{clock.sigma_ppb.toFixed(3)} ppb</span></div>)}</div> : <EmptyAnalysis analysis={clockDecomposition} message="Learning pairwise clock differences" />}
        <div className="dyn-evidence-note"><AlertTriangle size={14} /><span>{clockDecomposition?.eligibility_reason ?? "Independent holdover is required before attributing noise to individual clocks."}</span></div>
      </section>

      <section className="instrument-panel dyn-wide">
        <div className="panel-heading"><div><span className="section-kicker">TIMING OAM</span><h2>Constant, dynamic, and accumulated time error</h2></div><div className="dyn-threshold-badges">{oam?.thresholds?.map((threshold) => <span className={threshold.pass ? "pass" : "fail"} key={threshold.label}>{threshold.pass ? <Check size={10} /> : <AlertTriangle size={10} />}{threshold.label}</span>)}</div></div>
        {(oam?.nodes ?? []).length ? <div className="dyn-oam-table"><div><span>Clock</span><span>cTE</span><span>dTE RMS</span><span>P95 |TE|</span><span>Peak-to-peak</span><span>Max |TE|</span></div>{oam?.nodes?.map((node) => <div key={node.id}><strong>{node.id}</strong><span>{ns(node.cte_ns)}</span><span>{ns(node.dte_rms_ns)}</span><span>{ns(node.p95_abs_te_ns)}</span><span>{ns(node.peak_to_peak_ns)}</span><span>{ns(node.max_abs_te_ns)}</span></div>)}</div> : <EmptyAnalysis analysis={oam} message="Building timing OAM ledger" />}
        <div className="dyn-accumulation">{oam?.accumulation?.map((hop) => <span key={hop.id}><b>{hop.id}</b><i style={{ height: `${Math.min(100, 12 + Math.abs(hop.accumulated_cte_ns) / Math.max(1, Math.max(...(oam.accumulation ?? []).map((item) => Math.abs(item.accumulated_cte_ns)))) * 80)}%` }} /><strong>{ns(hop.accumulated_cte_ns)}</strong><small>Σ cTE</small></span>)}</div>
      </section>

      <section className="instrument-panel">
        <div className="panel-heading"><div><span className="section-kicker">DELAY REGIMES</span><h2>Congestion and directional imbalance</h2></div><span className={`quality-badge ${pathRegimes?.calibrated_asymmetry ? "" : "warning"}`}>{pathRegimes?.calibrated_asymmetry ? "CALIBRATED" : "NOT ASYMMETRY"}</span></div>
        {(pathRegimes?.regimes ?? []).length ? <div className="dyn-path-regimes"><div><span>Median round trip</span><strong>{ns(pathRegimes?.median_round_trip_ns)}</strong></div><div><span>Directional imbalance</span><strong>{ns(pathRegimes?.median_directional_imbalance_ns)}</strong></div>{pathRegimes?.regimes?.map((regime) => <div key={regime.name}><span>{regime.name}</span><strong>{(regime.share * 100).toFixed(1)}%</strong></div>)}</div> : <EmptyAnalysis analysis={pathRegimes} message="Waiting for paired forward/reverse path events" />}
        <div className="dyn-evidence-note"><AlertTriangle size={14} /><span>{pathRegimes?.interpretation ?? "Four timestamps alone cannot identify one-way delay asymmetry."}</span></div>
      </section>

      <section className="instrument-panel">
        <div className="panel-heading"><div><span className="section-kicker">OBSERVABILITY</span><h2>Can the current record identify the model?</h2></div><span className={`quality-badge ${identifiability?.persistently_exciting ? "" : "warning"}`}>{identifiability?.persistently_exciting ? "INFORMATIVE" : "WEAK INPUT"}</span></div>
        <div className="dyn-eigen-ladder">{(identifiability?.normalized_information_eigenvalues ?? []).map((value, index, all) => <div key={index}><span>λ{index + 1}</span><i><b style={{ width: `${100 * value / Math.max(1e-12, all[0] ?? 1)}%` }} /></i><strong>{value.toExponential(2)}</strong></div>)}</div>
        <div className="dyn-evidence-note"><Gauge size={14} /><span>Information rank {identifiability?.rank ?? 0}/{identifiability?.parameter_count ?? 5}; input σ {compact(identifiability?.input_sigma_ppb)} ppb; condition κ {compact(identifiability?.condition_number)}.</span></div>
      </section>

      <section className="instrument-panel dyn-wide">
        <div className="panel-heading"><div><span className="section-kicker">EVIDENCE-GATED NONLINEAR LAB</span><h2>Higher-order, topological, directed, and complexity diagnostics</h2></div><span className="quality-badge warning">RESEARCH MODE</span></div>
        <ResearchDiagnostics dynamics={dynamics} />
        <div className="dyn-evidence-note"><ShieldCheck size={14} /><span>These views report finite-record structure. They do not convert recurrence, loops, bicoherence, entropy, or predictive direction into a chaos or causality claim.</span></div>
      </section>
    </div>
  );
}
