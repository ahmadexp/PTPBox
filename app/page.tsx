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
  Terminal,
  TimerReset,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Section = "Overview" | "Analytics" | "Experiments" | "Interfaces" | "Configuration" | "Event log";
type ConnectionMode = "checking" | "live" | "simulation";

type ClockNode = {
  id: string;
  label: string;
  role: "Grandmaster" | "Boundary" | "Ordinary";
  offset: number;
  meanPathDelay: number;
  rms: number;
  state: "LOCKED" | "TRACKING";
  ingress: string;
  egress: string;
  phc: string;
  color: string;
};

type HistoryPoint = {
  t: number;
  values: Record<string, number>;
};

type AgentStatus = {
  hostname?: string;
  linuxptp?: string;
  interfaces?: number;
  ptp_interfaces?: number;
  namespaces?: string[];
  running?: boolean;
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
  { id: "GM", label: "GM", role: "Grandmaster", offset: 0.4, meanPathDelay: 184, rms: 0.8, state: "LOCKED", ingress: "GNSS", egress: "enp25s0f0", phc: "ptp0", color: TRACE_COLORS[0] },
  { id: "BC01", label: "BC–01", role: "Boundary", offset: 4.8, meanPathDelay: 212, rms: 3.2, state: "LOCKED", ingress: "enp25s0f1", egress: "enp26s0f0", phc: "ptp1", color: TRACE_COLORS[1] },
  { id: "BC02", label: "BC–02", role: "Boundary", offset: 11.7, meanPathDelay: 228, rms: 6.1, state: "LOCKED", ingress: "enp26s0f1", egress: "enp27s0f0", phc: "ptp3", color: TRACE_COLORS[2] },
  { id: "BC03", label: "BC–03", role: "Boundary", offset: 24.3, meanPathDelay: 241, rms: 10.8, state: "LOCKED", ingress: "enp27s0f1", egress: "enp28s0f0", phc: "ptp5", color: TRACE_COLORS[3] },
  { id: "BC04", label: "BC–04", role: "Boundary", offset: 41.6, meanPathDelay: 237, rms: 18.9, state: "LOCKED", ingress: "enp28s0f1", egress: "enp103s0f0", phc: "ptp9", color: TRACE_COLORS[4] },
  { id: "BC05", label: "BC–05", role: "Boundary", offset: 63.8, meanPathDelay: 255, rms: 27.6, state: "LOCKED", ingress: "enp103s0f1", egress: "enp104s0f0", phc: "ptp11", color: TRACE_COLORS[5] },
  { id: "BC06", label: "BC–06", role: "Boundary", offset: 91.2, meanPathDelay: 269, rms: 40.2, state: "TRACKING", ingress: "enp104s0f1", egress: "enp105s0f0", phc: "ptp13", color: TRACE_COLORS[6] },
  { id: "OC", label: "OC", role: "Ordinary", offset: 118.4, meanPathDelay: 276, rms: 53.4, state: "LOCKED", ingress: "enp105s0f1", egress: "—", phc: "ptp14", color: TRACE_COLORS[7] },
];

const INTERFACES = [
  ["enp25s0f0np0", "BC–01 / OUT", "100 Gb/s", "ptp0", "UP"],
  ["enp25s0f1np1", "BC–01 / IN", "50 Gb/s", "ptp2", "UP"],
  ["enp26s0f0np0", "BC–02 / OUT", "50 Gb/s", "ptp1", "UP"],
  ["enp26s0f1np1", "BC–02 / IN", "50 Gb/s", "ptp1", "UP"],
  ["enp27s0f0np0", "BC–03 / OUT", "100 Gb/s", "ptp3", "UP"],
  ["enp27s0f1np1", "BC–03 / IN", "100 Gb/s", "ptp4", "UP"],
  ["enp28s0f0np0", "BC–04 / OUT", "100 Gb/s", "ptp5", "UP"],
  ["enp28s0f1np1", "BC–04 / IN", "100 Gb/s", "ptp8", "UP"],
  ["enp103s0f0np0", "BC–05 / OUT", "100 Gb/s", "ptp9", "UP"],
  ["enp103s0f1np1", "BC–05 / IN", "100 Gb/s", "ptp10", "UP"],
  ["enp104s0f0np0", "BC–06 / OUT", "100 Gb/s", "ptp11", "UP"],
  ["enp104s0f1np1", "BC–06 / IN", "100 Gb/s", "ptp12", "UP"],
  ["enp105s0f0np0", "OC / IN", "50 Gb/s", "ptp13", "UP"],
  ["enp105s0f1np1", "OC / MON", "100 Gb/s", "ptp14", "UP"],
  ["enp179s0f0", "MANAGEMENT", "1 Gb/s", "ptp6", "UP"],
  ["enp179s0f1", "SPARE", "—", "ptp7", "DOWN"],
];

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

function formatOffset(value: number) {
  if (Math.abs(value) < 1) return `${value >= 0 ? "+" : ""}${value.toFixed(1)} ns`;
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)} ns`;
}

function LineChart({ data, selected, compact = false }: { data: HistoryPoint[]; selected: string[]; compact?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap || data.length < 2) return;
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
    const allValues = data.flatMap((point) => selected.map((id) => point.values[id] ?? 0));
    const rawMax = Math.max(20, ...allValues.map((value) => Math.abs(value)));
    const yMax = Math.ceil(rawMax / 25) * 25;
    const yMin = -Math.max(25, Math.round(yMax * 0.18));
    const span = yMax - yMin;
    const x = (index: number) => pad.l + (index / (data.length - 1)) * plotW;
    const y = (value: number) => pad.t + ((yMax - value) / span) * plotH;

    ctx.clearRect(0, 0, w, h);
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
        ctx.fillText(`${Math.round(value)}`, pad.l - 10, py);
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
        const seconds = Math.round(-120 + i * 20);
        ctx.fillStyle = "#69818a";
        ctx.textAlign = i === 0 ? "left" : i === 6 ? "right" : "center";
        ctx.fillText(i === 6 ? "now" : `${seconds}s`, px, h - 12);
      }
    }

    selected.forEach((id) => {
      const nodeIndex = INITIAL_NODES.findIndex((node) => node.id === id);
      if (nodeIndex < 0) return;
      ctx.strokeStyle = TRACE_COLORS[nodeIndex];
      ctx.lineWidth = id === selected[selected.length - 1] ? 2.2 : 1.25;
      ctx.globalAlpha = id === selected[selected.length - 1] ? 1 : 0.54;
      ctx.beginPath();
      data.forEach((point, index) => {
        const px = x(index);
        const py = y(point.values[id] ?? 0);
        if (index === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
    });
    ctx.globalAlpha = 1;

    const activeId = selected[selected.length - 1];
    if (activeId) {
      const last = data[data.length - 1].values[activeId] ?? 0;
      const nodeIndex = INITIAL_NODES.findIndex((node) => node.id === activeId);
      ctx.fillStyle = TRACE_COLORS[nodeIndex];
      ctx.beginPath();
      ctx.arc(x(data.length - 1), y(last), 3.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }, [compact, data, selected]);

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
  const [nodes, setNodes] = useState(INITIAL_NODES);
  const [selectedNode, setSelectedNode] = useState("OC");
  const [visibleTraces, setVisibleTraces] = useState(["GM", "BC03", "BC06", "OC"]);
  const [history, setHistory] = useState(buildHistory);
  const [paused, setPaused] = useState(false);
  const [time, setTime] = useState("");
  const [connection, setConnection] = useState<ConnectionMode>("checking");
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [range, setRange] = useState("2 min");
  const [experimentRunning, setExperimentRunning] = useState(false);
  const [experimentProgress, setExperimentProgress] = useState(38);
  const [kp, setKp] = useState(0.7);
  const [ki, setKi] = useState(0.3);
  const [stepThreshold, setStepThreshold] = useState(20);
  const [applyOpen, setApplyOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [profile, setProfile] = useState("G.8275.1 Telecom");
  const [twoStep, setTwoStep] = useState(true);
  const [hardwareTs, setHardwareTs] = useState(true);
  const [sanity, setSanity] = useState(true);
  const tickRef = useRef(120);

  const activeNode = nodes.find((node) => node.id === selectedNode) ?? nodes[nodes.length - 1];

  useEffect(() => {
    const updateClock = () => setTime(new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZoneName: "short" }).format(new Date()));
    updateClock();
    const timer = window.setInterval(updateClock, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 1800);
    fetch(`${agentBaseUrl()}/api/status`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("agent unavailable");
        return response.json();
      })
      .then((status: AgentStatus) => {
        setAgentStatus(status);
        setConnection("live");
      })
      .catch(() => setConnection("simulation"))
      .finally(() => window.clearTimeout(timer));
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (paused) return;
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
  }, [experimentRunning, paused]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const stats = useMemo(() => {
    const final = nodes[nodes.length - 1];
    const peak = Math.max(...history.slice(-60).flatMap((point) => Object.values(point.values).map(Math.abs)));
    return [
      { label: "Cascade RMS", value: `${final.rms.toFixed(1)} ns`, delta: "−8.2%", note: "vs. previous run", icon: Activity, good: true },
      { label: "Peak offset", value: `${peak.toFixed(0)} ns`, delta: "+14 ns", note: "95th percentile", icon: Zap, good: false },
      { label: "Locked clocks", value: `${nodes.filter((node) => node.state === "LOCKED").length}/${nodes.length}`, delta: "Stable", note: "for 18m 42s", icon: ShieldCheck, good: true },
      { label: "MTIE · 300 s", value: "324 ns", delta: "Pass", note: "G.8273.2 mask", icon: TimerReset, good: true },
    ];
  }, [history, nodes]);

  const selectNode = (id: string) => {
    setSelectedNode(id);
    setVisibleTraces((current) => (current.includes(id) ? current : [...current.slice(-3), id]));
  };

  const handleApply = async () => {
    const payload = {
      profile,
      domain: 24,
      transport: "L2",
      delay_mechanism: "P2P",
      log_sync_interval: -4,
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
    if (connection === "live") {
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
    if (starting && connection === "live") {
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
            <div className="host-title"><span className={`status-orb ${connection}`} /> <strong>{connection === "live" ? "Hardware connected" : connection === "checking" ? "Finding host…" : "Hardware model"}</strong></div>
            <div className="host-row"><span>PTPBox</span><code>192.168.1.60</code></div>
            <div className="host-row"><span>LinuxPTP</span><code>{agentStatus?.linuxptp ?? "4.4"}</code></div>
            <div className="host-row"><span>PTP ports</span><code>{agentStatus?.ptp_interfaces ?? 16}</code></div>
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
              <div className="eyebrow"><span className={`status-orb ${connection}`} /> {connection === "live" ? "LIVE HARDWARE" : "INTERACTIVE HARDWARE MODEL"} <b>·</b> SESSION 024</div>
              <h1>{section === "Overview" ? "Cascade overview" : section}</h1>
              <p>{section === "Overview" ? "See timing quality degrade hop by hop, then tune the system that creates it." : section === "Analytics" ? "Interrogate stability, noise, and accumulated time error across every clock." : section === "Experiments" ? "Design, run, and compare repeatable servo response tests." : section === "Interfaces" ? "Map physical ports, PHCs, namespaces, and timestamping capability." : section === "Configuration" ? "Shape protocol and servo behavior with guarded, reviewable changes." : "A precise account of state changes, measurements, and operator actions."}</p>
            </div>
            <div className="heading-actions">
              <button className="secondary-button" type="button" onClick={() => setToast("Snapshot saved to run 024")}><Download size={15} /> Snapshot</button>
              <button className={`live-control ${paused ? "paused" : ""}`} type="button" onClick={() => setPaused((value) => !value)}>{paused ? <Play size={14} /> : <Pause size={14} />} {paused ? "Resume" : "Streaming"}</button>
            </div>
          </div>

          {section === "Overview" && (
            <div className="overview-layout">
              <section className="instrument-panel topology-panel">
                <div className="panel-heading">
                  <div><span className="section-kicker">LIVE TOPOLOGY</span><h2>Seven-hop clock cascade</h2></div>
                  <div className="panel-tools"><span><Radio size={13} /> 8 clocks · 14 PTP links</span><button className="quiet-button" type="button"><RefreshCw size={14} /> Rediscover</button></div>
                </div>
                <div className="topology-scroll">
                  <div className="topology-track">
                    {nodes.map((node, index) => (
                      <div className="node-unit" key={node.id}>
                        {index > 0 && <div className="hop-link"><span className="signal-dot one" /><span className="signal-dot two" /><small>H{index} · {node.meanPathDelay} ns</small></div>}
                        <button type="button" onClick={() => selectNode(node.id)} className={`clock-node ${selectedNode === node.id ? "selected" : ""} ${node.state === "TRACKING" ? "tracking" : ""}`}>
                          <div className="node-topline"><span>{node.role === "Boundary" ? "BC" : node.role === "Grandmaster" ? "GM" : "OC"}</span><i style={{ background: node.color }} /></div>
                          <div className="node-symbol"><Clock3 size={20} strokeWidth={1.4} /><span className="pulse-ring" /></div>
                          <strong>{node.label}</strong>
                          <span className="node-offset">{formatOffset(node.offset)}</span>
                          <div className="node-state"><span /> {node.state}</div>
                          <div className="node-ports"><code>{node.ingress}</code><ArrowRight size={11} /><code>{node.egress}</code></div>
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="topology-footer">
                  <span><i className="legend-dot locked" /> Locked</span><span><i className="legend-dot tracking" /> Tracking</span><span><i className="legend-line" /> Measured hop</span>
                  <p><Info size={13} /> Error growth is measured relative to the GM reference PHC.</p>
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
                    <div><span className="section-kicker">TIME ERROR</span><h2>Cascade offset</h2></div>
                    <div className="segmented-control">{["30 s", "2 min", "15 min"].map((item) => <button className={range === item ? "active" : ""} type="button" key={item} onClick={() => setRange(item)}>{item}</button>)}</div>
                  </div>
                  <div className="chart-legend">
                    {visibleTraces.map((id) => {
                      const node = nodes.find((item) => item.id === id)!;
                      return <button type="button" key={id} onClick={() => selectNode(id)} className={selectedNode === id ? "active" : ""}><i style={{ background: node.color }} /> {node.label}<strong>{formatOffset(node.offset)}</strong></button>;
                    })}
                    <span className="chart-unit">OFFSET · ns</span>
                  </div>
                  <LineChart data={history} selected={visibleTraces} />
                  <div className="chart-footer-note"><Sparkles size={14} /><span><strong>Insight:</strong> BC–06 contributes 31% of total cascade variance. The trace recovers cleanly after the 13:41 step.</span><button type="button" onClick={() => setSection("Analytics")}>Inspect <ArrowRight size={13} /></button></div>
                </section>

                <section className="instrument-panel selected-panel">
                  <div className="panel-heading">
                    <div><span className="section-kicker">SELECTED CLOCK</span><h2>{activeNode.label}</h2></div>
                    <button className="more-button" type="button">•••</button>
                  </div>
                  <div className="selected-status">
                    <div className="radial-score"><span>{Math.max(0, 100 - Math.round(activeNode.rms / 2))}</span><small>QUALITY</small></div>
                    <div><span className="locked-pill"><Check size={12} /> {activeNode.state}</span><strong>{formatOffset(activeNode.offset)}</strong><small>current master offset</small></div>
                  </div>
                  <div className="selected-metrics">
                    <div><span>RMS offset</span><strong>{activeNode.rms.toFixed(1)} ns</strong></div>
                    <div><span>Mean path delay</span><strong>{activeNode.meanPathDelay} ns</strong></div>
                    <div><span>Frequency adj.</span><strong>−12.4 ppb</strong></div>
                    <div><span>PHC device</span><strong>{activeNode.phc}</strong></div>
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
                  <div><span className="section-kicker">STABILITY EXPLORER</span><h2>Accumulated offset by clock</h2></div>
                  <div className="panel-tools"><button className="quiet-button"><ListFilter size={14} /> Signals</button><button className="quiet-button"><Download size={14} /> CSV</button></div>
                </div>
                <div className="analytics-traces">
                  {nodes.map((node) => <button type="button" key={node.id} className={visibleTraces.includes(node.id) ? "active" : ""} onClick={() => setVisibleTraces((current) => current.includes(node.id) ? current.filter((item) => item !== node.id) : [...current, node.id])}><i style={{ background: node.color }} />{node.label}</button>)}
                </div>
                <LineChart data={history} selected={visibleTraces.length ? visibleTraces : ["OC"]} />
              </section>
              <section className="instrument-panel distribution-panel">
                <div className="panel-heading"><div><span className="section-kicker">DISTRIBUTION</span><h2>OC offset density</h2></div><span className="quality-badge">NORMAL</span></div>
                <div className="histogram" aria-label="Offset histogram">{[8, 14, 21, 33, 47, 68, 88, 100, 93, 74, 56, 38, 24, 13, 7].map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div>
                <div className="hist-axis"><span>−50</span><span>0</span><span>+50</span><span>+100</span><span>+150 ns</span></div>
                <div className="distribution-stats"><div><span>σ</span><strong>53.4 ns</strong></div><div><span>P95</span><strong>138 ns</strong></div><div><span>Skew</span><strong>0.18</strong></div></div>
              </section>
              <section className="instrument-panel hop-table-panel">
                <div className="panel-heading"><div><span className="section-kicker">ERROR BUDGET</span><h2>Per-hop contribution</h2></div><span className="panel-meta">120 s rolling window</span></div>
                <div className="data-table hop-table">
                  <div className="table-header"><span>Clock</span><span>Offset</span><span>Hop Δ</span><span>RMS</span><span>MTIE · 300 s</span><span>Contribution</span><span>State</span></div>
                  {nodes.slice(1).map((node, index) => {
                    const contribution = Math.round((node.rms / nodes.slice(1).reduce((sum, item) => sum + item.rms, 0)) * 100);
                    return <button type="button" className="table-row" key={node.id} onClick={() => selectNode(node.id)}><span><i style={{ background: node.color }} />{node.label}<small>{node.phc}</small></span><strong>{formatOffset(node.offset)}</strong><span>+{(node.offset - nodes[index].offset).toFixed(1)} ns</span><span>{node.rms.toFixed(1)} ns</span><span>{Math.round(node.rms * 5.8)} ns</span><span><i className="contribution-bar"><b style={{ width: `${contribution * 3}%` }} /></i>{contribution}%</span><em className={node.state === "LOCKED" ? "state-good" : "state-warn"}>{node.state}</em></button>;
                  })}
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
                <article><Cpu size={18} /><span>PTP-capable ports</span><strong>16</strong><small>14 cascade · 2 utility</small></article>
                <article><Gauge size={18} /><span>Aggregate line rate</span><strong>1.2 Tb/s</strong><small>12 × 100G · 3 × 50G · 1 × 1G</small></article>
                <article><Clock3 size={18} /><span>Hardware clocks</span><strong>15</strong><small>All precise IEEE 1588 quality</small></article>
                <article><Network size={18} /><span>Drivers</span><strong>3</strong><small>mlx5_core · ice · ixgbe</small></article>
              </div>
              <section className="instrument-panel interface-table-panel">
                <div className="panel-heading"><div><span className="section-kicker">LIVE INVENTORY</span><h2>Physical interfaces & PHCs</h2></div><div className="panel-tools"><span className="scan-time"><RefreshCw size={13} /> Discovered 8 s ago</span><button className="quiet-button">Rescan</button></div></div>
                <div className="interface-map">
                  <div className="interface-map-labels"><span>BC–01</span><span>BC–02</span><span>BC–03</span><span>BC–04</span><span>BC–05</span><span>BC–06</span><span>OC</span></div>
                  <div className="interface-map-line">{Array.from({ length: 14 }).map((_, index) => <i key={index} className={index % 2 ? "in" : "out"} />)}</div>
                </div>
                <div className="data-table interface-table">
                  <div className="table-header"><span>Interface</span><span>Assignment</span><span>Link</span><span>PHC</span><span>Timestamping</span><span>Driver</span><span>State</span></div>
                  {INTERFACES.map((item, index) => <div className="table-row" key={item[0]}><span><i className={`port-icon ${item[4] === "UP" ? "up" : "down"}`} /> <strong>{item[0]}</strong><small>0000:{index < 2 ? "19" : index < 4 ? "1a" : index < 6 ? "1b" : index < 8 ? "1c" : `${67 + Math.floor((index - 8) / 2)}`}:00.{index % 2}</small></span><span>{item[1]}</span><strong>{item[2]}</strong><code>/dev/{item[3]}</code><span><ShieldCheck size={13} /> HW TX/RX</span><span>{index === 2 || index === 3 ? "ice" : index > 13 ? "ixgbe" : "mlx5_core"}</span><em className={item[4] === "UP" ? "state-good" : "state-off"}>{item[4]}</em></div>)}
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
                    <label><span>Sync interval</span><button className="select-control" type="button">−4 · 16/s <ChevronDown size={14} /></button></label>
                    <label><span>Delay mechanism</span><button className="select-control" type="button">Peer-to-peer <ChevronDown size={14} /></button></label>
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
            <div className="validation-banner"><ShieldCheck size={20} /><div><strong>Preflight checks passed</strong><span>16 interfaces available · 8 clocks responsive · rollback ready</span></div></div>
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
