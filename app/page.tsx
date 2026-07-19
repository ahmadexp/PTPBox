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

type Section = "Overview" | "Analytics" | "Experiments" | "Interfaces" | "Configuration" | "Event log";
type ConnectionMode = "checking" | "live" | "waiting" | "stale" | "simulation";
type ClockState = "LOCKED" | "TRACKING" | "UNLOCKED" | "REFERENCE" | "NO DATA" | "STALE" | "FAULTY";

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
  source: string;
  lastSampleAt: number | null;
};

type HistoryPoint = {
  t: number;
  values: Record<string, number>;
  key?: string;
};

type AgentStatus = {
  hostname?: string;
  linuxptp?: string;
  interfaces?: number;
  ptp_interfaces?: number;
  namespaces?: string[];
  running?: boolean;
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
  measurement_source: "direct PHC comparison";
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
  { id: "BC1", label: "BC1 · GM", role: "Grandmaster", offset: 0, meanPathDelay: 0, rms: 0, frequencyPpb: 0, state: "REFERENCE", ingress: "enp25s0f0np0", egress: "enp25s0f1np1", phc: "ptp1", color: TRACE_COLORS[0], measured: true, sampleCount: 120, source: "simulation", lastSampleAt: null },
  { id: "BC2", label: "BC2", role: "Boundary", offset: 4.8, meanPathDelay: 212, rms: 3.2, frequencyPpb: -2.4, state: "LOCKED", ingress: "enp26s0f0np0", egress: "enp26s0f1np1", phc: "ptp2", color: TRACE_COLORS[1], measured: true, sampleCount: 120, source: "simulation", lastSampleAt: null },
  { id: "BC7", label: "BC7", role: "Boundary", offset: 11.7, meanPathDelay: 228, rms: 6.1, frequencyPpb: 3.1, state: "LOCKED", ingress: "enp105s0f0np0", egress: "enp105s0f1np1", phc: "ptp14", color: TRACE_COLORS[2], measured: true, sampleCount: 120, source: "simulation", lastSampleAt: null },
  { id: "BC6", label: "BC6", role: "Boundary", offset: 24.3, meanPathDelay: 241, rms: 10.8, frequencyPpb: -8.7, state: "LOCKED", ingress: "enp104s0f0np0", egress: "enp104s0f1np1", phc: "ptp12", color: TRACE_COLORS[3], measured: true, sampleCount: 120, source: "simulation", lastSampleAt: null },
  { id: "BC5", label: "BC5", role: "Boundary", offset: 41.6, meanPathDelay: 237, rms: 18.9, frequencyPpb: -6.2, state: "LOCKED", ingress: "enp103s0f0np0", egress: "enp103s0f1np1", phc: "ptp10", color: TRACE_COLORS[4], measured: true, sampleCount: 120, source: "simulation", lastSampleAt: null },
  { id: "BC3", label: "BC3", role: "Boundary", offset: 63.8, meanPathDelay: 255, rms: 27.6, frequencyPpb: -12.4, state: "TRACKING", ingress: "enp27s0f0np0", egress: "enp27s0f1np1", phc: "ptp6", color: TRACE_COLORS[5], measured: true, sampleCount: 120, source: "simulation", lastSampleAt: null },
  { id: "BC4", label: "BC4 · OC", role: "Ordinary", offset: 91.2, meanPathDelay: 269, rms: 40.2, frequencyPpb: 7.9, state: "LOCKED", ingress: "enp28s0f0np0", egress: "enp28s0f1np1", phc: "ptp8", color: TRACE_COLORS[6], measured: true, sampleCount: 120, source: "simulation", lastSampleAt: null },
];

const FALLBACK_INTERFACES: HostInterface[] = [
  ["enp25s0f0np0", "BC1 / INACTIVE IN", 100000, "ptp0", "0000:19:00.0", "mlx5_core", "BC1"],
  ["enp25s0f1np1", "BC1 / GM OUT", 100000, "ptp1", "0000:19:00.1", "mlx5_core", "BC1"],
  ["enp26s0f0np0", "BC2 / IN", 100000, "ptp2", "0000:1a:00.0", "mlx5_core", "BC2"],
  ["enp26s0f1np1", "BC2 / OUT", 100000, "ptp3", "0000:1a:00.1", "mlx5_core", "BC2"],
  ["enp105s0f0np0", "BC7 / IN", 100000, "ptp14", "0000:69:00.0", "mlx5_core", "BC7"],
  ["enp105s0f1np1", "BC7 / OUT", 100000, "ptp15", "0000:69:00.1", "mlx5_core", "BC7"],
  ["enp104s0f0np0", "BC6 / IN", 100000, "ptp12", "0000:68:00.0", "mlx5_core", "BC6"],
  ["enp104s0f1np1", "BC6 / OUT", 100000, "ptp13", "0000:68:00.1", "mlx5_core", "BC6"],
  ["enp103s0f0np0", "BC5 / IN", 100000, "ptp10", "0000:67:00.0", "mlx5_core", "BC5"],
  ["enp103s0f1np1", "BC5 / OUT", 100000, "ptp11", "0000:67:00.1", "mlx5_core", "BC5"],
  ["enp27s0f0np0", "BC3 / IN", 100000, "ptp6", "0000:1b:00.0", "mlx5_core", "BC3"],
  ["enp27s0f1np1", "BC3 / OUT", 100000, "ptp7", "0000:1b:00.1", "mlx5_core", "BC3"],
  ["enp28s0f0np0", "BC4 / OC IN", 100000, "ptp8", "0000:1c:00.0", "mlx5_core", "BC4"],
  ["enp28s0f1np1", "BC4 / INACTIVE OUT", 100000, "ptp9", "0000:1c:00.1", "mlx5_core", "BC4"],
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
  return Array.from({ length }, (_, i) => ({
    t: i - length + 1,
    values: Object.fromEntries(
      INITIAL_NODES.map((node, nodeIndex) => {
        const wander = seededNoise(i, nodeIndex) * (nodeIndex * 3.4 + 1.5);
        const step = i > 54 && i < 82 ? Math.exp(-(i - 54) / 12) * nodeIndex * 5.4 : 0;
        return [node.id, Number((node.offset + wander + step).toFixed(2))];
      }),
    ),
  }));
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
    const stale = Boolean(ptpMeasurement && payload.timestamp - ptpMeasurement.observed_at > 5);
    return {
      id: clock.id,
      label: `${clock.id}${clock.role === "grandmaster" ? " · GM" : clock.role === "ordinary" ? " · OC" : ""}`,
      role: clock.role === "grandmaster" ? "Grandmaster" : clock.role === "ordinary" ? "Ordinary" : "Boundary",
      offset: phcMeasurement?.offset_ns ?? 0,
      hopOffset: phcMeasurement?.previous_hop_offset_ns ?? 0,
      meanPathDelay: ptpMeasurement?.mean_path_delay_ns ?? 0,
      rms: clock.phc_rms_ns ?? 0,
      frequencyPpb: ptpMeasurement?.frequency_ppb ?? 0,
      state: stateFromMeasurement(ptpMeasurement, stale, clock.role),
      ingress: clock.ingress,
      egress: clock.egress,
      phc: clock.measurement_phc ? `/dev/${clock.measurement_phc}` : "—",
      color: TRACE_COLORS[index % TRACE_COLORS.length],
      measured: Boolean(phcMeasurement?.valid && phcMeasurement.offset_ns !== null),
      ptpMeasured: Boolean(ptpMeasurement?.valid),
      sampleCount: clock.phc_window_sample_count,
      source: phcMeasurement?.error ?? (clock.measurement_phc ? `direct /dev/${clock.measurement_phc} read` : "No PHC mapping"),
      lastSampleAt: phcMeasurement?.observed_at ?? null,
    };
  });
}

function historyFromTelemetry(payload: TelemetryPayload): HistoryPoint[] {
  return payload.clocks
    .flatMap((clock) => clock.phc_samples.filter((sample) => sample.valid && sample.offset_ns !== null).map((sample) => ({ t: sample.observed_at, values: { [clock.id]: sample.offset_ns as number }, key: `${clock.id}:${sample.sample_id}` })))
    .sort((left, right) => left.t - right.t);
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
  const [selectedNode, setSelectedNode] = useState("BC4");
  const [visibleTraces, setVisibleTraces] = useState(["BC2", "BC6", "BC3", "BC4"]);
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
  const tickRef = useRef(0);
  const latestTelemetryAtRef = useRef(0);

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
      const timeout = window.setTimeout(() => controller.abort(), 1800);
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
      setHistory((current) => [
        ...current.slice(-119),
        {
          t: tick,
          values: Object.fromEntries(INITIAL_NODES.map((node, index) => [node.id, Number((node.offset + seededNoise(tick, index) * (index * 3.4 + 1.5)).toFixed(2))])),
        },
      ]);
      if (experimentRunning) setExperimentProgress((value) => (value >= 100 ? 100 : value + 1));
    }, 900);
    return () => window.clearInterval(timer);
  }, [connection, experimentRunning, paused]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const stats = useMemo(() => {
    const final = nodes[nodes.length - 1];
    const values = history.flatMap((point) => Object.values(point.values)).filter(Number.isFinite);
    const peak = values.length ? Math.max(...values.map(Math.abs)) : 0;
    const receiverCount = nodes.filter((node) => node.role !== "Grandmaster").length;
    const locked = nodes.filter((node) => node.role !== "Grandmaster" && node.state === "LOCKED").length;
    if (connection !== "simulation") {
      return [
        { label: "Endpoint PHC RMS", value: final.measured ? formatNanoseconds(final.rms) : "—", delta: "DIRECT", note: `${final.sampleCount} PHC reads`, icon: Activity, good: final.measured },
        { label: "Peak PHC difference", value: values.length ? formatNanoseconds(peak) : "—", delta: "UNFILTERED", note: `${range} vs BC1`, icon: Zap, good: values.length > 0 },
        { label: "Locked receivers", value: `${locked}/${receiverCount}`, delta: telemetryStatus?.mode.toUpperCase() ?? "WAITING", note: "LinuxPTP servo state", icon: ShieldCheck, good: locked === receiverCount && receiverCount > 0 },
        { label: "PHC comparisons", value: history.length.toLocaleString(), delta: "NO CONTROL", note: "read-only browser buffer", icon: TimerReset, good: history.length > 0 },
      ];
    }
    return [
      { label: "Modeled RMS", value: `${final.rms.toFixed(1)} ns`, delta: "SIM", note: "not a measurement", icon: Activity, good: false },
      { label: "Modeled peak", value: `${peak.toFixed(0)} ns`, delta: "SIM", note: "not a measurement", icon: Zap, good: false },
      { label: "Modeled locks", value: `${locked}/${receiverCount}`, delta: "SIM", note: "not hardware state", icon: ShieldCheck, good: false },
      { label: "Generated samples", value: history.length.toLocaleString(), delta: "SIM", note: "deterministic fallback", icon: TimerReset, good: false },
    ];
  }, [connection, history, nodes, range, telemetryStatus]);

  const selectNode = (id: string) => {
    setSelectedNode(id);
    setVisibleTraces((current) => (current.includes(id) ? current : [...current.slice(-3), id]));
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
        type: "pi",
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
          body: JSON.stringify({ type: "step", target: "BC4", amplitude_ns: 1000, duration_s: 120, servo: { kp, ki, step_threshold_ns: stepThreshold } }),
        });
      } catch {
        setToast("Experiment is running locally; host capture could not be staged");
      }
    }
  };

  const navItems: { label: Section; icon: typeof LayoutDashboard; badge?: string }[] = [
    { label: "Overview", icon: LayoutDashboard },
    { label: "Analytics", icon: BarChart3 },
    { label: "Experiments", icon: FlaskConical, badge: experimentRunning ? "RUN" : undefined },
    { label: "Interfaces", icon: Cable },
    { label: "Configuration", icon: SlidersHorizontal },
    { label: "Event log", icon: Terminal, badge: "6" },
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
            <button className="icon-button notification" type="button" aria-label="Notifications"><Bell size={17} /><i /></button>
            <button className="primary-action" type="button" onClick={() => setApplyOpen(true)}><SlidersHorizontal size={15} /> Apply settings</button>
          </div>
        </header>

        <div className="content-shell">
          <div className="page-heading">
            <div>
              <div className="eyebrow"><span className={`status-orb ${connection}`} /> {dataModeLabel}</div>
              <h1>{section === "Overview" ? "Cascade overview" : section}</h1>
              <p>{section === "Overview" ? "Compare every NIC PHC against BC1 while LinuxPTP synchronizes the isolated daisy chain." : section === "Analytics" ? "Interrogate direct PHC differences alongside LinuxPTP servo state, frequency correction, and path delay." : section === "Experiments" ? "Design, run, and compare repeatable servo response tests." : section === "Interfaces" ? "Map physical ports, PHCs, namespaces, and timestamping capability." : section === "Configuration" ? "Shape protocol and servo behavior with guarded, reviewable changes." : "A precise account of state changes, measurements, and operator actions."}</p>
            </div>
            <div className="heading-actions">
              <button className="secondary-button" type="button" onClick={() => setToast("Snapshot saved to run 024")}><Download size={15} /> Snapshot</button>
              <button className={`live-control ${paused ? "paused" : ""}`} type="button" onClick={() => setPaused((value) => !value)}>{paused ? <Play size={14} /> : <Pause size={14} />} {paused ? "Resume" : connection === "simulation" ? "Simulating" : "Raw stream"}</button>
            </div>
          </div>

          <div className={`data-provenance ${connection}`}>
            <span><Radio size={13} /> {connection === "simulation" ? "DETERMINISTIC FALLBACK" : connection === "checking" ? "PTPBOX AGENT PROBE" : "DIRECT PHC COMPARISON"}</span>
            <code>{connection === "simulation" ? "synthetic" : connection === "checking" ? "measurement mode pending" : "read-only · raw=true · smoothing=none"}</code>
            <span>{history.length.toLocaleString()} PHC samples buffered</span>
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
                        <button type="button" onClick={() => selectNode(node.id)} className={`clock-node ${selectedNode === node.id ? "selected" : ""} ${node.state === "TRACKING" ? "tracking" : ""} ${node.state === "FAULTY" ? "faulty" : ""} ${node.state === "STALE" || node.state === "NO DATA" ? "stale" : ""}`}>
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
                  <div className="chart-footer-note"><Sparkles size={14} /><span><strong>Provenance:</strong> Every point is a direct, read-only PHC comparison. No phc2sys loop, smoothing, or interpolation is involved.</span><button type="button" onClick={() => setSection("Analytics")}>Inspect <ArrowRight size={13} /></button></div>
                </section>

                <section className="instrument-panel selected-panel">
                  <div className="panel-heading">
                    <div><span className="section-kicker">SELECTED CLOCK</span><h2>{activeNode.label}</h2></div>
                    <button className="more-button" type="button">•••</button>
                  </div>
                  <div className="selected-status">
                    <div className="radial-score"><span>{activeNode.measured ? activeNode.sampleCount : 0}</span><small>SAMPLES</small></div>
                    <div><span className="locked-pill"><Check size={12} /> {activeNode.state}</span><strong>{formatOffset(activeNode.offset, activeNode.measured)}</strong><small>direct PHC difference vs BC1 · {activeNode.phc}</small></div>
                  </div>
                  <div className="selected-metrics">
                    <div><span>PHC window RMS</span><strong>{activeNode.measured ? formatNanoseconds(activeNode.rms) : "—"}</strong></div>
                    <div><span>Previous-hop PHC Δ</span><strong>{formatOffset(activeNode.hopOffset ?? 0, activeNode.measured)}</strong></div>
                    <div><span>PTP path delay</span><strong>{activeNode.ptpMeasured ? `${activeNode.meanPathDelay} ns` : "—"}</strong></div>
                    <div><span>PTP frequency adj.</span><strong>{activeNode.ptpMeasured ? `${activeNode.frequencyPpb >= 0 ? "+" : ""}${activeNode.frequencyPpb.toFixed(1)} ppb` : "—"}</strong></div>
                  </div>
                  <div className="servo-mini">
                    <div><span>PI SERVO</span><strong>K<sub>p</sub> {kp.toFixed(2)} · K<sub>i</sub> {ki.toFixed(2)}</strong></div>
                    <div className="servo-rail"><i style={{ width: `${kp * 76}%` }} /></div>
                  </div>
                  <button className="full-secondary" type="button" onClick={() => setSection("Configuration")}><Settings2 size={14} /> Tune this clock <ArrowRight size={14} /></button>
                </section>
              </div>
            </div>
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
                <div className="panel-heading"><div><span className="section-kicker">DISTRIBUTION</span><h2>Endpoint PHC difference density</h2></div><span className="quality-badge">RAW</span></div>
                <div className="histogram" aria-label="Raw endpoint offset histogram">{endpointDistribution.bins.map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div>
                <div className="hist-axis"><span>{formatNanoseconds(endpointDistribution.min)}</span><span>raw samples</span><span>{formatNanoseconds(endpointDistribution.max)}</span></div>
                <div className="distribution-stats"><div><span>σ</span><strong>{formatNanoseconds(endpointDistribution.sigma)}</strong></div><div><span>P95</span><strong>{formatNanoseconds(endpointDistribution.p95)}</strong></div><div><span>Skew</span><strong>{endpointDistribution.skew.toFixed(2)}</strong></div></div>
              </section>
              <section className="instrument-panel hop-table-panel">
                <div className="panel-heading"><div><span className="section-kicker">PHC + SERVO DATA</span><h2>Read-only clock comparisons</h2></div><span className="panel-meta">{range} raw window</span></div>
                <div className="data-table hop-table">
                  <div className="table-header"><span>Clock / PHC</span><span>Δ vs BC1</span><span>Hop Δ</span><span>PHC RMS</span><span>PTP frequency</span><span>PHC reads</span><span>PTP state</span></div>
                  {nodes.slice(1).map((node) => <button type="button" className="table-row" key={node.id} onClick={() => selectNode(node.id)}><span><i style={{ background: node.color }} />{node.label}<small>{node.phc}</small></span><strong>{formatOffset(node.offset, node.measured)}</strong><span>{formatOffset(node.hopOffset ?? 0, node.measured)}</span><span>{node.measured ? formatNanoseconds(node.rms) : "—"}</span><span>{node.ptpMeasured ? `${node.frequencyPpb.toFixed(1)} ppb` : "—"}</span><span>{node.sampleCount.toLocaleString()}</span><em className={node.state === "LOCKED" ? "state-good" : node.state === "NO DATA" || node.state === "STALE" ? "state-off" : "state-warn"}>{node.state}</em></button>)}
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
                  <div className="panel-heading"><div><span className="section-kicker">SERVO</span><h2>Clock discipline</h2></div><button className="quiet-button">Copy to all</button></div>
                  <div className="form-grid">
                    <label><span>Servo type</span><button className="select-control" type="button">PI controller <ChevronDown size={14} /></button></label>
                    <label><span>Target</span><button className="select-control" type="button">All boundary clocks <ChevronDown size={14} /></button></label>
                    <label><span>Proportional constant</span><div className="input-unit"><input value={kp.toFixed(2)} onChange={(event) => setKp(Number(event.target.value))} /><em>Kp</em></div></label>
                    <label><span>Integral constant</span><div className="input-unit"><input value={ki.toFixed(2)} onChange={(event) => setKi(Number(event.target.value))} /><em>Ki</em></div></label>
                    <label><span>First-step threshold</span><div className="input-unit"><input value="20,000" readOnly /><em>ns</em></div></label>
                    <label><span>Step threshold</span><div className="input-unit"><input value={stepThreshold} onChange={(event) => setStepThreshold(Number(event.target.value))} /><em>ns</em></div></label>
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
