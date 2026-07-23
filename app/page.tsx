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

type Section = "Overview" | "Multi-pendulum" | "Covariance" | "State space" | "Analytics" | "Experiments" | "Interfaces" | "Configuration" | "Event log";
type ConnectionMode = "checking" | "live" | "waiting" | "stale" | "simulation";
type ClockState = "LOCKED" | "TRACKING" | "UNLOCKED" | "REFERENCE" | "HOLDOVER" | "NO DATA" | "STALE" | "FAULTY";
type ServoType = "pi" | "linreg" | "nullf";

type ServoNodeControl = {
  mode: "reference" | "active" | "holdover";
  enabled: boolean;
  type: ServoType | null;
  changed_at: number | null;
  holdover_started: number | null;
};

type ServoControlState = { updated_at: number | null; nodes: Record<string, ServoNodeControl> };

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
  servo_control?: ServoControlState;
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
  servo_control: ServoControlState;
  measurement_source: string;
  raw: true;
  smoothing: "none";
  history_seconds: number;
};

function agentBaseUrl() {
  if (typeof window === "undefined") return "";
  const override = new URLSearchParams(window.location.search).get("agent");
  if (override?.startsWith("http://") || override?.startsWith("https://")) return override.replace(/\/$/, "");
  const hostname = override || (["localhost", "127.0.0.1"].includes(window.location.hostname) ? "192.168.1.60" : window.location.hostname);
  return `http://${hostname}:8090`;
}

const TRACE_COLORS = ["#f3f8f8", "#71d9e3", "#4de1c1", "#9ed873", "#f2c96e", "#ee9070", "#d7a4f4", "#ff6f91"];

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
      return Number.isFinite(value) ? [{ t: point.t, value }] : [];
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
  const vectors = Array.from({ length: size }, (_, row) => Array.from({ length: size }, (_, column) => row === column ? 1 : 0));

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
  const [kp, setKp] = useState(0.7);
  const [ki, setKi] = useState(0.3);
  const [stepThreshold, setStepThreshold] = useState(0);
  const [applyOpen, setApplyOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [profile, setProfile] = useState("G.8275.1 Telecom");
  const [twoStep, setTwoStep] = useState(true);
  const [hardwareTs, setHardwareTs] = useState(true);
  const [sanity, setSanity] = useState(true);
  const [controlBusy, setControlBusy] = useState(false);
  const [servoType, setServoType] = useState<ServoType>("pi");
  const [servoTarget, setServoTarget] = useState("BC7");
  const [servoBusy, setServoBusy] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [readNotificationIds, setReadNotificationIds] = useState<string[]>([]);
  const [pendulumAutoZero, setPendulumAutoZero] = useState(true);
  const [pendulumZeroState, setPendulumZeroState] = useState<PendulumZeroState>({ at: null, baselines: {} });
  const tickRef = useRef(0);
  const latestTelemetryAtRef = useRef(0);
  const servoSelectionHydratedRef = useRef(false);
  const notificationCenterRef = useRef<HTMLDivElement>(null);

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
  const controlledServoNodes = useMemo(() => servoTarget === "all" ? nodes.filter((node) => node.role !== "Grandmaster") : nodes.filter((node) => node.id === servoTarget), [nodes, servoTarget]);
  const targetInHoldover = controlledServoNodes.length > 0 && controlledServoNodes.every((node) => node.servoEnabled === false);
  const targetHasHoldover = controlledServoNodes.some((node) => node.servoEnabled === false);
  const servoStatusLabel = targetInHoldover ? "HOLDOVER" : targetHasHoldover ? "MIXED" : "DISCIPLINED";
  const holdoverElapsedSeconds = activeNode.holdoverStarted && telemetryStatus ? Math.max(0, Math.floor(telemetryStatus.timestamp - activeNode.holdoverStarted)) : null;
  const selectedServoLabel = activeNode.role === "Grandmaster" ? "REFERENCE CLOCK" : `${activeNode.servoType?.toUpperCase() ?? "PTP"}${activeNode.servoEnabled === false ? " · FROZEN" : " SERVO"}`;
  const selectedServoDescription = activeNode.servoType === "linreg" ? "Adaptive frequency regression" : activeNode.servoType === "nullf" ? "Zero frequency correction · SyncE" : `Kp ${kp.toFixed(2)} · Ki ${ki.toFixed(2)}`;
  const selectedServoRail = activeNode.servoEnabled === false ? 0 : activeNode.servoType === "linreg" ? 82 : activeNode.servoType === "nullf" ? 4 : Math.min(100, kp * 76);
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
    const points = history
      .filter((point) => point.t >= activeNode.holdoverStarted && Number.isFinite(point.values[activeNode.id]))
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
    let disposed = false;
    let initialProbe = true;
    const pollStatus = async () => {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 3200);
      try {
        const response = await fetch(`${agentBaseUrl()}/api/status`, { signal: controller.signal });
        if (!response.ok) throw new Error("agent unavailable");
        const status = await response.json() as AgentStatus;
        if (disposed) return;
        setAgentStatus(status);
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
        window.clearTimeout(timeout);
      }
    };
    void pollStatus();
    const timer = window.setInterval(() => void pollStatus(), 2000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!agentStatus || paused) return;
    latestTelemetryAtRef.current = 0;
    let disposed = false;
    let polling = false;
    const controller = new AbortController();
    const seconds = rangeSeconds(range);

    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const query = new URLSearchParams({ history: String(seconds), limit: "4096" });
        if (latestTelemetryAtRef.current) query.set("since", String(latestTelemetryAtRef.current));
        const response = await fetch(`${agentBaseUrl()}/api/telemetry?${query}`, { signal: controller.signal });
        if (!response.ok) throw new Error("telemetry unavailable");
        const payload = await response.json() as TelemetryPayload;
        if (disposed) return;
        const incoming = historyFromTelemetry(payload);
        const newest = incoming.reduce((value, point) => Math.max(value, point.t), latestTelemetryAtRef.current);
        latestTelemetryAtRef.current = newest;
        setTelemetryStatus(payload);
        const initialServo = payload.servo_control?.nodes?.BC7?.type;
        if (!servoSelectionHydratedRef.current && initialServo) {
          setServoType(initialServo);
          servoSelectionHydratedRef.current = true;
        }
        setConnection(payload.phc_mode);
        setNodes(nodesFromTelemetry(payload));
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
  }, [agentStatus, paused, range]);

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
    const payload = {
      profile,
      domain: 24,
      transport: "L2",
      delay_mechanism: "E2E",
      log_sync_interval: 0,
      two_step: twoStep,
      hardware_timestamping: hardwareTs,
      servo: {
        type: servoType,
        kp,
        ki,
        step_threshold_ns: stepThreshold,
        first_step_threshold_ns: 20_000,
        sanity_freq_limit_ppb: sanity ? 200_000 : 0,
      },
    };
    if (agentStatus) {
      try {
        const response = await fetch(`${agentBaseUrl()}/api/config/apply`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error("agent rejected configuration");
        setToast("Configuration staged on PTPBox · validation passed");
      } catch {
        setToast("Saved in the console · host staging is unavailable");
      }
    } else {
      setToast("Configuration validated in hardware-model mode");
    }
    setApplyOpen(false);
  };

  const toggleExperiment = async () => {
    const starting = !experimentRunning;
    setExperimentRunning(starting);
    if (starting && experimentProgress >= 100) setExperimentProgress(0);
    if (starting && agentStatus) {
      try {
        await fetch(`${agentBaseUrl()}/api/experiments/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ type: "step", target: "BC7", amplitude_ns: 1000, duration_s: 120, servo: { kp, ki, step_threshold_ns: stepThreshold } }),
        });
      } catch {
        setToast("Experiment is running locally; host capture could not be staged");
      }
    }
  };

  const navItems: { label: Section; icon: typeof LayoutDashboard; badge?: string }[] = [
    { label: "Overview", icon: LayoutDashboard },
    { label: "Multi-pendulum", icon: Orbit },
    { label: "Covariance", icon: Network },
    { label: "State space", icon: Activity },
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
          <div className="brand-mark"><span /><span /><span /></div>
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
            <button className="search-box" type="button"><Search size={15} /><span>Search or jump to…</span><kbd>⌘ K</kbd></button>
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
              <h1>{section === "Overview" ? "Cascade overview" : section === "Covariance" ? "Covariance lab" : section === "State space" ? "State-space atlas" : section}</h1>
              <p>{section === "Overview" ? "Compare every NIC PHC against BC1 while LinuxPTP synchronizes the isolated daisy chain." : section === "Multi-pendulum" ? "Watch every previous-hop phase residual swing around its learned equilibrium." : section === "Covariance" ? "Reveal coupled phase changes, evolving relationships, and the cascade's dominant eigenmodes." : section === "State space" ? "Trace the cascade's modal trajectory, empirical Poincaré crossings, and evolving eigenstructure." : section === "Analytics" ? "Interrogate direct PHC differences alongside LinuxPTP servo state, frequency correction, and path delay." : section === "Experiments" ? "Design, run, and compare repeatable servo response tests." : section === "Interfaces" ? "Map physical ports, PHCs, namespaces, and timestamping capability." : section === "Configuration" ? "Shape protocol and servo behavior with guarded, reviewable changes." : "A precise account of state changes, measurements, and operator actions."}</p>
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
                          <div className="node-ports"><code>{node.ingress}</code><ArrowRight size={11} /><code>{node.egress}</code></div>
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="topology-footer">
                  <span><i className="legend-dot locked" /> Locked</span><span><i className="legend-dot tracking" /> Tracking</span><span><i className="legend-line" /> Measured hop</span>
                  <p><Info size={13} /> NICs synchronize only through ptp4l over the wires. PHCs are read for comparison and never disciplined by the observatory.</p>
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
                    <div className="segmented-control">{["30 s", "2 min", "15 min"].map((item) => <button className={range === item ? "active" : ""} type="button" key={item} onClick={() => setRange(item)}>{item}</button>)}</div>
                  </div>
                  <div className="chart-legend">
                    {visibleTraces.map((id) => {
                      const node = nodes.find((item) => item.id === id);
                      return node ? <button type="button" key={id} onClick={() => selectNode(id)} className={selectedNode === id ? "active" : ""}><i style={{ background: node.color }} /> {node.label}<strong>{formatOffset(node.offset, node.measured)}</strong></button> : null;
                    })}
                    <span className="chart-unit">PHC Δ VS BC1 · AUTO-SCALED</span>
                  </div>
                  <LineChart data={history} selected={visibleTraces} nodes={nodes} />
                  <div className="chart-footer-note"><Sparkles size={14} /><span><strong>Provenance:</strong> Kernel cross timestamps place every PHC at a common epoch; BC1 is interpolated only between its two bracketing reads. No phc2sys loop or time-series smoothing is involved.</span><button type="button" onClick={() => setSection("Analytics")}>Inspect <ArrowRight size={13} /></button></div>
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
                  <button type="button" className="run-button" onClick={toggleExperiment}>{experimentRunning ? <Pause size={18} /> : <Play size={18} fill="currentColor" />}{experimentRunning ? "Pause capture" : "Start experiment"}</button>
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
                    <label className="wide"><span>PTP profile</span><button className="select-control" type="button" onClick={() => setProfile((value) => value === "G.8275.1 Telecom" ? "IEEE 1588 Default" : "G.8275.1 Telecom")}>{profile}<ChevronDown size={14} /></button><small>Defines message rates, transport, and BMCA defaults.</small></label>
                    <label><span>Domain number</span><div className="input-unit"><input value="24" readOnly /><em>0–127</em></div></label>
                    <label><span>Transport</span><button className="select-control" type="button">Layer 2 <ChevronDown size={14} /></button></label>
                    <label><span>Sync interval</span><button className="select-control" type="button">0 · 1/s <ChevronDown size={14} /></button></label>
                    <label><span>Delay mechanism</span><button className="select-control" type="button">End-to-end <ChevronDown size={14} /></button></label>
                  </div>
                  <div className="toggle-list">
                    <div><div><strong>Two-step clock</strong><small>Send preciseOriginTimestamp in Follow_Up messages.</small></div><Toggle on={twoStep} onChange={setTwoStep} label="Two-step clock" /></div>
                    <div><div><strong>Hardware timestamping</strong><small>Use the NIC PHC for transmit and receive timestamps.</small></div><Toggle on={hardwareTs} onChange={setHardwareTs} label="Hardware timestamping" /></div>
                  </div>
                </section>
                <section className="instrument-panel config-section">
                  <div className="panel-heading"><div><span className="section-kicker">SERVO & HOLDOVER</span><h2>Clock discipline</h2></div><span className={`quality-badge ${targetHasHoldover ? "holdover" : ""}`}>{servoStatusLabel}</span></div>
                  <div className="form-grid">
                    <label><span>Servo type</span><select className="select-control" value={servoType} onChange={(event) => setServoType(event.target.value as ServoType)}><option value="pi">PI controller</option><option value="linreg">Linear regression</option><option value="nullf">Null frequency · SyncE diagnostic</option></select><small>LinuxPTP native servo implementation.</small></label>
                    <label><span>Target</span><select className="select-control" value={servoTarget} onChange={(event) => selectServoTarget(event.target.value)}><option value="all">All downstream clocks</option>{nodes.filter((node) => node.role !== "Grandmaster").map((node) => <option value={node.id} key={node.id}>{node.label}</option>)}</select><small>Holdover can be isolated to one cascade stage.</small></label>
                    <label><span>Proportional constant</span><div className="input-unit"><input value={kp.toFixed(2)} onChange={(event) => setKp(Number(event.target.value))} /><em>Kp</em></div></label>
                    <label><span>Integral constant</span><div className="input-unit"><input value={ki.toFixed(2)} onChange={(event) => setKi(Number(event.target.value))} /><em>Ki</em></div></label>
                    <label><span>First-step threshold</span><div className="input-unit"><input value="20,000" readOnly /><em>ns</em></div></label>
                    <label><span>Step threshold</span><div className="input-unit"><input value={stepThreshold} onChange={(event) => setStepThreshold(Number(event.target.value))} /><em>ns</em></div></label>
                  </div>
                  <div className="servo-live-control">
                    <div><strong>{targetInHoldover ? "Holdover observation active" : targetHasHoldover ? "Mixed discipline state" : "Clock discipline active"}</strong><span>{targetInHoldover ? "PTP messages, raw offsets, and PHC comparisons continue while clock adjustments are frozen." : targetHasHoldover ? "Some selected clocks are in holdover; apply a servo to resume all, or enter holdover for a coordinated comparison." : `${servoType.toUpperCase()} will discipline ${servoTarget === "all" ? "all downstream clocks" : servoTarget}.`}</span></div>
                    <button className="full-secondary" type="button" disabled={servoBusy || !agentStatus?.running || targetInHoldover} onClick={() => void controlServo(false)}><Pause size={14} /> {servoBusy ? "Transitioning…" : "Enter holdover"}</button>
                    <button className="primary-action" type="button" disabled={servoBusy || !agentStatus?.running} onClick={() => void controlServo(true)}><Play size={14} /> {servoBusy ? "Applying…" : targetInHoldover ? "Resume servo" : "Apply & run"}</button>
                  </div>
                </section>
                <section className="instrument-panel config-section">
                  <div className="panel-heading"><div><span className="section-kicker">GUARDRAILS</span><h2>Safety & recovery</h2></div></div>
                  <div className="toggle-list">
                    <div><div><strong>Sanity-frequency limit</strong><small>Reject adjustments beyond ±200,000 ppb.</small></div><Toggle on={sanity} onChange={setSanity} label="Sanity-frequency limit" /></div>
                    <div><div><strong>Auto-recover unlocked clocks</strong><small>Restart the affected servo after three failed lock cycles.</small></div><Toggle on={true} onChange={() => setToast("Recovery guard remains enabled")} label="Auto-recover" /></div>
                    <div><div><strong>Capture before apply</strong><small>Create a rollback snapshot before changing a running cascade.</small></div><Toggle on={true} onChange={() => setToast("Safety snapshots are always on")} label="Capture before apply" /></div>
                  </div>
                </section>
              </div>
              <aside className="config-aside">
                <div className="change-card"><span className="section-kicker">PENDING CHANGES</span><strong>3 values modified</strong><p>Changes are validated first, then rolled through the cascade from OC to GM.</p><div><span>BC–01…06</span><em>Servo gains</em></div><div><span>All clocks</span><em>Step threshold</em></div><button className="primary-action" type="button" onClick={() => setApplyOpen(true)}>Review & apply <ArrowRight size={14} /></button><button className="full-secondary" type="button"><RotateCcw size={14} /> Discard changes</button></div>
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

      {applyOpen && (
        <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="apply-title">
          <button className="modal-backdrop" onClick={() => setApplyOpen(false)} aria-label="Close review" />
          <section className="apply-drawer">
            <div className="drawer-heading"><div><span className="section-kicker">SAFE APPLY</span><h2 id="apply-title">Review configuration</h2></div><button className="icon-button" onClick={() => setApplyOpen(false)} aria-label="Close"><X size={18} /></button></div>
            <div className="validation-banner"><ShieldCheck size={20} /><div><strong>Preflight checks passed</strong><span>{interfaceInventory.length} interfaces available · {hardwareClocks} clocks responsive · rollback ready</span></div></div>
            <div className="change-list">
              <div><span>Target</span><strong>BC–01 through BC–06</strong></div>
              <div><span>Proportional constant</span><p><del>0.50</del><ArrowRight size={13} /><ins>{kp.toFixed(2)}</ins></p></div>
              <div><span>Integral constant</span><p><del>0.20</del><ArrowRight size={13} /><ins>{ki.toFixed(2)}</ins></p></div>
              <div><span>Step threshold</span><p><del>0 ns</del><ArrowRight size={13} /><ins>{stepThreshold} ns</ins></p></div>
            </div>
            <div className="rollout-plan"><span>ROLLOUT PLAN</span><ol><li><i>1</i><div><strong>Snapshot</strong><small>Save configuration and active clock state</small></div></li><li><i>2</i><div><strong>Apply downstream first</strong><small>OC → BC–06 → … → BC–01</small></div></li><li><i>3</i><div><strong>Verify lock</strong><small>Pause and roll back if any node fails</small></div></li></ol></div>
            <div className="drawer-actions"><button className="full-secondary" onClick={() => setApplyOpen(false)}>Cancel</button><button className="primary-action" onClick={handleApply}><Zap size={15} /> Apply to cascade</button></div>
          </section>
        </div>
      )}

      {toast && <div className="toast-message"><Check size={15} /><span>{toast}</span><button onClick={() => setToast("")}><X size={14} /></button></div>}
    </div>
  );
}
