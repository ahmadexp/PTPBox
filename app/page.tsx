"use client";

import {
  Activity,
  ArrowRight,
  BarChart3,
  Bell,
  Boxes,
  Cable,
  Check,
  ChevronDown,
  Clock3,
  Cpu,
  Download,
  FlaskConical,
  Gauge,
  Info,
  LayoutDashboard,
  ListFilter,
  Menu,
  Network,
  Orbit,
  Pause,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Square,
  Terminal,
  TimerReset,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Section = "Overview" | "Multi-pendulum" | "Covariance" | "State space" | "Metrology" | "Path microscope" | "Intelligence" | "Resilience" | "Analytics" | "Experiments" | "Interfaces" | "Configuration" | "Event log";
type ConnectionMode = "checking" | "live" | "waiting" | "stale" | "simulation";
type ClockState = "LOCKED" | "TRACKING" | "UNLOCKED" | "REFERENCE" | "HOLDOVER" | "NO DATA" | "STALE" | "FAULTY";
type NativeServoType = "pi" | "linreg" | "nullf";
type ServoType = NativeServoType | "kalman" | "adaptive-kalman" | "imm";
type PpsPolarity = "rising" | "falling" | "both";
type PpsNodeState = "active" | "starting" | "stopped" | "ready" | "external" | "unavailable";

type ServoNodeControl = {
  mode: "reference" | "active" | "holdover";
  enabled: boolean;
  type: ServoType | null;
  changed_at: number | null;
  holdover_started: number | null;
};

type ServoControlState = { updated_at: number | null; nodes: Record<string, ServoNodeControl> };

type PpsPinStatus = {
  index: number;
  name: string;
  function: "none" | "external-timestamp" | "periodic-output" | "physical-sync" | string;
  channel: number;
};

type PpsNodeStatus = {
  role: "source" | "sink" | "disabled";
  state: PpsNodeState;
  configured: boolean;
  running: boolean;
  capable: boolean;
  phc: string | null;
  device: string | null;
  pin: PpsPinStatus | null;
  channel: number;
};

type PpsStatus = {
  enabled: boolean;
  running: boolean;
  source: string;
  sinks: string[];
  servo: NativeServoType;
  pulse_width_ns: number;
  mode?: "common-edge-measurement" | "ts2phc-discipline";
  comparison?: {
    enabled?: boolean;
    measure_only?: boolean;
    reference?: string;
    state?: {
      mode?: string;
      latest?: { offsets_ns?: Record<string, number>; observed_at?: number };
      samples?: Array<{ offsets_ns: Record<string, number>; observed_at: number }>;
    };
  };
  nodes: Record<string, PpsNodeStatus>;
};

type ObservatoryNotification = {
  id: string;
  level: "good" | "info" | "warn";
  title: string;
  detail: string;
  meta: string;
  section: Section;
  nodeId?: string;
  servoTarget?: string;
  icon: typeof Bell;
};

type CommandItem = {
  id: string;
  group: "Navigate" | "Clocks" | "Controls";
  label: string;
  description: string;
  keywords: string;
  section?: Section;
  nodeId?: string;
  action?: "notifications" | "apply" | "servo-control" | "sync-frequency" | "pps-control";
  icon: typeof Search;
};

type ClockNode = {
  id: string;
  label: string;
  role: "Grandmaster" | "Boundary" | "Ordinary";
  offset: number;
  hopOffset?: number;
  meanPathDelay: number;
  rms: number;
  frequencyPpb: number;
  state: ClockState;
  ingress: string;
  egress: string;
  phc: string;
  color: string;
  measured: boolean;
  ptpMeasured?: boolean;
  sampleCount: number;
  servoSampleCount: number;
  phcReadSpan: number | null;
  phcUncertainty?: number | null;
  phcMethod?: string | null;
  servoType?: ServoType | null;
  servoEnabled?: boolean;
  holdoverStarted?: number | null;
  kalman?: KalmanStatus | null;
  source: string;
  lastSampleAt: number | null;
};

type HistoryPoint = {
  t: number;
  values: Record<string, number>;
  hopValues?: Record<string, number>;
  key?: string;
};

type PendulumZeroState = {
  at: number | null;
  baselines: Record<string, number>;
};

type AgentStatus = {
  hostname?: string;
  linuxptp?: string;
  interfaces?: number;
  ptp_interfaces?: number;
  namespaces?: string[];
  running?: boolean;
  phc_sample_rate_hz?: number;
  servo_control?: ServoControlState;
  pps?: PpsStatus;
  agent_version?: string;
  advanced_capabilities?: Record<string, boolean>;
  active_experiment?: ResearchExperiment | null;
  profile_compliance?: ResearchPayload["profiles"];
  fault?: { enabled?: boolean; target?: string; expires_at?: number };
};

type HostInterface = {
  name: string;
  state: string;
  carrier: boolean;
  speed_mbps: number | null;
  mac: string;
  driver: string | null;
  bus: string | null;
  phc: string | null;
  hardware_timestamping: boolean;
  namespace: string | null;
  assignment: string | null;
};

type TelemetrySample = {
  offset_ns: number;
  frequency_ppb: number;
  mean_path_delay_ns: number;
  servo_state: string | null;
  source_time: number | null;
  observed_at: number;
  sample_id: string;
  source: string;
  raw: true;
  valid: boolean;
  validation_error: string | null;
};

type KalmanStatus = {
  state: "acquiring" | "locked" | "innovation-gated" | "invalid-interval" | string;
  fresh: boolean;
  phase_estimate_ns: number;
  frequency_estimate_ppb: number;
  correction_ppb: number;
  innovation_ns: number;
  phase_sigma_ns: number;
  frequency_sigma_ppb: number;
  accepted_count: number;
  rejected_count: number;
  measurement_noise_ns: number;
  process_noise_ppb: number;
  phase_time_constant_s: number;
  drift_estimate_ppb_s?: number;
  drift_sigma_ppb_s?: number;
  adaptive_measurement_noise_ns?: number;
  regime?: "quiet" | "dynamic" | "holdover" | string;
  model_probabilities?: Record<string, number>;
};

type StabilityPoint = {
  tau_s: number;
  value: number;
  pairs: number;
  confidence: number | null;
};

type ResearchExperiment = {
  id: string;
  name: string;
  kind: string;
  state: "running" | "completed" | string;
  started_at: number;
  stopped_at: number | null;
  sample_count?: number;
  event_count?: number;
};

type PathEvent = {
  node: string;
  kind: "sync" | "delay";
  observed_at: number;
  sequence_id: number;
  correction_ns: number;
  t1_ns?: number | string;
  t2_ns?: number | string;
  t3_ns?: number | string;
  t4_ns?: number | string;
  forward_transit_ns?: number;
  reverse_transit_ns?: number;
  raw?: boolean;
};

type ResearchPayload = {
  generated_at: number;
  mode: "live" | "stale" | "waiting" | "simulation";
  sample_count: number;
  aligned_sample_count: number;
  sample_rate_hz: number;
  endpoint: string | null;
  stability: Record<"adev" | "mdev" | "tdev" | "hdev" | "mtie" | "theo1", StabilityPoint[]>;
  fusion: {
    status?: string;
    reference?: string;
    nodes?: Record<string, { offset_ns: number; sigma_ns: number }>;
    residuals?: Array<{ source: string; edge: string; residual_ns: number; normalized: number }>;
    chi_square?: number;
    degrees_of_freedom?: number;
  };
  ensemble: {
    status: string;
    samples?: number;
    virtual_offset_ns?: number;
    one_sigma_ns?: number;
    weights?: Record<string, number>;
  };
  change_detection: {
    status: string;
    latest_probability?: number;
    probabilities?: number[];
    change_points?: number[];
  };
  recurrence: {
    status: string;
    matrix?: string[];
    samples?: number;
    recurrence_rate?: number;
    determinism?: number;
    diagonal_lines?: number;
    threshold_sigma?: number;
  };
  bifurcation: {
    status: string;
    samples?: number;
    parameter?: string;
    parameter_min?: number;
    parameter_max?: number;
    current_gain_scale?: number;
    base_gains?: { kp: number; ki: number };
    active_controller?: string;
    baseline_is_live?: boolean;
    points?: Array<{
      gain_scale: number;
      residual_ns: number;
      stable: boolean;
      regime: string;
      branch: number;
      clipped?: boolean;
    }>;
    summaries?: Array<{
      gain_scale: number;
      kp: number;
      ki: number;
      stable: boolean;
      regime: string;
      branch_count: number;
      tail_rms_ns: number | null;
      peak_ns: number | null;
    }>;
    current?: {
      gain_scale: number;
      kp: number;
      ki: number;
      stable: boolean;
      regime: string;
      branch_count: number;
      tail_rms_ns: number | null;
      peak_ns: number | null;
    };
    stable_through_gain?: number | null;
    first_transition_gain?: number | null;
    display_limit_ns?: number;
    forcing_envelope_ns?: number;
    method?: string;
    provenance?: string;
    interpretation?: string;
    live_changes?: number;
    reason?: string;
  };
  fractal: {
    status: string;
    samples?: number;
    higuchi: {
      status: string;
      samples?: number;
      dimension?: number;
      r_squared?: number;
      k_max?: number;
      points?: Array<{ k: number; length: number; log_inverse_k: number; log_length: number }>;
      fit?: { slope: number; intercept: number; r_squared: number };
      interpretation?: string;
    };
    correlation: {
      status: string;
      samples?: number;
      dimension?: number;
      r_squared?: number;
      embedding_dimension?: number;
      delay_samples?: number;
      theiler_window_samples?: number;
      converged?: boolean;
      embeddings?: Array<{
        dimension: number;
        status: string;
        estimate?: number;
        r_squared?: number;
        pairs?: number;
        scaling_radius_min?: number;
        scaling_radius_max?: number;
      }>;
      points?: Array<{ radius: number; correlation_sum: number; log_radius: number; log_correlation: number }>;
      fit?: {
        slope: number;
        intercept: number;
        r_squared: number;
        start_index: number;
        end_index: number;
        radius_min: number;
        radius_max: number;
        point_count: number;
      };
      interpretation?: string;
    };
    multifractal: {
      status: string;
      samples?: number;
      q_min?: number;
      q_max?: number;
      spectrum_width?: number;
      surrogate_width?: number | null;
      correlation_excess_width?: number | null;
      surrogate_count?: number;
      exponents?: Array<{
        q: number;
        h: number;
        r_squared: number;
        points: Array<{ scale: number; fluctuation: number }>;
      }>;
      scales?: number[];
      interpretation?: string;
    };
    method?: string;
    provenance?: string;
    interpretation?: string;
    live_changes?: number;
  };
  koopman: {
    status: string;
    singular_values?: number[];
    spectral_norm?: number;
    residual_sigma_ns?: number;
    interpretation?: string;
  };
  system_identification: {
    status: string;
    samples?: number;
    spectral_radius?: number;
    settling_time_s?: number | null;
    dc_gain?: number;
    r_squared?: number;
    residual_sigma_ns?: number;
    poles?: Array<{ real: number; imag: number; magnitude: number }>;
  };
  auto_tune: {
    status: string;
    samples?: number;
    predicted_improvement_pct?: number;
    safe_candidates?: number;
    evaluated_candidates?: number;
    recommendation?: { kp: number; ki: number; rms_ns: number; peak_ns: number; score: number; safe: boolean };
    baseline?: { kp: number; ki: number; rms_ns: number; score: number };
    frontier?: Array<{ kp: number; ki: number; score: number; rms_ns: number }>;
  };
  temperature_holdover: Record<string, {
    status: string;
    temperature_c?: number;
    predicted_frequency_ppb?: number;
    predicted_phase_ns?: number;
    one_sigma_ns?: number;
    horizon_s?: number;
  }>;
  error_budget: {
    nodes: Record<string, {
      rss_ns: number;
      components_ns: Record<string, number>;
      contribution_pct: Record<string, number>;
    }>;
    cascade?: {
      hop_count: number;
      samples: number;
      independent_sigma_ns: number;
      correlated_sigma_ns: number;
      cross_covariance_ns2: number;
      covariance_ns2?: number[][];
    } | null;
    method?: string;
  };
  capabilities: {
    dpll?: { supported: boolean; devices?: unknown[] | Record<string, unknown>; reason?: string | null };
    synce?: { supported: boolean; state?: string; reason?: string | null };
    devlink_health?: { supported: boolean; reporters?: unknown };
    temperature?: { supported: boolean; nodes?: Record<string, number> };
    path_monitor?: { supported: boolean; events?: number; reason?: string | null };
    pps_common_edge?: { supported: boolean; state?: { latest?: { offsets_ns?: Record<string, number> } } };
  };
  profiles: {
    profile: string;
    compliant: boolean;
    available_profiles?: string[];
    checks?: Array<{ name: string; actual: unknown; expected: unknown; pass: boolean }>;
  };
  path_microscope: { events: PathEvent[]; mode: "live" | "waiting" | "simulation"; provenance: string };
  experiments: ResearchExperiment[];
  active_experiment: ResearchExperiment | null;
  security: { authentication: { enabled: boolean; spp: number; active_key_id: number; allow_unauth: number; key_material_exposed: false } };
};

type PhcSample = {
  offset_ns: number | null;
  previous_hop_offset_ns: number | null;
  read_span_ns: number | null;
  comparison_uncertainty_ns: number | null;
  cross_timestamp_method: string | null;
  observed_at: number;
  sample_id: string;
  phc: string;
  raw: true;
  valid: boolean;
  error: string | null;
};

type TelemetryClock = {
  id: string;
  role: "grandmaster" | "boundary" | "ordinary";
  ingress: string;
  egress: string;
  measurement: TelemetrySample | null;
  samples: TelemetrySample[];
  window_sample_count: number;
  window_valid_sample_count: number;
  window_locked_sample_count: number;
  window_invalid_sample_count: number;
  rms_ns: number | null;
  logs: number;
  measurement_phc: string | null;
  phc_measurement: PhcSample | null;
  phc_samples: PhcSample[];
  phc_window_sample_count: number;
  phc_rms_ns: number | null;
  kalman?: KalmanStatus | null;
};

type TelemetryPayload = {
  timestamp: number;
  clocks: TelemetryClock[];
  measured_clocks: number;
  fresh_clocks: number;
  degraded_clocks: number;
  sample_count: number;
  valid_sample_count: number;
  invalid_sample_count: number;
  mode: "live" | "stale" | "waiting";
  phc_mode: "live" | "stale" | "waiting";
  phc_reference: string | null;
  phc_reference_device: string | null;
  phc_fresh_clocks: number;
  phc_method: string;
  phc_sample_rate_hz?: number;
  servo_control: ServoControlState;
  measurement_source: string;
  raw: true;
  smoothing: "none";
  history_seconds: number;
};

type PhcTelemetryClock = {
  id: string;
  phc: string;
  measurement: PhcSample | null;
  samples: PhcSample[];
  window_sample_count: number;
  rms_ns: number | null;
};

type PhcTelemetryPayload = {
  timestamp: number;
  reference: string | null;
  reference_phc: string | null;
  clocks: PhcTelemetryClock[];
  fresh_clocks: number;
  mode: "live" | "stale" | "waiting";
  sample_rate_hz?: number;
  method: string;
  raw: true;
  smoothing: "none";
};

function agentBaseUrl() {
  if (typeof window === "undefined") return "";
  const override = new URLSearchParams(window.location.search).get("agent");
  if (override?.startsWith("http://") || override?.startsWith("https://")) return override.replace(/\/$/, "");
  const hostname = override || (["localhost", "127.0.0.1"].includes(window.location.hostname) ? "192.168.1.60" : window.location.hostname);
  return `http://${hostname}:8090`;
}

const TRACE_COLORS = ["#f3f8f8", "#71d9e3", "#4de1c1", "#9ed873", "#f2c96e", "#ee9070", "#d7a4f4", "#ff6f91"];

const SECTION_META: Record<Section, { title: string; description: string }> = {
  Overview: { title: "Cascade overview", description: "Compare every NIC PHC against BC1 while LinuxPTP synchronizes the isolated daisy chain." },
  "Multi-pendulum": { title: "Multi-pendulum", description: "Watch every previous-hop phase residual swing around its learned equilibrium." },
  Covariance: { title: "Covariance lab", description: "Reveal coupled phase changes, evolving relationships, and the cascade's dominant eigenmodes." },
  "State space": { title: "State-space atlas", description: "Trace the cascade's modal trajectory, empirical Poincaré crossings, and evolving eigenstructure." },
  Metrology: { title: "Metrology workbench", description: "Quantify stability, fuse clock states, build an ensemble timescale, and preserve reproducible raw experiments." },
  "Path microscope": { title: "Path microscope", description: "Inspect raw LinuxPTP exchange timestamps, correction fields, directional residuals, and common-edge PPS comparisons." },
  Intelligence: { title: "Control intelligence", description: "Estimate drift, identify loop dynamics, detect regime changes, and tune controllers against captured data." },
  Resilience: { title: "Resilience lab", description: "Validate timing profiles, expose DPLL and SyncE truth, authenticate messages, and inject bounded faults." },
  Analytics: { title: "Timing analytics", description: "Interrogate direct PHC differences alongside LinuxPTP servo state, frequency correction, and path delay." },
  Experiments: { title: "Experiments", description: "Design, run, and compare repeatable servo response tests." },
  Interfaces: { title: "Interfaces & PHCs", description: "Map physical ports, PHCs, namespaces, and timestamping capability." },
  Configuration: { title: "Configuration", description: "Shape protocol, servo, PPS I/O, authentication, and ts2phc behavior with guarded, reviewable changes." },
  "Event log": { title: "Event log", description: "A precise account of state changes, measurements, and operator actions." },
};

const INITIAL_NODES: ClockNode[] = [
  { id: "BC1", label: "BC1 · GM", role: "Grandmaster", offset: 0, meanPathDelay: 0, rms: 0, frequencyPpb: 0, state: "REFERENCE", ingress: "enp25s0f0np0", egress: "enp25s0f1np1", phc: "ptp1", color: TRACE_COLORS[0], measured: true, sampleCount: 120, servoSampleCount: 120, phcReadSpan: 0, source: "simulation", lastSampleAt: null },
  { id: "BC2", label: "BC2", role: "Boundary", offset: 4.8, meanPathDelay: 212, rms: 3.2, frequencyPpb: -2.4, state: "LOCKED", ingress: "enp26s0f0np0", egress: "enp26s0f1np1", phc: "ptp2", color: TRACE_COLORS[1], measured: true, sampleCount: 120, servoSampleCount: 120, phcReadSpan: 2100, source: "simulation", lastSampleAt: null },
  { id: "BC3", label: "BC3", role: "Boundary", offset: 11.7, meanPathDelay: 228, rms: 6.1, frequencyPpb: 3.1, state: "LOCKED", ingress: "enp105s0f0np0", egress: "enp105s0f1np1", phc: "ptp14", color: TRACE_COLORS[2], measured: true, sampleCount: 120, servoSampleCount: 120, phcReadSpan: 2100, source: "simulation", lastSampleAt: null },
  { id: "BC4", label: "BC4", role: "Boundary", offset: 24.3, meanPathDelay: 241, rms: 10.8, frequencyPpb: -8.7, state: "LOCKED", ingress: "enp104s0f0np0", egress: "enp104s0f1np1", phc: "ptp12", color: TRACE_COLORS[3], measured: true, sampleCount: 120, servoSampleCount: 120, phcReadSpan: 2100, source: "simulation", lastSampleAt: null },
  { id: "BC5", label: "BC5", role: "Boundary", offset: 41.6, meanPathDelay: 237, rms: 18.9, frequencyPpb: -6.2, state: "LOCKED", ingress: "enp103s0f0np0", egress: "enp103s0f1np1", phc: "ptp10", color: TRACE_COLORS[4], measured: true, sampleCount: 120, servoSampleCount: 120, phcReadSpan: 2100, source: "simulation", lastSampleAt: null },
  { id: "BC6", label: "BC6", role: "Boundary", offset: 63.8, meanPathDelay: 255, rms: 27.6, frequencyPpb: -12.4, state: "TRACKING", ingress: "enp27s0f0np0", egress: "enp27s0f1np1", phc: "ptp6", color: TRACE_COLORS[5], measured: true, sampleCount: 120, servoSampleCount: 120, phcReadSpan: 2100, source: "simulation", lastSampleAt: null },
  { id: "BC7", label: "BC7 · OC", role: "Ordinary", offset: 91.2, meanPathDelay: 269, rms: 40.2, frequencyPpb: 7.9, state: "LOCKED", ingress: "enp28s0f0np0", egress: "enp28s0f1np1", phc: "ptp8", color: TRACE_COLORS[6], measured: true, sampleCount: 120, servoSampleCount: 120, phcReadSpan: 2100, source: "simulation", lastSampleAt: null },
];

const FALLBACK_INTERFACES: HostInterface[] = [
  ["enp25s0f0np0", "BC1 / INACTIVE IN", 100000, "ptp0", "0000:19:00.0", "mlx5_core", "BC1"],
  ["enp25s0f1np1", "BC1 / GM OUT", 100000, "ptp1", "0000:19:00.1", "mlx5_core", "BC1"],
  ["enp26s0f0np0", "BC2 / IN", 100000, "ptp2", "0000:1a:00.0", "mlx5_core", "BC2"],
  ["enp26s0f1np1", "BC2 / OUT", 100000, "ptp3", "0000:1a:00.1", "mlx5_core", "BC2"],
  ["enp105s0f0np0", "BC3 / IN", 100000, "ptp14", "0000:69:00.0", "mlx5_core", "BC3"],
  ["enp105s0f1np1", "BC3 / OUT", 100000, "ptp15", "0000:69:00.1", "mlx5_core", "BC3"],
  ["enp104s0f0np0", "BC4 / IN", 100000, "ptp12", "0000:68:00.0", "mlx5_core", "BC4"],
  ["enp104s0f1np1", "BC4 / OUT", 100000, "ptp13", "0000:68:00.1", "mlx5_core", "BC4"],
  ["enp103s0f0np0", "BC5 / IN", 100000, "ptp10", "0000:67:00.0", "mlx5_core", "BC5"],
  ["enp103s0f1np1", "BC5 / OUT", 100000, "ptp11", "0000:67:00.1", "mlx5_core", "BC5"],
  ["enp27s0f0np0", "BC6 / IN", 100000, "ptp6", "0000:1b:00.0", "mlx5_core", "BC6"],
  ["enp27s0f1np1", "BC6 / OUT", 100000, "ptp7", "0000:1b:00.1", "mlx5_core", "BC6"],
  ["enp28s0f0np0", "BC7 / OC IN", 100000, "ptp8", "0000:1c:00.0", "mlx5_core", "BC7"],
  ["enp28s0f1np1", "BC7 / INACTIVE OUT", 100000, "ptp9", "0000:1c:00.1", "mlx5_core", "BC7"],
  ["enp179s0f0", "MANAGEMENT", 1000, "ptp4", "0000:b3:00.0", "ixgbe", null],
  ["enp179s0f1", "SPARE", null, "ptp5", "0000:b3:00.1", "ixgbe", null],
].map(([name, assignment, speed, phc, bus, driver, namespace]) => ({
  name: name as string,
  assignment: assignment as string,
  speed_mbps: speed as number | null,
  phc: phc as string,
  bus: bus as string,
  driver: driver as string,
  namespace: namespace as string | null,
  state: speed ? "UP" : "DOWN",
  carrier: Boolean(speed),
  mac: "",
  hardware_timestamping: true,
}));

const EVENTS = [
  ["13:42:18.420", "INFO", "BC–06", "SERVO_LOCKED_STABLE", "Offset settled within ±100 ns"],
  ["13:42:16.114", "MEASURE", "OC", "MTIE_WINDOW_COMPLETE", "300 s window · 324 ns"],
  ["13:41:58.907", "WARN", "BC–06", "OFFSET_THRESHOLD", "+102.7 ns exceeded advisory limit"],
  ["13:41:42.380", "INFO", "BC–03", "PORT_STATE", "UNCALIBRATED → SLAVE"],
  ["13:40:05.021", "SYSTEM", "LAB", "CAPTURE_STARTED", "Run 024 · PI baseline / step response"],
  ["13:39:48.772", "INFO", "GM", "CLOCK_CLASS", "Class 6 · traceable to primary reference"],
];

function seededNoise(i: number, node: number) {
  return Math.sin(i * 0.43 + node * 1.7) * 0.62 + Math.sin(i * 0.17 + node * 0.8) * 0.38;
}

function buildHistory(length = 120): HistoryPoint[] {
  return Array.from({ length }, (_, i) => {
    const values = Object.fromEntries(
      INITIAL_NODES.map((node, nodeIndex) => {
        const wander = seededNoise(i, nodeIndex) * (nodeIndex * 3.4 + 1.5);
        const step = i > 54 && i < 82 ? Math.exp(-(i - 54) / 12) * nodeIndex * 5.4 : 0;
        return [node.id, Number((node.offset + wander + step).toFixed(2))];
      }),
    ) as Record<string, number>;
    const hopValues = Object.fromEntries(INITIAL_NODES.slice(1).map((node, index) => [node.id, values[node.id] - values[INITIAL_NODES[index].id]]));
    return { t: i - length + 1, values, hopValues };
  });
}

function formatNanoseconds(value: number, signed = false) {
  const absolute = Math.abs(value);
  const [scaled, unit] = absolute >= 1_000_000_000
    ? [value / 1_000_000_000, "s"]
    : absolute >= 1_000_000
      ? [value / 1_000_000, "ms"]
      : absolute >= 1_000
        ? [value / 1_000, "µs"]
        : [value, "ns"];
  const decimals = Math.abs(scaled) >= 100 ? 1 : Math.abs(scaled) >= 10 ? 2 : 3;
  return `${signed && value >= 0 ? "+" : ""}${scaled.toFixed(decimals)} ${unit}`;
}

function formatOffset(value: number, measured = true) {
  if (!measured || !Number.isFinite(value)) return "—";
  return formatNanoseconds(value, true);
}

function formatLineRate(speedMbps: number | null) {
  if (!speedMbps) return "—";
  if (speedMbps >= 1_000_000) return `${(speedMbps / 1_000_000).toFixed(2)} Tb/s`;
  if (speedMbps >= 1_000) return `${speedMbps / 1_000} Gb/s`;
  return `${speedMbps} Mb/s`;
}

function rangeSeconds(range: string) {
  return range === "30 s" ? 30 : range === "15 min" ? 900 : 120;
}

const PROTOCOL_SYNC_RATES = [0.5, 1, 2, 4, 8] as const;

function synchronizationRate(requestedHz: number) {
  const effectiveHz = PROTOCOL_SYNC_RATES.reduce((nearest, candidate) =>
    Math.abs(candidate - requestedHz) < Math.abs(nearest - requestedHz) ? candidate : nearest,
  );
  return { effectiveHz, logInterval: Math.round(-Math.log2(effectiveHz)) };
}

function frequencyFromLogInterval(logInterval: number) {
  return 2 ** -logInterval;
}

function stateFromMeasurement(sample: TelemetrySample | null, stale: boolean, role: TelemetryClock["role"]): ClockState {
  if (role === "grandmaster") return "REFERENCE";
  if (!sample) return "NO DATA";
  if (!sample.valid) return "FAULTY";
  if (stale) return "STALE";
  if (sample.servo_state === "s2") return "LOCKED";
  if (sample.servo_state === "s1") return "TRACKING";
  return "UNLOCKED";
}

function nodesFromTelemetry(payload: TelemetryPayload): ClockNode[] {
  return payload.clocks.map((clock, index) => {
    const ptpMeasurement = clock.measurement;
    const phcMeasurement = clock.phc_measurement;
    const servoControl = payload.servo_control?.nodes?.[clock.id];
    const stale = Boolean(ptpMeasurement && payload.timestamp - ptpMeasurement.observed_at > 5);
    return {
      id: clock.id,
      label: `${clock.id}${clock.role === "grandmaster" ? " · GM" : clock.role === "ordinary" ? " · OC" : ""}`,
      role: clock.role === "grandmaster" ? "Grandmaster" : clock.role === "ordinary" ? "Ordinary" : "Boundary",
      offset: phcMeasurement?.offset_ns ?? 0,
      hopOffset: phcMeasurement?.previous_hop_offset_ns ?? 0,
      meanPathDelay: ptpMeasurement?.mean_path_delay_ns ?? 0,
      rms: clock.rms_ns ?? 0,
      frequencyPpb: ptpMeasurement?.frequency_ppb ?? 0,
      state: servoControl?.mode === "holdover" ? "HOLDOVER" : stateFromMeasurement(ptpMeasurement, stale, clock.role),
      ingress: clock.ingress,
      egress: clock.egress,
      phc: clock.measurement_phc ? `/dev/${clock.measurement_phc}` : "—",
      color: TRACE_COLORS[index % TRACE_COLORS.length],
      measured: Boolean(phcMeasurement?.valid && phcMeasurement.offset_ns !== null),
      ptpMeasured: Boolean(ptpMeasurement?.valid),
      sampleCount: clock.phc_window_sample_count,
      servoSampleCount: clock.window_locked_sample_count,
      phcReadSpan: phcMeasurement?.read_span_ns ?? null,
      phcUncertainty: phcMeasurement?.comparison_uncertainty_ns ?? null,
      phcMethod: phcMeasurement?.cross_timestamp_method ?? null,
      servoType: servoControl?.type ?? (clock.role === "grandmaster" ? null : "pi"),
      servoEnabled: servoControl?.enabled ?? clock.role !== "grandmaster",
      holdoverStarted: servoControl?.holdover_started ?? null,
      kalman: clock.kalman ?? null,
      source: phcMeasurement?.error ?? (clock.measurement_phc ? `direct /dev/${clock.measurement_phc} read` : "No PHC mapping"),
      lastSampleAt: phcMeasurement?.observed_at ?? null,
    };
  });
}

function historyFromTelemetry(payload: TelemetryPayload): HistoryPoint[] {
  const synchronized = new Map<string, HistoryPoint>();
  for (const clock of payload.clocks) {
    for (const sample of clock.phc_samples) {
      if (!sample.valid || sample.offset_ns === null) continue;
      const cycle = sample.sample_id.replace(/:[^:]+$/, "");
      const point = synchronized.get(cycle) ?? { t: sample.observed_at, values: {}, hopValues: {}, key: cycle };
      point.values[clock.id] = sample.offset_ns;
      if (sample.previous_hop_offset_ns !== null) point.hopValues![clock.id] = sample.previous_hop_offset_ns;
      synchronized.set(cycle, point);
    }
  }
  return [...synchronized.values()].sort((left, right) => left.t - right.t);
}

function historyFromPhcTelemetry(payload: PhcTelemetryPayload): HistoryPoint[] {
  const synchronized = new Map<string, HistoryPoint>();
  for (const clock of payload.clocks) {
    for (const sample of clock.samples) {
      if (!sample.valid || sample.offset_ns === null) continue;
      const cycle = sample.sample_id.replace(/:[^:]+$/, "");
      const point = synchronized.get(cycle) ?? { t: sample.observed_at, values: {}, hopValues: {}, key: cycle };
      point.values[clock.id] = sample.offset_ns;
      if (sample.previous_hop_offset_ns !== null) point.hopValues![clock.id] = sample.previous_hop_offset_ns;
      synchronized.set(cycle, point);
    }
  }
  return [...synchronized.values()].sort((left, right) => left.t - right.t);
}

function nodesWithPhcTelemetry(current: ClockNode[], payload: PhcTelemetryPayload) {
  const byId = new Map(payload.clocks.map((clock) => [clock.id, clock]));
  return current.map((node) => {
    const clock = byId.get(node.id);
    const measurement = clock?.measurement;
    if (!clock || !measurement || measurement.observed_at < (node.lastSampleAt ?? 0)) return node;
    return {
      ...node,
      phc: `/dev/${clock.phc}`,
      offset: measurement.offset_ns ?? 0,
      hopOffset: measurement.previous_hop_offset_ns ?? 0,
      measured: Boolean(measurement.valid && measurement.offset_ns !== null),
      sampleCount: clock.window_sample_count,
      phcReadSpan: measurement.read_span_ns,
      phcUncertainty: measurement.comparison_uncertainty_ns ?? null,
      phcMethod: measurement.cross_timestamp_method,
      source: measurement.error ?? `direct /dev/${clock.phc} read`,
      lastSampleAt: measurement.observed_at,
    };
  });
}

function preserveNewerPhcTelemetry(incoming: ClockNode[], current: ClockNode[]) {
  const currentById = new Map(current.map((node) => [node.id, node]));
  return incoming.map((node) => {
    const newer = currentById.get(node.id);
    if (!newer || (newer.lastSampleAt ?? 0) <= (node.lastSampleAt ?? 0)) return node;
    return {
      ...node,
      phc: newer.phc,
      offset: newer.offset,
      hopOffset: newer.hopOffset,
      measured: newer.measured,
      sampleCount: newer.sampleCount,
      phcReadSpan: newer.phcReadSpan,
      phcUncertainty: newer.phcUncertainty,
      phcMethod: newer.phcMethod,
      source: newer.source,
      lastSampleAt: newer.lastSampleAt,
    };
  });
}

function waitingNodes(source: string): ClockNode[] {
  return INITIAL_NODES.map((node) => ({
    ...node,
    offset: 0,
    meanPathDelay: 0,
    rms: 0,
    frequencyPpb: 0,
    measured: false,
    sampleCount: 0,
    servoSampleCount: 0,
    phcReadSpan: null,
    source,
    state: node.role === "Grandmaster" ? "REFERENCE" : "NO DATA",
  }));
}

function mergeRawHistory(current: HistoryPoint[], incoming: HistoryPoint[], seconds: number) {
  const cutoff = Date.now() / 1000 - seconds;
  const unique = new Map<string, HistoryPoint>();
  for (const point of [...current, ...incoming]) {
    if (point.t >= cutoff) unique.set(point.key ?? `${point.t}:${Object.keys(point.values)[0]}`, point);
  }
  return [...unique.values()].sort((left, right) => left.t - right.t).slice(-30_000);
}

function buildResearchModel(history: HistoryPoint[], nodes: ClockNode[]): ResearchPayload {
  const endpoint = nodes[nodes.length - 1]?.id ?? null;
  const endpointValues = endpoint ? history.map((point) => point.values[endpoint]).filter(Number.isFinite) : [];
  const tauFactors = [1, 2, 4, 8, 16, 32].filter((factor) => endpointValues.length > factor * 3);
  const stability: ResearchPayload["stability"] = { adev: [], mdev: [], tdev: [], hdev: [], mtie: [], theo1: [] };
  tauFactors.forEach((factor) => {
    const second = endpointValues.slice(0, -2 * factor).map((value, index) => endpointValues[index + 2 * factor] - 2 * endpointValues[index + factor] + value);
    const adev = Math.sqrt(second.reduce((sum, value) => sum + value * value, 0) / Math.max(1, 2 * second.length)) * 1e-9 / factor;
    const windows = endpointValues.slice(0, -factor).map((_value, index) => {
      const values = endpointValues.slice(index, index + factor + 1);
      return Math.max(...values) - Math.min(...values);
    });
    const confidence = Math.min(.99, 1 - 1 / Math.sqrt(second.length + 1));
    stability.adev.push({ tau_s: factor, value: adev, pairs: second.length, confidence });
    stability.mdev.push({ tau_s: factor, value: adev * (.86 + factor * .003), pairs: second.length, confidence });
    stability.tdev.push({ tau_s: factor, value: factor * adev * 1e9 / Math.sqrt(3), pairs: second.length, confidence });
    stability.hdev.push({ tau_s: factor, value: adev * .78, pairs: second.length, confidence });
    stability.mtie.push({ tau_s: factor, value: Math.max(...windows, 0), pairs: windows.length, confidence: null });
    stability.theo1.push({ tau_s: factor, value: Math.sqrt(second.reduce((sum, value) => sum + value * value, 0) / Math.max(1, second.length)), pairs: second.length, confidence });
  });
  const hopIds = nodes.slice(1).map((node) => node.id);
  const hopSeries = hopIds.map((id) => history.map((point) => point.hopValues?.[id]).filter((value): value is number => Number.isFinite(value)));
  const recurrenceLength = Math.min(64, ...hopSeries.map((series) => series.length));
  const vectors = Array.from({ length: Math.max(0, recurrenceLength) }, (_, index) => hopSeries.map((series) => series[series.length - recurrenceLength + index]));
  const distances = vectors.flatMap((left, row) => vectors.slice(0, row).map((right) => Math.sqrt(left.reduce((sum, value, index) => sum + (value - right[index]) ** 2, 0))));
  const sortedDistances = [...distances].sort((left, right) => left - right);
  const recurrenceThreshold = sortedDistances[Math.floor(sortedDistances.length * .14)] ?? 0;
  const recurrenceMatrix = vectors.map((left) => vectors.map((right) => Math.sqrt(left.reduce((sum, value, index) => sum + (value - right[index]) ** 2, 0)) <= recurrenceThreshold ? "1" : "0").join(""));
  const recurrenceCount = recurrenceMatrix.reduce((sum, row) => sum + [...row].filter((value) => value === "1").length, 0);
  const bifurcationInput = endpointValues.slice(-384);
  const bifurcationCenter = median(bifurcationInput);
  const bifurcationForcing = bifurcationInput.map((value) => value - bifurcationCenter);
  const bifurcationEnvelope = Math.max(1, percentile(bifurcationForcing.map(Math.abs), .95));
  const bifurcationHardLimit = Math.max(20_000, bifurcationEnvelope * 12);
  const bifurcationPoints: NonNullable<ResearchPayload["bifurcation"]["points"]> = [];
  const bifurcationSummaries: NonNullable<ResearchPayload["bifurcation"]["summaries"]> = [];
  Array.from({ length: 46 }, (_, index) => .25 + index * 2.25 / 45).forEach((gainScale) => {
    let correction = 0;
    let integral = 0;
    let peak = 0;
    let divergent = false;
    let settled: number[] = [];
    for (let replayPass = 0; replayPass < 4 && !divergent; replayPass += 1) {
      for (const measurement of bifurcationForcing) {
        const residual = measurement - correction;
        if (!Number.isFinite(residual) || Math.abs(residual) > bifurcationHardLimit * 8) {
          divergent = true;
          break;
        }
        integral = Math.max(-200_000, Math.min(200_000, integral + residual));
        correction += gainScale * (.7 * residual + .3 * integral);
        peak = Math.max(peak, Math.abs(residual));
        if (replayPass === 3) settled.push(residual);
      }
    }
    settled = settled.slice(-Math.max(16, Math.min(96, Math.floor(settled.length / 3))));
    let extrema = settled.filter((value, index) => index > 0 && index < settled.length - 1 && (value - settled[index - 1]) * (settled[index + 1] - value) <= 0);
    if (extrema.length < 3 && settled.length) extrema = Array.from({ length: 8 }, (_, index) => settled[Math.round(index * (settled.length - 1) / 7)]);
    if (extrema.length > 20) extrema = Array.from({ length: 20 }, (_, index) => extrema[Math.round(index * (extrema.length - 1) / 19)]);
    if (divergent) extrema = [-bifurcationHardLimit, bifurcationHardLimit];
    const tailRms = settled.length ? Math.sqrt(settled.reduce((sum, value) => sum + value * value, 0) / settled.length) : null;
    const stable = !divergent && tailRms !== null && peak <= bifurcationHardLimit && tailRms <= Math.max(4 * bifurcationEnvelope, 500);
    const branchCount = Math.min(16, Math.max(1, Math.round(extrema.length / 2)));
    const regime = stable ? (branchCount <= 2 ? "single-band" : branchCount <= 8 ? "multi-band" : "broadband") : "divergent";
    extrema.forEach((value, branch) => bifurcationPoints.push({ gain_scale: gainScale, residual_ns: Math.max(-bifurcationHardLimit, Math.min(bifurcationHardLimit, value)), stable, regime, branch, clipped: divergent || Math.abs(value) >= bifurcationHardLimit }));
    bifurcationSummaries.push({ gain_scale: gainScale, kp: .7 * gainScale, ki: .3 * gainScale, stable, regime, branch_count: branchCount, tail_rms_ns: tailRms, peak_ns: peak });
  });
  const bifurcationTransition = bifurcationSummaries.find((item) => !item.stable)?.gain_scale ?? null;
  const bifurcationCurrent = bifurcationSummaries.reduce((closest, item) => Math.abs(item.gain_scale - 1) < Math.abs(closest.gain_scale - 1) ? item : closest, bifurcationSummaries[0]);
  const bifurcationVisibleValues = bifurcationPoints.filter((point) => !point.clipped).map((point) => Math.abs(point.residual_ns));
  const bifurcationDisplayLimit = Math.min(bifurcationHardLimit, Math.max(25, bifurcationEnvelope * 1.25, percentile(bifurcationVisibleValues, .99) * 1.08));
  const modeledCorrelationPoints = Array.from({ length: 20 }, (_, index) => {
    const radius = .08 * (1.18 ** index);
    const correlationSum = Math.min(.78, .42 * (radius ** 1.37));
    return { radius, correlation_sum: correlationSum, log_radius: Math.log(radius), log_correlation: Math.log(correlationSum) };
  });
  const modeledHiguchiPoints = Array.from({ length: 32 }, (_, index) => {
    const k = index + 1;
    const length = 18 * ((1 / k) ** 1.28) * (1 + .015 * Math.sin(index * .8));
    return { k, length, log_inverse_k: Math.log(1 / k), log_length: Math.log(length) };
  });
  const modeledMultifractalExponents = [-4, -2, 0, 2, 4].map((q) => ({
    q,
    h: .82 - q * .018,
    r_squared: .97,
    points: [8, 12, 18, 27, 40, 60].map((scale) => ({ scale, fluctuation: (scale ** (.82 - q * .018)) * 2.4 })),
  }));
  const variances = nodes.slice(1).map((node) => Math.max(1, node.rms ** 2));
  const rawWeights = variances.map((value) => 1 / value);
  const weightTotal = rawWeights.reduce((sum, value) => sum + value, 0);
  const weights = Object.fromEntries(nodes.slice(1).map((node, index) => [node.id, rawWeights[index] / weightTotal]));
  const virtualOffset = nodes.slice(1).reduce((sum, node) => sum + node.offset * (weights[node.id] ?? 0), 0);
  const currentOffset = nodes[nodes.length - 1]?.offset ?? 0;
  const pathEvents: PathEvent[] = nodes.slice(1).flatMap((node, index) => {
    const base = 10_000_000_000 + index * 1_000_000;
    const transit = Math.max(30, node.meanPathDelay / 2);
    return [
      { node: node.id, kind: "sync" as const, observed_at: Date.now() / 1000 - index * .04, sequence_id: 4100 + index, correction_ns: 8 + index, t1_ns: base, t2_ns: base + transit, forward_transit_ns: transit - 8 - index, raw: false },
      { node: node.id, kind: "delay" as const, observed_at: Date.now() / 1000 - index * .04 + .01, sequence_id: 8200 + index, correction_ns: 5 + index, t3_ns: base + 600_000, t4_ns: base + 600_000 + transit + 12, reverse_transit_ns: transit + 7 - index, raw: false },
    ];
  });
  const errorNodes = Object.fromEntries(nodes.map((node, index) => {
    const components = {
      cross_timestamp: node.phcUncertainty ?? (6 + index * 2),
      servo: node.rms,
      path: Math.max(0, node.meanPathDelay * .015),
      holdover: index * 1.8,
    };
    const squared = Object.fromEntries(Object.entries(components).map(([key, value]) => [key, value * value]));
    const total = Object.values(squared).reduce((sum, value) => sum + value, 0);
    return [node.id, { rss_ns: Math.sqrt(total), components_ns: components, contribution_pct: Object.fromEntries(Object.entries(squared).map(([key, value]) => [key, 100 * value / Math.max(1e-9, total)])) }];
  }));
  return {
    generated_at: Date.now() / 1000,
    mode: "simulation",
    sample_count: history.length,
    aligned_sample_count: history.length,
    sample_rate_hz: 1,
    endpoint,
    stability,
    fusion: {
      status: "solved",
      reference: "BC1",
      nodes: Object.fromEntries(nodes.map((node, index) => [node.id, { offset_ns: node.offset, sigma_ns: index === 0 ? 0 : 2 + index * 1.7 }])),
      residuals: nodes.slice(1).map((node, index) => ({ source: "modeled PHC factor", edge: `BC1→${node.id}`, residual_ns: Math.sin(index) * 1.7, normalized: Math.sin(index) * .4 })),
      chi_square: 1.86,
      degrees_of_freedom: Math.max(0, nodes.length - 2),
    },
    ensemble: { status: "ready", samples: history.length, virtual_offset_ns: virtualOffset, one_sigma_ns: Math.sqrt(1 / weightTotal), weights },
    change_detection: { status: "stable", latest_probability: .018, probabilities: history.map((_point, index) => .01 + .15 * Math.max(0, Math.sin(index * .12 - 2.5)) ** 8), change_points: [] },
    recurrence: { status: "ready", matrix: recurrenceMatrix, samples: recurrenceLength, recurrence_rate: recurrenceCount / Math.max(1, recurrenceLength ** 2), determinism: .71, diagonal_lines: 18, threshold_sigma: recurrenceThreshold },
    bifurcation: {
      status: bifurcationInput.length >= 32 ? "ready" : "learning",
      samples: bifurcationInput.length,
      parameter: "PI gain scale",
      parameter_min: .25,
      parameter_max: 2.5,
      current_gain_scale: 1,
      base_gains: { kp: .7, ki: .3 },
      active_controller: nodes[nodes.length - 1]?.servoType ?? "pi",
      baseline_is_live: (nodes[nodes.length - 1]?.servoType ?? "pi") === "pi",
      points: bifurcationPoints,
      summaries: bifurcationSummaries,
      current: bifurcationCurrent,
      stable_through_gain: bifurcationSummaries.filter((item) => item.stable).at(-1)?.gain_scale ?? null,
      first_transition_gain: bifurcationTransition,
      display_limit_ns: bifurcationDisplayLimit,
      forcing_envelope_ns: bifurcationEnvelope,
      method: "settled extrema from bounded offline PI replay",
      provenance: "modeled endpoint phase; centered and replayed without writing a clock",
      interpretation: "A response-branch screening map. A true hardware bifurcation requires a controlled gain sweep with settled observations at every step.",
      live_changes: 0,
    },
    fractal: {
      status: endpointValues.length >= 128 ? "ready" : endpointValues.length >= 32 ? "partial" : "learning",
      samples: endpointValues.length,
      higuchi: {
        status: endpointValues.length >= 32 ? "ready" : "learning",
        samples: endpointValues.length,
        dimension: 1.28,
        r_squared: .986,
        k_max: 32,
        points: modeledHiguchiPoints,
        fit: { slope: 1.28, intercept: Math.log(18), r_squared: .986 },
        interpretation: "graph roughness of modeled endpoint phase versus sample index",
      },
      correlation: {
        status: endpointValues.length >= 64 ? "ready" : "learning",
        samples: endpointValues.length,
        dimension: 1.37,
        r_squared: .974,
        embedding_dimension: 5,
        delay_samples: 3,
        theiler_window_samples: 6,
        converged: true,
        embeddings: [2, 3, 4, 5].map((dimension, index) => ({ dimension, status: "ready", estimate: 1.19 + index * .06, r_squared: .96 + index * .004, pairs: 4096 })),
        points: modeledCorrelationPoints,
        fit: { slope: 1.37, intercept: Math.log(.42), r_squared: .974, start_index: 3, end_index: 14, radius_min: modeledCorrelationPoints[3].radius, radius_max: modeledCorrelationPoints[14].radius, point_count: 12 },
        interpretation: "correlation-sum slope in a selected modeled scaling window",
      },
      multifractal: {
        status: endpointValues.length >= 128 ? "ready" : "learning",
        samples: endpointValues.length,
        q_min: -4,
        q_max: 4,
        spectrum_width: .144,
        surrogate_width: .061,
        correlation_excess_width: .083,
        surrogate_count: 6,
        exponents: modeledMultifractalExponents,
        scales: [8, 12, 18, 27, 40, 60],
        interpretation: "generalized Hurst spread with deterministic shuffled surrogates",
      },
      method: "Higuchi graph dimension + Grassberger–Procaccia D2 + MF-DFA",
      provenance: "modeled endpoint phase; no interpolation and no clock writes",
      interpretation: "Finite-record scaling diagnostics. A non-integer dimension is not, by itself, evidence of deterministic chaos or a strange attractor.",
      live_changes: 0,
    },
    koopman: { status: "ready", singular_values: [1.014, .982, .941, .72, .38, .17], spectral_norm: 1.014, residual_sigma_ns: 2.84, interpretation: "amplifying" },
    system_identification: { status: "stable", samples: history.length, spectral_radius: .921, settling_time_s: 48.6, dc_gain: .84, r_squared: .91, residual_sigma_ns: 4.2, poles: [{ real: .91, imag: .14, magnitude: .921 }, { real: .91, imag: -.14, magnitude: .921 }] },
    auto_tune: {
      status: "recommended",
      samples: history.length,
      predicted_improvement_pct: 22.4,
      safe_candidates: 21,
      evaluated_candidates: 36,
      recommendation: { kp: .6, ki: .2, rms_ns: Math.max(1, (nodes[nodes.length - 1]?.rms ?? 20) * .776), peak_ns: Math.abs(currentOffset) * 1.2, score: 24.8, safe: true },
      baseline: { kp: .7, ki: .3, rms_ns: nodes[nodes.length - 1]?.rms ?? 20, score: 31.9 },
      frontier: [{ kp: .6, ki: .2, score: 24.8, rms_ns: 18.1 }, { kp: .8, ki: .2, score: 25.4, rms_ns: 18.6 }, { kp: .6, ki: .35, score: 27.1, rms_ns: 19.2 }],
    },
    temperature_holdover: Object.fromEntries(nodes.slice(1).map((node, index) => [node.id, { status: "ready", temperature_c: 42.8 + index * .7, predicted_frequency_ppb: node.frequencyPpb + index * .4, predicted_phase_ns: node.offset + node.frequencyPpb * 300, one_sigma_ns: 18 + index * 8, horizon_s: 300 }])),
    error_budget: {
      nodes: errorNodes,
      cascade: {
        hop_count: Math.max(0, nodes.length - 1),
        samples: history.length,
        independent_sigma_ns: Math.sqrt(nodes.slice(1).reduce((sum, node) => sum + node.rms ** 2, 0)),
        correlated_sigma_ns: Math.sqrt(nodes.slice(1).reduce((sum, node, index) => sum + node.rms ** 2 * (1 + index * .08), 0)),
        cross_covariance_ns2: nodes.slice(1).reduce((sum, node, index) => sum + node.rms ** 2 * index * .08, 0),
      },
      method: "modeled covariance propagation",
    },
    capabilities: {
      dpll: { supported: false, reason: "Hardware-model mode does not invent DPLL state." },
      synce: { supported: false, state: "not-reported", reason: "Hardware-model mode does not infer SyncE from PTP lock." },
      devlink_health: { supported: false },
      temperature: { supported: false, nodes: {} },
      path_monitor: { supported: false, events: pathEvents.length, reason: "Modeled exchange shown for the hosted demo." },
      pps_common_edge: { supported: false, state: {} },
    },
    profiles: {
      profile: "G.8275.1 Telecom",
      compliant: true,
      available_profiles: ["IEEE 1588 Default", "G.8275.1 Telecom", "G.8275.2 Telecom", "IEEE 802.1AS gPTP", "IEEE C37.238 Power"],
      checks: [
        { name: "Transport", actual: "L2", expected: ["L2"], pass: true },
        { name: "Delay mechanism", actual: "E2E", expected: ["E2E"], pass: true },
        { name: "Two-step operation", actual: true, expected: true, pass: true },
      ],
    },
    path_microscope: { events: pathEvents, mode: "simulation", provenance: "Deterministic modeled exchange; live mode uses LinuxPTP slave-event-monitor TLVs" },
    experiments: [{ id: "run-model-024", name: "PI baseline / step response", kind: "step", state: "completed", started_at: Date.now() / 1000 - 1440, stopped_at: Date.now() / 1000 - 1320, sample_count: history.length * nodes.length, event_count: 6 }],
    active_experiment: null,
    security: { authentication: { enabled: false, spp: 0, active_key_id: 1, allow_unauth: 0, key_material_exposed: false } },
  };
}

function LineChart({ data, selected, nodes, compact = false }: { data: HistoryPoint[]; selected: string[]; nodes: ClockNode[]; compact?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const bounds = wrap.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = bounds.width * dpr;
    canvas.height = bounds.height * dpr;
    canvas.style.width = `${bounds.width}px`;
    canvas.style.height = `${bounds.height}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    const w = bounds.width;
    const h = bounds.height;
    const pad = compact ? { l: 18, r: 12, t: 12, b: 20 } : { l: 52, r: 18, t: 18, b: 34 };
    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;
    ctx.clearRect(0, 0, w, h);
    const allValues = data.flatMap((point) => selected.map((id) => point.values[id]).filter((value): value is number => Number.isFinite(value)));
    if (data.length < 2 || allValues.length < 2) {
      ctx.fillStyle = "#69818a";
      ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("Waiting for direct PHC samples", w / 2, h / 2);
      return;
    }
    const measuredMin = Math.min(...allValues);
    const measuredMax = Math.max(...allValues);
    const measuredSpan = Math.max(25, measuredMax - measuredMin);
    const padding = Math.max(12.5, measuredSpan * 0.08);
    const yMax = Math.max(0, measuredMax) + padding;
    const yMin = Math.min(0, measuredMin) - padding;
    const span = yMax - yMin;
    const tMin = Math.min(...data.map((point) => point.t));
    const tMax = Math.max(...data.map((point) => point.t));
    const timeSpan = Math.max(1, tMax - tMin);
    const x = (timestamp: number) => pad.l + ((timestamp - tMin) / timeSpan) * plotW;
    const y = (value: number) => pad.t + ((yMax - value) / span) * plotH;

    ctx.lineWidth = 1;
    ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    const horizontalLines = compact ? 3 : 5;
    for (let i = 0; i <= horizontalLines; i++) {
      const value = yMax - (i / horizontalLines) * span;
      const py = y(value);
      ctx.strokeStyle = value === 0 ? "rgba(119, 220, 218, .24)" : "rgba(126, 155, 164, .12)";
      ctx.beginPath();
      ctx.moveTo(pad.l, py);
      ctx.lineTo(w - pad.r, py);
      ctx.stroke();
      if (!compact) {
        ctx.fillStyle = "#69818a";
        ctx.fillText(formatNanoseconds(value), pad.l - 10, py);
      }
    }
    for (let i = 0; i <= 6; i++) {
      const px = pad.l + (i / 6) * plotW;
      ctx.strokeStyle = "rgba(126, 155, 164, .08)";
      ctx.beginPath();
      ctx.moveTo(px, pad.t);
      ctx.lineTo(px, h - pad.b);
      ctx.stroke();
      if (!compact) {
        const seconds = Math.round(-timeSpan + (i / 6) * timeSpan);
        ctx.fillStyle = "#69818a";
        ctx.textAlign = i === 0 ? "left" : i === 6 ? "right" : "center";
        ctx.fillText(i === 6 ? "now" : `${seconds}s`, px, h - 12);
      }
    }

    selected.forEach((id) => {
      const nodeIndex = nodes.findIndex((node) => node.id === id);
      if (nodeIndex < 0) return;
      const series = data.filter((point) => Number.isFinite(point.values[id]));
      if (series.length < 2) return;
      const deltas = series.slice(1).map((point, index) => point.t - series[index].t).filter((value) => value > 0).sort((left, right) => left - right);
      const medianDelta = deltas[Math.floor(deltas.length / 2)] ?? 1;
      const gapThreshold = Math.max(1, medianDelta * 4);
      ctx.strokeStyle = nodes[nodeIndex].color;
      ctx.lineWidth = id === selected[selected.length - 1] ? 2.2 : 1.25;
      ctx.globalAlpha = id === selected[selected.length - 1] ? 1 : 0.54;
      ctx.beginPath();
      series.forEach((point, index) => {
        const px = x(point.t);
        const py = y(point.values[id]);
        if (index === 0 || point.t - series[index - 1].t > gapThreshold) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
    });
    ctx.globalAlpha = 1;

    const activeId = selected[selected.length - 1];
    if (activeId) {
      const series = data.filter((point) => Number.isFinite(point.values[activeId]));
      const lastPoint = series[series.length - 1];
      const nodeIndex = nodes.findIndex((node) => node.id === activeId);
      if (!lastPoint || nodeIndex < 0) return;
      const last = lastPoint.values[activeId];
      ctx.fillStyle = nodes[nodeIndex].color;
      ctx.beginPath();
      ctx.arc(x(lastPoint.t), y(last), 3.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }, [compact, data, nodes, selected]);

  useEffect(() => {
    draw();
    const observer = new ResizeObserver(draw);
    if (wrapRef.current) observer.observe(wrapRef.current);
    return () => observer.disconnect();
  }, [draw]);

  return (
    <div className={`chart-canvas ${compact ? "compact" : ""}`} ref={wrapRef}>
      <canvas ref={canvasRef} aria-label="PTP offset traces over time" role="img" />
    </div>
  );
}

type PendulumLinkAnalysis = {
  node: ClockNode;
  hop: number;
  current: number;
  equilibrium: number;
  residual: number;
  envelope: number;
  samples: number;
  autoZeros: number;
  regime: "STABLE" | "LEARNING" | "SHIFT CHECK" | "NO DATA";
};

function median(values: number[]) {
  if (!values.length) return 0;
  const ordered = [...values].sort((left, right) => left - right);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
}

function percentile(values: number[], fraction: number) {
  if (!values.length) return 0;
  const ordered = [...values].sort((left, right) => left - right);
  const position = Math.max(0, Math.min(ordered.length - 1, (ordered.length - 1) * fraction));
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  const weight = position - lower;
  return ordered[lower] * (1 - weight) + ordered[upper] * weight;
}

function robustSigma(values: number[]) {
  if (values.length < 3) return 0;
  const center = median(values);
  return median(values.map((value) => Math.abs(value - center))) * 1.4826;
}

function analyzePendulumLink(
  node: ClockNode,
  hop: number,
  series: { t: number; value: number }[],
  manualEquilibrium: number | undefined,
  autoZero: boolean,
): PendulumLinkAnalysis {
  if (!series.length) {
    const current = node.measured ? node.hopOffset ?? 0 : 0;
    const equilibrium = manualEquilibrium ?? current;
    return { node, hop, current, equilibrium, residual: current - equilibrium, envelope: 0, samples: 0, autoZeros: 0, regime: node.measured ? "LEARNING" : "NO DATA" };
  }

  let equilibrium = manualEquilibrium ?? median(series.slice(0, Math.min(12, series.length)).map((point) => point.value));
  let equilibriumAt = series[0].t;
  let stableResiduals: number[] = [];
  let candidate: { t: number; value: number }[] = [];
  let autoZeros = 0;

  for (const point of series) {
    const residual = point.value - equilibrium;
    const sigma = robustSigma(stableResiduals.slice(-30));
    const shiftThreshold = Math.max(80, sigma * 8);
    if (autoZero && Math.abs(residual) > shiftThreshold) {
      candidate = [...candidate.slice(-5), point];
      if (candidate.length >= 5) {
        const values = candidate.map((item) => item.value);
        const nextEquilibrium = median(values);
        const distance = Math.abs(nextEquilibrium - equilibrium);
        const candidateSigma = robustSigma(values);
        if (distance > shiftThreshold && candidateSigma <= Math.max(18, distance * 0.18)) {
          equilibrium = nextEquilibrium;
          equilibriumAt = candidate[0].t;
          stableResiduals = [];
          candidate = [];
          autoZeros += 1;
        }
      }
    } else {
      candidate = [];
      stableResiduals = [...stableResiduals.slice(-59), residual];
    }
  }

  const residuals = series.filter((point) => point.t >= equilibriumAt).map((point) => point.value - equilibrium);
  const current = series[series.length - 1].value;
  const regime = candidate.length ? "SHIFT CHECK" : residuals.length < 8 ? "LEARNING" : "STABLE";
  return {
    node,
    hop,
    current,
    equilibrium,
    residual: current - equilibrium,
    envelope: percentile(residuals.map(Math.abs), 0.95),
    samples: residuals.length,
    autoZeros,
    regime,
  };
}

function MultiPendulum({
  history,
  nodes,
  autoZero,
  setAutoZero,
  zeroState,
  onZero,
  paused,
  connection,
}: {
  history: HistoryPoint[];
  nodes: ClockNode[];
  autoZero: boolean;
  setAutoZero: (value: boolean) => void;
  zeroState: PendulumZeroState;
  onZero: () => void;
  paused: boolean;
  connection: ConnectionMode;
}) {
  const links = useMemo(() => nodes.slice(1).map((node, index) => {
    const fullSeries = history.flatMap((point) => {
      const value = point.hopValues?.[node.id];
      return Number.isFinite(value) ? [{ t: point.t, value: value as number }] : [];
    });
    const series = zeroState.at === null ? fullSeries : fullSeries.filter((point) => point.t >= zeroState.at!);
    return analyzePendulumLink(node, index + 1, series, zeroState.baselines[node.id], autoZero);
  }), [autoZero, history, nodes, zeroState]);

  const motionScale = useMemo(() => Math.max(5, percentile(links.flatMap((link) => [Math.abs(link.residual), link.envelope]).filter(Number.isFinite), 0.95)), [links]);
  const maxAngle = 31;
  const anchor = { x: 500, y: 54 };
  const rodLength = Math.min(64, 372 / Math.max(1, links.length));
  const geometry = links.reduce<{ x: number; y: number; angle: number }[]>((points, link) => {
    const origin = points[points.length - 1] ?? { ...anchor, angle: 0 };
    const angle = maxAngle * Math.tanh(link.residual / Math.max(1, motionScale * 0.88));
    const radians = angle * Math.PI / 180;
    points.push({ x: origin.x + Math.sin(radians) * rodLength, y: origin.y + Math.cos(radians) * rodLength, angle });
    return points;
  }, []);
  const largest = links.reduce<PendulumLinkAnalysis | null>((current, link) => !current || Math.abs(link.residual) > Math.abs(current.residual) ? link : current, null);
  const stableLinks = links.filter((link) => link.regime === "STABLE").length;
  const zeroEvents = links.reduce((total, link) => total + link.autoZeros, 0) + (zeroState.at === null ? 0 : 1);
  const hasSamples = links.some((link) => link.samples > 0 || link.node.measured);
  const accessibleSummary = links.map((link) => `${link.node.id} residual ${formatNanoseconds(link.residual, true)}, ${link.regime.toLowerCase()}`).join("; ");

  return (
    <div className="pendulum-layout">
      <section className="instrument-panel pendulum-panel">
        <div className="panel-heading pendulum-heading">
          <div><span className="section-kicker">MEASUREMENT-DRIVEN KINEMATICS</span><h2>Cascade phase pendulum</h2></div>
          <div className="pendulum-controls">
            <span className="auto-zero-control"><span>Auto-zero</span><Toggle on={autoZero} onChange={setAutoZero} label="Automatic equilibrium zeroing" /></span>
            <button className="quiet-button" type="button" disabled={!hasSamples} onClick={onZero}><TimerReset size={14} /> Zero now</button>
          </div>
        </div>
        <div className="pendulum-workbench">
          <div className="pendulum-stage">
            <svg className="pendulum-svg" viewBox="0 0 1000 500" role="img" aria-label={`Multi-pendulum of previous-hop PHC residuals. ${accessibleSummary}`}>
              <title>PTP cascade multi-pendulum</title>
              <desc>Each rod angle is driven by one boundary-clock previous-hop PHC residual around its current equilibrium. Positive residuals swing right and negative residuals swing left.</desc>
              <line className="pendulum-centerline" x1={anchor.x} y1="24" x2={anchor.x} y2="462" />
              <text className="pendulum-polarity" x="72" y="36">− PHASE</text>
              <text className="pendulum-polarity" x="928" y="36" textAnchor="end">+ PHASE</text>
              <g className="pendulum-anchor">
                <line x1={anchor.x - 42} y1="34" x2={anchor.x + 42} y2="34" />
                <circle cx={anchor.x} cy={anchor.y} r="8" />
                <text x={anchor.x} y="19" textAnchor="middle">BC1 · GM REFERENCE</text>
              </g>
              {links.map((link, index) => {
                const origin = index === 0 ? anchor : geometry[index - 1];
                const point = geometry[index];
                const labelRight = point.x <= 530;
                const labelX = point.x + (labelRight ? 18 : -18);
                const regimeClass = link.regime === "STABLE" ? "stable" : link.regime === "SHIFT CHECK" ? "shift" : "learning";
                return (
                  <g className={`pendulum-link ${regimeClass}`} key={link.node.id}>
                    <line className="pendulum-equilibrium" x1={origin.x} y1={origin.y} x2={origin.x} y2={origin.y + rodLength} />
                    <path className="pendulum-envelope" d={`M ${origin.x - Math.sin(maxAngle * Math.PI / 180) * rodLength} ${origin.y + Math.cos(maxAngle * Math.PI / 180) * rodLength} Q ${origin.x} ${origin.y + rodLength * 1.16} ${origin.x + Math.sin(maxAngle * Math.PI / 180) * rodLength} ${origin.y + Math.cos(maxAngle * Math.PI / 180) * rodLength}`} />
                    <line className="pendulum-rod" x1={origin.x} y1={origin.y} x2={point.x} y2={point.y} stroke={link.node.color} />
                    <circle className="pendulum-joint" cx={origin.x} cy={origin.y} r="3.5" />
                    <circle className="pendulum-bob-halo" cx={point.x} cy={point.y} r="13" stroke={link.node.color} />
                    <circle className="pendulum-bob" cx={point.x} cy={point.y} r="7" fill={link.node.color} />
                    <text className="pendulum-link-name" x={labelX} y={point.y - 5} textAnchor={labelRight ? "start" : "end"}>{`H${link.hop} · ${link.node.id}`}</text>
                    <text className="pendulum-link-value" x={labelX} y={point.y + 10} textAnchor={labelRight ? "start" : "end"}>{link.regime === "NO DATA" ? "—" : formatNanoseconds(link.residual, true)}</text>
                  </g>
                );
              })}
              <text className="pendulum-axis-note" x={anchor.x} y="486" textAnchor="middle">ANGLE = SOFT-CLAMPED PREVIOUS-HOP RESIDUAL · ZERO = LEARNED EQUILIBRIUM</text>
            </svg>
          </div>
          <aside className="pendulum-summary" aria-label="Pendulum summary">
            <div><span>Motion</span><strong>{paused ? "FROZEN" : connection === "live" ? "LIVE" : connection === "simulation" ? "MODELED" : connection.toUpperCase()}</strong><small>{paused ? "Raw capture paused" : "One update per PHC sample"}</small></div>
            <div><span>Angular scale</span><strong>±{formatNanoseconds(motionScale)}</strong><small>Adaptive P95 swing envelope</small></div>
            <div><span>Largest residual</span><strong>{largest ? formatNanoseconds(Math.abs(largest.residual)) : "—"}</strong><small>{largest ? `${largest.node.id} · H${largest.hop}` : "Waiting for hop data"}</small></div>
            <div><span>Equilibria</span><strong>{stableLinks}/{links.length}</strong><small>{zeroEvents} zero event{zeroEvents === 1 ? "" : "s"} in view</small></div>
          </aside>
        </div>
        <div className="pendulum-provenance"><Info size={14} /><span><strong>Interpretation:</strong> this is not a simulated gravity model. Each rod is a measured previous-hop PHC delta after subtracting a robust equilibrium. Auto-zero accepts a regime change only after five coherent samples beyond max(80 ns, 8 × MAD).</span></div>
      </section>

      <section className="instrument-panel pendulum-ledger">
        <div className="panel-heading"><div><span className="section-kicker">EQUILIBRIUM LEDGER</span><h2>Per-hop swing decomposition</h2></div><span className="panel-meta">positive swings right · negative swings left</span></div>
        <div className="data-table pendulum-table">
          <div className="table-header"><span>Hop / stage</span><span>Current hop Δ</span><span>Equilibrium</span><span>Swing residual</span><span>P95 envelope</span><span>Regime</span></div>
          {links.map((link) => <div className="table-row" key={link.node.id}><span><i style={{ background: link.node.color }} />H{link.hop} · {link.node.label}<small>{link.node.phc}</small></span><strong>{link.regime === "NO DATA" ? "—" : formatNanoseconds(link.current, true)}</strong><span>{link.regime === "NO DATA" ? "—" : formatNanoseconds(link.equilibrium, true)}</span><span>{link.regime === "NO DATA" ? "—" : formatNanoseconds(link.residual, true)}</span><span>{link.samples ? formatNanoseconds(link.envelope) : "—"}</span><em className={link.regime === "STABLE" ? "state-good" : link.regime === "NO DATA" ? "state-off" : "state-warn"}>{link.regime}</em></div>)}
        </div>
      </section>
    </div>
  );
}

type MatrixMode = "covariance" | "correlation";

type PhaseChangeRow = {
  t: number;
  dt: number;
  values: number[];
};

type MatrixSnapshot = {
  t: number;
  covariance: number[][];
  correlation: number[][];
  matrix: number[][];
  eigenvalues: number[];
};

function phaseChangeRows(history: HistoryPoint[], nodeIds: string[]): PhaseChangeRow[] {
  const synchronized = history
    .filter((point) => nodeIds.every((id) => Number.isFinite(point.hopValues?.[id])))
    .map((point) => ({ t: point.t, values: nodeIds.map((id) => point.hopValues![id]) }))
    .sort((left, right) => left.t - right.t);
  const changes: PhaseChangeRow[] = [];
  for (let index = 1; index < synchronized.length; index += 1) {
    const previous = synchronized[index - 1];
    const current = synchronized[index];
    const dt = current.t - previous.t;
    if (dt < 0.05 || dt > 5) continue;
    changes.push({ t: current.t, dt, values: current.values.map((value, column) => (value - previous.values[column]) / dt) });
  }
  return changes;
}

function covarianceMatrix(rows: PhaseChangeRow[], width: number) {
  const matrix = Array.from({ length: width }, () => Array(width).fill(0) as number[]);
  if (rows.length < 2) return matrix;
  const means = Array.from({ length: width }, (_, column) => rows.reduce((sum, row) => sum + row.values[column], 0) / rows.length);
  for (let row = 0; row < width; row += 1) {
    for (let column = row; column < width; column += 1) {
      const value = rows.reduce((sum, point) => sum + (point.values[row] - means[row]) * (point.values[column] - means[column]), 0) / (rows.length - 1);
      matrix[row][column] = value;
      matrix[column][row] = value;
    }
  }
  return matrix;
}

function correlationMatrix(covariance: number[][]) {
  return covariance.map((row, rowIndex) => row.map((value, columnIndex) => {
    const scale = Math.sqrt(Math.max(0, covariance[rowIndex][rowIndex] * covariance[columnIndex][columnIndex]));
    return scale ? Math.max(-1, Math.min(1, value / scale)) : 0;
  }));
}

function eigenDecomposeSymmetric(input: number[][]) {
  const size = input.length;
  if (!size) return { values: [] as number[], vectors: [] as number[][] };
  const matrix = input.map((row) => [...row]);
  const vectors: number[][] = Array.from({ length: size }, (_, row) => Array.from({ length: size }, (_, column) => row === column ? 1 : 0));

  for (let iteration = 0; iteration < size * size * 24; iteration += 1) {
    let pivotRow = 0;
    let pivotColumn = 1;
    let largest = 0;
    for (let row = 0; row < size; row += 1) {
      for (let column = row + 1; column < size; column += 1) {
        const magnitude = Math.abs(matrix[row][column]);
        if (magnitude > largest) {
          largest = magnitude;
          pivotRow = row;
          pivotColumn = column;
        }
      }
    }
    const scale = Math.max(1, ...matrix.map((row, index) => Math.abs(row[index])));
    if (largest <= scale * 1e-12) break;

    const app = matrix[pivotRow][pivotRow];
    const aqq = matrix[pivotColumn][pivotColumn];
    const apq = matrix[pivotRow][pivotColumn];
    const tau = (aqq - app) / (2 * apq);
    const tangent = (tau >= 0 ? 1 : -1) / (Math.abs(tau) + Math.sqrt(1 + tau * tau));
    const cosine = 1 / Math.sqrt(1 + tangent * tangent);
    const sine = tangent * cosine;

    matrix[pivotRow][pivotRow] = app - tangent * apq;
    matrix[pivotColumn][pivotColumn] = aqq + tangent * apq;
    matrix[pivotRow][pivotColumn] = 0;
    matrix[pivotColumn][pivotRow] = 0;
    for (let index = 0; index < size; index += 1) {
      if (index !== pivotRow && index !== pivotColumn) {
        const aip = matrix[index][pivotRow];
        const aiq = matrix[index][pivotColumn];
        matrix[index][pivotRow] = cosine * aip - sine * aiq;
        matrix[pivotRow][index] = matrix[index][pivotRow];
        matrix[index][pivotColumn] = sine * aip + cosine * aiq;
        matrix[pivotColumn][index] = matrix[index][pivotColumn];
      }
      const vip = vectors[index][pivotRow];
      const viq = vectors[index][pivotColumn];
      vectors[index][pivotRow] = cosine * vip - sine * viq;
      vectors[index][pivotColumn] = sine * vip + cosine * viq;
    }
  }

  const pairs = matrix.map((row, index) => ({ value: Math.max(0, row[index]), vector: vectors.map((vectorRow) => vectorRow[index]) }))
    .sort((left, right) => right.value - left.value);
  return { values: pairs.map((pair) => pair.value), vectors: pairs.map((pair) => pair.vector) };
}

function rollingMatrixSnapshots(rows: PhaseChangeRow[], width: number, rollingSamples: number, mode: MatrixMode) {
  if (rows.length < 4) return [] as MatrixSnapshot[];
  const minimum = Math.min(rows.length, Math.max(4, rollingSamples));
  const available = rows.length - minimum + 1;
  const step = Math.max(1, Math.ceil(available / 52));
  const snapshots: MatrixSnapshot[] = [];
  const append = (end: number) => {
    const covariance = covarianceMatrix(rows.slice(Math.max(0, end - rollingSamples), end), width);
    const correlation = correlationMatrix(covariance);
    const matrix = mode === "covariance" ? covariance : correlation;
    snapshots.push({ t: rows[end - 1].t, covariance, correlation, matrix, eigenvalues: eigenDecomposeSymmetric(matrix).values });
  };
  for (let end = minimum; end <= rows.length; end += step) append(end);
  if (snapshots.at(-1)?.t !== rows.at(-1)?.t) append(rows.length);
  return snapshots;
}

function compactMatrixValue(value: number, mode: MatrixMode) {
  if (mode === "correlation") return (Math.abs(value) < 0.005 ? 0 : value).toFixed(2);
  const magnitude = Math.abs(value);
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  if (magnitude >= 1_000_000) return `${sign}${(magnitude / 1_000_000).toFixed(2)}M`;
  if (magnitude >= 1_000) return `${sign}${(magnitude / 1_000).toFixed(1)}k`;
  if (magnitude >= 100) return `${sign}${magnitude.toFixed(0)}`;
  return `${sign}${magnitude.toFixed(1)}`;
}

function covarianceColor(value: number, scale: number) {
  const intensity = Math.min(0.72, 0.06 + Math.sqrt(Math.abs(value) / Math.max(scale, 1e-12)) * 0.58);
  return value >= 0 ? `rgba(97, 220, 227, ${intensity})` : `rgba(241, 136, 114, ${intensity})`;
}

function EigenTrendChart({ snapshots }: { snapshots: MatrixSnapshot[] }) {
  const width = 760;
  const height = 255;
  const padding = { left: 42, right: 18, top: 18, bottom: 30 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const shares = snapshots.map((snapshot) => {
    const total = snapshot.eigenvalues.reduce((sum, value) => sum + value, 0);
    return snapshot.eigenvalues.slice(0, 3).map((value) => total ? value / total : 0);
  });
  const colors = ["#61dce3", "#c4a0ef", "#f3c36f"];
  const pathFor = (mode: number) => shares.map((values, index) => {
    const x = padding.left + (snapshots.length < 2 ? plotWidth : index / (snapshots.length - 1) * plotWidth);
    const y = padding.top + (1 - (values[mode] ?? 0)) * plotHeight;
    return `${index ? "L" : "M"} ${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(" ");
  const latest = shares.at(-1) ?? [0, 0, 0];

  return (
    <div className="eigen-trend-wrap">
      <div className="eigen-trend-legend">{colors.map((color, index) => <span key={color}><i style={{ background: color }} />λ{index + 1}<strong>{((latest[index] ?? 0) * 100).toFixed(1)}%</strong></span>)}</div>
      <svg className="eigen-trend-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Rolling percentage of covariance carried by the first three eigenmodes">
        <title>Rolling eigenmode energy</title>
        <desc>The first three eigenvalues are divided by the matrix trace at each rolling window.</desc>
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + (1 - tick) * plotHeight;
          return <g key={tick}><line className="covariance-gridline" x1={padding.left} y1={y} x2={width - padding.right} y2={y} /><text className="covariance-axis-label" x={padding.left - 8} y={y + 3} textAnchor="end">{Math.round(tick * 100)}%</text></g>;
        })}
        {colors.map((color, index) => <path key={color} className="eigen-trend-line" d={pathFor(index)} stroke={color} />)}
        <text className="covariance-axis-label" x={padding.left} y={height - 8}>window start</text>
        <text className="covariance-axis-label" x={width - padding.right} y={height - 8} textAnchor="end">now</text>
      </svg>
    </div>
  );
}

function CovarianceLab({
  history,
  nodes,
  range,
  setRange,
  paused,
  connection,
}: {
  history: HistoryPoint[];
  nodes: ClockNode[];
  range: string;
  setRange: (value: string) => void;
  paused: boolean;
  connection: ConnectionMode;
}) {
  const hopNodes = nodes.slice(1);
  const nodeIds = useMemo(() => hopNodes.map((node) => node.id), [hopNodes]);
  const changes = useMemo(() => phaseChangeRows(history, nodeIds), [history, nodeIds]);
  const [matrixMode, setMatrixMode] = useState<MatrixMode>("covariance");
  const [rollingSamples, setRollingSamples] = useState(24);
  const [selectedPair, setSelectedPair] = useState<[number, number]>([0, 1]);
  const activeRows = changes.slice(-rollingSamples);
  const covariance = useMemo(() => covarianceMatrix(activeRows, hopNodes.length), [activeRows, hopNodes.length]);
  const correlation = useMemo(() => correlationMatrix(covariance), [covariance]);
  const matrix = matrixMode === "covariance" ? covariance : correlation;
  const eigen = useMemo(() => eigenDecomposeSymmetric(matrix), [matrix]);
  const snapshots = useMemo(() => rollingMatrixSnapshots(changes, hopNodes.length, rollingSamples, matrixMode), [changes, hopNodes.length, matrixMode, rollingSamples]);
  const matrixScale = matrixMode === "correlation" ? 1 : Math.max(1, ...matrix.flat().map(Math.abs));
  const timelineScale = matrixMode === "correlation" ? 1 : Math.max(1, ...snapshots.flatMap((snapshot) => snapshot.matrix.flat().map(Math.abs)));
  const trace = eigen.values.reduce((sum, value) => sum + value, 0);
  const eigenShares = eigen.values.map((value) => trace ? value / trace : 0);
  const effectiveRank = Math.exp(-eigenShares.filter((share) => share > 0).reduce((sum, share) => sum + share * Math.log(share), 0));
  const dominantVectorRaw = eigen.vectors[0] ?? Array(hopNodes.length).fill(0);
  const dominantPivot = dominantVectorRaw.reduce((best, value, index) => Math.abs(value) > Math.abs(dominantVectorRaw[best] ?? 0) ? index : best, 0);
  const dominantSign = (dominantVectorRaw[dominantPivot] ?? 0) < 0 ? -1 : 1;
  const dominantVector = dominantVectorRaw.map((value) => value * dominantSign);
  const hasPositiveLoading = dominantVector.some((value) => value > 0.15);
  const hasNegativeLoading = dominantVector.some((value) => value < -0.15);
  const dominantModeLabel = hasPositiveLoading && hasNegativeLoading ? "Differential cascade mode" : "Common-direction mode";
  const pairs = hopNodes.flatMap((_, row) => hopNodes.map((__, column) => row < column ? [row, column] as [number, number] : null).filter((pair): pair is [number, number] => pair !== null));
  const selectedRow = Math.min(selectedPair[0], Math.max(0, hopNodes.length - 1));
  const selectedColumn = Math.min(selectedPair[1], Math.max(0, hopNodes.length - 1));
  const selectedCovariance = covariance[selectedRow]?.[selectedColumn] ?? 0;
  const selectedCorrelation = correlation[selectedRow]?.[selectedColumn] ?? 0;
  const latestChange = changes.at(-1)?.values ?? [];
  const strongestPair = pairs.reduce<[number, number] | null>((best, pair) => !best || Math.abs(correlation[pair[0]]?.[pair[1]] ?? 0) > Math.abs(correlation[best[0]]?.[best[1]] ?? 0) ? pair : best, null);
  const strongestCorrelation = strongestPair ? correlation[strongestPair[0]][strongestPair[1]] : 0;
  const dataState = paused ? "FROZEN" : connection === "live" ? "LIVE" : connection === "simulation" ? "MODELED" : connection.toUpperCase();

  return (
    <div className="covariance-layout">
      <section className="instrument-panel covariance-matrix-panel">
        <div className="panel-heading covariance-heading">
          <div><span className="section-kicker">SYNCHRONIZED PHASE-CHANGE RATE</span><h2>Cross-hop covariance matrix</h2></div>
          <span className={`quality-badge ${connection === "live" ? "" : "holdover"}`}>{dataState}</span>
        </div>
        <div className="covariance-controls" aria-label="Covariance analysis controls">
          <div><span>History</span><div className="segmented-control">{["30 s", "2 min", "15 min"].map((item) => <button className={range === item ? "active" : ""} type="button" key={item} onClick={() => setRange(item)}>{item}</button>)}</div></div>
          <div><span>Matrix</span><div className="segmented-control"><button className={matrixMode === "covariance" ? "active" : ""} type="button" onClick={() => setMatrixMode("covariance")}>Covariance</button><button className={matrixMode === "correlation" ? "active" : ""} type="button" onClick={() => setMatrixMode("correlation")}>Correlation</button></div></div>
          <div><span>Rolling window</span><div className="segmented-control">{[12, 24, 48].map((item) => <button className={rollingSamples === item ? "active" : ""} type="button" key={item} onClick={() => setRollingSamples(item)}>{item}</button>)}</div></div>
        </div>
        <div className="covariance-matrix-wrap">
          <div className="covariance-matrix-grid" style={{ gridTemplateColumns: `62px repeat(${Math.max(1, hopNodes.length)}, minmax(54px, 1fr))` }} role="grid" aria-label={`${matrixMode} matrix of synchronized previous-hop phase-change rates`}>
            <span className="matrix-corner">Δ/ns·s⁻¹</span>
            {hopNodes.map((node, index) => <span className="matrix-column-label" key={node.id}>H{index + 1}</span>)}
            {hopNodes.flatMap((rowNode, row) => [
              <span className="matrix-row-label" key={`${rowNode.id}-label`}>H{row + 1}<small>{rowNode.id}</small></span>,
              ...hopNodes.map((columnNode, column) => {
                const value = matrix[row]?.[column] ?? 0;
                const selected = (row === selectedRow && column === selectedColumn) || (row === selectedColumn && column === selectedRow);
                return <button key={`${rowNode.id}-${columnNode.id}`} type="button" className={`matrix-cell ${selected ? "selected" : ""} ${row === column ? "diagonal" : ""}`} style={{ background: covarianceColor(value, matrixScale) }} aria-label={`H${row + 1} and H${column + 1}: ${compactMatrixValue(value, matrixMode)}${matrixMode === "covariance" ? " squared nanoseconds per squared second" : " correlation"}`} onClick={() => row !== column && setSelectedPair([row, column])}><strong>{compactMatrixValue(value, matrixMode)}</strong><small>{row === column ? "variance" : row < column ? "" : "mirror"}</small></button>;
              }),
            ])}
          </div>
          <div className="covariance-scale"><span>negative</span><i /><span>0</span><b /><span>positive</span></div>
        </div>
        <div className="selected-covariance">
          <div><span>Selected relationship</span><strong>H{selectedRow + 1} · {hopNodes[selectedRow]?.id ?? "—"} ↔ H{selectedColumn + 1} · {hopNodes[selectedColumn]?.id ?? "—"}</strong></div>
          <div><span>Covariance</span><strong>{compactMatrixValue(selectedCovariance, "covariance")} <small>(ns/s)²</small></strong></div>
          <div><span>Correlation</span><strong>{selectedCorrelation >= 0 ? "+" : ""}{selectedCorrelation.toFixed(3)}</strong></div>
          <div><span>Latest Δ rate</span><strong>{compactMatrixValue(latestChange[selectedRow] ?? 0, "covariance")} / {compactMatrixValue(latestChange[selectedColumn] ?? 0, "covariance")} <small>ns/s</small></strong></div>
        </div>
        <div className="pendulum-provenance covariance-provenance"><Info size={14} /><span><strong>Method:</strong> covariance is calculated from synchronized first differences of the raw previous-hop PHC deltas, divided by the measured sample interval. Pendulum zeroing does not enter this path. Cell color uses a signed square-root scale; the printed values are exact.</span></div>
      </section>

      <section className="instrument-panel eigen-panel">
        <div className="panel-heading"><div><span className="section-kicker">ORTHOGONAL MODES</span><h2>Eigen spectrum</h2></div><span className="panel-meta">displayed matrix</span></div>
        <div className="eigen-summary">
          <div><span>Dominant share</span><strong>{((eigenShares[0] ?? 0) * 100).toFixed(1)}%</strong><small>{dominantModeLabel}</small></div>
          <div><span>Effective rank</span><strong>{Number.isFinite(effectiveRank) ? effectiveRank.toFixed(2) : "—"}</strong><small>of {hopNodes.length} possible modes</small></div>
          <div><span>Phase changes</span><strong>{changes.length}</strong><small>{activeRows.length} in current matrix</small></div>
        </div>
        <div className="eigen-spectrum" aria-label="Eigenvalues and explained matrix trace">
          {eigen.values.map((value, index) => <div className="eigenvalue-row" key={index}><span>λ{index + 1}</span><div><i style={{ width: `${Math.max(1.5, (eigenShares[index] ?? 0) * 100)}%` }} /></div><strong>{compactMatrixValue(value, matrixMode)}</strong><small>{((eigenShares[index] ?? 0) * 100).toFixed(1)}%</small></div>)}
        </div>
        <div className="eigen-loadings">
          <div className="subpanel-heading"><span>λ1 eigenvector</span><strong>signed hop loading</strong></div>
          {hopNodes.map((node, index) => {
            const loading = dominantVector[index] ?? 0;
            return <div className="loading-row" key={node.id}><span>H{index + 1}</span><div><i className={loading < 0 ? "negative" : "positive"} style={loading < 0 ? { right: "50%", width: `${Math.abs(loading) * 50}%` } : { left: "50%", width: `${Math.abs(loading) * 50}%` }} /></div><strong>{loading >= 0 ? "+" : ""}{loading.toFixed(3)}</strong></div>;
          })}
        </div>
      </section>

      <section className="instrument-panel covariance-timeline-panel">
        <div className="panel-heading"><div><span className="section-kicker">RELATIONSHIPS THROUGH TIME</span><h2>Rolling pair matrix</h2></div><span className="panel-meta">{snapshots.length} windows · {rollingSamples} changes each</span></div>
        <div className="pair-timeline" role="img" aria-label={`Rolling ${matrixMode} for every unique pair of pendulum links`}>
          {pairs.map(([row, column]) => {
            const selected = (row === selectedRow && column === selectedColumn) || (row === selectedColumn && column === selectedRow);
            return <div className={`pair-timeline-row ${selected ? "selected" : ""}`} key={`${row}-${column}`} style={{ gridTemplateColumns: `72px repeat(${Math.max(1, snapshots.length)}, minmax(4px, 1fr))` }}><span>H{row + 1}↔H{column + 1}</span>{snapshots.map((snapshot, index) => <i key={index} style={{ background: covarianceColor(snapshot.matrix[row]?.[column] ?? 0, timelineScale) }} />)}</div>;
          })}
        </div>
        <div className="timeline-axis"><span>window start</span><strong>{strongestPair ? `Strongest now H${strongestPair[0] + 1}↔H${strongestPair[1] + 1} · ρ ${strongestCorrelation >= 0 ? "+" : ""}${strongestCorrelation.toFixed(2)}` : "Awaiting synchronized changes"}</strong><span>now</span></div>
      </section>

      <section className="instrument-panel eigen-trend-panel">
        <div className="panel-heading"><div><span className="section-kicker">MODE ENERGY THROUGH TIME</span><h2>Rolling eigenvalue share</h2></div><span className="panel-meta">λ / trace</span></div>
        {snapshots.length ? <EigenTrendChart snapshots={snapshots} /> : <div className="covariance-empty">Waiting for four synchronized phase changes…</div>}
      </section>
    </div>
  );
}

type StateScale = "sigma" | "physical";
type CrossingDirection = "rising" | "falling" | "both";

type ModalPoint = {
  t: number;
  scores: number[];
  display: number[];
};

type PoincareCrossing = {
  t: number;
  direction: "rising" | "falling";
  display: number[];
};

function orientedEigenvectors(vectors: number[][]) {
  return vectors.map((vector) => {
    const pivot = vector.reduce((best, value, index) => Math.abs(value) > Math.abs(vector[best] ?? 0) ? index : best, 0);
    const sign = (vector[pivot] ?? 0) < 0 ? -1 : 1;
    return vector.map((value) => value * sign);
  });
}

function projectPhaseChanges(rows: PhaseChangeRow[], vectors: number[][], eigenvalues: number[], scale: StateScale) {
  if (!rows.length || !vectors.length) return [] as ModalPoint[];
  const width = rows[0].values.length;
  const means = Array.from({ length: width }, (_, column) => rows.reduce((sum, row) => sum + row.values[column], 0) / rows.length);
  return rows.map((row) => {
    const centered = row.values.map((value, index) => value - means[index]);
    const scores = vectors.map((vector) => vector.reduce((sum, loading, index) => sum + loading * centered[index], 0));
    const display = scores.map((value, index) => scale === "sigma" ? value / Math.sqrt(Math.max(eigenvalues[index] ?? 0, 1e-9)) : value);
    return { t: row.t, scores, display };
  });
}

function poincareCrossings(points: ModalPoint[], sectionAxis: number, direction: CrossingDirection) {
  const crossings: PoincareCrossing[] = [];
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1];
    const current = points[index];
    const before = previous.scores[sectionAxis] ?? 0;
    const after = current.scores[sectionAxis] ?? 0;
    const rising = before <= 0 && after > 0;
    const falling = before >= 0 && after < 0;
    if ((!rising && !falling) || (direction !== "both" && direction !== (rising ? "rising" : "falling"))) continue;
    const fraction = Math.max(0, Math.min(1, -before / (after - before)));
    crossings.push({
      t: previous.t + (current.t - previous.t) * fraction,
      direction: rising ? "rising" : "falling",
      display: previous.display.map((value, column) => value + ((current.display[column] ?? value) - value) * fraction),
    });
  }
  return crossings;
}

function symmetricExtent(values: number[], fallback = 1) {
  return Math.max(fallback, ...values.filter(Number.isFinite).map(Math.abs)) * 1.08;
}

function modalValue(value: number, scale: StateScale) {
  if (scale === "sigma") return `${value >= 0 ? "+" : ""}${value.toFixed(2)} σ`;
  return `${formatNanoseconds(value, true)}/s`;
}

function StatePlaneChart({ points, eigenvalues, scale }: { points: ModalPoint[]; eigenvalues: number[]; scale: StateScale }) {
  const width = 820;
  const height = 430;
  const padding = { left: 58, right: 24, top: 24, bottom: 44 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const xValues = points.map((point) => point.display[0] ?? 0);
  const yValues = points.map((point) => point.display[1] ?? 0);
  const sigmaX = scale === "sigma" ? 1 : Math.sqrt(Math.max(eigenvalues[0] ?? 0, 1e-9));
  const sigmaY = scale === "sigma" ? 1 : Math.sqrt(Math.max(eigenvalues[1] ?? 0, 1e-9));
  const xLimit = symmetricExtent([...xValues, sigmaX * 2], scale === "sigma" ? 2.5 : 1);
  const yLimit = symmetricExtent([...yValues, sigmaY * 2], scale === "sigma" ? 2.5 : 1);
  const x = (value: number) => padding.left + ((value + xLimit) / (xLimit * 2)) * plotWidth;
  const y = (value: number) => padding.top + (1 - (value + yLimit) / (yLimit * 2)) * plotHeight;
  const path = points.map((point, index) => `${index ? "L" : "M"} ${x(point.display[0] ?? 0).toFixed(2)} ${y(point.display[1] ?? 0).toFixed(2)}`).join(" ");
  const latest = points.at(-1);
  const unit = scale === "sigma" ? "σ" : "ns/s";

  return (
    <div className="state-plane-wrap">
      <svg className="state-plane-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Principal state plane of PC1 versus PC2 with ${points.length} synchronized phase-change states`}>
        <title>Principal state-space trajectory</title>
        <desc>The six hop-change rates are centered and projected onto the first two covariance eigenvectors. Older states are faint and the newest state is ringed.</desc>
        {[-1, 0, 1].map((fraction) => <g key={`x-${fraction}`}><line className={fraction === 0 ? "state-zero-axis" : "state-gridline"} x1={x(fraction * xLimit)} y1={padding.top} x2={x(fraction * xLimit)} y2={height - padding.bottom} /><text className="state-axis-label" x={x(fraction * xLimit)} y={height - 18} textAnchor="middle">{fraction === 0 ? "0" : `${fraction > 0 ? "+" : "−"}${xLimit.toFixed(scale === "sigma" ? 1 : 0)}`}</text></g>)}
        {[-1, 0, 1].map((fraction) => <g key={`y-${fraction}`}><line className={fraction === 0 ? "state-zero-axis" : "state-gridline"} x1={padding.left} y1={y(fraction * yLimit)} x2={width - padding.right} y2={y(fraction * yLimit)} /><text className="state-axis-label" x={padding.left - 10} y={y(fraction * yLimit) + 3} textAnchor="end">{fraction === 0 ? "0" : `${fraction > 0 ? "+" : "−"}${yLimit.toFixed(scale === "sigma" ? 1 : 0)}`}</text></g>)}
        {[2, 1].map((sigma) => <ellipse key={sigma} className={`state-sigma-ellipse sigma-${sigma}`} cx={x(0)} cy={y(0)} rx={Math.abs(x(sigmaX * sigma) - x(0))} ry={Math.abs(y(sigmaY * sigma) - y(0))} />)}
        <path className="state-trajectory" d={path} />
        {points.map((point, index) => <circle key={`${point.t}-${index}`} className="state-point" cx={x(point.display[0] ?? 0)} cy={y(point.display[1] ?? 0)} r={index === points.length - 1 ? 4.5 : 2.3} style={{ opacity: 0.16 + index / Math.max(1, points.length - 1) * 0.68 }} />)}
        {latest && <circle className="state-current-ring" cx={x(latest.display[0] ?? 0)} cy={y(latest.display[1] ?? 0)} r="10" />}
        <text className="state-axis-title" x={padding.left + plotWidth / 2} y={height - 2} textAnchor="middle">PC1 · {unit}</text>
        <text className="state-axis-title" transform={`translate(12 ${padding.top + plotHeight / 2}) rotate(-90)`} textAnchor="middle">PC2 · {unit}</text>
      </svg>
      <div className="state-plane-note"><span><i className="ellipse-two" />2σ covariance ellipse</span><span><i className="ellipse-one" />1σ covariance ellipse</span><strong>latest {latest ? `${modalValue(latest.display[0] ?? 0, scale)} · ${modalValue(latest.display[1] ?? 0, scale)}` : "—"}</strong></div>
    </div>
  );
}

function PoincareChart({ crossings, axes, scale }: { crossings: PoincareCrossing[]; axes: [number, number]; scale: StateScale }) {
  const width = 600;
  const height = 430;
  const padding = { left: 56, right: 22, top: 24, bottom: 44 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const xLimit = symmetricExtent(crossings.map((point) => point.display[axes[0]] ?? 0), scale === "sigma" ? 2 : 1);
  const yLimit = symmetricExtent(crossings.map((point) => point.display[axes[1]] ?? 0), scale === "sigma" ? 2 : 1);
  const x = (value: number) => padding.left + ((value + xLimit) / (xLimit * 2)) * plotWidth;
  const y = (value: number) => padding.top + (1 - (value + yLimit) / (yLimit * 2)) * plotHeight;
  const unit = scale === "sigma" ? "σ" : "ns/s";

  return (
    <svg className="poincare-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Empirical Poincaré map with ${crossings.length} zero-plane crossings`}>
      <title>Empirical Poincaré section</title>
      <desc>Points are linearly interpolated crossings of the selected principal-component zero plane. Cyan circles rise through the plane and coral diamonds fall through it.</desc>
      <line className="state-zero-axis" x1={x(0)} y1={padding.top} x2={x(0)} y2={height - padding.bottom} />
      <line className="state-zero-axis" x1={padding.left} y1={y(0)} x2={width - padding.right} y2={y(0)} />
      <rect className="poincare-frame" x={padding.left} y={padding.top} width={plotWidth} height={plotHeight} />
      {crossings.map((point, index) => {
        const pointX = x(point.display[axes[0]] ?? 0);
        const pointY = y(point.display[axes[1]] ?? 0);
        const opacity = 0.32 + index / Math.max(1, crossings.length - 1) * 0.68;
        return point.direction === "rising" ? <circle key={`${point.t}-${index}`} className="poincare-point rising" cx={pointX} cy={pointY} r="4.5" style={{ opacity }} /> : <path key={`${point.t}-${index}`} className="poincare-point falling" d={`M ${pointX} ${pointY - 5} L ${pointX + 5} ${pointY} L ${pointX} ${pointY + 5} L ${pointX - 5} ${pointY} Z`} style={{ opacity }} />;
      })}
      {!crossings.length && <text className="poincare-empty" x={padding.left + plotWidth / 2} y={padding.top + plotHeight / 2} textAnchor="middle">No qualifying crossings in this window</text>}
      <text className="state-axis-title" x={padding.left + plotWidth / 2} y={height - 3} textAnchor="middle">PC{axes[0] + 1} · {unit}</text>
      <text className="state-axis-title" transform={`translate(12 ${padding.top + plotHeight / 2}) rotate(-90)`} textAnchor="middle">PC{axes[1] + 1} · {unit}</text>
    </svg>
  );
}

function ModalTrendChart({ points, scale }: { points: ModalPoint[]; scale: StateScale }) {
  const width = 900;
  const height = 300;
  const padding = { left: 54, right: 18, top: 20, bottom: 35 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const limit = symmetricExtent(points.flatMap((point) => point.display.slice(0, 3)), scale === "sigma" ? 2 : 1);
  const x = (index: number) => padding.left + (points.length < 2 ? plotWidth : index / (points.length - 1) * plotWidth);
  const y = (value: number) => padding.top + (1 - (value + limit) / (limit * 2)) * plotHeight;
  const colors = ["#61dce3", "#c4a0ef", "#f3c36f"];
  const pathFor = (mode: number) => points.map((point, index) => `${index ? "L" : "M"} ${x(index).toFixed(2)} ${y(point.display[mode] ?? 0).toFixed(2)}`).join(" ");
  const latest = points.at(-1);

  return (
    <div className="modal-trend-wrap">
      <div className="modal-trend-legend">{colors.map((color, index) => <span key={color}><i style={{ background: color }} />PC{index + 1}<strong>{latest ? modalValue(latest.display[index] ?? 0, scale) : "—"}</strong></span>)}</div>
      <svg className="modal-trend-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Time trend of the first three principal state coordinates">
        <title>Principal state coordinates through time</title>
        <desc>The centered six-hop phase-change state projected onto the current first three covariance eigenvectors.</desc>
        {[-1, 0, 1].map((fraction) => <g key={fraction}><line className={fraction === 0 ? "state-zero-axis" : "state-gridline"} x1={padding.left} y1={y(fraction * limit)} x2={width - padding.right} y2={y(fraction * limit)} /><text className="state-axis-label" x={padding.left - 9} y={y(fraction * limit) + 3} textAnchor="end">{fraction === 0 ? "0" : `${fraction > 0 ? "+" : "−"}${limit.toFixed(scale === "sigma" ? 1 : 0)}`}</text></g>)}
        {colors.map((color, index) => <path className="modal-trend-line" key={color} d={pathFor(index)} stroke={color} />)}
        <text className="state-axis-label" x={padding.left} y={height - 8}>basis window start</text>
        <text className="state-axis-label" x={width - padding.right} y={height - 8} textAnchor="end">now</text>
      </svg>
    </div>
  );
}

function StateSpaceAtlas({
  history,
  nodes,
  range,
  setRange,
  paused,
  connection,
}: {
  history: HistoryPoint[];
  nodes: ClockNode[];
  range: string;
  setRange: (value: string) => void;
  paused: boolean;
  connection: ConnectionMode;
}) {
  const hopNodes = nodes.slice(1);
  const nodeIds = useMemo(() => hopNodes.map((node) => node.id), [hopNodes]);
  const changes = useMemo(() => phaseChangeRows(history, nodeIds), [history, nodeIds]);
  const [basisSamples, setBasisSamples] = useState(48);
  const [stateScale, setStateScale] = useState<StateScale>("sigma");
  const [sectionAxis, setSectionAxis] = useState(2);
  const [crossingDirection, setCrossingDirection] = useState<CrossingDirection>("rising");
  const basisRows = changes.slice(-basisSamples);
  const covariance = useMemo(() => covarianceMatrix(basisRows, hopNodes.length), [basisRows, hopNodes.length]);
  const eigen = useMemo(() => eigenDecomposeSymmetric(covariance), [covariance]);
  const vectors = useMemo(() => orientedEigenvectors(eigen.vectors), [eigen.vectors]);
  const modalPoints = useMemo(() => projectPhaseChanges(basisRows, vectors, eigen.values, stateScale), [basisRows, eigen.values, stateScale, vectors]);
  const crossings = useMemo(() => poincareCrossings(modalPoints, sectionAxis, crossingDirection), [crossingDirection, modalPoints, sectionAxis]);
  const sectionAxes = [0, 1, 2].filter((axis) => axis !== sectionAxis).slice(0, 2) as [number, number];
  const eigenTrace = eigen.values.reduce((sum, value) => sum + value, 0);
  const eigenShares = eigen.values.map((value) => eigenTrace ? value / eigenTrace : 0);
  const effectiveRank = Math.exp(-eigenShares.filter((share) => share > 0).reduce((sum, share) => sum + share * Math.log(share), 0));
  const latest = modalPoints.at(-1);
  const stateRadius = latest ? Math.sqrt(latest.scores.reduce((sum, value, index) => sum + value * value / Math.max(eigen.values[index] ?? 0, 1e-9), 0)) : 0;
  const eigenTrendWindow = Math.max(8, Math.min(24, Math.floor(basisSamples / 3)));
  const snapshots = useMemo(() => rollingMatrixSnapshots(changes, hopNodes.length, eigenTrendWindow, "covariance"), [changes, eigenTrendWindow, hopNodes.length]);
  const dataState = paused ? "FROZEN" : connection === "live" ? "LIVE" : connection === "simulation" ? "MODELED" : connection.toUpperCase();

  return (
    <div className="state-space-layout">
      <section className="instrument-panel state-space-toolbar">
        <div className="panel-heading">
          <div><span className="section-kicker">SIX-DIMENSIONAL PHASE-CHANGE STATE</span><h2>Modal coordinate system</h2></div>
          <span className={`quality-badge ${connection === "live" ? "" : "holdover"}`}>{dataState}</span>
        </div>
        <div className="state-space-controls" aria-label="State-space analysis controls">
          <div><span>History</span><div className="segmented-control">{["30 s", "2 min", "15 min"].map((item) => <button className={range === item ? "active" : ""} type="button" key={item} onClick={() => setRange(item)}>{item}</button>)}</div></div>
          <div><span>PCA basis</span><div className="segmented-control">{[24, 48, 96].map((item) => <button className={basisSamples === item ? "active" : ""} type="button" key={item} onClick={() => setBasisSamples(item)}>{item}</button>)}</div></div>
          <div><span>Coordinate scale</span><div className="segmented-control"><button className={stateScale === "sigma" ? "active" : ""} type="button" onClick={() => setStateScale("sigma")}>σ-normalized</button><button className={stateScale === "physical" ? "active" : ""} type="button" onClick={() => setStateScale("physical")}>Physical</button></div></div>
          <div className="state-space-live-readout"><span>Current state</span><strong>{stateRadius.toFixed(2)} σ</strong><small>{basisRows.length} synchronized changes</small></div>
        </div>
        <div className="state-method-strip"><Info size={14} /><span>State vector = centered H1…H6 phase-change rates. The PCA basis comes from the current covariance window; pendulum zeroing is excluded. Eigenvector signs are oriented deterministically to keep the live trajectory visually stable.</span></div>
      </section>

      <section className="instrument-panel state-plane-panel">
        <div className="panel-heading"><div><span className="section-kicker">PRINCIPAL STATE PLANE</span><h2>PC1 × PC2 trajectory</h2></div><span className="panel-meta">older → newer · latest ringed</span></div>
        <StatePlaneChart points={modalPoints} eigenvalues={eigen.values} scale={stateScale} />
      </section>

      <section className="instrument-panel poincare-panel">
        <div className="panel-heading"><div><span className="section-kicker">EMPIRICAL RETURN SECTION</span><h2>Poincaré map</h2></div><span className="panel-meta">{crossings.length} crossings</span></div>
        <div className="poincare-controls" aria-label="Poincaré section controls">
          <div><span>Zero plane</span><div className="segmented-control">{[0, 1, 2].map((axis) => <button type="button" className={sectionAxis === axis ? "active" : ""} key={axis} onClick={() => setSectionAxis(axis)}>PC{axis + 1}</button>)}</div></div>
          <div><span>Crossing</span><div className="segmented-control"><button type="button" className={crossingDirection === "rising" ? "active" : ""} onClick={() => setCrossingDirection("rising")}>Rising</button><button type="button" className={crossingDirection === "falling" ? "active" : ""} onClick={() => setCrossingDirection("falling")}>Falling</button><button type="button" className={crossingDirection === "both" ? "active" : ""} onClick={() => setCrossingDirection("both")}>Both</button></div></div>
        </div>
        <PoincareChart crossings={crossings} axes={sectionAxes} scale={stateScale} />
        <div className="poincare-legend"><span><i className="rising" />rising crossing</span><span><i className="falling" />falling crossing</span><strong>PC{sectionAxis + 1} = 0 · linear crossing interpolation</strong></div>
        <div className="poincare-note"><Info size={13} /><span>This empirical section reveals recurrence and clustering in measured timing dynamics; it is not, by itself, evidence of a periodic orbit or deterministic attractor.</span></div>
      </section>

      <section className="instrument-panel modal-trend-panel">
        <div className="panel-heading"><div><span className="section-kicker">MODAL TIME TREND</span><h2>Principal coordinates through time</h2></div><span className="panel-meta">current PCA basis</span></div>
        <ModalTrendChart points={modalPoints} scale={stateScale} />
      </section>

      <section className="instrument-panel state-eigen-panel">
        <div className="panel-heading"><div><span className="section-kicker">EVOLVING STATE GEOMETRY</span><h2>Eigenvalues through time</h2></div><span className="panel-meta">rank {Number.isFinite(effectiveRank) ? effectiveRank.toFixed(2) : "—"} · {eigenTrendWindow}-change rolling</span></div>
        <div className="state-eigen-spectrum" aria-label="Current covariance eigenvalue spectrum">
          {eigen.values.map((value, index) => <div className="eigenvalue-row" key={index}><span>λ{index + 1}</span><div><i style={{ width: `${Math.max(1.5, (eigenShares[index] ?? 0) * 100)}%` }} /></div><strong>{compactMatrixValue(value, "covariance")}</strong><small>{((eigenShares[index] ?? 0) * 100).toFixed(1)}%</small></div>)}
        </div>
        {snapshots.length ? <EigenTrendChart snapshots={snapshots} /> : <div className="covariance-empty">Waiting for four synchronized phase changes…</div>}
      </section>
    </div>
  );
}

function ResearchLineChart({ points, metric }: { points: StabilityPoint[]; metric: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const bounds = wrap.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = bounds.width * dpr;
    canvas.height = bounds.height * dpr;
    canvas.style.width = `${bounds.width}px`;
    canvas.style.height = `${bounds.height}px`;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.scale(dpr, dpr);
    const width = bounds.width;
    const height = bounds.height;
    const padding = { left: 62, right: 22, top: 22, bottom: 38 };
    context.clearRect(0, 0, width, height);
    if (points.length < 2) {
      context.fillStyle = "#69818a";
      context.font = "11px ui-monospace, monospace";
      context.textAlign = "center";
      context.fillText("Collecting enough phase samples for stability statistics", width / 2, height / 2);
      return;
    }
    const logX = points.map((point) => Math.log10(Math.max(1e-9, point.tau_s)));
    const logY = points.map((point) => Math.log10(Math.max(1e-18, Math.abs(point.value))));
    const xMin = Math.min(...logX);
    const xMax = Math.max(...logX);
    const yMin = Math.min(...logY) - .18;
    const yMax = Math.max(...logY) + .18;
    const x = (value: number) => padding.left + (value - xMin) / Math.max(.1, xMax - xMin) * (width - padding.left - padding.right);
    const y = (value: number) => padding.top + (yMax - value) / Math.max(.1, yMax - yMin) * (height - padding.top - padding.bottom);
    context.font = "9px ui-monospace, monospace";
    context.textBaseline = "middle";
    for (let index = 0; index <= 4; index += 1) {
      const value = yMax - index / 4 * (yMax - yMin);
      const py = y(value);
      context.strokeStyle = "rgba(125,157,166,.12)";
      context.beginPath();
      context.moveTo(padding.left, py);
      context.lineTo(width - padding.right, py);
      context.stroke();
      context.fillStyle = "#62777e";
      context.textAlign = "right";
      const displayed = 10 ** value;
      context.fillText(metric === "ADEV" || metric === "MDEV" || metric === "HDEV" ? displayed.toExponential(1) : formatNanoseconds(displayed), padding.left - 9, py);
    }
    points.forEach((point, index) => {
      const px = x(logX[index]);
      context.strokeStyle = "rgba(125,157,166,.08)";
      context.beginPath();
      context.moveTo(px, padding.top);
      context.lineTo(px, height - padding.bottom);
      context.stroke();
      context.fillStyle = "#62777e";
      context.textAlign = "center";
      context.fillText(`${point.tau_s < 1 ? point.tau_s.toFixed(2) : point.tau_s.toFixed(0)}s`, px, height - 16);
    });
    const gradient = context.createLinearGradient(padding.left, 0, width - padding.right, 0);
    gradient.addColorStop(0, "#61dce3");
    gradient.addColorStop(.55, "#77e2b3");
    gradient.addColorStop(1, "#c4a0ef");
    context.strokeStyle = gradient;
    context.lineWidth = 2;
    context.beginPath();
    points.forEach((_point, index) => {
      const px = x(logX[index]);
      const py = y(logY[index]);
      if (index === 0) context.moveTo(px, py);
      else context.lineTo(px, py);
    });
    context.stroke();
    points.forEach((_point, index) => {
      context.fillStyle = "#0c171c";
      context.strokeStyle = index === points.length - 1 ? "#c4a0ef" : "#77e2b3";
      context.lineWidth = 1.4;
      context.beginPath();
      context.arc(x(logX[index]), y(logY[index]), index === points.length - 1 ? 4 : 3, 0, Math.PI * 2);
      context.fill();
      context.stroke();
    });
  }, [metric, points]);
  useEffect(() => {
    draw();
    const observer = new ResizeObserver(draw);
    if (wrapRef.current) observer.observe(wrapRef.current);
    return () => observer.disconnect();
  }, [draw]);
  return <div className="research-chart" ref={wrapRef}><canvas ref={canvasRef} aria-label={`${metric} stability chart`} /></div>;
}

function RecurrenceCanvas({ matrix }: { matrix: string[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !matrix.length) return;
    const size = 320;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.scale(dpr, dpr);
    context.fillStyle = "#071014";
    context.fillRect(0, 0, size, size);
    const cell = size / matrix.length;
    matrix.forEach((row, y) => [...row].forEach((value, x) => {
      if (value !== "1") return;
      const distance = Math.abs(x - y);
      context.fillStyle = distance < 2 ? "#77e2b3" : distance < 8 ? "#61dce3" : "rgba(196,160,239,.78)";
      context.fillRect(x * cell, y * cell, Math.max(1, cell), Math.max(1, cell));
    }));
  }, [matrix]);
  return <canvas className="recurrence-canvas" ref={canvasRef} width="320" height="320" aria-label="Recurrence matrix" />;
}

function BifurcationDiagram({ analysis }: { analysis: ResearchPayload["bifurcation"] }) {
  const points = analysis.points ?? [];
  const width = 620;
  const height = 270;
  const padding = { left: 58, right: 18, top: 17, bottom: 42 };
  if (analysis.status !== "ready" || !points.length) {
    return <div className="bifurcation-empty"><Orbit size={20} /><span>{analysis.reason ?? `Collecting endpoint phase samples for the gain sweep (${analysis.samples ?? 0}/32)`}</span></div>;
  }
  const xMin = analysis.parameter_min ?? Math.min(...points.map((point) => point.gain_scale));
  const xMax = analysis.parameter_max ?? Math.max(...points.map((point) => point.gain_scale));
  const yLimit = Math.max(1, analysis.display_limit_ns ?? Math.max(...points.map((point) => Math.abs(point.residual_ns))));
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const x = (value: number) => padding.left + (value - xMin) / Math.max(.01, xMax - xMin) * plotWidth;
  const y = (value: number) => padding.top + (yLimit - Math.max(-yLimit, Math.min(yLimit, value))) / (2 * yLimit) * plotHeight;
  const xTicks = [xMin, .5, 1, 1.5, 2, xMax].filter((value, index, values) => value >= xMin && value <= xMax && values.findIndex((item) => Math.abs(item - value) < .01) === index);
  const yTicks = [-yLimit, -yLimit / 2, 0, yLimit / 2, yLimit];
  const transition = analysis.first_transition_gain;
  return (
    <div className="bifurcation-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="bifurcation-title bifurcation-description">
        <title id="bifurcation-title">Offline PI replay bifurcation diagram</title>
        <desc id="bifurcation-description">Settled endpoint phase residual extrema across a sweep from one quarter to two and a half times the configured PI gains. The one-times line is the live controller only when the endpoint uses PI. Cyan points passed replay bounds; coral points did not.</desc>
        <defs><clipPath id="bifurcation-clip"><rect x={padding.left} y={padding.top} width={plotWidth} height={plotHeight} /></clipPath></defs>
        <g className="bifurcation-legend" transform={`translate(${width - 220} 9)`}><circle className="bifurcation-point stable" cx="0" cy="0" r="2.5" /><text x="7" y="2.5">WITHIN REPLAY BOUND</text><circle className="bifurcation-point unstable" cx="116" cy="0" r="2.5" /><text x="123" y="2.5">BOUND EXCEEDED</text></g>
        <rect className="bifurcation-frame" x={padding.left} y={padding.top} width={plotWidth} height={plotHeight} />
        {yTicks.map((value) => <g key={`y-${value}`}><line className={Math.abs(value) < .001 ? "bifurcation-zero" : "bifurcation-grid"} x1={padding.left} x2={width - padding.right} y1={y(value)} y2={y(value)} /><text className="bifurcation-axis-label" x={padding.left - 9} y={y(value) + 3} textAnchor="end">{formatNanoseconds(value, true)}</text></g>)}
        {xTicks.map((value) => <g key={`x-${value}`}><line className="bifurcation-grid" x1={x(value)} x2={x(value)} y1={padding.top} y2={height - padding.bottom} /><text className="bifurcation-axis-label" x={x(value)} y={height - 21} textAnchor="middle">{value.toFixed(value % 1 === 0 ? 1 : 2)}×</text></g>)}
        <line className="bifurcation-current-line" x1={x(analysis.current_gain_scale ?? 1)} x2={x(analysis.current_gain_scale ?? 1)} y1={padding.top} y2={height - padding.bottom} />
        <text className="bifurcation-current-label" x={x(analysis.current_gain_scale ?? 1) + 5} y={padding.top + 11}>{analysis.baseline_is_live ? "LIVE PI" : "PI BASELINE"}</text>
        {transition != null && transition >= xMin && transition <= xMax && <><line className="bifurcation-transition-line" x1={x(transition)} x2={x(transition)} y1={padding.top} y2={height - padding.bottom} /><text className="bifurcation-transition-label" x={x(transition) - 5} y={padding.top + 11} textAnchor="end">BOUND</text></>}
        <g clipPath="url(#bifurcation-clip)">
          {points.map((point, index) => <circle className={`bifurcation-point ${point.stable ? "stable" : "unstable"} ${point.clipped ? "clipped" : ""}`} key={`${point.gain_scale}-${point.branch}-${index}`} cx={x(point.gain_scale)} cy={y(point.residual_ns)} r={point.stable ? 1.45 : 2.1} />)}
        </g>
        <text className="bifurcation-axis-title" x={padding.left + plotWidth / 2} y={height - 4} textAnchor="middle">PI GAIN SCALE × CONFIGURED Kp / Ki</text>
        <text className="bifurcation-axis-title" transform={`translate(11 ${padding.top + plotHeight / 2}) rotate(-90)`} textAnchor="middle">SETTLED PHASE RESIDUAL</text>
      </svg>
    </div>
  );
}

function FractalDiagnostics({ analysis }: { analysis: ResearchPayload["fractal"] }) {
  const correlationPoints = analysis.correlation.points ?? [];
  const higuchiPoints = analysis.higuchi.points ?? [];
  const multifractalPoints = analysis.multifractal.exponents ?? [];
  if (!correlationPoints.length && !higuchiPoints.length && !multifractalPoints.length) {
    return <div className="bifurcation-empty"><Orbit size={20} /><span>Collecting endpoint phase samples for fractal scaling ({analysis.samples ?? 0}/64; MF-DFA begins at 128)</span></div>;
  }
  const width = 620;
  const height = 270;
  const facetWidth = width / 3;
  const plotTop = 48;
  const plotBottom = 226;
  const localLeft = 36;
  const localRight = 12;
  const extent = (values: number[]) => {
    if (!values.length) return [0, 1] as const;
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const padding = Math.max(.01, (maximum - minimum) * .08);
    return [minimum - padding, maximum + padding] as const;
  };
  const correlationX = extent(correlationPoints.map((point) => point.log_radius));
  const correlationY = extent(correlationPoints.map((point) => point.log_correlation));
  const higuchiX = extent(higuchiPoints.map((point) => point.log_inverse_k));
  const higuchiY = extent(higuchiPoints.map((point) => point.log_length));
  const multifractalX = extent(multifractalPoints.map((point) => point.q));
  const multifractalY = extent(multifractalPoints.map((point) => point.h));
  const mapX = (facet: number, value: number, range: readonly [number, number]) => facet * facetWidth + localLeft + (value - range[0]) / Math.max(.001, range[1] - range[0]) * (facetWidth - localLeft - localRight);
  const mapY = (value: number, range: readonly [number, number]) => plotTop + (range[1] - value) / Math.max(.001, range[1] - range[0]) * (plotBottom - plotTop);
  const path = (facet: number, points: Array<[number, number]>, xRange: readonly [number, number], yRange: readonly [number, number]) => points.map(([xValue, yValue], index) => `${index ? "L" : "M"} ${mapX(facet, xValue, xRange).toFixed(2)} ${mapY(yValue, yRange).toFixed(2)}`).join(" ");
  const correlationFit = analysis.correlation.fit;
  const scalingPoints = correlationFit ? correlationPoints.slice(correlationFit.start_index, correlationFit.end_index + 1) : [];
  const higuchiFit = analysis.higuchi.fit;
  const correlationValue = analysis.correlation.dimension;
  const higuchiValue = analysis.higuchi.dimension;
  const multifractalValue = analysis.multifractal.spectrum_width;
  return (
    <div className="fractal-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="fractal-title fractal-description">
        <title id="fractal-title">Endpoint PHC fractal scaling diagnostics</title>
        <desc id="fractal-description">Three finite-record diagnostics: Grassberger–Procaccia correlation sum and scaling window, Higuchi curve-length scaling, and generalized Hurst exponents from multifractal detrended fluctuation analysis.</desc>
        {[0, 1, 2].map((facet) => <rect key={facet} className="fractal-frame" x={facet * facetWidth + localLeft} y={plotTop} width={facetWidth - localLeft - localRight} height={plotBottom - plotTop} />)}
        {[1, 2].map((facet) => <line key={facet} className="fractal-separator" x1={facet * facetWidth} x2={facet * facetWidth} y1="8" y2={height - 8} />)}
        <text className="fractal-title" x="9" y="17">CORRELATION DIMENSION</text>
        <text className="fractal-value" x="9" y="34">{correlationValue == null ? "D₂ learning" : `D₂ ${correlationValue.toFixed(3)}`}</text>
        <text className="fractal-title" x={facetWidth + 9} y="17">HIGUCHI GRAPH DIMENSION</text>
        <text className="fractal-value" x={facetWidth + 9} y="34">{higuchiValue == null ? "Dᴴ learning" : `Dᴴ ${higuchiValue.toFixed(3)}`}</text>
        <text className="fractal-title" x={facetWidth * 2 + 9} y="17">MULTIFRACTAL SCALING</text>
        <text className="fractal-value" x={facetWidth * 2 + 9} y="34">{multifractalValue == null ? "Δh learning" : `Δh ${multifractalValue.toFixed(3)}`}</text>

        {correlationPoints.length ? <>
          <path className="fractal-trace correlation" d={path(0, correlationPoints.map((point) => [point.log_radius, point.log_correlation]), correlationX, correlationY)} />
          {scalingPoints.length > 1 && <path className="fractal-scaling-fit" d={path(0, scalingPoints.map((point) => [point.log_radius, point.log_correlation]), correlationX, correlationY)} />}
          {correlationPoints.map((point, index) => <circle className={correlationFit && index >= correlationFit.start_index && index <= correlationFit.end_index ? "fractal-point fit" : "fractal-point"} key={index} cx={mapX(0, point.log_radius, correlationX)} cy={mapY(point.log_correlation, correlationY)} r="1.8" />)}
          <text className="fractal-tick" x={mapX(0, correlationX[0], correlationX)} y={plotBottom + 14} textAnchor="start">{correlationX[0].toFixed(1)}</text>
          <text className="fractal-tick" x={mapX(0, correlationX[1], correlationX)} y={plotBottom + 14} textAnchor="end">{correlationX[1].toFixed(1)}</text>
          <text className="fractal-axis-title" x={facetWidth / 2 + 10} y={height - 8} textAnchor="middle">log radius · highlighted scaling window</text>
        </> : <text className="fractal-empty" x={facetWidth / 2} y={(plotTop + plotBottom) / 2} textAnchor="middle">64 samples required</text>}

        {higuchiPoints.length ? <>
          <path className="fractal-trace higuchi" d={path(1, higuchiPoints.map((point) => [point.log_inverse_k, point.log_length]), higuchiX, higuchiY)} />
          {higuchiFit && <line className="fractal-fit-line" x1={mapX(1, higuchiX[0], higuchiX)} y1={mapY(higuchiFit.intercept + higuchiFit.slope * higuchiX[0], higuchiY)} x2={mapX(1, higuchiX[1], higuchiX)} y2={mapY(higuchiFit.intercept + higuchiFit.slope * higuchiX[1], higuchiY)} />}
          {higuchiPoints.map((point, index) => <circle className="fractal-point higuchi" key={index} cx={mapX(1, point.log_inverse_k, higuchiX)} cy={mapY(point.log_length, higuchiY)} r="1.7" />)}
          <text className="fractal-tick" x={mapX(1, higuchiX[0], higuchiX)} y={plotBottom + 14} textAnchor="start">{higuchiX[0].toFixed(1)}</text>
          <text className="fractal-tick" x={mapX(1, higuchiX[1], higuchiX)} y={plotBottom + 14} textAnchor="end">{higuchiX[1].toFixed(1)}</text>
          <text className="fractal-axis-title" x={facetWidth * 1.5 + 10} y={height - 8} textAnchor="middle">log 1/k · curve-length regression</text>
        </> : <text className="fractal-empty" x={facetWidth * 1.5} y={(plotTop + plotBottom) / 2} textAnchor="middle">32 samples required</text>}

        {multifractalPoints.length ? <>
          <path className="fractal-trace multifractal" d={path(2, multifractalPoints.map((point) => [point.q, point.h]), multifractalX, multifractalY)} />
          {multifractalPoints.map((point) => <g key={point.q}><rect className="fractal-point multifractal" x={mapX(2, point.q, multifractalX) - 2.3} y={mapY(point.h, multifractalY) - 2.3} width="4.6" height="4.6" transform={`rotate(45 ${mapX(2, point.q, multifractalX)} ${mapY(point.h, multifractalY)})`} /><text className="fractal-q-label" x={mapX(2, point.q, multifractalX)} y={plotBottom + 14} textAnchor="middle">{point.q.toFixed(0)}</text></g>)}
          <text className="fractal-axis-title" x={facetWidth * 2.5 + 10} y={height - 8} textAnchor="middle">moment q · generalized Hurst h(q)</text>
        </> : <text className="fractal-empty" x={facetWidth * 2.5} y={(plotTop + plotBottom) / 2} textAnchor="middle">128 samples required</text>}
      </svg>
    </div>
  );
}

function MetrologyWorkbench({
  research,
  nodes,
  metric,
  setMetric,
  experimentBusy,
  toggleCapture,
  exportRun,
}: {
  research: ResearchPayload;
  nodes: ClockNode[];
  metric: keyof ResearchPayload["stability"];
  setMetric: (metric: keyof ResearchPayload["stability"]) => void;
  experimentBusy: boolean;
  toggleCapture: () => void;
  exportRun: (id: string) => void;
}) {
  const endpoint = research.endpoint ?? nodes[nodes.length - 1]?.id;
  const endpointBudget = research.error_budget.nodes[endpoint] ?? null;
  const metricLabels: Array<[keyof ResearchPayload["stability"], string]> = [["adev", "ADEV"], ["mdev", "MDEV"], ["tdev", "TDEV"], ["hdev", "HDEV"], ["mtie", "MTIE"], ["theo1", "Theo1"]];
  const metricLabel = metricLabels.find(([id]) => id === metric)?.[1] ?? metric.toUpperCase();
  const latestMetric = research.stability[metric].at(-1);
  const fusionNodes = research.fusion.nodes ?? {};
  const ensembleWeights = research.ensemble.weights ?? {};
  return (
    <div className="research-layout">
      <section className="instrument-panel research-hero">
        <div>
          <span className="section-kicker">TRACEABLE EXPERIMENT RECORD</span>
          <h2>{research.active_experiment?.name ?? "Metrology recorder armed"}</h2>
          <p>Raw PHC cycles, LinuxPTP observations, configuration, temperatures, controls, and events share one run identity. The database uses WAL commits so a browser disconnect does not erase the experiment.</p>
          <div className="research-tags"><span>SQLITE / WAL</span><span>RAW + DERIVED</span><span>{research.sample_count.toLocaleString()} CYCLES</span><span>{research.mode.toUpperCase()}</span></div>
        </div>
        <div className="recorder-console">
          <span>{research.active_experiment ? "CAPTURE ELAPSED" : "LAST COMPLETE RUN"}</span>
          <strong>{research.active_experiment ? `${Math.max(0, Math.floor(research.generated_at - research.active_experiment.started_at))} s` : research.experiments[0]?.id ?? "NO RUN"}</strong>
          <button className={research.active_experiment ? "danger-action" : "primary-action"} type="button" disabled={experimentBusy || research.mode === "simulation"} onClick={toggleCapture}>
            {research.active_experiment ? <Square size={13} fill="currentColor" /> : <Radio size={14} />}
            {experimentBusy ? "Transitioning…" : research.active_experiment ? "Stop & seal run" : "Start raw capture"}
          </button>
          {research.mode === "simulation" && <small>Recorder controls become active on the appliance.</small>}
        </div>
      </section>

      <section className="instrument-panel stability-panel">
        <div className="panel-heading">
          <div><span className="section-kicker">IEEE 1139 / ITU-T G.810 METRICS</span><h2>Phase & frequency stability</h2></div>
          <div className="segmented-control metrology-tabs">{metricLabels.map(([id, label]) => <button type="button" key={id} className={metric === id ? "active" : ""} onClick={() => setMetric(id)}>{label}</button>)}</div>
        </div>
        <div className="stability-summary">
          <div><span>Endpoint</span><strong>{endpoint ?? "—"}</strong><small>relative to BC1</small></div>
          <div><span>{metricLabel} at longest τ</span><strong>{latestMetric ? metric === "adev" || metric === "mdev" || metric === "hdev" ? latestMetric.value.toExponential(2) : formatNanoseconds(latestMetric.value) : "—"}</strong><small>τ {latestMetric?.tau_s.toFixed(1) ?? "—"} s</small></div>
          <div><span>Effective pairs</span><strong>{latestMetric?.pairs.toLocaleString() ?? "—"}</strong><small>{latestMetric?.confidence ? `${(latestMetric.confidence * 100).toFixed(0)}% coverage proxy` : "max interval statistic"}</small></div>
          <div><span>Cadence</span><strong>{research.sample_rate_hz.toFixed(1)} Hz</strong><small>{research.aligned_sample_count.toLocaleString()} aligned cycles</small></div>
        </div>
        <ResearchLineChart points={research.stability[metric]} metric={metricLabel} />
        <div className="instrument-note"><Info size={13} /><span>ADEV, MDEV, and HDEV are fractional-frequency deviations. TDEV, MTIE, and Theo1 retain nanosecond units. No display smoothing is applied; each point states its effective pair count.</span></div>
      </section>

      <section className="instrument-panel fusion-panel">
        <div className="panel-heading"><div><span className="section-kicker">WEIGHTED FACTOR GRAPH</span><h2>Clock-state fusion</h2></div><span className={`quality-badge ${research.fusion.status === "solved" ? "" : "pending"}`}>{research.fusion.status?.toUpperCase() ?? "WAITING"}</span></div>
        <div className="fusion-chain">
          {nodes.map((node) => {
            const estimate = fusionNodes[node.id];
            return <div key={node.id}><i style={{ background: node.color }} /><span>{node.id}</span><strong>{estimate ? formatNanoseconds(estimate.offset_ns, true) : "—"}</strong><small>{estimate ? `±${formatNanoseconds(estimate.sigma_ns)}` : "no factor"}</small></div>;
          })}
        </div>
        <div className="fusion-residuals">
          {(research.fusion.residuals ?? []).slice(0, 6).map((residual, index) => <div key={`${residual.edge}-${index}`}><span>{residual.edge}</span><strong>{formatNanoseconds(residual.residual_ns, true)}</strong><i><em style={{ width: `${Math.min(100, Math.abs(residual.normalized) * 20)}%` }} /></i><small>{residual.source}</small></div>)}
        </div>
      </section>

      <section className="instrument-panel ensemble-panel">
        <div className="panel-heading"><div><span className="section-kicker">AT1-STYLE ENSEMBLE</span><h2>Virtual lab timescale</h2></div><span className="quality-badge">COVARIANCE WEIGHTED</span></div>
        <div className="ensemble-value"><span>VIRTUAL OFFSET</span><strong>{research.ensemble.virtual_offset_ns == null ? "—" : formatNanoseconds(research.ensemble.virtual_offset_ns, true)}</strong><small>{research.ensemble.one_sigma_ns == null ? "learning covariance" : `one sigma ${formatNanoseconds(research.ensemble.one_sigma_ns)}`}</small></div>
        <div className="ensemble-weights">{Object.entries(ensembleWeights).map(([node, weight], index) => <div key={node}><span><i style={{ background: TRACE_COLORS[index + 1] }} />{node}</span><strong>{(weight * 100).toFixed(1)}%</strong><em><i style={{ width: `${weight * 100}%`, background: TRACE_COLORS[index + 1] }} /></em></div>)}</div>
      </section>

      <section className="instrument-panel budget-panel">
        <div className="panel-heading"><div><span className="section-kicker">UNCERTAINTY LEDGER</span><h2>{endpoint} error budget</h2></div><span className="quality-badge">{endpointBudget ? `RSS ${formatNanoseconds(endpointBudget.rss_ns)}` : "LEARNING"}</span></div>
        {endpointBudget ? <><div className="budget-bars">{Object.entries(endpointBudget.components_ns).map(([name, value]) => <div key={name}><span>{name.replace("_", " ")}</span><strong>{formatNanoseconds(value)}</strong><em><i style={{ width: `${endpointBudget.contribution_pct[name] ?? 0}%` }} /></em><small>{(endpointBudget.contribution_pct[name] ?? 0).toFixed(1)}%</small></div>)}</div>{research.error_budget.cascade && <div className="covariance-budget"><span>CASCADE COVARIANCE</span><strong>{formatNanoseconds(research.error_budget.cascade.correlated_sigma_ns)}</strong><small>vs {formatNanoseconds(research.error_budget.cascade.independent_sigma_ns)} if hops were independent · {research.error_budget.cascade.samples} aligned samples</small></div>}</> : <div className="empty-instrument">Waiting for uncertainty factors.</div>}
      </section>

      <section className="instrument-panel run-ledger-panel">
        <div className="panel-heading"><div><span className="section-kicker">RUN LEDGER</span><h2>Reproducible captures</h2></div><span className="scan-time">{research.experiments.length} runs</span></div>
        <div className="run-ledger">
          {research.experiments.length ? research.experiments.slice(0, 5).map((run) => <div key={run.id}><i className={run.state === "running" ? "running" : ""} /><span><strong>{run.name}</strong><small>{run.id} · {run.kind} · {new Date(run.started_at * 1000).toLocaleString()}</small></span><em>{run.sample_count?.toLocaleString() ?? 0} samples</em><button type="button" disabled={research.mode === "simulation"} onClick={() => exportRun(run.id)}><Download size={13} /> CSV</button></div>) : <div className="empty-instrument">No captured experiments yet.</div>}
        </div>
      </section>
    </div>
  );
}

function PathMicroscopeView({ research, nodes }: { research: ResearchPayload; nodes: ClockNode[] }) {
  const events = research.path_microscope.events;
  const latestByNode = Object.fromEntries(nodes.slice(1).map((node) => {
    const nodeEvents = events.filter((event) => event.node === node.id);
    return [node.id, { sync: [...nodeEvents].reverse().find((event) => event.kind === "sync"), delay: [...nodeEvents].reverse().find((event) => event.kind === "delay") }];
  })) as Record<string, { sync?: PathEvent; delay?: PathEvent }>;
  const comparison = research.capabilities.pps_common_edge?.state?.latest?.offsets_ns ?? {};
  return (
    <div className="path-layout">
      <section className="instrument-panel path-hero">
        <div><span className="section-kicker">RAW IEEE 1588 EXCHANGE</span><h2>The packet path, timestamp by timestamp</h2><p>LinuxPTP emits slave-event-monitor TLVs directly from the active port state machine. Sync and Delay records retain their independent sequence numbers; the Observatory never fabricates a four-timestamp exchange by pairing unrelated sequences.</p></div>
        <div className={`path-capture-state ${research.path_microscope.mode}`}><Radio size={17} /><span><strong>{research.path_microscope.mode === "live" ? "TLV CAPTURE LIVE" : research.path_microscope.mode === "simulation" ? "MODELED DEMO" : "WAITING FOR TLVs"}</strong><small>{events.length} exchange records · {research.capabilities.path_monitor?.events ?? 0} hardware events</small></span></div>
      </section>
      <section className="instrument-panel exchange-rack">
        <div className="panel-heading"><div><span className="section-kicker">HOP MICROSCOPES</span><h2>Origin, ingress, correction, response</h2></div><code>t1 / t2 · t3 / t4</code></div>
        <div className="exchange-grid">
          {nodes.slice(1).map((node, index) => {
            const pair = latestByNode[node.id];
            return (
              <article className="exchange-card" key={node.id}>
                <header><span style={{ color: node.color }}>H{index + 1}</span><strong>{nodes[index].id} → {node.id}</strong><em>{pair.sync || pair.delay ? "CAPTURED" : "WAITING"}</em></header>
                <div className="timestamp-lane">
                  <div><i>T1</i><span>Sync origin</span><strong>{pair.sync?.t1_ns == null ? "—" : `${String(pair.sync.t1_ns).slice(-9)} ns`}</strong></div>
                  <b><ArrowRight size={14} /><small>{pair.sync?.forward_transit_ns == null ? "—" : `${formatNanoseconds(pair.sync.forward_transit_ns)} apparent`}</small></b>
                  <div><i>T2</i><span>Sync ingress</span><strong>{pair.sync?.t2_ns == null ? "—" : `${String(pair.sync.t2_ns).slice(-9)} ns`}</strong></div>
                </div>
                <div className="timestamp-lane reverse">
                  <div><i>T4</i><span>Delay response</span><strong>{pair.delay?.t4_ns == null ? "—" : `${String(pair.delay.t4_ns).slice(-9)} ns`}</strong></div>
                  <b><ArrowRight size={14} /><small>{pair.delay?.reverse_transit_ns == null ? "—" : `${formatNanoseconds(pair.delay.reverse_transit_ns)} apparent`}</small></b>
                  <div><i>T3</i><span>Delay origin</span><strong>{pair.delay?.t3_ns == null ? "—" : `${String(pair.delay.t3_ns).slice(-9)} ns`}</strong></div>
                </div>
                <footer><span>SYNC #{pair.sync?.sequence_id ?? "—"}</span><span>DELAY #{pair.delay?.sequence_id ?? "—"}</span><span>CF {formatNanoseconds((pair.sync?.correction_ns ?? 0) + (pair.delay?.correction_ns ?? 0))}</span></footer>
              </article>
            );
          })}
        </div>
      </section>
      <section className="instrument-panel asymmetry-panel">
        <div className="panel-heading"><div><span className="section-kicker">DIRECTIONAL TIMESTAMP RESIDUAL</span><h2>Forward minus reverse observation</h2><p>This raw difference contains twice the inter-clock offset plus any path asymmetry; it is not labeled as one-way delay.</p></div><span className="quality-badge">SEQUENCES KEPT SEPARATE</span></div>
        <div className="asymmetry-rows">{nodes.slice(1).map((node, index) => {
          const pair = latestByNode[node.id];
          const directionalDelta = pair.sync?.forward_transit_ns != null && pair.delay?.reverse_transit_ns != null ? pair.sync.forward_transit_ns - pair.delay.reverse_transit_ns : null;
          return <div key={node.id}><span>H{index + 1} · {node.id}</span><i><em style={{ width: directionalDelta == null ? "0%" : `${Math.min(100, Math.abs(directionalDelta))}%`, marginLeft: directionalDelta != null && directionalDelta < 0 ? `${50 - Math.min(50, Math.abs(directionalDelta) / 2)}%` : "50%" }} /></i><strong>{directionalDelta == null ? "—" : formatNanoseconds(directionalDelta, true)}</strong></div>;
        })}</div>
      </section>
      <section className="instrument-panel pps-compare-panel">
        <div className="panel-heading"><div><span className="section-kicker">COMMON-EDGE PHC COMPARISON</span><h2>PPS event timestamps</h2></div><span className={`quality-badge ${research.capabilities.pps_common_edge?.supported ? "" : "pending"}`}>{research.capabilities.pps_common_edge?.supported ? "LIVE EDGE" : "CAPABILITY GATED"}</span></div>
        {Object.keys(comparison).length ? <div className="pps-comparison-values">{Object.entries(comparison).map(([node, value], index) => <div key={node}><i style={{ background: TRACE_COLORS[index + 1] }} /><span>{node}</span><strong>{formatNanoseconds(value, true)}</strong></div>)}</div> : <div className="capability-empty"><Zap size={20} /><div><strong>No common physical edge captured</strong><span>Select External PPS + measure-only comparison in Configuration. At least two PHCs must expose programmable EXTS pins; unsupported hardware remains explicitly unavailable.</span></div></div>}
      </section>
      <div className="instrument-note path-provenance"><ShieldCheck size={13} /><span>{research.path_microscope.provenance}</span></div>
    </div>
  );
}

function IntelligenceWorkbench({
  research,
  activeNode,
  stageTune,
}: {
  research: ResearchPayload;
  activeNode: ClockNode;
  stageTune: (kp: number, ki: number) => void;
}) {
  const probabilities = activeNode.servoType === "imm" ? activeNode.kalman?.model_probabilities : undefined;
  const temperature = research.temperature_holdover[activeNode.id];
  const changeProbability = research.change_detection.latest_probability ?? 0;
  const [nonlinearView, setNonlinearView] = useState<"bifurcation" | "recurrence" | "fractal">("bifurcation");
  const bifurcation = research.bifurcation ?? { status: "learning", samples: 0, points: [], summaries: [], live_changes: 0 };
  const fractal = research.fractal ?? {
    status: "learning",
    samples: 0,
    higuchi: { status: "learning", points: [] },
    correlation: { status: "learning", points: [], embeddings: [] },
    multifractal: { status: "learning", exponents: [] },
    live_changes: 0,
  };
  const currentBranch = bifurcation.current;
  const nonlinearTitle = nonlinearView === "bifurcation" ? "Replay bifurcation map" : nonlinearView === "fractal" ? "Fractal scaling diagnostics" : "Recurrence quantification";
  const nonlinearBadge = nonlinearView === "bifurcation"
    ? (bifurcation.status === "ready" ? "NO LIVE CHANGES" : bifurcation.status.toUpperCase())
    : nonlinearView === "fractal"
      ? (fractal.status === "ready" ? "FINITE RECORD" : fractal.status.toUpperCase())
      : `${((research.recurrence.recurrence_rate ?? 0) * 100).toFixed(1)}% RR`;
  return (
    <div className="intelligence-layout">
      <section className="instrument-panel estimator-hero">
        <div><span className="section-kicker">ADAPTIVE THREE-STATE / IMM</span><h2>Phase · frequency · oscillator drift</h2><p>The adaptive filter estimates phase, fractional frequency, and aging rate. IMM mode runs quiet, dynamic, and holdover models together and changes their probabilities from measured innovations.</p></div>
        <div className="state-vector">
          <div><span>x₀ · PHASE</span><strong>{activeNode.kalman ? formatNanoseconds(activeNode.kalman.phase_estimate_ns, true) : formatNanoseconds(activeNode.offset, true)}</strong><small>σ {activeNode.kalman ? formatNanoseconds(activeNode.kalman.phase_sigma_ns) : "modeled"}</small></div>
          <div><span>x₁ · FREQUENCY</span><strong>{(activeNode.kalman?.frequency_estimate_ppb ?? activeNode.frequencyPpb).toFixed(3)} ppb</strong><small>correction state</small></div>
          <div><span>x₂ · DRIFT</span><strong>{activeNode.kalman?.drift_estimate_ppb_s == null ? "—" : `${activeNode.kalman.drift_estimate_ppb_s.toFixed(4)} ppb/s`}</strong><small>oscillator aging</small></div>
        </div>
      </section>
      <section className="instrument-panel regime-panel">
        <div className="panel-heading"><div><span className="section-kicker">INTERACTING MULTIPLE MODEL</span><h2>Regime probability</h2></div><span className={`quality-badge ${probabilities ? "" : "pending"}`}>{probabilities ? activeNode.kalman?.regime?.toUpperCase() ?? "ACQUIRING" : "NOT ACTIVE"}</span></div>
        {probabilities ? <><div className="regime-bars">{Object.entries(probabilities).map(([name, probability], index) => <div key={name}><span><i style={{ background: TRACE_COLORS[index + 1] }} />{name}</span><strong>{(probability * 100).toFixed(1)}%</strong><em><i style={{ width: `${probability * 100}%`, background: TRACE_COLORS[index + 1] }} /></em></div>)}</div>
        <div className="innovation-ledger"><div><span>Innovation</span><strong>{activeNode.kalman ? formatNanoseconds(activeNode.kalman.innovation_ns, true) : "—"}</strong></div><div><span>Adaptive R</span><strong>{activeNode.kalman?.adaptive_measurement_noise_ns ? formatNanoseconds(activeNode.kalman.adaptive_measurement_noise_ns) : "learning"}</strong></div><div><span>Rejected</span><strong>{activeNode.kalman?.rejected_count ?? 0}</strong></div></div></> : <div className="capability-empty"><Gauge size={20} /><div><strong>IMM is not controlling {activeNode.id}</strong><span>Select the IMM servo to observe measured quiet, dynamic, and holdover model probabilities. No modeled probabilities are substituted into a live session.</span></div></div>}
      </section>
      <section className="instrument-panel holdover-predictor">
        <div className="panel-heading"><div><span className="section-kicker">TEMPERATURE-AWARE HOLDOVER</span><h2>{activeNode.id} forecast</h2></div><span className={`quality-badge ${temperature?.status === "ready" ? "" : "pending"}`}>{temperature?.status?.toUpperCase() ?? "NO SENSOR"}</span></div>
        {temperature?.status === "ready" ? <><div className="forecast-dial"><span>+{temperature.horizon_s?.toFixed(0)} s</span><strong>{formatNanoseconds(temperature.predicted_phase_ns ?? 0, true)}</strong><small>±{formatNanoseconds(temperature.one_sigma_ns ?? 0)} · {(temperature.predicted_frequency_ppb ?? 0).toFixed(3)} ppb</small></div><div className="temperature-strip"><ThermometerFallback /><span>{temperature.temperature_c?.toFixed(1)} °C</span><em><i style={{ width: `${Math.min(100, Math.max(0, ((temperature.temperature_c ?? 20) - 20) / 60 * 100))}%` }} /></em></div></> : <div className="capability-empty"><TimerReset size={20} /><div><strong>Learning thermal coefficients</strong><span>A prediction is published only after aligned temperature and phase samples are available.</span></div></div>}
      </section>
      <section className="instrument-panel system-id-panel">
        <div className="panel-heading"><div><span className="section-kicker">ARX SYSTEM IDENTIFICATION</span><h2>Measured loop dynamics</h2></div><span className={`quality-badge ${research.system_identification.status === "stable" ? "" : "pending"}`}>{research.system_identification.status.toUpperCase()}</span></div>
        <div className="system-id-metrics"><div><span>Spectral radius</span><strong>{research.system_identification.spectral_radius?.toFixed(3) ?? "—"}</strong></div><div><span>Settling time</span><strong>{research.system_identification.settling_time_s == null ? "—" : `${research.system_identification.settling_time_s.toFixed(1)} s`}</strong></div><div><span>Model fit</span><strong>{research.system_identification.r_squared == null ? "—" : `${(research.system_identification.r_squared * 100).toFixed(1)}%`}</strong></div><div><span>Residual</span><strong>{research.system_identification.residual_sigma_ns == null ? "—" : formatNanoseconds(research.system_identification.residual_sigma_ns)}</strong></div></div>
        <div className="pole-plane">{(research.system_identification.poles ?? []).map((pole, index) => <i key={index} style={{ left: `${50 + pole.real * 42}%`, top: `${50 - pole.imag * 42}%`, borderColor: TRACE_COLORS[index + 2] }}><span>{index + 1}</span></i>)}<span className="unit-circle" /><b className="axis-x" /><b className="axis-y" /></div>
      </section>
      <section className="instrument-panel tuner-panel">
        <div className="panel-heading"><div><span className="section-kicker">REPLAY-SAFE OPTIMIZATION</span><h2>Constrained gain recommendation</h2></div><span className={`quality-badge ${research.auto_tune.status === "recommended" ? "" : "pending"}`}>{research.auto_tune.status.toUpperCase()}</span></div>
        {research.auto_tune.recommendation ? <><div className="tune-recommendation"><div><span>RECOMMENDED PI</span><strong>Kp {research.auto_tune.recommendation.kp.toFixed(2)} · Ki {research.auto_tune.recommendation.ki.toFixed(2)}</strong><small>{research.auto_tune.safe_candidates}/{research.auto_tune.evaluated_candidates} replay-safe candidates</small></div><em><strong>{research.auto_tune.predicted_improvement_pct?.toFixed(1)}%</strong><span>predicted RMS improvement</span></em></div><div className="frontier-row">{research.auto_tune.frontier?.slice(0, 5).map((candidate, index) => <div key={`${candidate.kp}-${candidate.ki}`} style={{ height: `${35 + (1 - index / 5) * 45}%` }}><span>{candidate.score.toFixed(1)}</span></div>)}</div><button type="button" className="full-secondary" onClick={() => stageTune(research.auto_tune.recommendation!.kp, research.auto_tune.recommendation!.ki)}><SlidersHorizontal size={14} /> Stage recommendation for review</button></> : <div className="capability-empty"><Gauge size={20} /><div><strong>More samples required</strong><span>Optimization never explores gains on live hardware. It ranks candidates against captured data and enforces peak/error constraints.</span></div></div>}
      </section>
      <section className="instrument-panel recurrence-panel nonlinear-panel">
        <div className="panel-heading"><div><span className="section-kicker">NONLINEAR RETURN ANALYSIS</span><h2>{nonlinearTitle}</h2></div><span className={`quality-badge ${nonlinearView !== "recurrence" && ((nonlinearView === "bifurcation" ? bifurcation.status : fractal.status) !== "ready") ? "pending" : ""}`}>{nonlinearBadge}</span></div>
        <div className="nonlinear-switch" aria-label="Nonlinear analysis view"><div className="segmented-control"><button type="button" className={nonlinearView === "bifurcation" ? "active" : ""} onClick={() => setNonlinearView("bifurcation")}>Bifurcation map</button><button type="button" className={nonlinearView === "recurrence" ? "active" : ""} onClick={() => setNonlinearView("recurrence")}>Recurrence plot</button><button type="button" className={nonlinearView === "fractal" ? "active" : ""} onClick={() => setNonlinearView("fractal")}>Fractal analysis</button></div><span>{nonlinearView === "bifurcation" ? `${bifurcation.samples ?? 0} endpoint samples · ${bifurcation.summaries?.length ?? 0} replay gains` : nonlinearView === "fractal" ? `${fractal.samples ?? 0} endpoint samples · three scaling estimators` : `${research.recurrence.samples ?? 0} aligned hop states`}</span></div>
        {nonlinearView === "bifurcation" ? <>
          <BifurcationDiagram analysis={bifurcation} />
          <div className="bifurcation-ledger">
            <div><span>1.00× PI replay</span><strong>{currentBranch?.tail_rms_ns == null ? "—" : formatNanoseconds(currentBranch.tail_rms_ns)}</strong><small>{currentBranch ? `${currentBranch.branch_count} response bands · ${currentBranch.regime}` : "learning"}</small></div>
            <div><span>First bound crossing</span><strong>{bifurcation.first_transition_gain == null ? "not found" : `${bifurcation.first_transition_gain.toFixed(2)}×`}</strong><small>{bifurcation.first_transition_gain == null ? "within scanned range" : "replay safety envelope"}</small></div>
            <div><span>PI baseline</span><strong>{bifurcation.base_gains ? `Kp ${bifurcation.base_gains.kp.toFixed(2)} · Ki ${bifurcation.base_gains.ki.toFixed(2)}` : "—"}</strong><small>{bifurcation.baseline_is_live ? "active endpoint controller" : `candidate only · active ${bifurcation.active_controller?.replaceAll("-", " ") ?? "servo"}`}</small></div>
          </div>
          <div className="bifurcation-note"><Info size={13} /><span>Settled extrema come from offline replay of captured endpoint PHC phase. {bifurcation.baseline_is_live ? "The 1.00× line matches the active endpoint PI gains." : `The endpoint is running ${bifurcation.active_controller?.replaceAll("-", " ") ?? "another servo"}; the PI baseline is not live.`} A true physical bifurcation claim requires a controlled hardware sweep with dwell at every gain.</span></div>
        </> : nonlinearView === "fractal" ? <>
          <FractalDiagnostics analysis={fractal} />
          <div className="bifurcation-ledger fractal-ledger">
            <div><span>Correlation D₂</span><strong>{fractal.correlation.dimension == null ? "—" : fractal.correlation.dimension.toFixed(3)}</strong><small>{fractal.correlation.r_squared == null ? `${fractal.correlation.samples ?? 0}/64 samples` : `R² ${fractal.correlation.r_squared.toFixed(3)} · m${fractal.correlation.embedding_dimension} · ${fractal.correlation.converged ? "converged" : "not converged"}`}</small></div>
            <div><span>Higuchi Dᴴ</span><strong>{fractal.higuchi.dimension == null ? "—" : fractal.higuchi.dimension.toFixed(3)}</strong><small>{fractal.higuchi.r_squared == null ? `${fractal.higuchi.samples ?? 0}/32 samples` : `R² ${fractal.higuchi.r_squared.toFixed(3)} · k≤${fractal.higuchi.k_max}`}</small></div>
            <div><span>MF-DFA width Δh</span><strong>{fractal.multifractal.spectrum_width == null ? "—" : fractal.multifractal.spectrum_width.toFixed(3)}</strong><small>{fractal.multifractal.surrogate_width == null ? `${fractal.multifractal.samples ?? 0}/128 samples` : `shuffled ${fractal.multifractal.surrogate_width.toFixed(3)} · excess ${(fractal.multifractal.correlation_excess_width ?? 0).toFixed(3)}`}</small></div>
          </div>
          <div className="bifurcation-note fractal-note"><Info size={13} /><span>These are finite-record scaling estimates from raw endpoint PHC phase. Dᴴ measures trace roughness; D₂ must stabilize across embeddings; Δh is compared with six shuffled surrogates. None alone proves chaos, self-similarity, or a strange attractor.</span></div>
        </> : <div className="recurrence-body"><RecurrenceCanvas matrix={research.recurrence.matrix ?? []} /><div><div><span>Determinism</span><strong>{((research.recurrence.determinism ?? 0) * 100).toFixed(1)}%</strong></div><div><span>Diagonal lines</span><strong>{research.recurrence.diagonal_lines ?? 0}</strong></div><div><span>Threshold</span><strong>{(research.recurrence.threshold_sigma ?? 0).toFixed(2)} σ</strong></div><p>Diagonal structures indicate repeatable evolution, not proof of chaos or a deterministic attractor.</p></div></div>}
      </section>
      <section className="instrument-panel koopman-panel">
        <div className="panel-heading"><div><span className="section-kicker">KOOPMAN / DMD</span><h2>Dynamic mode amplification</h2></div><span className={`quality-badge ${research.koopman.interpretation === "contracting" ? "" : "pending"}`}>{research.koopman.interpretation?.toUpperCase() ?? "LEARNING"}</span></div>
        <div className="koopman-spectrum">{(research.koopman.singular_values ?? []).map((value, index) => <div key={index}><span>σ{index + 1}</span><em><i style={{ height: `${Math.min(100, value * 82)}%`, background: TRACE_COLORS[index + 1] }} /></em><strong>{value.toFixed(3)}</strong></div>)}</div>
        <div className="change-readout"><span>BOCPD REGIME CHANGE</span><strong>{(changeProbability * 100).toFixed(2)}%</strong><em><i style={{ width: `${changeProbability * 100}%` }} /></em><small>{research.change_detection.status.toUpperCase()}</small></div>
      </section>
    </div>
  );
}

function ThermometerFallback() {
  return <span className="thermometer-symbol" aria-hidden="true"><i /></span>;
}

function ResilienceWorkbench({
  research,
  authenticationEnabled,
  setAuthenticationEnabled,
  profile,
  setProfile,
  faultTarget,
  setFaultTarget,
  faultDelayUs,
  setFaultDelayUs,
  faultJitterUs,
  setFaultJitterUs,
  faultLossPct,
  setFaultLossPct,
  faultDurationS,
  setFaultDurationS,
  faultActive,
  faultBusy,
  controlFault,
  nodes,
}: {
  research: ResearchPayload;
  authenticationEnabled: boolean;
  setAuthenticationEnabled: (value: boolean) => void;
  profile: string;
  setProfile: (value: string) => void;
  faultTarget: string;
  setFaultTarget: (value: string) => void;
  faultDelayUs: number;
  setFaultDelayUs: (value: number) => void;
  faultJitterUs: number;
  setFaultJitterUs: (value: number) => void;
  faultLossPct: number;
  setFaultLossPct: (value: number) => void;
  faultDurationS: number;
  setFaultDurationS: (value: number) => void;
  faultActive: boolean;
  faultBusy: boolean;
  controlFault: (enabled: boolean) => void;
  nodes: ClockNode[];
}) {
  const dpll = research.capabilities.dpll;
  const synce = research.capabilities.synce;
  const checks = research.profiles.checks ?? [];
  const profileChanged = profile !== research.profiles.profile;
  const expectedValue = (check: NonNullable<ResearchPayload["profiles"]["checks"]>[number]) => (
    check.name === "Domain" && Array.isArray(check.expected) && check.expected.length === 2
      ? `${check.expected[0]}–${check.expected[1]}`
      : Array.isArray(check.expected) ? check.expected.join(" / ") : String(check.expected)
  );
  return (
    <div className="resilience-layout">
      <section className="instrument-panel profile-panel">
        <div className="panel-heading"><div><span className="section-kicker">PROFILE ENGINE</span><h2>PTP configuration guardrails</h2></div><span className={`quality-badge ${research.profiles.compliant && !profileChanged ? "" : "pending"}`}>{profileChanged ? "STAGED CHANGE" : research.profiles.compliant ? "CONFIG MATCH" : "REVIEW"}</span></div>
        <div className="profile-selector"><label><span>Target profile preset</span><select value={profile} onChange={(event) => setProfile(event.target.value)}>{(research.profiles.available_profiles ?? [profile]).map((name) => <option key={name}>{name}</option>)}</select></label><p>The applied {research.profiles.profile} configuration is checked below for transport, delay, domain, and two-step compatibility. This is a configuration guard, not standards certification. Apply remains a separate guarded action.</p></div>
        <div className="compliance-checks">{checks.map((check) => <div key={check.name}><i className={check.pass ? "pass" : "fail"}>{check.pass ? <Check size={11} /> : <X size={11} />}</i><span><strong>{check.name}</strong><small>{String(check.actual)} · expected {expectedValue(check)}</small></span></div>)}</div>
      </section>
      <section className="instrument-panel frequency-chain-panel">
        <div className="panel-heading"><div><span className="section-kicker">PHYSICAL FREQUENCY LAYER</span><h2>DPLL & SyncE</h2></div><span className={`quality-badge ${dpll?.supported ? "" : "pending"}`}>{dpll?.supported ? "KERNEL REPORTED" : "NOT EXPOSED"}</span></div>
        <div className="frequency-capabilities">
          <article><Radio size={18} /><span><strong>DPLL netlink</strong><small>{dpll?.supported ? "Device and pin state reported by the kernel" : dpll?.reason ?? "Not reported"}</small></span><em className={dpll?.supported ? "on" : ""} /></article>
          <article><Network size={18} /><span><strong>Synchronous Ethernet</strong><small>{synce?.supported ? `Pin state ${synce.state}` : synce?.reason ?? "Not reported"}</small></span><em className={synce?.supported ? "on" : ""} /></article>
          <article><ShieldCheck size={18} /><span><strong>Devlink health</strong><small>{research.capabilities.devlink_health?.supported ? "NIC health reporters available" : "No health reporter JSON available"}</small></span><em className={research.capabilities.devlink_health?.supported ? "on" : ""} /></article>
        </div>
        <div className="instrument-note"><Info size={13} /><span>PTP lock never implies SyncE lock. The Observatory displays physical-frequency state only when the driver and kernel expose it.</span></div>
      </section>
      <section className="instrument-panel authentication-panel">
        <div className="panel-heading"><div><span className="section-kicker">IEEE 1588 SECURITY</span><h2>Message authentication</h2></div><span className={`quality-badge ${authenticationEnabled ? "" : "pending"}`}>{authenticationEnabled ? "STAGED" : "DISABLED"}</span></div>
        <div className="security-toggle"><div><ShieldCheck size={20} /><span><strong>LinuxPTP security association</strong><small>Enable SPP, active key ID, and anti-replay verification. Key material stays in the root-owned SA file and is never returned by the API.</small></span></div><Toggle on={authenticationEnabled} onChange={setAuthenticationEnabled} label="Enable PTP authentication" /></div>
        <div className="security-ledger"><div><span>Security parameter pointer</span><strong>{research.security.authentication.spp}</strong></div><div><span>Active key ID</span><strong>{research.security.authentication.active_key_id}</strong></div><div><span>Unauthenticated policy</span><strong>{research.security.authentication.allow_unauth === 0 ? "REJECT" : research.security.authentication.allow_unauth}</strong></div><div><span>Key exposure</span><strong className="good">NEVER</strong></div></div>
      </section>
      <section className="instrument-panel fault-panel">
        <div className="panel-heading"><div><span className="section-kicker">BOUNDED FAULT INJECTION</span><h2>One-hop netem chamber</h2></div><span className={`quality-badge ${faultActive ? "pending" : ""}`}>{faultActive ? "FAULT ACTIVE" : "SAFE / IDLE"}</span></div>
        <div className="fault-form">
          <label><span>Upstream egress</span><select value={faultTarget} onChange={(event) => setFaultTarget(event.target.value)}>{nodes.slice(0, -1).map((node) => <option key={node.id} value={node.id}>{node.id} → next hop</option>)}</select></label>
          <label><span>Delay</span><div className="input-unit"><input type="number" min="0" max="1000000" value={faultDelayUs} onChange={(event) => setFaultDelayUs(Number(event.target.value))} /><em>µs</em></div></label>
          <label><span>Jitter</span><div className="input-unit"><input type="number" min="0" max="1000000" value={faultJitterUs} onChange={(event) => setFaultJitterUs(Number(event.target.value))} /><em>µs</em></div></label>
          <label><span>Loss</span><div className="input-unit"><input type="number" min="0" max="100" step=".1" value={faultLossPct} onChange={(event) => setFaultLossPct(Number(event.target.value))} /><em>%</em></div></label>
          <label><span>Automatic expiry</span><div className="input-unit"><input type="number" min="1" max="3600" value={faultDurationS} onChange={(event) => setFaultDurationS(Number(event.target.value))} /><em>s</em></div></label>
        </div>
        <div className="fault-actions"><div><Info size={15} /><span>Only the declared namespace egress is touched. The controller removes the qdisc when the timer expires or the cascade stops.</span></div><button type="button" className="full-secondary" disabled={!faultActive || faultBusy} onClick={() => controlFault(false)}>Clear now</button><button type="button" className="danger-action" disabled={faultBusy || faultActive} onClick={() => controlFault(true)}><Zap size={14} /> {faultBusy ? "Applying…" : "Inject bounded fault"}</button></div>
      </section>
      <section className="instrument-panel root-cause-panel">
        <div className="panel-heading"><div><span className="section-kicker">BAYESIAN ROOT-CAUSE WATCH</span><h2>Regime transition evidence</h2></div><span className={`quality-badge ${research.change_detection.status === "stable" ? "" : "pending"}`}>{research.change_detection.status.toUpperCase()}</span></div>
        <div className="change-probability"><strong>{((research.change_detection.latest_probability ?? 0) * 100).toFixed(2)}%</strong><span>posterior change probability</span><em><i style={{ width: `${(research.change_detection.latest_probability ?? 0) * 100}%` }} /></em></div>
        <div className="root-cause-factors"><div><span>PHC cross timestamp</span><strong>observed</strong></div><div><span>LinuxPTP servo</span><strong>observed</strong></div><div><span>Path TLVs</span><strong>{research.capabilities.path_monitor?.supported ? "observed" : "waiting"}</strong></div><div><span>Temperature</span><strong>{research.capabilities.temperature?.supported ? "observed" : "unavailable"}</strong></div></div>
      </section>
    </div>
  );
}

function Toggle({ on, onChange, label }: { on: boolean; onChange: (value: boolean) => void; label: string }) {
  return (
    <button className={`toggle ${on ? "on" : ""}`} type="button" role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)}>
      <span />
    </button>
  );
}

export default function PTPBoxDashboard() {
  const [section, setSection] = useState<Section>("Overview");
  const [mobileNav, setMobileNav] = useState(false);
  const [nodes, setNodes] = useState(() => waitingNodes("Finding PTPBox agent"));
  const [selectedNode, setSelectedNode] = useState("BC7");
  const [visibleTraces, setVisibleTraces] = useState(["BC4", "BC5", "BC6", "BC7"]);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [paused, setPaused] = useState(false);
  const [time, setTime] = useState("");
  const [connection, setConnection] = useState<ConnectionMode>("checking");
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [interfaceInventory, setInterfaceInventory] = useState<HostInterface[]>(FALLBACK_INTERFACES);
  const [interfaceUpdatedAt, setInterfaceUpdatedAt] = useState<number | null>(null);
  const [telemetryStatus, setTelemetryStatus] = useState<TelemetryPayload | null>(null);
  const [range, setRange] = useState("2 min");
  const [experimentRunning, setExperimentRunning] = useState(false);
  const [experimentProgress, setExperimentProgress] = useState(38);
  const [research, setResearch] = useState<ResearchPayload | null>(null);
  const [researchMetric, setResearchMetric] = useState<keyof ResearchPayload["stability"]>("tdev");
  const [experimentBusy, setExperimentBusy] = useState(false);
  const [kp, setKp] = useState(0.7);
  const [ki, setKi] = useState(0.3);
  const [kalmanMeasurementNoiseNs, setKalmanMeasurementNoiseNs] = useState(200);
  const [kalmanProcessNoisePpb, setKalmanProcessNoisePpb] = useState(10);
  const [kalmanPhaseTimeConstantS, setKalmanPhaseTimeConstantS] = useState(4);
  const [kalmanInnovationGateSigma, setKalmanInnovationGateSigma] = useState(6);
  const [kalmanDriftNoisePpbS2, setKalmanDriftNoisePpbS2] = useState(.05);
  const [stepThreshold, setStepThreshold] = useState(0);
  const [applyOpen, setApplyOpen] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [profile, setProfile] = useState("G.8275.1 Telecom");
  const [twoStep, setTwoStep] = useState(true);
  const [hardwareTs, setHardwareTs] = useState(true);
  const [sanity, setSanity] = useState(true);
  const [authenticationEnabled, setAuthenticationEnabled] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [servoType, setServoType] = useState<ServoType>("pi");
  const [servoTarget, setServoTarget] = useState("BC7");
  const [servoBusy, setServoBusy] = useState(false);
  const [syncFrequencyHz, setSyncFrequencyHz] = useState(1);
  const [activeSyncFrequencyHz, setActiveSyncFrequencyHz] = useState(1);
  const [phcSampleRateHz, setPhcSampleRateHz] = useState(1);
  const [ppsEnabled, setPpsEnabled] = useState(false);
  const [ppsSource, setPpsSource] = useState("BC1");
  const [ppsSinks, setPpsSinks] = useState<string[]>(["BC2", "BC3", "BC4", "BC5", "BC6", "BC7"]);
  const [ppsOutputPin, setPpsOutputPin] = useState(0);
  const [ppsInputPin, setPpsInputPin] = useState(0);
  const [ppsChannel, setPpsChannel] = useState(0);
  const [ppsPolarity, setPpsPolarity] = useState<PpsPolarity>("rising");
  const [ppsPulseWidthNs, setPpsPulseWidthNs] = useState(100_000_000);
  const [ppsPhaseNs, setPpsPhaseNs] = useState(0);
  const [ppsCorrectionNs, setPpsCorrectionNs] = useState(0);
  const [ppsServo, setPpsServo] = useState<NativeServoType>("pi");
  const [ppsStepThresholdNs, setPpsStepThresholdNs] = useState(0);
  const [ppsFirstStepThresholdNs, setPpsFirstStepThresholdNs] = useState(20_000);
  const [ppsHoldoverSeconds, setPpsHoldoverSeconds] = useState(0);
  const [ppsStableThresholdNs, setPpsStableThresholdNs] = useState(100);
  const [ppsComparisonEnabled, setPpsComparisonEnabled] = useState(false);
  const [ppsComparisonReference, setPpsComparisonReference] = useState("BC2");
  const [faultTarget, setFaultTarget] = useState("BC3");
  const [faultDelayUs, setFaultDelayUs] = useState(250);
  const [faultJitterUs, setFaultJitterUs] = useState(50);
  const [faultLossPct, setFaultLossPct] = useState(0);
  const [faultDurationS, setFaultDurationS] = useState(30);
  const [faultActive, setFaultActive] = useState(false);
  const [faultBusy, setFaultBusy] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [readNotificationIds, setReadNotificationIds] = useState<string[]>([]);
  const [commandOpen, setCommandOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");
  const [commandIndex, setCommandIndex] = useState(0);
  const [pendulumAutoZero, setPendulumAutoZero] = useState(true);
  const [pendulumZeroState, setPendulumZeroState] = useState<PendulumZeroState>({ at: null, baselines: {} });
  const tickRef = useRef(0);
  const latestTelemetryAtRef = useRef(0);
  const latestPhcAtRef = useRef(0);
  const servoSelectionHydratedRef = useRef(false);
  const configHydratedRef = useRef(false);
  const configuredServoTypeRef = useRef<ServoType>("pi");
  const notificationCenterRef = useRef<HTMLDivElement>(null);
  const commandInputRef = useRef<HTMLInputElement>(null);

  const refreshInterfaces = useCallback(async () => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 1800);
    try {
      const response = await fetch(`${agentBaseUrl()}/api/interfaces`, { signal: controller.signal });
      if (!response.ok) throw new Error("interface inventory unavailable");
      const payload = await response.json() as { interfaces?: HostInterface[]; timestamp?: number };
      if (Array.isArray(payload.interfaces) && payload.interfaces.length) {
        setInterfaceInventory(payload.interfaces);
        setInterfaceUpdatedAt(payload.timestamp ?? Date.now() / 1000);
      }
    } finally {
      window.clearTimeout(timeout);
    }
  }, []);

  const activeNode = nodes.find((node) => node.id === selectedNode) ?? nodes[nodes.length - 1];
  const agentConnected = agentStatus !== null;
  const controlledServoNodes = useMemo(() => servoTarget === "all" ? nodes.filter((node) => node.role !== "Grandmaster") : nodes.filter((node) => node.id === servoTarget), [nodes, servoTarget]);
  const targetInHoldover = controlledServoNodes.length > 0 && controlledServoNodes.every((node) => node.servoEnabled === false);
  const targetHasHoldover = controlledServoNodes.some((node) => node.servoEnabled === false);
  const servoStatusLabel = targetInHoldover ? "HOLDOVER" : targetHasHoldover ? "MIXED" : "DISCIPLINED";
  const holdoverElapsedSeconds = activeNode.holdoverStarted && telemetryStatus ? Math.max(0, Math.floor(telemetryStatus.timestamp - activeNode.holdoverStarted)) : null;
  const selectedServoLabel = activeNode.role === "Grandmaster" ? "REFERENCE CLOCK" : `${activeNode.servoType?.toUpperCase() ?? "PTP"}${activeNode.servoEnabled === false ? " · FROZEN" : " SERVO"}`;
  const selectedServoDescription = activeNode.servoType === "imm"
    ? `3-state IMM · ${activeNode.kalman?.regime ?? "regime learning"}`
    : activeNode.servoType === "adaptive-kalman"
      ? activeNode.kalman?.fresh
        ? `3-state adaptive · drift ${(activeNode.kalman.drift_estimate_ppb_s ?? 0).toFixed(4)} ppb/s`
        : "3-state phase / frequency / drift"
      : activeNode.servoType === "kalman"
    ? activeNode.kalman?.fresh
      ? `2-state estimate · phase σ ${formatNanoseconds(activeNode.kalman.phase_sigma_ns)}`
      : "2-state phase / frequency estimator"
    : activeNode.servoType === "linreg"
      ? "Adaptive frequency regression"
      : activeNode.servoType === "nullf"
        ? "Zero frequency correction · SyncE"
        : `Kp ${kp.toFixed(2)} · Ki ${ki.toFixed(2)}`;
  const selectedServoRail = activeNode.servoEnabled === false ? 0 : activeNode.servoType === "imm" ? 96 : activeNode.servoType === "adaptive-kalman" ? 94 : activeNode.servoType === "kalman" ? 92 : activeNode.servoType === "linreg" ? 82 : activeNode.servoType === "nullf" ? 4 : Math.min(100, kp * 76);
  const hostStateLabel = connection === "live" ? "Live raw stream" : connection === "waiting" ? "Waiting for PTP" : connection === "stale" ? "Raw stream stale" : connection === "checking" ? "Finding host…" : "Simulation fallback";
  const dataModeLabel = connection === "live" ? "LIVE · RAW · UNSMOOTHED" : connection === "waiting" ? "HARDWARE · WAITING FOR PTP" : connection === "stale" ? "HARDWARE · RAW DATA STALE" : connection === "checking" ? "FINDING PTPBOX AGENT" : "SIMULATION · NOT MEASURED";
  const newestSampleAt = nodes.reduce((latest, node) => Math.max(latest, node.lastSampleAt ?? 0), 0);
  const newestSampleAge = newestSampleAt && telemetryStatus ? Math.max(0, telemetryStatus.timestamp - newestSampleAt) : null;
  const invalidWindowSamples = telemetryStatus?.clocks.reduce((total, clock) => total + clock.window_invalid_sample_count, 0) ?? 0;
  const timingInterfaces = interfaceInventory.filter((item) => item.namespace);
  const ptpCapableInterfaces = interfaceInventory.filter((item) => item.hardware_timestamping);
  const activeLineRateMbps = interfaceInventory.reduce((total, item) => total + (item.carrier ? item.speed_mbps ?? 0 : 0), 0);
  const hardwareClocks = new Set(interfaceInventory.map((item) => item.phc).filter(Boolean)).size;
  const interfaceDrivers = [...new Set(interfaceInventory.map((item) => item.driver).filter((item): item is string => Boolean(item)))];
  const hundredGigTimingPorts = timingInterfaces.filter((item) => item.speed_mbps === 100000).length;
  const bufferedPhcComparisons = history.reduce((total, point) => total + Object.keys(point.values).length, 0);
  const { effectiveHz: effectiveSyncFrequencyHz, logInterval: syncLogInterval } = synchronizationRate(syncFrequencyHz);
  const syncFrequencyExact = Math.abs(syncFrequencyHz - effectiveSyncFrequencyHz) < 0.001;
  const syncSliderProgress = ((syncFrequencyHz - 0.5) / 9.5) * 100;
  const ppsHardwareStatus = agentStatus?.pps;
  const ppsConfiguredRoles = (ppsSource === "external" ? 0 : 1) + ppsSinks.length;
  const ppsStateLabel = ppsHardwareStatus?.running ? "PPS ACTIVE" : ppsEnabled ? "RESTART TO APPLY" : "PPS OFF";
  const modeledResearch = useMemo(() => buildResearchModel(history.length ? history : buildHistory(), nodes.length ? nodes : INITIAL_NODES), [history, nodes]);
  const activeResearch = research ?? modeledResearch;
  const profileProtocol = profile === "G.8275.2 Telecom"
    ? { domain: 44, transport: "UDPv4", delay: "E2E" }
    : profile === "IEEE 802.1AS gPTP" || profile === "IEEE C37.238 Power"
      ? { domain: profile === "IEEE C37.238 Power" ? 254 : 0, transport: "L2", delay: "P2P" }
      : { domain: 24, transport: "L2", delay: "E2E" };

  const setPpsSourceSafely = (value: string) => {
    setPpsSource(value);
    if (value !== "external") setPpsSinks((current) => current.filter((id) => id !== value));
  };

  const togglePpsSink = (id: string) => {
    if (id === ppsSource) return;
    setPpsSinks((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  };

  const setPpsComparisonSafely = (value: boolean) => {
    setPpsComparisonEnabled(value);
    if (!value) return;
    const sinks = ppsSinks.length >= 2 ? ppsSinks : nodes.slice(1).map((node) => node.id);
    setPpsEnabled(true);
    setPpsSource("external");
    setPpsPolarity((current) => current === "both" ? "rising" : current);
    setPpsSinks(sinks);
    if (!sinks.includes(ppsComparisonReference)) setPpsComparisonReference(sinks[0] ?? "BC2");
  };

  const ppsNodeLabel = (id: string) => {
    const pps = ppsHardwareStatus?.nodes[id];
    if (!pps) return "PPS · CHECKING";
    const role = pps.role === "source" ? "PPS OUT" : pps.role === "sink" ? "PPS IN" : "PPS";
    const state = pps.state === "active" ? "ACTIVE" : pps.state === "starting" ? "ARMING" : pps.state === "stopped" ? "STOPPED" : pps.state === "external" ? "IN USE" : pps.state === "unavailable" ? "N/A" : "READY";
    return `${role} · ${state}`;
  };

  const endpointDistribution = useMemo(() => {
    const endpoint = nodes[nodes.length - 1];
    const values = history.map((point) => point.values[endpoint?.id]).filter((value): value is number => Number.isFinite(value));
    if (!values.length) return { bins: Array(15).fill(0) as number[], min: 0, max: 0, sigma: 0, p95: 0, skew: 0 };
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const width = Math.max(1, maximum - minimum);
    const counts = Array(15).fill(0) as number[];
    values.forEach((value) => { counts[Math.min(14, Math.floor(((value - minimum) / width) * 15))] += 1; });
    const highest = Math.max(...counts, 1);
    const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
    const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length;
    const sigma = Math.sqrt(variance);
    const sorted = [...values].sort((left, right) => left - right);
    const p95 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))];
    const skew = sigma ? values.reduce((sum, value) => sum + ((value - mean) / sigma) ** 3, 0) / values.length : 0;
    return { bins: counts.map((count) => (count / highest) * 100), min: minimum, max: maximum, sigma, p95, skew };
  }, [history, nodes]);

  const holdoverMetrics = useMemo(() => {
    if (!activeNode.holdoverStarted) return null;
    const holdoverStarted = activeNode.holdoverStarted;
    const points = history
      .filter((point) => point.t >= holdoverStarted && Number.isFinite(point.values[activeNode.id]))
      .map((point) => ({ t: point.t, offset: point.values[activeNode.id] }));
    if (points.length < 2) return { driftPpb: null };
    const origin = points[0].t;
    const xs = points.map((point) => point.t - origin);
    const meanX = xs.reduce((sum, value) => sum + value, 0) / xs.length;
    const meanY = points.reduce((sum, point) => sum + point.offset, 0) / points.length;
    const denominator = xs.reduce((sum, value) => sum + (value - meanX) ** 2, 0);
    const driftPpb = denominator ? points.reduce((sum, point, index) => sum + (xs[index] - meanX) * (point.offset - meanY), 0) / denominator : null;
    return { driftPpb };
  }, [activeNode.holdoverStarted, activeNode.id, history]);

  useEffect(() => {
    const updateClock = () => setTime(new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZoneName: "short" }).format(new Date()));
    updateClock();
    const timer = window.setInterval(updateClock, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    void refreshInterfaces().catch(() => undefined);
    const timer = window.setInterval(() => void refreshInterfaces().catch(() => undefined), 5000);
    return () => window.clearInterval(timer);
  }, [refreshInterfaces]);

  useEffect(() => {
    if (!agentStatus || configHydratedRef.current) return;
    configHydratedRef.current = true;
    const controller = new AbortController();
    const hydrateConfiguration = async () => {
      try {
        const response = await fetch(`${agentBaseUrl()}/api/config`, { signal: controller.signal });
        if (!response.ok) throw new Error("configuration unavailable");
        const value = await response.json() as {
          profile?: string;
          log_sync_interval?: number;
          two_step?: boolean;
          hardware_timestamping?: boolean;
          servo?: {
            type?: ServoType;
            kp?: number;
            ki?: number;
            step_threshold_ns?: number;
            sanity_freq_limit_ppb?: number;
            kalman?: {
              measurement_noise_ns?: number;
              process_noise_ppb?: number;
              phase_time_constant_s?: number;
              innovation_gate_sigma?: number;
              drift_noise_ppb_s2?: number;
            };
          };
          security?: {
            authentication?: {
              enabled?: boolean;
            };
          };
          pps?: {
            enabled?: boolean;
            source?: string;
            sinks?: string[];
            output_pin?: number;
            input_pin?: number;
            channel?: number;
            polarity?: PpsPolarity;
            pulse_width_ns?: number;
            perout_phase_ns?: number;
            extts_correction_ns?: number;
            comparison?: {
              enabled?: boolean;
              reference?: string;
            };
            ts2phc?: {
              servo?: NativeServoType;
              step_threshold_ns?: number;
              first_step_threshold_ns?: number;
              holdover_seconds?: number;
              stable_threshold_ns?: number;
            };
          };
        };
        if (value.profile) setProfile(value.profile);
        if (Number.isInteger(value.log_sync_interval)) {
          const configuredFrequency = frequencyFromLogInterval(value.log_sync_interval as number);
          setSyncFrequencyHz(configuredFrequency);
          setActiveSyncFrequencyHz(configuredFrequency);
          setPhcSampleRateHz(configuredFrequency);
        }
        if (typeof value.two_step === "boolean") setTwoStep(value.two_step);
        if (typeof value.hardware_timestamping === "boolean") setHardwareTs(value.hardware_timestamping);
        if (value.servo?.type && ["pi", "linreg", "nullf", "kalman", "adaptive-kalman", "imm"].includes(value.servo.type)) {
          configuredServoTypeRef.current = value.servo.type;
          setServoType(value.servo.type);
        }
        if (typeof value.servo?.kp === "number") setKp(value.servo.kp);
        if (typeof value.servo?.ki === "number") setKi(value.servo.ki);
        if (typeof value.servo?.step_threshold_ns === "number") setStepThreshold(value.servo.step_threshold_ns);
        if (typeof value.servo?.sanity_freq_limit_ppb === "number") setSanity(value.servo.sanity_freq_limit_ppb > 0);
        if (typeof value.servo?.kalman?.measurement_noise_ns === "number") setKalmanMeasurementNoiseNs(value.servo.kalman.measurement_noise_ns);
        if (typeof value.servo?.kalman?.process_noise_ppb === "number") setKalmanProcessNoisePpb(value.servo.kalman.process_noise_ppb);
        if (typeof value.servo?.kalman?.phase_time_constant_s === "number") setKalmanPhaseTimeConstantS(value.servo.kalman.phase_time_constant_s);
        if (typeof value.servo?.kalman?.innovation_gate_sigma === "number") setKalmanInnovationGateSigma(value.servo.kalman.innovation_gate_sigma);
        if (typeof value.servo?.kalman?.drift_noise_ppb_s2 === "number") setKalmanDriftNoisePpbS2(value.servo.kalman.drift_noise_ppb_s2);
        if (typeof value.security?.authentication?.enabled === "boolean") setAuthenticationEnabled(value.security.authentication.enabled);
        if (typeof value.pps?.enabled === "boolean") setPpsEnabled(value.pps.enabled);
        if (typeof value.pps?.source === "string") setPpsSource(value.pps.source);
        if (Array.isArray(value.pps?.sinks) && value.pps.sinks.length) setPpsSinks(value.pps.sinks);
        if (typeof value.pps?.output_pin === "number") setPpsOutputPin(value.pps.output_pin);
        if (typeof value.pps?.input_pin === "number") setPpsInputPin(value.pps.input_pin);
        if (typeof value.pps?.channel === "number") setPpsChannel(value.pps.channel);
        if (value.pps?.polarity && ["rising", "falling", "both"].includes(value.pps.polarity)) setPpsPolarity(value.pps.polarity);
        if (typeof value.pps?.pulse_width_ns === "number") setPpsPulseWidthNs(value.pps.pulse_width_ns);
        if (typeof value.pps?.perout_phase_ns === "number") setPpsPhaseNs(value.pps.perout_phase_ns);
        if (typeof value.pps?.extts_correction_ns === "number") setPpsCorrectionNs(value.pps.extts_correction_ns);
        if (typeof value.pps?.comparison?.enabled === "boolean") setPpsComparisonEnabled(value.pps.comparison.enabled);
        if (typeof value.pps?.comparison?.reference === "string") setPpsComparisonReference(value.pps.comparison.reference);
        if (value.pps?.ts2phc?.servo && ["pi", "linreg", "nullf"].includes(value.pps.ts2phc.servo)) setPpsServo(value.pps.ts2phc.servo);
        if (typeof value.pps?.ts2phc?.step_threshold_ns === "number") setPpsStepThresholdNs(value.pps.ts2phc.step_threshold_ns);
        if (typeof value.pps?.ts2phc?.first_step_threshold_ns === "number") setPpsFirstStepThresholdNs(value.pps.ts2phc.first_step_threshold_ns);
        if (typeof value.pps?.ts2phc?.holdover_seconds === "number") setPpsHoldoverSeconds(value.pps.ts2phc.holdover_seconds);
        if (typeof value.pps?.ts2phc?.stable_threshold_ns === "number") setPpsStableThresholdNs(value.pps.ts2phc.stable_threshold_ns);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) configHydratedRef.current = false;
      }
    };
    void hydrateConfiguration();
    return () => controller.abort();
  }, [agentStatus]);

  useEffect(() => {
    let disposed = false;
    let initialProbe = true;
    let polling = false;
    const pollStatus = async () => {
      if (polling) return;
      polling = true;
      const controller = new AbortController();
      // The first status request after an agent upgrade can overlap the initial
      // seven-PHC sampling pass. Keep that cold-start path from being mistaken
      // for an unreachable host; steady-state status returns in milliseconds.
      const timeout = window.setTimeout(() => controller.abort(), 12_000);
      try {
        const response = await fetch(`${agentBaseUrl()}/api/status`, { signal: controller.signal });
        if (!response.ok) throw new Error("agent unavailable");
        const status = await response.json() as AgentStatus;
        if (disposed) return;
        setAgentStatus(status);
        setFaultActive(Boolean(status.fault?.enabled));
        if (status.fault?.target) setFaultTarget(status.fault.target);
        if (typeof status.phc_sample_rate_hz === "number") {
          setActiveSyncFrequencyHz(status.phc_sample_rate_hz);
          setPhcSampleRateHz(status.phc_sample_rate_hz);
        }
        if (initialProbe) {
          setConnection("waiting");
          setHistory([]);
          setNodes(waitingNodes("Waiting for LinuxPTP"));
        }
      } catch {
        if (!disposed && initialProbe) {
          setConnection("simulation");
          setNodes(INITIAL_NODES);
          setHistory(buildHistory());
        }
      } finally {
        initialProbe = false;
        polling = false;
        window.clearTimeout(timeout);
      }
    };
    void pollStatus();
    const timer = window.setInterval(() => void pollStatus(), 5000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!agentConnected || paused) return;
    latestTelemetryAtRef.current = 0;
    let disposed = false;
    let polling = false;
    const controller = new AbortController();
    const seconds = rangeSeconds(range);

    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const query = new URLSearchParams({ history: String(seconds), limit: String(Math.min(4096, Math.ceil(seconds * 10))) });
        if (latestTelemetryAtRef.current) query.set("since", String(latestTelemetryAtRef.current));
        const response = await fetch(`${agentBaseUrl()}/api/telemetry?${query}`, { signal: controller.signal });
        if (!response.ok) throw new Error("telemetry unavailable");
        const payload = await response.json() as TelemetryPayload;
        if (disposed) return;
        const incoming = historyFromTelemetry(payload);
        const newest = incoming.reduce((value, point) => Math.max(value, point.t), latestTelemetryAtRef.current);
        latestTelemetryAtRef.current = newest;
        setTelemetryStatus(payload);
        if (typeof payload.phc_sample_rate_hz === "number") setPhcSampleRateHz(payload.phc_sample_rate_hz);
        const initialServo = payload.servo_control?.nodes?.BC7?.type;
        if (!servoSelectionHydratedRef.current && initialServo) {
          setServoType(initialServo);
          servoSelectionHydratedRef.current = true;
        }
        setConnection(payload.phc_mode);
        setNodes((current) => preserveNewerPhcTelemetry(nodesFromTelemetry(payload), current));
        setHistory((current) => mergeRawHistory(current, incoming, seconds));

        const ids = payload.clocks.map((clock) => clock.id);
        const measuredIds = payload.clocks.filter((clock) => clock.phc_measurement?.valid && clock.phc_measurement.offset_ns !== null && clock.role !== "grandmaster").map((clock) => clock.id);
        setSelectedNode((current) => ids.includes(current) ? current : measuredIds[measuredIds.length - 1] ?? ids[ids.length - 1] ?? current);
        setVisibleTraces((current) => {
          const retained = current.filter((id) => measuredIds.includes(id));
          return retained.length ? retained : measuredIds.slice(-4);
        });
      } catch {
        if (!disposed) setConnection((current) => current === "live" ? "stale" : current);
      } finally {
        polling = false;
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => {
      disposed = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [agentConnected, paused, range]);

  useEffect(() => {
    if (!agentConnected || paused) return;
    latestPhcAtRef.current = 0;
    let disposed = false;
    let polling = false;
    const controller = new AbortController();
    const seconds = rangeSeconds(range);
    const intervalMs = Math.max(125, Math.round(1000 / activeSyncFrequencyHz));

    const pollPhc = async () => {
      if (polling) return;
      polling = true;
      try {
        const query = new URLSearchParams({ history: String(seconds) });
        if (latestPhcAtRef.current) query.set("since", String(latestPhcAtRef.current));
        const response = await fetch(`${agentBaseUrl()}/api/phc?${query}`, { signal: controller.signal });
        if (!response.ok) throw new Error("PHC telemetry unavailable");
        const payload = await response.json() as PhcTelemetryPayload;
        if (disposed) return;
        const incoming = historyFromPhcTelemetry(payload);
        latestPhcAtRef.current = incoming.reduce((value, point) => Math.max(value, point.t), latestPhcAtRef.current);
        if (typeof payload.sample_rate_hz === "number") setPhcSampleRateHz(payload.sample_rate_hz);
        setConnection(payload.mode);
        setNodes((current) => nodesWithPhcTelemetry(current, payload));
        setHistory((current) => mergeRawHistory(current, incoming, seconds));
      } catch (error) {
        if (!disposed && !(error instanceof DOMException && error.name === "AbortError")) {
          setConnection((current) => current === "live" ? "stale" : current);
        }
      } finally {
        polling = false;
      }
    };

    void pollPhc();
    const timer = window.setInterval(() => void pollPhc(), intervalMs);
    return () => {
      disposed = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [activeSyncFrequencyHz, agentConnected, paused, range]);

  useEffect(() => {
    if (!agentConnected || paused) return;
    let disposed = false;
    let polling = false;
    const controller = new AbortController();
    const pollResearch = async () => {
      if (polling) return;
      polling = true;
      try {
        const response = await fetch(`${agentBaseUrl()}/api/research?history=900`, { signal: controller.signal });
        if (!response.ok) throw new Error("research snapshot unavailable");
        const payload = await response.json() as ResearchPayload;
        if (!disposed) {
          setResearch(payload);
          setExperimentRunning(Boolean(payload.active_experiment));
        }
      } catch (error) {
        if (!disposed && !(error instanceof DOMException && error.name === "AbortError")) setResearch((current) => current);
      } finally {
        polling = false;
      }
    };
    void pollResearch();
    const timer = window.setInterval(() => void pollResearch(), 5000);
    return () => {
      disposed = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [agentConnected, paused]);

  useEffect(() => {
    if (paused || connection !== "simulation") return;
    const timer = window.setInterval(() => {
      tickRef.current += 1;
      const tick = tickRef.current;
      setNodes((current) => current.map((node, index) => ({ ...node, offset: Number((INITIAL_NODES[index].offset + seededNoise(tick, index) * (index * 3.4 + 1.5)).toFixed(2)) })));
      setHistory((current) => {
        const values = Object.fromEntries(INITIAL_NODES.map((node, index) => [node.id, Number((node.offset + seededNoise(tick, index) * (index * 3.4 + 1.5)).toFixed(2))])) as Record<string, number>;
        const hopValues = Object.fromEntries(INITIAL_NODES.slice(1).map((node, index) => [node.id, values[node.id] - values[INITIAL_NODES[index].id]]));
        return [...current.slice(-119), { t: tick, values, hopValues }];
      });
      if (experimentRunning) setExperimentProgress((value) => (value >= 100 ? 100 : value + 1));
    }, 900);
    return () => window.clearInterval(timer);
  }, [connection, experimentRunning, paused]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    const handleCommandShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setNotificationsOpen(false);
        setApplyOpen(false);
        if (commandOpen) {
          setCommandOpen(false);
        } else {
          setCommandQuery("");
          setCommandIndex(0);
          setCommandOpen(true);
        }
      } else if (event.key === "Escape" && commandOpen) {
        event.preventDefault();
        setCommandOpen(false);
      }
    };
    document.addEventListener("keydown", handleCommandShortcut);
    return () => document.removeEventListener("keydown", handleCommandShortcut);
  }, [commandOpen]);

  useEffect(() => {
    if (!commandOpen) return;
    window.requestAnimationFrame(() => commandInputRef.current?.focus());
  }, [commandOpen]);

  useEffect(() => {
    if (!notificationsOpen) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (notificationCenterRef.current && !notificationCenterRef.current.contains(event.target as Node)) setNotificationsOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setNotificationsOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [notificationsOpen]);

  const notifications = useMemo<ObservatoryNotification[]>(() => {
    const items: ObservatoryNotification[] = [];
    const receivers = nodes.filter((node) => node.role !== "Grandmaster");
    const holdover = receivers.filter((node) => node.state === "HOLDOVER");
    const unhealthy = receivers.filter((node) => ["FAULTY", "STALE", "NO DATA", "UNLOCKED"].includes(node.state));

    if (holdover.length) {
      const names = holdover.map((node) => node.id).join(", ");
      items.push({ id: `holdover-${names}`, level: "warn", title: `Holdover active on ${names}`, detail: "PHC adjustment is frozen; raw offset and drift measurement continue.", meta: "SERVO", section: "Overview", nodeId: holdover[0].id, icon: Pause });
    }
    if (unhealthy.length && connection !== "checking") {
      const names = unhealthy.map((node) => node.id).join(", ");
      items.push({ id: `clock-state-${unhealthy.map((node) => `${node.id}-${node.state}`).join("-")}`, level: "warn", title: `${unhealthy.length} receiver${unhealthy.length === 1 ? "" : "s"} need attention`, detail: `${names} · ${unhealthy.map((node) => node.state).join(" / ")}`, meta: "CLOCK STATE", section: "Overview", nodeId: unhealthy[0].id, icon: Zap });
    }
    if (invalidWindowSamples > 0) {
      items.push({ id: `invalid-samples-${invalidWindowSamples}`, level: "warn", title: `${invalidWindowSamples} raw sample${invalidWindowSamples === 1 ? "" : "s"} rejected`, detail: "The original LinuxPTP values remain in the API but are excluded from RMS and charts.", meta: "MEASUREMENT", section: "Analytics", icon: Activity });
    }

    if (agentStatus?.running && connection === "live") {
      const fresh = telemetryStatus?.phc_fresh_clocks ?? nodes.filter((node) => node.measured).length;
      items.push({ id: "phc-stream-live", level: "info", title: "Raw PHC monitoring is live", detail: `${fresh}/${nodes.length} hardware clocks fresh · no smoothing or clock control`, meta: newestSampleAge == null ? "LIVE" : `${newestSampleAge.toFixed(1)} s AGO`, section: "Analytics", icon: Radio });
    } else if (agentStatus?.running) {
      items.push({ id: `stream-${connection}`, level: "warn", title: connection === "waiting" ? "Waiting for timing samples" : "Raw measurement stream is not live", detail: "The agent is running; check PTP process state and the latest LinuxPTP logs.", meta: connection.toUpperCase(), section: "Overview", icon: Activity });
    } else if (agentStatus) {
      items.push({ id: "cascade-stopped", level: "warn", title: "Timing cascade is stopped", detail: "Observation remains available, but no managed LinuxPTP processes are running.", meta: "SYSTEM", section: "Overview", icon: Square });
    }

    const locked = receivers.filter((node) => node.state === "LOCKED").length;
    if (receivers.length && locked === receivers.length) {
      items.push({ id: "all-receivers-locked", level: "good", title: "All downstream clocks are locked", detail: `${locked}/${receivers.length} receivers report LinuxPTP servo state s2.`, meta: "HEALTH", section: "Overview", icon: ShieldCheck });
    }

    const servoCounts = receivers.reduce<Record<string, number>>((counts, node) => {
      const type = node.servoType?.toUpperCase();
      if (type && node.servoEnabled !== false) counts[type] = (counts[type] ?? 0) + 1;
      return counts;
    }, {});
    const servoSummary = Object.entries(servoCounts).map(([type, count]) => `${count} ${type}`).join(" · ");
    if (servoSummary) {
      const signature = Object.entries(servoCounts).map(([type, count]) => `${type}-${count}`).join("-");
      items.push({ id: `servo-profile-${signature}`, level: "info", title: "Servo profile active", detail: servoSummary, meta: "CONFIGURATION", section: "Configuration", servoTarget: "all", icon: SlidersHorizontal });
    }

    return items.slice(0, 6);
  }, [agentStatus, connection, invalidWindowSamples, newestSampleAge, nodes, telemetryStatus?.phc_fresh_clocks]);

  const unreadNotificationCount = notifications.filter((item) => item.level === "warn" && !readNotificationIds.includes(item.id)).length;

  const stats = useMemo(() => {
    const final = nodes[nodes.length - 1];
    const values = history.flatMap((point) => Object.values(point.values)).filter(Number.isFinite);
    const peak = values.length ? Math.max(...values.map(Math.abs)) : 0;
    const receiverCount = nodes.filter((node) => node.role !== "Grandmaster").length;
    const locked = nodes.filter((node) => node.role !== "Grandmaster" && node.state === "LOCKED").length;
    if (connection !== "simulation") {
      return [
        { label: "Endpoint servo RMS", value: final.ptpMeasured ? formatNanoseconds(final.rms) : "—", delta: "PTP RAW", note: `${final.servoSampleCount} valid offset samples`, icon: Activity, good: final.ptpMeasured },
        { label: "Peak PHC difference", value: values.length ? formatNanoseconds(peak) : "—", delta: "UNFILTERED", note: `${range} vs BC1`, icon: Zap, good: values.length > 0 },
        { label: "Locked receivers", value: `${locked}/${receiverCount}`, delta: telemetryStatus?.mode.toUpperCase() ?? "WAITING", note: "LinuxPTP servo state", icon: ShieldCheck, good: locked === receiverCount && receiverCount > 0 },
        { label: "PHC comparisons", value: bufferedPhcComparisons.toLocaleString(), delta: "NO CONTROL", note: "read-only browser buffer", icon: TimerReset, good: bufferedPhcComparisons > 0 },
      ];
    }
    return [
      { label: "Modeled RMS", value: `${final.rms.toFixed(1)} ns`, delta: "SIM", note: "not a measurement", icon: Activity, good: false },
      { label: "Modeled peak", value: `${peak.toFixed(0)} ns`, delta: "SIM", note: "not a measurement", icon: Zap, good: false },
      { label: "Modeled locks", value: `${locked}/${receiverCount}`, delta: "SIM", note: "not hardware state", icon: ShieldCheck, good: false },
      { label: "Generated samples", value: history.length.toLocaleString(), delta: "SIM", note: "deterministic fallback", icon: TimerReset, good: false },
    ];
  }, [bufferedPhcComparisons, connection, history, nodes, range, telemetryStatus]);

  const selectNode = (id: string) => {
    setSelectedNode(id);
    setVisibleTraces((current) => (current.includes(id) ? current : [...current.slice(-3), id]));
  };

  const commandItems = useMemo<CommandItem[]>(() => [
    { id: "nav-overview", group: "Navigate", label: "Cascade overview", description: "Topology, offsets, lock state, and live PHC traces", keywords: "home cascade topology clocks", section: "Overview", icon: LayoutDashboard },
    { id: "nav-pendulum", group: "Navigate", label: "Multi-pendulum", description: "Coupled previous-hop phase residuals", keywords: "swing equilibrium phase", section: "Multi-pendulum", icon: Orbit },
    { id: "nav-covariance", group: "Navigate", label: "Covariance lab", description: "Pair relationships and dominant eigenmodes", keywords: "matrix correlation eigenvalues", section: "Covariance", icon: Network },
    { id: "nav-state", group: "Navigate", label: "State-space atlas", description: "Modal trajectory and empirical Poincaré map", keywords: "pca poincare phase portrait", section: "State space", icon: Activity },
    { id: "nav-metrology", group: "Navigate", label: "Metrology workbench", description: "Stability statistics, factor fusion, ensemble time, and run recorder", keywords: "adev mdev tdev hdev mtie theo1 uncertainty experiment", section: "Metrology", icon: TimerReset },
    { id: "nav-path", group: "Navigate", label: "Path microscope", description: "Raw t1/t2 and t3/t4 LinuxPTP exchange timestamps", keywords: "packet sync delay timestamps asymmetry pps", section: "Path microscope", icon: Radio },
    { id: "nav-intelligence", group: "Navigate", label: "Control intelligence", description: "Adaptive Kalman, bifurcation, recurrence, fractal scaling, and Koopman", keywords: "kalman drift model auto tune bocpd bifurcation gain sweep recurrence fractal higuchi correlation dimension multifractal mfdfa dmd holdover", section: "Intelligence", icon: Gauge },
    { id: "nav-resilience", group: "Navigate", label: "Resilience lab", description: "Profiles, DPLL, SyncE, authentication, and bounded faults", keywords: "security profile synce dpll fault injection netem", section: "Resilience", icon: ShieldCheck },
    { id: "nav-analytics", group: "Navigate", label: "Timing analytics", description: "Raw PHC statistics, RMS, and exports", keywords: "graphs measurements rms raw", section: "Analytics", icon: BarChart3 },
    { id: "nav-experiments", group: "Navigate", label: "Experiments", description: "Step, wander, holdover, and gain-sweep recipes", keywords: "test run servo", section: "Experiments", icon: FlaskConical },
    { id: "nav-interfaces", group: "Navigate", label: "Interfaces & PHCs", description: "NIC, namespace, link, and timestamp inventory", keywords: "hardware ports network nic", section: "Interfaces", icon: Cable },
    { id: "nav-config", group: "Navigate", label: "Configuration", description: "PTP profile, servo, PPS I/O, ts2phc, and safety controls", keywords: "settings tune apply pulse", section: "Configuration", icon: SlidersHorizontal },
    { id: "nav-events", group: "Navigate", label: "Event log", description: "Clock, measurement, and operator events", keywords: "logs terminal activity", section: "Event log", icon: Terminal },
    ...nodes.map((node) => ({ id: `clock-${node.id}`, group: "Clocks" as const, label: node.label, description: `${node.role} · ${node.phc} · ${node.state}`, keywords: `${node.id} ${node.role} ${node.phc} ${node.ingress} ${node.egress} offset`, section: "Overview" as Section, nodeId: node.id, icon: Clock3 })),
    { id: "control-servo", group: "Controls", label: "Servo & holdover", description: "Select discipline or freeze adjustment while observing", keywords: "pi linreg kalman adaptive imm filter nullf stop start holdover", section: "Configuration", action: "servo-control", icon: Gauge },
    { id: "control-frequency", group: "Controls", label: "Synchronization frequency", description: `${syncFrequencyHz.toFixed(1)} Hz requested · ${effectiveSyncFrequencyHz} Hz effective`, keywords: "sync rate interval hertz frequency logSyncInterval", section: "Configuration", action: "sync-frequency", icon: Radio },
    { id: "control-pps", group: "Controls", label: "PPS & ts2phc", description: `${ppsStateLabel} · ${ppsSource === "external" ? "external source" : `${ppsSource} output`} · ${ppsSinks.length} inputs`, keywords: "pps pulse per second input output extts perout ts2phc pin", section: "Configuration", action: "pps-control", icon: Zap },
    { id: "control-notifications", group: "Controls", label: "Notification center", description: `${unreadNotificationCount} unread timing alert${unreadNotificationCount === 1 ? "" : "s"}`, keywords: "bell alerts warnings health", action: "notifications", icon: Bell },
    { id: "control-apply", group: "Controls", label: "Review & apply settings", description: "Validate, stage, restart, and verify the cascade", keywords: "save configuration restart", action: "apply", icon: ShieldCheck },
  ], [effectiveSyncFrequencyHz, nodes, ppsSinks.length, ppsSource, ppsStateLabel, syncFrequencyHz, unreadNotificationCount]);

  const filteredCommandItems = useMemo(() => {
    const query = commandQuery.trim().toLowerCase();
    if (!query) return commandItems;
    const terms = query.split(/\s+/);
    return commandItems.filter((item) => {
      const searchable = `${item.label} ${item.description} ${item.keywords}`.toLowerCase();
      return terms.every((term) => searchable.includes(term));
    });
  }, [commandItems, commandQuery]);

  const activeCommandId = filteredCommandItems[commandIndex]?.id;

  useEffect(() => {
    if (!commandOpen || !activeCommandId) return;
    window.requestAnimationFrame(() => document.getElementById(`command-option-${activeCommandId}`)?.scrollIntoView({ block: "nearest" }));
  }, [activeCommandId, commandOpen]);

  const openCommandPalette = () => {
    setNotificationsOpen(false);
    setApplyOpen(false);
    setCommandQuery("");
    setCommandIndex(0);
    setCommandOpen(true);
  };

  const runCommand = (item: CommandItem) => {
    if (item.section) setSection(item.section);
    if (item.nodeId) selectNode(item.nodeId);
    if (item.action === "notifications") setNotificationsOpen(true);
    if (item.action === "apply") setApplyOpen(true);
    if (item.action === "servo-control" || item.action === "sync-frequency" || item.action === "pps-control") {
      const target = item.action === "sync-frequency" ? "sync-frequency-control" : item.action === "pps-control" ? "pps-control" : "servo-control";
      window.setTimeout(() => document.getElementById(target)?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
    }
    setCommandOpen(false);
    setCommandQuery("");
  };

  const zeroPendulum = () => {
    const baselines: Record<string, number> = {};
    const sampleTimes: number[] = [];
    for (const node of nodes.slice(1)) {
      const point = [...history].reverse().find((item) => Number.isFinite(item.hopValues?.[node.id]));
      const value = point?.hopValues?.[node.id];
      if (Number.isFinite(value)) {
        baselines[node.id] = value as number;
        sampleTimes.push(point!.t);
      } else if (node.measured && Number.isFinite(node.hopOffset)) {
        baselines[node.id] = node.hopOffset as number;
      }
    }
    if (!Object.keys(baselines).length) {
      setToast("No measured hop deltas are available to zero");
      return;
    }
    setPendulumZeroState({ at: sampleTimes.length ? Math.min(...sampleTimes) : null, baselines });
    setToast(`${Object.keys(baselines).length} pendulum links zeroed to the current PHC equilibrium`);
  };

  const selectServoTarget = (target: string) => {
    setServoTarget(target);
    const targets = target === "all" ? nodes.filter((node) => node.role !== "Grandmaster") : nodes.filter((node) => node.id === target);
    const types = [...new Set(targets.map((node) => node.servoType).filter((value): value is ServoType => Boolean(value)))];
    if (types.length === 1) setServoType(types[0]);
  };

  const openNotification = (item: ObservatoryNotification) => {
    setReadNotificationIds((current) => current.includes(item.id) ? current : [...current, item.id]);
    setNotificationsOpen(false);
    setSection(item.section);
    if (item.nodeId) selectNode(item.nodeId);
    if (item.servoTarget) selectServoTarget(item.servoTarget);
  };

  const openEventLog = () => {
    setReadNotificationIds((current) => [...new Set([...current, ...notifications.map((item) => item.id)])]);
    setNotificationsOpen(false);
    setSection("Event log");
  };

  const downloadRawCsv = () => {
    const rows = ["timestamp_utc,clock,phc_offset_vs_bc1_ns"];
    history.forEach((point) => Object.entries(point.values).forEach(([clock, offset]) => rows.push(`${new Date(point.t * 1000).toISOString()},${clock},${offset}`)));
    const url = URL.createObjectURL(new Blob([`${rows.join("\n")}\n`], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `ptpbox-phc-comparison-${new Date().toISOString().replaceAll(":", "-")}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const controlCascade = async (action: "start" | "stop") => {
    setControlBusy(true);
    try {
      const response = await fetch(`${agentBaseUrl()}/api/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const result = await response.json() as { error?: string };
      if (!response.ok) throw new Error(result.error || `cascade ${action} failed`);
      setAgentStatus((current) => ({ ...current, running: action === "start" }));
      if (action === "start") {
        setToast("Cascade started · waiting for the first raw LinuxPTP samples");
        setConnection("waiting");
      } else {
        setToast("Cascade stopped · captured raw samples remain available");
        setConnection("stale");
      }
    } catch (error) {
      setToast(error instanceof Error ? error.message : `Cascade ${action} is unavailable`);
    } finally {
      setControlBusy(false);
    }
  };

  const controlServo = async (enabled: boolean) => {
    setServoBusy(true);
    try {
      const response = await fetch(`${agentBaseUrl()}/api/servo/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: servoTarget, enabled, type: servoType }),
      });
      const result = await response.json() as { error?: string; changed?: string[] };
      if (!response.ok) throw new Error(result.error || "servo transition failed");
      const targets = result.changed?.join(", ") || servoTarget;
      setToast(enabled ? `${targets}: ${servoType.toUpperCase()} discipline active` : `${targets}: holdover started · PHC monitoring continues`);
      setAgentStatus((current) => current ? { ...current } : current);
    } catch (error) {
      setToast(error instanceof Error ? error.message : "Servo control is unavailable");
    } finally {
      setServoBusy(false);
    }
  };

  const handleApply = async () => {
    const comparisonSinks = ppsSinks.filter((id) => id !== ppsSource);
    const comparisonReference = comparisonSinks.includes(ppsComparisonReference) ? ppsComparisonReference : comparisonSinks[0] ?? "BC2";
    const payload = {
      profile,
      domain: profileProtocol.domain,
      transport: profileProtocol.transport,
      delay_mechanism: profileProtocol.delay,
      log_sync_interval: syncLogInterval,
      two_step: twoStep,
      hardware_timestamping: hardwareTs,
      servo: {
        // Per-clock servo selection is persisted separately by /api/servo/control.
        // A cadence-only apply must not replace that state with the UI's currently
        // selected target servo.
        type: configuredServoTypeRef.current,
        kp,
        ki,
        step_threshold_ns: stepThreshold,
        first_step_threshold_ns: 20_000,
        sanity_freq_limit_ppb: sanity ? 200_000 : 0,
        kalman: {
          measurement_noise_ns: kalmanMeasurementNoiseNs,
          process_noise_ppb: kalmanProcessNoisePpb,
          phase_time_constant_s: kalmanPhaseTimeConstantS,
          innovation_gate_sigma: kalmanInnovationGateSigma,
          drift_noise_ppb_s2: kalmanDriftNoisePpbS2,
        },
      },
      security: {
        authentication: {
          enabled: authenticationEnabled,
          spp: 0,
          active_key_id: 1,
          sa_file: "/etc/linuxptp/ptpbox-sa.cfg",
          allow_unauth: 0,
        },
      },
      pps: {
        enabled: ppsEnabled,
        source: ppsSource,
        sinks: comparisonSinks,
        output_pin: ppsOutputPin,
        input_pin: ppsInputPin,
        channel: ppsChannel,
        polarity: ppsPolarity,
        pulse_width_ns: ppsPulseWidthNs,
        perout_phase_ns: ppsPhaseNs,
        extts_correction_ns: ppsCorrectionNs,
        comparison: {
          enabled: ppsComparisonEnabled,
          measure_only: true,
          reference: comparisonReference,
          history: 256,
        },
        ts2phc: {
          servo: ppsServo,
          kp: 0.7,
          ki: 0.3,
          step_threshold_ns: ppsStepThresholdNs,
          first_step_threshold_ns: ppsFirstStepThresholdNs,
          holdover_seconds: ppsHoldoverSeconds,
          stable_threshold_ns: ppsStableThresholdNs,
          stable_samples: 10,
          logging_level: 6,
        },
      },
    };
    setApplyBusy(true);
    if (agentStatus) {
      try {
        const response = await fetch(`${agentBaseUrl()}/api/config/apply`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json() as { error?: string; details?: string[] };
        if (!response.ok) throw new Error(result.details?.join(" · ") || result.error || "agent rejected configuration");
        setActiveSyncFrequencyHz(effectiveSyncFrequencyHz);
        setPhcSampleRateHz(effectiveSyncFrequencyHz);
        if (agentStatus.running) {
          const restartResponse = await fetch(`${agentBaseUrl()}/api/control`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "restart" }),
          });
          const restart = await restartResponse.json() as { error?: string };
          if (!restartResponse.ok) throw new Error(`Configuration staged, but restart failed: ${restart.error || "control helper rejected restart"}`);
          setConnection("waiting");
          setToast(`Cascade restarted · Sync ${effectiveSyncFrequencyHz} Hz · log interval ${syncLogInterval}`);
        } else {
          setToast(`Configuration staged · Sync ${effectiveSyncFrequencyHz} Hz will apply on next start`);
        }
      } catch (error) {
        setToast(error instanceof Error ? error.message : "Host configuration is unavailable");
      }
    } else {
      setToast("Configuration validated in hardware-model mode");
    }
    setApplyOpen(false);
    setApplyBusy(false);
  };

  const toggleExperiment = async () => {
    const starting = !experimentRunning;
    setExperimentBusy(true);
    try {
      if (!agentStatus) {
        setExperimentRunning(starting);
        setToast("Experiment controls require the live appliance");
        return;
      }
      const response = await fetch(`${agentBaseUrl()}${starting ? "/api/experiments/start" : "/api/experiments/stop"}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(starting
            ? { name: `Raw cascade capture · ${servoType.toUpperCase()}`, kind: "metrology", target: servoTarget, servo: { type: servoType, kp, ki, step_threshold_ns: stepThreshold } }
            : { id: activeResearch.active_experiment?.id }),
      });
      const result = await response.json() as ResearchExperiment & { error?: string };
      if (!response.ok) throw new Error(result.error || "experiment transition failed");
      setExperimentRunning(starting);
      if (starting && experimentProgress >= 100) setExperimentProgress(0);
      setResearch((current) => current ? { ...current, active_experiment: starting ? result : null, experiments: starting ? [result, ...current.experiments] : current.experiments.map((run) => run.id === result.id ? result : run) } : current);
      setToast(starting ? `${result.id}: raw metrology capture started` : `${result.id}: capture sealed and ready to export`);
    } catch (error) {
      setToast(error instanceof Error ? error.message : "Experiment control is unavailable");
    } finally {
      setExperimentBusy(false);
    }
  };

  const exportExperiment = (id: string) => {
    window.open(`${agentBaseUrl()}/api/experiments/${encodeURIComponent(id)}/export`, "_blank", "noopener,noreferrer");
  };

  const stageTune = (nextKp: number, nextKi: number) => {
    setKp(nextKp);
    setKi(nextKi);
    setSection("Configuration");
    setToast(`Replay-safe PI recommendation staged · Kp ${nextKp.toFixed(2)} · Ki ${nextKi.toFixed(2)}`);
    window.setTimeout(() => document.getElementById("servo-control")?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  };

  const controlFault = async (enabled: boolean) => {
    setFaultBusy(true);
    try {
      const response = await fetch(`${agentBaseUrl()}/api/fault/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: faultTarget, enabled, delay_us: faultDelayUs, jitter_us: faultJitterUs, loss_pct: faultLossPct, duration_s: faultDurationS }),
      });
      const result = await response.json() as { error?: string };
      if (!response.ok) throw new Error(result.error || "fault control failed");
      setFaultActive(enabled);
      setToast(enabled ? `${faultTarget}: bounded fault active for ${faultDurationS} s` : `${faultTarget}: fault cleared`);
    } catch (error) {
      setToast(error instanceof Error ? error.message : "Fault control is unavailable");
    } finally {
      setFaultBusy(false);
    }
  };

  const navItems: { label: Section; icon: typeof LayoutDashboard; badge?: string }[] = [
    { label: "Overview", icon: LayoutDashboard },
    { label: "Multi-pendulum", icon: Orbit },
    { label: "Covariance", icon: Network },
    { label: "State space", icon: Activity },
    { label: "Metrology", icon: TimerReset, badge: activeResearch.active_experiment ? "REC" : undefined },
    { label: "Path microscope", icon: Radio },
    { label: "Intelligence", icon: Gauge },
    { label: "Resilience", icon: ShieldCheck, badge: faultActive ? "LIVE" : undefined },
    { label: "Analytics", icon: BarChart3 },
    { label: "Experiments", icon: FlaskConical, badge: experimentRunning ? "RUN" : undefined },
    { label: "Interfaces", icon: Cable },
    { label: "Configuration", icon: SlidersHorizontal },
    { label: "Event log", icon: Terminal, badge: unreadNotificationCount ? String(unreadNotificationCount) : undefined },
  ];

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
        <div className="brand-block">
          <span className="brand-mark" role="img" aria-label="PTPBox hardware logo" />
          <div><strong>PTPBOX</strong><small>PRECISION TIME LAB</small></div>
          <button className="icon-button close-nav" type="button" onClick={() => setMobileNav(false)} aria-label="Close navigation"><X size={18} /></button>
        </div>
        <div className="lab-switcher">
          <div className="lab-icon"><Boxes size={17} /></div>
          <div><small>ACTIVE LAB</small><strong>Cascade A · 7 hops</strong></div>
          <ChevronDown size={15} />
        </div>
        <nav className="primary-nav" aria-label="Primary navigation">
          <span className="nav-label">OBSERVE & CONTROL</span>
          {navItems.map((item) => (
            <button key={item.label} type="button" className={section === item.label ? "active" : ""} onClick={() => { setSection(item.label); setMobileNav(false); }}>
              <item.icon size={17} /><span>{item.label}</span>{item.badge && <em>{item.badge}</em>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="host-card">
            <div className="host-title"><span className={`status-orb ${connection}`} /> <strong>{hostStateLabel}</strong></div>
            <div className="host-row"><span>PTPBox</span><code>192.168.1.60</code></div>
            <div className="host-row"><span>LinuxPTP</span><code>{agentStatus?.linuxptp ?? "4.4"}</code></div>
            <div className="host-row"><span>PTP ports</span><code>{agentStatus?.ptp_interfaces ?? 16}</code></div>
            <div className="host-row"><span>Cascade</span><code>{connection === "live" || agentStatus?.running ? "RUNNING" : "STOPPED"}</code></div>
          </div>
          <div className="user-row"><div className="avatar">AB</div><div><strong>Lab operator</strong><small>Administrator</small></div><Settings2 size={16} /></div>
        </div>
      </aside>

      {mobileNav && <button className="nav-backdrop" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}

      <main className="main-shell">
        <header className="topbar">
          <div className="topbar-left">
            <button className="icon-button menu-button" type="button" onClick={() => setMobileNav(true)} aria-label="Open navigation"><Menu size={19} /></button>
            <span className="breadcrumb">PTPBOX <b>/</b> CASCADE A <b>/</b> <strong>{section.toUpperCase()}</strong></span>
          </div>
          <div className="topbar-actions">
            <button className="search-box" type="button" aria-haspopup="dialog" aria-expanded={commandOpen} aria-controls="command-palette" onClick={openCommandPalette}><Search size={15} /><span>Search or jump to…</span><kbd>⌘ K</kbd></button>
            <span className="utc-clock"><Clock3 size={14} /> {time || "--:--:--"}</span>
            <div className="notification-center" ref={notificationCenterRef}>
              <button className={`icon-button notification ${notificationsOpen ? "open" : ""}`} type="button" aria-label={`Notifications, ${unreadNotificationCount} unread`} aria-expanded={notificationsOpen} aria-controls="notification-panel" onClick={() => setNotificationsOpen((value) => !value)}>
                <Bell size={17} />
                {unreadNotificationCount > 0 && <span className="notification-count warning">{unreadNotificationCount > 9 ? "9+" : unreadNotificationCount}</span>}
              </button>
              {notificationsOpen && (
                <section className="notification-panel" id="notification-panel" aria-label="Notification center">
                  <header className="notification-panel-header">
                    <div><span className="section-kicker">LIVE OBSERVATORY</span><strong>Notifications</strong></div>
                    <div>
                      <button type="button" className="notification-mark-read" disabled={!unreadNotificationCount} onClick={() => setReadNotificationIds((current) => [...new Set([...current, ...notifications.map((item) => item.id)])])}>Mark all read</button>
                      <button type="button" className="notification-close" aria-label="Close notifications" onClick={() => setNotificationsOpen(false)}><X size={15} /></button>
                    </div>
                  </header>
                  <div className="notification-list">
                    {notifications.map((item) => (
                      <button type="button" className={`notification-item ${item.level} ${item.level === "warn" ? readNotificationIds.includes(item.id) ? "read" : "unread" : "status"}`} key={item.id} onClick={() => openNotification(item)}>
                        <span className="notification-symbol"><item.icon size={15} /></span>
                        <span className="notification-copy"><span>{item.meta}</span><strong>{item.title}</strong><small>{item.detail}</small></span>
                        <ArrowRight size={14} />
                      </button>
                    ))}
                  </div>
                  <button type="button" className="notification-footer" onClick={openEventLog}><Terminal size={14} /> Open full event log <ArrowRight size={13} /></button>
                </section>
              )}
            </div>
            <button className="primary-action" type="button" onClick={() => { setNotificationsOpen(false); setApplyOpen(true); }}><SlidersHorizontal size={15} /> Apply settings</button>
          </div>
        </header>

        <div className="content-shell">
          <div className="page-heading">
            <div>
              <div className="eyebrow"><span className={`status-orb ${connection}`} /> {dataModeLabel}</div>
              <h1>{SECTION_META[section].title}</h1>
              <p>{SECTION_META[section].description}</p>
            </div>
            <div className="heading-actions">
              <button className="secondary-button" type="button" onClick={() => setToast("Snapshot saved to run 024")}><Download size={15} /> Snapshot</button>
              <button className={`live-control ${paused ? "paused" : ""}`} type="button" onClick={() => setPaused((value) => !value)}>{paused ? <Play size={14} /> : <Pause size={14} />} {paused ? "Resume" : connection === "simulation" ? "Simulating" : "Raw stream"}</button>
            </div>
          </div>

          <div className={`data-provenance ${connection}`}>
            <span><Radio size={13} /> {connection === "simulation" ? "DETERMINISTIC FALLBACK" : connection === "checking" ? "PTPBOX AGENT PROBE" : "CROSS-TIMESTAMPED PHC COMPARISON"}</span>
            <code>{connection === "simulation" ? "synthetic" : connection === "checking" ? "measurement mode pending" : activeNode.phcMethod ?? "read-only kernel cross timestamps"}</code>
            <span>{bufferedPhcComparisons.toLocaleString()} PHC samples buffered</span>
            {invalidWindowSamples > 0 && <span className="rejected-samples">{invalidWindowSamples} ptp4l samples rejected</span>}
            <span>{newestSampleAge === null ? "no raw sample yet" : `newest ${newestSampleAge.toFixed(1)} s ago`}</span>
            {agentStatus && connection !== "simulation" && (
              <button
                type="button"
                className={`quiet-button cascade-control ${agentStatus.running ? "running" : ""}`}
                disabled={controlBusy}
                onClick={() => void controlCascade(agentStatus.running ? "stop" : "start")}
              >
                {agentStatus.running ? <Square size={11} fill="currentColor" /> : <Play size={12} />}
                {controlBusy ? (agentStatus.running ? "Stopping…" : "Starting…") : (agentStatus.running ? "Stop cascade" : "Start cascade")}
              </button>
            )}
          </div>

          {section === "Overview" && (
            <div className="overview-layout">
              <section className="instrument-panel topology-panel">
                <div className="panel-heading">
                  <div><span className="section-kicker">PHYSICAL TOPOLOGY</span><h2>Seven-stage clock cascade</h2></div>
                  <div className="panel-tools"><span><Radio size={13} /> {nodes.length} clocks · {Math.max(0, nodes.length - 1)} measured hops</span><button className="quiet-button" type="button"><RefreshCw size={14} /> Rediscover</button></div>
                </div>
                <div className="topology-scroll">
                  <div className="topology-track">
                    {nodes.map((node, index) => (
                      <div className="node-unit" key={node.id}>
                        {index > 0 && <div className="hop-link"><span className="signal-dot one" /><span className="signal-dot two" /><small>H{index} · {formatOffset(node.hopOffset ?? 0, node.measured)}</small></div>}
                        <button type="button" onClick={() => selectNode(node.id)} className={`clock-node ${selectedNode === node.id ? "selected" : ""} ${node.state === "TRACKING" ? "tracking" : ""} ${node.state === "HOLDOVER" ? "holdover" : ""} ${node.state === "FAULTY" ? "faulty" : ""} ${node.state === "STALE" || node.state === "NO DATA" ? "stale" : ""}`}>
                          <div className="node-topline"><span>{node.role === "Boundary" ? "BC" : node.role === "Grandmaster" ? "GM" : "OC"}</span><i style={{ background: node.color }} /></div>
                          <div className="node-symbol"><Clock3 size={20} strokeWidth={1.4} /><span className="pulse-ring" /></div>
                          <strong>{node.label}</strong>
                          <span className="node-offset">{formatOffset(node.offset, node.measured)}</span>
                          <div className="node-state"><span /> {node.state}</div>
                          <div
                            className={`node-pps ${agentStatus?.pps?.nodes[node.id]?.state ?? "checking"} ${agentStatus?.pps?.nodes[node.id]?.role ?? "disabled"}`}
                            title={agentStatus?.pps?.nodes[node.id]?.pin ? `${agentStatus?.pps?.nodes[node.id]?.device} · ${agentStatus?.pps?.nodes[node.id]?.pin?.name} · channel ${agentStatus?.pps?.nodes[node.id]?.channel}` : "Reading hardware PPS capability"}
                          >
                            <Zap size={8} /> {ppsNodeLabel(node.id)}
                          </div>
                          <div className="node-ports"><code>{node.ingress}</code><ArrowRight size={11} /><code>{node.egress}</code></div>
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="topology-footer">
                  <span><i className="legend-dot locked" /> Locked</span><span><i className="legend-dot tracking" /> Tracking</span><span><i className="legend-line" /> Measured hop</span>
                  <p><Info size={13} /> PTP packets remain the only synchronization reference over the wires. The PHC comparison sampler is read-only; a selected Kalman worker controls only its mapped PHC.</p>
                </div>
              </section>

              <div className="metric-grid">
                {stats.map((stat) => (
                  <article className="metric-card" key={stat.label}>
                    <div className="metric-card-top"><span>{stat.label}</span><stat.icon size={16} /></div>
                    <strong>{stat.value}</strong>
                    <div><em className={stat.good ? "good" : "warn"}>{stat.delta}</em><span>{stat.note}</span></div>
                  </article>
                ))}
              </div>

              <div className="overview-grid">
                <section className="instrument-panel chart-panel">
                  <div className="panel-heading chart-heading">
                    <div><span className="section-kicker">RAW PHC DIFFERENCE</span><h2>NIC clocks relative to BC1</h2></div>
                    <div className="chart-heading-tools"><span className="phc-rate-badge"><Radio size={12} /> {phcSampleRateHz.toFixed(1)} Hz PHC sampling</span><div className="segmented-control">{["30 s", "2 min", "15 min"].map((item) => <button className={range === item ? "active" : ""} type="button" key={item} onClick={() => setRange(item)}>{item}</button>)}</div></div>
                  </div>
                  <div className="chart-legend">
                    {visibleTraces.map((id) => {
                      const node = nodes.find((item) => item.id === id);
                      return node ? <button type="button" key={id} onClick={() => selectNode(id)} className={selectedNode === id ? "active" : ""}><i style={{ background: node.color }} /> {node.label}<strong>{formatOffset(node.offset, node.measured)}</strong></button> : null;
                    })}
                    <span className="chart-unit">PHC Δ VS BC1 · AUTO-SCALED</span>
                  </div>
                  <LineChart data={history} selected={visibleTraces} nodes={nodes} />
                  <div className="chart-footer-note"><Sparkles size={14} /><span><strong>Provenance:</strong> Kernel cross timestamps place every PHC at a common epoch; BC1 is interpolated only between its two bracketing reads. Read-only PHC sampling follows the applied Sync cadence at {phcSampleRateHz.toFixed(1)} Hz; no phc2sys loop or smoothing is involved.</span><button type="button" onClick={() => setSection("Analytics")}>Inspect <ArrowRight size={13} /></button></div>
                </section>

                <section className="instrument-panel selected-panel">
                  <div className="panel-heading">
                    <div><span className="section-kicker">SELECTED CLOCK</span><h2>{activeNode.label}</h2></div>
                    <button className="more-button" type="button">•••</button>
                  </div>
                  <div className="selected-status">
                    <div className="radial-score"><span>{activeNode.ptpMeasured ? activeNode.servoSampleCount : 0}</span><small>PTP SAMPLES</small></div>
                    <div><span className={`locked-pill ${activeNode.state === "HOLDOVER" ? "holdover" : ""}`}>{activeNode.state === "HOLDOVER" ? <Pause size={12} /> : <Check size={12} />} {activeNode.state}</span><strong>{formatOffset(activeNode.offset, activeNode.measured)}</strong><small>direct PHC difference vs BC1 · {activeNode.phc}</small></div>
                  </div>
                  <div className="selected-metrics">
                    <div><span>Servo offset RMS</span><strong>{activeNode.ptpMeasured ? formatNanoseconds(activeNode.rms) : "—"}</strong></div>
                    <div><span>Previous-hop PHC Δ</span><strong>{formatOffset(activeNode.hopOffset ?? 0, activeNode.measured)}</strong></div>
                    <div><span>PTP path delay</span><strong>{activeNode.ptpMeasured ? `${activeNode.meanPathDelay} ns` : "—"}</strong></div>
                    <div><span>PTP frequency adj.</span><strong>{activeNode.ptpMeasured ? `${activeNode.frequencyPpb >= 0 ? "+" : ""}${activeNode.frequencyPpb.toFixed(1)} ppb` : "—"}</strong></div>
                    <div><span>Comparison error bound</span><strong>{activeNode.phcUncertainty == null ? "—" : `≤ ${formatNanoseconds(activeNode.phcUncertainty)}`}</strong></div>
                    <div><span>PHC comparisons</span><strong>{activeNode.sampleCount}</strong></div>
                    <div><span>Servo mode</span><strong>{activeNode.role === "Grandmaster" ? "REFERENCE" : activeNode.servoEnabled === false ? "HOLDOVER" : activeNode.servoType?.toUpperCase() ?? "—"}</strong></div>
                    <div><span>Holdover drift{holdoverElapsedSeconds == null ? "" : ` · ${holdoverElapsedSeconds} s`}</span><strong>{holdoverMetrics?.driftPpb == null ? "—" : `${holdoverMetrics.driftPpb >= 0 ? "+" : ""}${holdoverMetrics.driftPpb.toFixed(2)} ppb`}</strong></div>
                    {["kalman", "adaptive-kalman", "imm"].includes(activeNode.servoType ?? "") && <div><span>{activeNode.servoType === "imm" ? "IMM phase / regime" : "Kalman phase estimate"}</span><strong>{activeNode.kalman?.fresh ? `${formatNanoseconds(activeNode.kalman.phase_estimate_ns)}${activeNode.kalman.regime ? ` · ${activeNode.kalman.regime}` : ""}` : "ACQUIRING"}</strong></div>}
                    {activeNode.servoType === "kalman" && <div><span>Oscillator estimate</span><strong>{activeNode.kalman?.fresh ? `${activeNode.kalman.frequency_estimate_ppb >= 0 ? "+" : ""}${activeNode.kalman.frequency_estimate_ppb.toFixed(2)} ppb` : "—"}</strong></div>}
                  </div>
                  <div className="servo-mini">
                    <div><span>{selectedServoLabel}</span><strong>{selectedServoDescription}</strong></div>
                    <div className="servo-rail"><i style={{ width: `${selectedServoRail}%` }} /></div>
                  </div>
                  <button className="full-secondary" type="button" onClick={() => setSection("Configuration")}><Settings2 size={14} /> Tune this clock <ArrowRight size={14} /></button>
                </section>
              </div>
            </div>
          )}

          {section === "Multi-pendulum" && (
            <MultiPendulum
              history={history}
              nodes={nodes}
              autoZero={pendulumAutoZero}
              setAutoZero={setPendulumAutoZero}
              zeroState={pendulumZeroState}
              onZero={zeroPendulum}
              paused={paused}
              connection={connection}
            />
          )}

          {section === "Covariance" && (
            <CovarianceLab
              history={history}
              nodes={nodes}
              range={range}
              setRange={setRange}
              paused={paused}
              connection={connection}
            />
          )}

          {section === "State space" && (
            <StateSpaceAtlas
              history={history}
              nodes={nodes}
              range={range}
              setRange={setRange}
              paused={paused}
              connection={connection}
            />
          )}

          {section === "Metrology" && (
            <MetrologyWorkbench
              research={activeResearch}
              nodes={nodes}
              metric={researchMetric}
              setMetric={setResearchMetric}
              experimentBusy={experimentBusy}
              toggleCapture={() => void toggleExperiment()}
              exportRun={exportExperiment}
            />
          )}

          {section === "Path microscope" && (
            <PathMicroscopeView research={activeResearch} nodes={nodes} />
          )}

          {section === "Intelligence" && (
            <IntelligenceWorkbench research={activeResearch} activeNode={activeNode} stageTune={stageTune} />
          )}

          {section === "Resilience" && (
            <ResilienceWorkbench
              research={activeResearch}
              authenticationEnabled={authenticationEnabled}
              setAuthenticationEnabled={setAuthenticationEnabled}
              profile={profile}
              setProfile={setProfile}
              faultTarget={faultTarget}
              setFaultTarget={setFaultTarget}
              faultDelayUs={faultDelayUs}
              setFaultDelayUs={setFaultDelayUs}
              faultJitterUs={faultJitterUs}
              setFaultJitterUs={setFaultJitterUs}
              faultLossPct={faultLossPct}
              setFaultLossPct={setFaultLossPct}
              faultDurationS={faultDurationS}
              setFaultDurationS={setFaultDurationS}
              faultActive={faultActive}
              faultBusy={faultBusy}
              controlFault={(enabled) => void controlFault(enabled)}
              nodes={nodes}
            />
          )}

          {section === "Analytics" && (
            <div className="analytics-layout">
              <section className="instrument-panel analytics-chart-panel">
                <div className="panel-heading">
                  <div><span className="section-kicker">STABILITY EXPLORER</span><h2>Direct PHC difference by NIC</h2></div>
                  <div className="panel-tools"><button className="quiet-button"><ListFilter size={14} /> Signals</button><button className="quiet-button" onClick={downloadRawCsv}><Download size={14} /> Raw CSV</button></div>
                </div>
                <div className="analytics-traces">
                  {nodes.map((node) => <button type="button" key={node.id} className={visibleTraces.includes(node.id) ? "active" : ""} onClick={() => setVisibleTraces((current) => current.includes(node.id) ? current.filter((item) => item !== node.id) : [...current, node.id])}><i style={{ background: node.color }} />{node.label}</button>)}
                </div>
                <LineChart data={history} selected={visibleTraces.length ? visibleTraces : nodes.length ? [nodes[nodes.length - 1].id] : []} nodes={nodes} />
              </section>
              <section className="instrument-panel distribution-panel">
                <div className="panel-heading"><div><span className="section-kicker">KERNEL CROSS TIMESTAMPS</span><h2>Endpoint PHC difference distribution</h2></div><span className="quality-badge">COMMON EPOCH</span></div>
                <div className="histogram" aria-label="Raw endpoint offset histogram">{endpointDistribution.bins.map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div>
                <div className="hist-axis"><span>{formatNanoseconds(endpointDistribution.min)}</span><span>common-epoch PHC comparisons · not servo RMS</span><span>{formatNanoseconds(endpointDistribution.max)}</span></div>
                <div className="distribution-stats"><div><span>PHC Δ σ</span><strong>{formatNanoseconds(endpointDistribution.sigma)}</strong></div><div><span>PHC Δ P95</span><strong>{formatNanoseconds(endpointDistribution.p95)}</strong></div><div><span>Skew</span><strong>{endpointDistribution.skew.toFixed(2)}</strong></div></div>
              </section>
              <section className="instrument-panel hop-table-panel">
                <div className="panel-heading"><div><span className="section-kicker">PHC + SERVO DATA</span><h2>Read-only clock comparisons</h2></div><span className="panel-meta">{range} raw window</span></div>
                <div className="data-table hop-table">
                  <div className="table-header"><span>Clock / PHC</span><span>Δ vs BC1</span><span>Hop Δ</span><span>Servo RMS</span><span>PTP frequency</span><span>PTP samples</span><span>PTP state</span></div>
                  {nodes.slice(1).map((node) => <button type="button" className="table-row" key={node.id} onClick={() => selectNode(node.id)}><span><i style={{ background: node.color }} />{node.label}<small>{node.phc}</small></span><strong>{formatOffset(node.offset, node.measured)}</strong><span>{formatOffset(node.hopOffset ?? 0, node.measured)}</span><span>{node.ptpMeasured ? formatNanoseconds(node.rms) : "—"}</span><span>{node.ptpMeasured ? `${node.frequencyPpb.toFixed(1)} ppb` : "—"}</span><span>{node.servoSampleCount.toLocaleString()}</span><em className={node.state === "LOCKED" ? "state-good" : node.state === "NO DATA" || node.state === "STALE" ? "state-off" : "state-warn"}>{node.state}</em></button>)}
                </div>
              </section>
            </div>
          )}

          {section === "Experiments" && (
            <div className="experiments-layout">
              <section className="experiment-hero">
                <div className="experiment-copy"><span className="section-kicker">ACTIVE EXPERIMENT · RUN 024</span><h2>PI servo step response</h2><p>Inject a controlled +1 μs time step at BC–03 and compare settling behavior across the remaining cascade.</p><div className="experiment-tags"><span>120 s capture</span><span>8 signals</span><span>Hardware timestamped</span></div></div>
                <div className="run-control">
                  <div className="run-progress"><div><span>{experimentRunning ? "CAPTURING" : experimentProgress === 100 ? "COMPLETE" : "READY"}</span><strong>{experimentRunning ? `${experimentProgress}%` : "02:00"}</strong></div><i><b style={{ width: `${experimentRunning ? experimentProgress : 0}%` }} /></i></div>
                  <button type="button" className="run-button" disabled={experimentBusy} onClick={toggleExperiment}>{experimentRunning ? <Square size={16} fill="currentColor" /> : <Play size={18} fill="currentColor" />}{experimentBusy ? "Transitioning…" : experimentRunning ? "Stop & seal capture" : "Start experiment"}</button>
                </div>
              </section>
              <section className="instrument-panel experiment-setup">
                <div className="panel-heading"><div><span className="section-kicker">EXPERIMENT DESIGN</span><h2>Stimulus & capture</h2></div><button className="quiet-button"><RotateCcw size={14} /> Reset</button></div>
                <div className="setup-grid">
                  <label><span>Stimulus</span><button className="select-control" type="button">Time step <ChevronDown size={14} /></button><small>A discrete phase offset at the target PHC.</small></label>
                  <label><span>Target clock</span><button className="select-control" type="button">BC–03 · ptp5 <ChevronDown size={14} /></button><small>Downstream clocks remain in closed loop.</small></label>
                  <label><span>Step amplitude</span><div className="input-unit"><input value="+1,000" readOnly /><em>ns</em></div><small>Applied 10 s after capture begins.</small></label>
                  <label><span>Capture duration</span><div className="input-unit"><input value="120" readOnly /><em>s</em></div><small>10 s pre-trigger, 110 s post-trigger.</small></label>
                </div>
              </section>
              <section className="instrument-panel servo-editor">
                <div className="panel-heading"><div><span className="section-kicker">SERVO UNDER TEST</span><h2>PI controller</h2></div><span className="dirty-badge">2 UNSAVED VALUES</span></div>
                <div className="servo-controls">
                  <label><div><span>Proportional constant</span><code>Kp {kp.toFixed(2)}</code></div><input type="range" min="0.1" max="1.2" step="0.05" value={kp} onChange={(event) => setKp(Number(event.target.value))} /><small>Faster response ↔ lower overshoot</small></label>
                  <label><div><span>Integral constant</span><code>Ki {ki.toFixed(2)}</code></div><input type="range" min="0.05" max="0.8" step="0.05" value={ki} onChange={(event) => setKi(Number(event.target.value))} /><small>Drift rejection ↔ phase margin</small></label>
                  <label><div><span>Step threshold</span><code>{stepThreshold} ns</code></div><input type="range" min="0" max="100" step="5" value={stepThreshold} onChange={(event) => setStepThreshold(Number(event.target.value))} /><small>Slew below ↔ step above</small></label>
                </div>
                <div className="servo-preview"><div><span>Predicted settling</span><strong>18.4 s</strong></div><div><span>Predicted overshoot</span><strong>7.2%</strong></div><div><span>Stability margin</span><strong className="mint">GOOD</strong></div></div>
              </section>
              <section className="instrument-panel presets-panel">
                <div className="panel-heading"><div><span className="section-kicker">QUICK START</span><h2>Experiment recipes</h2></div></div>
                <div className="recipe-list">
                  {[ ["STEP", "Servo step response", "Quantify settling time and overshoot", "120 s"], ["WANDER", "Low-frequency wander", "Stress integral tracking across 20 minutes", "20 min"], ["HOLD", "Holdover recovery", "Remove upstream sync and measure reacquisition", "8 min"], ["SWEEP", "Gain sweep", "Compare five Kp / Ki combinations", "12 min"] ].map((recipe, index) => <button type="button" key={recipe[0]} className={index === 0 ? "active" : ""}><span>{recipe[0]}</span><div><strong>{recipe[1]}</strong><small>{recipe[2]}</small></div><em>{recipe[3]}</em><ArrowRight size={14} /></button>)}
                </div>
              </section>
            </div>
          )}

          {section === "Interfaces" && (
            <div className="interfaces-layout">
              <div className="interface-summary">
                <article><Cpu size={18} /><span>PTP-capable ports</span><strong>{ptpCapableInterfaces.length}</strong><small>{timingInterfaces.length} cascade · {interfaceInventory.length - timingInterfaces.length} host</small></article>
                <article><Gauge size={18} /><span>Active line rate</span><strong>{formatLineRate(activeLineRateMbps)}</strong><small>{hundredGigTimingPorts} × 100G timing · management separate</small></article>
                <article><Clock3 size={18} /><span>Hardware clocks</span><strong>{hardwareClocks}</strong><small>Distinct PHC device providers</small></article>
                <article><Network size={18} /><span>Drivers</span><strong>{interfaceDrivers.length}</strong><small>{interfaceDrivers.join(" · ") || "Awaiting inventory"}</small></article>
              </div>
              <section className="instrument-panel interface-table-panel">
                <div className="panel-heading"><div><span className="section-kicker">LIVE INVENTORY</span><h2>Physical interfaces & PHCs</h2></div><div className="panel-tools"><span className="scan-time"><RefreshCw size={13} /> {interfaceUpdatedAt === null ? "Hardware model" : "Live host snapshot"}</span><button className="quiet-button" type="button" onClick={() => void refreshInterfaces().catch(() => setToast("Live interface rescan unavailable"))}>Rescan</button></div></div>
                <div className="interface-map">
                  <div className="interface-map-labels">{nodes.map((node) => <span key={node.id}>{node.label}</span>)}</div>
                  <div className="interface-map-line">{Array.from({ length: nodes.length * 2 }).map((_, index) => <i key={index} className={index % 2 ? "in" : "out"} />)}</div>
                </div>
                <div className="data-table interface-table">
                  <div className="table-header"><span>Interface</span><span>Assignment</span><span>Link</span><span>PHC</span><span>Timestamping</span><span>Driver</span><span>State</span></div>
                  {interfaceInventory.map((item) => <div className="table-row" key={item.name}><span><i className={`port-icon ${item.carrier ? "up" : "down"}`} /> <strong>{item.name}</strong><small>{item.bus ?? item.namespace ?? "host"}</small></span><span>{item.assignment ?? item.namespace ?? "UNASSIGNED"}</span><strong>{formatLineRate(item.speed_mbps)}</strong><code>{item.phc ? `/dev/${item.phc}` : "—"}</code><span>{item.hardware_timestamping ? <><ShieldCheck size={13} /> HW TX/RX</> : "—"}</span><span>{item.driver ?? "—"}</span><em className={item.carrier ? "state-good" : "state-off"}>{item.carrier ? "LINK" : item.state === "DOWN" ? "DOWN" : "NO LINK"}</em></div>)}
                </div>
              </section>
            </div>
          )}

          {section === "Configuration" && (
            <div className="configuration-layout">
              <div className="config-main">
                <section className="instrument-panel config-section">
                  <div className="panel-heading"><div><span className="section-kicker">PTP DOMAIN</span><h2>Protocol & profile</h2></div><span className="config-scope">Applies to all clocks</span></div>
                  <div className="form-grid">
                    <label className="wide"><span>PTP profile</span><select className="select-control" value={profile} onChange={(event) => setProfile(event.target.value)}>{(activeResearch.profiles.available_profiles ?? ["G.8275.1 Telecom"]).map((name) => <option key={name}>{name}</option>)}</select><small>Applies a coherent transport, delay mechanism, BMCA dataset, and domain contract.</small></label>
                    <label><span>Domain number</span><div className="input-unit"><input value={profileProtocol.domain} readOnly /><em>profile</em></div></label>
                    <label><span>Transport</span><div className="input-unit"><input value={profileProtocol.transport === "L2" ? "Layer 2" : profileProtocol.transport} readOnly /><em>profile</em></div></label>
                    <label><span>Sync interval</span><button className="select-control" type="button" onClick={() => document.getElementById("sync-frequency-control")?.scrollIntoView({ behavior: "smooth", block: "center" })}>{syncLogInterval} · {effectiveSyncFrequencyHz.toFixed(1)} Hz <ArrowRight size={14} /></button></label>
                    <label><span>Delay mechanism</span><div className="input-unit"><input value={profileProtocol.delay === "P2P" ? "Peer-to-peer" : "End-to-end"} readOnly /><em>{profileProtocol.delay}</em></div></label>
                  </div>
                  <div className="toggle-list">
                    <div><div><strong>Two-step clock</strong><small>Send preciseOriginTimestamp in Follow_Up messages.</small></div><Toggle on={twoStep} onChange={setTwoStep} label="Two-step clock" /></div>
                    <div><div><strong>Hardware timestamping</strong><small>Use the NIC PHC for transmit and receive timestamps.</small></div><Toggle on={hardwareTs} onChange={setHardwareTs} label="Hardware timestamping" /></div>
                  </div>
                </section>
                <section className="instrument-panel config-section" id="servo-control">
                  <div className="panel-heading"><div><span className="section-kicker">SERVO & HOLDOVER</span><h2>Clock discipline</h2></div><span className={`quality-badge ${targetHasHoldover ? "holdover" : ""}`}>{servoStatusLabel}</span></div>
                  <div className="form-grid">
                    <label><span>Servo type</span><select className="select-control" value={servoType} onChange={(event) => setServoType(event.target.value as ServoType)}><option value="pi">PI controller</option><option value="linreg">Linear regression</option><option value="kalman">Kalman · phase + frequency</option><option value="adaptive-kalman">Adaptive Kalman · phase + frequency + drift</option><option value="imm">IMM · quiet + dynamic + holdover</option><option value="nullf">Null frequency · SyncE diagnostic</option></select><small>{servoType === "imm" ? "Three interacting models estimate the active oscillator regime." : servoType === "adaptive-kalman" ? "Adaptive three-state filter estimates phase, frequency, and drift." : servoType === "kalman" ? "Reproducible two-state estimator with bounded PHC frequency control." : "LinuxPTP native servo implementation."}</small></label>
                    <label><span>Target</span><select className="select-control" value={servoTarget} onChange={(event) => selectServoTarget(event.target.value)}><option value="all">All downstream clocks</option>{nodes.filter((node) => node.role !== "Grandmaster").map((node) => <option value={node.id} key={node.id}>{node.label}</option>)}</select><small>Holdover can be isolated to one cascade stage.</small></label>
                    {["kalman", "adaptive-kalman", "imm"].includes(servoType) ? <>
                      <label><span>Measurement noise</span><div className="input-unit"><input type="number" min="0.001" step="1" value={kalmanMeasurementNoiseNs} onChange={(event) => setKalmanMeasurementNoiseNs(Number(event.target.value))} /><em>ns σ</em></div><small>Expected hardware timestamp and path asymmetry noise.</small></label>
                      <label><span>Oscillator process noise</span><div className="input-unit"><input type="number" min="0.001" step="0.05" value={kalmanProcessNoisePpb} onChange={(event) => setKalmanProcessNoisePpb(Number(event.target.value))} /><em>ppb/√s</em></div><small>How quickly the estimated free-running frequency may wander.</small></label>
                      <label><span>Phase time constant</span><div className="input-unit"><input type="number" min="0.1" step="0.5" value={kalmanPhaseTimeConstantS} onChange={(event) => setKalmanPhaseTimeConstantS(Number(event.target.value))} /><em>s</em></div><small>State-feedback horizon used to remove estimated phase.</small></label>
                      <label><span>Innovation gate</span><div className="input-unit"><input type="number" min="1" step="0.5" value={kalmanInnovationGateSigma} onChange={(event) => setKalmanInnovationGateSigma(Number(event.target.value))} /><em>σ</em></div><small>Rejects transient measurements outside predicted uncertainty.</small></label>
                      {servoType !== "kalman" && <label><span>Oscillator drift noise</span><div className="input-unit"><input type="number" min="0.0001" step="0.01" value={kalmanDriftNoisePpbS2} onChange={(event) => setKalmanDriftNoisePpbS2(Number(event.target.value))} /><em>ppb/s²</em></div><small>How quickly the oscillator aging state may change.</small></label>}
                    </> : <>
                      <label><span>Proportional constant</span><div className="input-unit"><input value={kp.toFixed(2)} onChange={(event) => setKp(Number(event.target.value))} /><em>Kp</em></div></label>
                      <label><span>Integral constant</span><div className="input-unit"><input value={ki.toFixed(2)} onChange={(event) => setKi(Number(event.target.value))} /><em>Ki</em></div></label>
                    </>}
                    <label><span>First-step threshold</span><div className="input-unit"><input value="20,000" readOnly /><em>ns</em></div></label>
                    <label><span>Step threshold</span><div className="input-unit"><input value={stepThreshold} onChange={(event) => setStepThreshold(Number(event.target.value))} /><em>ns</em></div></label>
                  </div>
                  <div className="sync-rate-control" id="sync-frequency-control">
                    <div className="sync-rate-heading">
                      <div><span className="section-kicker">MESSAGE CADENCE</span><strong>Synchronization frequency</strong><small>Requested in 0.5 Hz steps; applied through LinuxPTP <code>logSyncInterval</code>.</small></div>
                      <button className="quiet-button" type="button" onClick={() => setApplyOpen(true)}>Review apply <ArrowRight size={13} /></button>
                    </div>
                    <div className="sync-rate-readout">
                      <div><span>REQUESTED</span><strong>{syncFrequencyHz.toFixed(1)} <small>Hz</small></strong></div>
                      <ArrowRight size={17} />
                      <div><span>EFFECTIVE ON WIRE</span><strong>{effectiveSyncFrequencyHz.toFixed(1)} <small>Hz</small></strong></div>
                      <div className="sync-log-value"><span>LINUXPTP</span><code>logSyncInterval {syncLogInterval}</code></div>
                    </div>
                    <input
                      type="range"
                      min="0.5"
                      max="10"
                      step="0.5"
                      value={syncFrequencyHz}
                      aria-label="Requested synchronization frequency"
                      aria-valuetext={`${syncFrequencyHz.toFixed(1)} hertz requested, ${effectiveSyncFrequencyHz.toFixed(1)} hertz effective`}
                      style={{ background: `linear-gradient(90deg, var(--cyan) 0%, var(--cyan) ${syncSliderProgress}%, #22343c ${syncSliderProgress}%, #22343c 100%)` }}
                      onChange={(event) => setSyncFrequencyHz(Number(event.target.value))}
                    />
                    <div className="sync-rate-ticks"><span>0.5</span><span>2</span><span>4</span><span>6</span><span>8</span><span>10 Hz</span></div>
                    <div className={`sync-rate-note ${syncFrequencyExact ? "exact" : "quantized"}`}>
                      {syncFrequencyExact ? <Check size={14} /> : <Info size={14} />}
                      <span>{syncFrequencyExact ? `${syncFrequencyHz.toFixed(1)} Hz is represented exactly by IEEE 1588.` : `IEEE 1588 encodes Sync intervals as powers of two; ${syncFrequencyHz.toFixed(1)} Hz will apply as the nearest valid rate, ${effectiveSyncFrequencyHz.toFixed(1)} Hz.`}</span>
                    </div>
                  </div>
                  <div className="servo-live-control">
                    <div><strong>{targetInHoldover ? "Holdover observation active" : targetHasHoldover ? "Mixed discipline state" : "Clock discipline active"}</strong><span>{targetInHoldover ? "PTP messages, raw offsets, and PHC comparisons continue while clock adjustments are frozen." : targetHasHoldover ? "Some selected clocks are in holdover; apply a servo to resume all, or enter holdover for a coordinated comparison." : `${servoType.toUpperCase()} will discipline ${servoTarget === "all" ? "all downstream clocks" : servoTarget}.`}</span></div>
                    <button className="full-secondary" type="button" disabled={servoBusy || !agentStatus?.running || targetInHoldover} onClick={() => void controlServo(false)}><Pause size={14} /> {servoBusy ? "Transitioning…" : "Enter holdover"}</button>
                    <button className="primary-action" type="button" disabled={servoBusy || !agentStatus?.running} onClick={() => void controlServo(true)}><Play size={14} /> {servoBusy ? "Applying…" : targetInHoldover ? "Resume servo" : "Apply & run"}</button>
                  </div>
                </section>
                <section className="instrument-panel config-section pps-config-section" id="pps-control">
                  <div className="panel-heading"><div><span className="section-kicker">PPS I/O & TS2PHC</span><h2>Hardware pulse distribution</h2></div><span className={`quality-badge pps-quality ${ppsHardwareStatus?.running ? "" : ppsEnabled ? "pending" : "off"}`}>{ppsStateLabel}</span></div>
                  <div className="pps-enable-row">
                    <div><Zap size={18} /><div><strong>Managed PPS experiment</strong><small>Configure PHC periodic output, external timestamp inputs, and one LinuxPTP ts2phc servo.</small></div></div>
                    <Toggle on={ppsEnabled} onChange={setPpsEnabled} label="Enable managed PPS experiment" />
                  </div>
                  <div className="form-grid pps-io-grid">
                    <label><span>PPS source</span><select className="select-control" value={ppsSource} onChange={(event) => setPpsSourceSafely(event.target.value)}><option value="external">External PPS · generic ToD</option>{nodes.map((node) => <option value={node.id} key={node.id}>{node.label} · {node.phc}</option>)}</select><small>A PHC source programs PPS out; external expects a lab PPS input.</small></label>
                    <label><span>Output pin</span><select className="select-control" value={ppsOutputPin} disabled={ppsSource === "external"} onChange={(event) => setPpsOutputPin(Number(event.target.value))}><option value={0}>Pin 0 · mlx5_pps0</option><option value={1}>Pin 1 · mlx5_pps1</option></select><small>Programmable periodic-output connector on the source NIC.</small></label>
                    <label><span>Input pin</span><select className="select-control" value={ppsInputPin} onChange={(event) => setPpsInputPin(Number(event.target.value))}><option value={0}>Pin 0 · mlx5_pps0</option><option value={1}>Pin 1 · mlx5_pps1</option></select></label>
                    <label><span>Hardware channel</span><select className="select-control" value={ppsChannel} onChange={(event) => setPpsChannel(Number(event.target.value))}><option value={0}>Channel 0</option></select></label>
                    <label><span>Input edge</span><select className="select-control" value={ppsPolarity} onChange={(event) => setPpsPolarity(event.target.value as PpsPolarity)}><option value="rising">Rising edge</option><option value="falling">Falling edge</option><option value="both">Both · pulse rejection</option></select></label>
                    <label><span>Pulse width</span><div className="input-unit"><input type="number" min="1" max="990" value={ppsPulseWidthNs / 1_000_000} onChange={(event) => setPpsPulseWidthNs(Math.round(Number(event.target.value) * 1_000_000))} /><em>ms</em></div></label>
                    <label><span>Output phase</span><div className="input-unit"><input type="number" min="0" max="999999999" value={ppsPhaseNs} onChange={(event) => setPpsPhaseNs(Number(event.target.value))} /><em>ns</em></div></label>
                    <label><span>Input correction</span><div className="input-unit"><input type="number" value={ppsCorrectionNs} onChange={(event) => setPpsCorrectionNs(Number(event.target.value))} /><em>ns</em></div></label>
                  </div>
                  <div className="pps-sink-selector">
                    <div><span className="section-kicker">PPS INPUT CLOCKS</span><strong>Select ts2phc sinks</strong><small>Each selected measurement PHC timestamps the same physical pulse.</small></div>
                    <div>{nodes.map((node) => {
                      const selected = ppsSinks.includes(node.id);
                      const sourceNode = ppsSource === node.id;
                      return <button type="button" key={node.id} disabled={sourceNode} className={selected ? "active" : ""} onClick={() => togglePpsSink(node.id)}><i>{selected ? <Check size={11} /> : <Clock3 size={11} />}</i><span><strong>{node.id}</strong><small>{sourceNode ? "PPS OUT" : selected ? "PPS IN" : "AVAILABLE"}</small></span></button>;
                    })}</div>
                  </div>
                  <div className={`pps-comparison-config ${ppsComparisonEnabled ? "active" : ""}`}>
                    <div><Radio size={18} /><span><strong>Common-edge measurement mode</strong><small>Read the same external PPS edge on multiple PHCs without starting a ts2phc discipline loop.</small></span></div>
                    <label><span>Comparison reference</span><select value={ppsComparisonReference} disabled={!ppsComparisonEnabled} onChange={(event) => setPpsComparisonReference(event.target.value)}>{ppsSinks.map((node) => <option key={node} value={node}>{node}</option>)}</select></label>
                    <Toggle on={ppsComparisonEnabled} onChange={setPpsComparisonSafely} label="Enable PPS common-edge measurement" />
                  </div>
                  <div className="pps-ts2phc">
                    <div className="pps-subheading"><div><span className="section-kicker">LINUXPTP 4.4</span><strong>{ppsComparisonEnabled ? "ts2phc discipline bypassed" : "ts2phc discipline"}</strong></div><code>{ppsConfiguredRoles} PHC role{ppsConfiguredRoles === 1 ? "" : "s"}</code></div>
                    <div className="form-grid">
                      <label><span>Servo</span><select className="select-control" disabled={ppsComparisonEnabled} value={ppsServo} onChange={(event) => setPpsServo(event.target.value as NativeServoType)}><option value="pi">PI controller</option><option value="linreg">Linear regression</option><option value="nullf">Null frequency</option></select></label>
                      <label><span>Stable-lock threshold</span><div className="input-unit"><input type="number" min="0" value={ppsStableThresholdNs} onChange={(event) => setPpsStableThresholdNs(Number(event.target.value))} /><em>ns</em></div></label>
                      <label><span>First-step threshold</span><div className="input-unit"><input type="number" min="0" value={ppsFirstStepThresholdNs} onChange={(event) => setPpsFirstStepThresholdNs(Number(event.target.value))} /><em>ns</em></div></label>
                      <label><span>Step threshold</span><div className="input-unit"><input type="number" min="0" value={ppsStepThresholdNs} onChange={(event) => setPpsStepThresholdNs(Number(event.target.value))} /><em>ns</em></div></label>
                      <label><span>ToD holdover</span><div className="input-unit"><input type="number" min="0" value={ppsHoldoverSeconds} onChange={(event) => setPpsHoldoverSeconds(Number(event.target.value))} /><em>s</em></div><small>Continues only from a stable servo when time-of-day is lost.</small></label>
                      <label><span>Stable samples</span><div className="input-unit"><input value="10" readOnly /><em>samples</em></div></label>
                    </div>
                  </div>
                  <div className={`pps-live-note ${ppsHardwareStatus?.running ? "active" : ""}`}>
                    <Radio size={15} />
                    <div><strong>{ppsHardwareStatus?.running ? ppsHardwareStatus.mode === "common-edge-measurement" ? "Common-edge PHC comparison is live" : `ts2phc is running · ${ppsHardwareStatus.servo.toUpperCase()} servo` : ppsEnabled ? "PPS changes are staged in the editor" : "PPS hardware remains untouched"}</strong><span>{ppsHardwareStatus?.running ? `${ppsHardwareStatus.source === "external" ? "External PPS" : `${ppsHardwareStatus.source} PPS out`} is feeding ${ppsHardwareStatus.sinks.length} configured input${ppsHardwareStatus.sinks.length === 1 ? "" : "s"}.` : ppsComparisonEnabled ? "Measure-only mode requires an external PPS and at least two programmable EXTS-capable PHCs; it never adjusts their clocks." : "Review & apply restarts the managed cascade; enabling ts2phc will adjust sink PHCs, so use PTP holdover when isolating PPS behavior."}</span></div>
                  </div>
                </section>
                <section className="instrument-panel config-section">
                  <div className="panel-heading"><div><span className="section-kicker">GUARDRAILS</span><h2>Safety & recovery</h2></div></div>
                  <div className="toggle-list">
                    <div><div><strong>Sanity-frequency limit</strong><small>Reject adjustments beyond ±200,000 ppb.</small></div><Toggle on={sanity} onChange={setSanity} label="Sanity-frequency limit" /></div>
                    <div><div><strong>PTP message authentication</strong><small>Stage LinuxPTP SPP/key verification; key material remains root-owned.</small></div><Toggle on={authenticationEnabled} onChange={setAuthenticationEnabled} label="PTP message authentication" /></div>
                    <div><div><strong>Auto-recover unlocked clocks</strong><small>Restart the affected servo after three failed lock cycles.</small></div><Toggle on={true} onChange={() => setToast("Recovery guard remains enabled")} label="Auto-recover" /></div>
                    <div><div><strong>Capture before apply</strong><small>Create a rollback snapshot before changing a running cascade.</small></div><Toggle on={true} onChange={() => setToast("Safety snapshots are always on")} label="Capture before apply" /></div>
                  </div>
                </section>
              </div>
              <aside className="config-aside">
                <div className="change-card"><span className="section-kicker">REVIEW CONFIGURATION</span><strong>PTP + PPS controls</strong><p>PTPBox validates the complete document, stages it atomically, then restarts the managed LinuxPTP processes.</p><div><span>BC2…BC7</span><em>Servo gains</em></div><div><span>All clocks</span><em>Sync {effectiveSyncFrequencyHz.toFixed(1)} Hz</em></div><div><span>PPS fabric</span><em>{ppsEnabled ? ppsComparisonEnabled ? "Measure only" : `${ppsConfiguredRoles} roles` : "Disabled"}</em></div><div><span>Authentication</span><em>{authenticationEnabled ? "Enabled" : "Disabled"}</em></div><button className="primary-action" type="button" onClick={() => setApplyOpen(true)}>Review & apply <ArrowRight size={14} /></button><button className="full-secondary" type="button" onClick={() => { setKp(0.7); setKi(0.3); setKalmanDriftNoisePpbS2(.05); setStepThreshold(0); setSyncFrequencyHz(1); setAuthenticationEnabled(false); setPpsEnabled(false); setPpsComparisonEnabled(false); setPpsSource("BC1"); setPpsSinks(nodes.slice(1).map((node) => node.id)); setPpsComparisonReference("BC2"); setPpsOutputPin(0); setPpsInputPin(0); setPpsChannel(0); setPpsPolarity("rising"); setPpsPulseWidthNs(100_000_000); setPpsPhaseNs(0); setPpsCorrectionNs(0); setPpsServo("pi"); setPpsStepThresholdNs(0); setPpsFirstStepThresholdNs(20_000); setPpsHoldoverSeconds(0); setPpsStableThresholdNs(100); }}> <RotateCcw size={14} /> Reset safe defaults</button></div>
                <div className="config-note"><ShieldCheck size={18} /><div><strong>Safe apply</strong><p>PTPBox validates interface ownership, clock state, and config syntax before touching the running chain.</p></div></div>
              </aside>
            </div>
          )}

          {section === "Event log" && (
            <div className="events-layout">
              <section className="instrument-panel events-panel">
                <div className="panel-heading"><div><span className="section-kicker">STREAMING EVENTS</span><h2>Lab activity</h2></div><div className="panel-tools"><button className="quiet-button"><Search size={14} /> Find</button><button className="quiet-button"><ListFilter size={14} /> Filter</button><button className="quiet-button"><Download size={14} /> Export</button></div></div>
                <div className="event-filters"><button className="active">All events <em>218</em></button><button>State <em>42</em></button><button>Servo <em>96</em></button><button>Measurements <em>67</em></button><button>Operator <em>13</em></button></div>
                <div className="event-list">
                  {EVENTS.map((event) => <div className="event-row" key={event[0]}><code>{event[0]}</code><em className={`event-${event[1].toLowerCase()}`}>{event[1]}</em><strong>{event[2]}</strong><span>{event[3]}</span><p>{event[4]}</p><button aria-label="Inspect event"><ArrowRight size={14} /></button></div>)}
                </div>
                <div className="terminal-strip"><span className="prompt">ptpbox@lab:~$</span><span>stream --follow --scope cascade-a</span><i className="cursor" /></div>
              </section>
              <aside className="event-aside instrument-panel"><span className="section-kicker">SESSION SUMMARY</span><h2>Run 024</h2><div className="session-time"><strong>00:23:18</strong><span>elapsed</span></div><dl><div><dt>Started</dt><dd>13:19:00</dd></div><div><dt>Operator</dt><dd>user@PTPBox</dd></div><div><dt>Profile</dt><dd>G.8275.1</dd></div><div><dt>Events</dt><dd>218</dd></div><div><dt>Warnings</dt><dd>3</dd></div></dl><button className="full-secondary"><Download size={14} /> Download bundle</button></aside>
            </div>
          )}
        </div>
      </main>

      {commandOpen && (
        <div className="command-layer" role="dialog" aria-modal="true" aria-labelledby="command-title">
          <button className="command-backdrop" type="button" onClick={() => setCommandOpen(false)} aria-label="Close command palette" />
          <section className="command-palette" id="command-palette">
            <div className="command-input-row">
              <Search size={18} />
              <input
                ref={commandInputRef}
                value={commandQuery}
                placeholder="Search pages, clocks, measurements, or controls…"
                aria-label="Search or jump to"
                aria-controls="command-results"
                aria-activedescendant={filteredCommandItems[commandIndex] ? `command-option-${filteredCommandItems[commandIndex].id}` : undefined}
                onChange={(event) => { setCommandQuery(event.target.value); setCommandIndex(0); }}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown" && filteredCommandItems.length) {
                    event.preventDefault();
                    setCommandIndex((value) => (value + 1) % filteredCommandItems.length);
                  } else if (event.key === "ArrowUp" && filteredCommandItems.length) {
                    event.preventDefault();
                    setCommandIndex((value) => (value - 1 + filteredCommandItems.length) % filteredCommandItems.length);
                  } else if (event.key === "Enter" && filteredCommandItems[commandIndex]) {
                    event.preventDefault();
                    runCommand(filteredCommandItems[commandIndex]);
                  }
                }}
              />
              <kbd>ESC</kbd>
            </div>
            <div className="command-results" id="command-results" role="listbox" aria-label="Command results">
              {filteredCommandItems.length ? (["Navigate", "Clocks", "Controls"] as const).map((group) => {
                const groupItems = filteredCommandItems.filter((item) => item.group === group);
                if (!groupItems.length) return null;
                return (
                  <div className="command-group" key={group}>
                    <span>{group}</span>
                    {groupItems.map((item) => {
                      const index = filteredCommandItems.indexOf(item);
                      const active = index === commandIndex;
                      return (
                        <button id={`command-option-${item.id}`} role="option" aria-selected={active} type="button" className={active ? "active" : ""} key={item.id} onMouseEnter={() => setCommandIndex(index)} onClick={() => runCommand(item)}>
                          <i><item.icon size={16} /></i>
                          <span><strong>{item.label}</strong><small>{item.description}</small></span>
                          <ArrowRight size={14} />
                        </button>
                      );
                    })}
                  </div>
                );
              }) : <div className="command-empty"><Search size={24} /><strong>No observatory command found</strong><span>Try a page name, BC number, measurement, or control.</span></div>}
            </div>
            <footer className="command-footer"><span id="command-title">PRECISION OBSERVATORY COMMAND</span><div><kbd>↑</kbd><kbd>↓</kbd> Navigate <kbd>↵</kbd> Open</div></footer>
          </section>
        </div>
      )}

      {applyOpen && (
        <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="apply-title">
          <button className="modal-backdrop" onClick={() => setApplyOpen(false)} aria-label="Close review" />
          <section className="apply-drawer">
            <div className="drawer-heading"><div><span className="section-kicker">SAFE APPLY</span><h2 id="apply-title">Review configuration</h2></div><button className="icon-button" onClick={() => setApplyOpen(false)} aria-label="Close"><X size={18} /></button></div>
            <div className="validation-banner"><ShieldCheck size={20} /><div><strong>Preflight checks passed</strong><span>{interfaceInventory.length} interfaces available · {hardwareClocks} clocks responsive · rollback ready</span></div></div>
            <div className="change-list">
              <div><span>Target</span><strong>BC2 through BC7</strong></div>
              <div><span>Timing profile</span><strong>{profile}</strong></div>
              <div><span>Proportional constant</span><strong>{kp.toFixed(2)}</strong></div>
              <div><span>Integral constant</span><strong>{ki.toFixed(2)}</strong></div>
              <div><span>Sync frequency</span><p><ins>{syncFrequencyHz.toFixed(1)} Hz requested</ins><ArrowRight size={13} /><ins>{effectiveSyncFrequencyHz.toFixed(1)} Hz effective</ins></p></div>
              <div><span>Step threshold</span><strong>{stepThreshold} ns</strong></div>
              <div><span>PPS distribution</span><strong>{ppsEnabled ? ppsComparisonEnabled ? `External common edge → ${ppsSinks.length} PHCs · measure only` : `${ppsSource === "external" ? "External" : `${ppsSource} out`} → ${ppsSinks.length} input${ppsSinks.length === 1 ? "" : "s"} · ${ppsServo.toUpperCase()}` : "Disabled · pins released"}</strong></div>
              <div><span>Message authentication</span><strong>{authenticationEnabled ? "Enabled · key material remains root-only" : "Disabled"}</strong></div>
            </div>
            <div className="rollout-plan"><span>ROLLOUT PLAN</span><ol><li><i>1</i><div><strong>Validate & stage</strong><small>Check topology, PPS pin/channel ranges, and a complete LinuxPTP document</small></div></li><li><i>2</i><div><strong>Restart managed timing processes</strong><small>Recreate namespace clocks and {ppsEnabled ? "start the hardware-backed ts2phc loop" : "leave every PPS pin released"}</small></div></li><li><i>3</i><div><strong>Observe reacquisition</strong><small>Raw PHC monitoring and per-node PPS state remain visible</small></div></li></ol></div>
            <div className="drawer-actions"><button className="full-secondary" disabled={applyBusy} onClick={() => setApplyOpen(false)}>Cancel</button><button className="primary-action" disabled={applyBusy} onClick={handleApply}><Zap size={15} /> {applyBusy ? "Applying…" : "Apply to cascade"}</button></div>
          </section>
        </div>
      )}

      {toast && <div className="toast-message"><Check size={15} /><span>{toast}</span><button onClick={() => setToast("")}><X size={14} /></button></div>}
    </div>
  );
}
