"use client";

import { Cpu, HardDrive, MemoryStick, Network, ShieldCheck, Thermometer } from "lucide-react";

export type SystemPayload = {
  timestamp?: number;
  host?: {
    hostname?: string | null;
    kernel?: string | null;
    os?: string | null;
    uptime_s?: number | null;
    boot_time?: number | null;
  } | null;
  cpu?: {
    model?: string | null;
    cores?: number | null;
    threads?: number | null;
    load_average?: number[] | null;
    mhz_current?: number | null;
    mhz_minimum?: number | null;
    mhz_maximum?: number | null;
    busy_pct?: number | null;
    sampled_over_s?: number | null;
  } | null;
  memory?: {
    total_kb?: number | null;
    available_kb?: number | null;
    used_kb?: number | null;
    used_pct?: number | null;
    buffers_kb?: number | null;
    cached_kb?: number | null;
    swap_total_kb?: number | null;
    swap_used_kb?: number | null;
  } | null;
  storage?: Array<{
    mount: string;
    device?: string;
    fstype?: string;
    total_bytes: number;
    used_bytes: number;
    available_bytes?: number;
    used_pct: number;
  }> | null;
  thermal?: Array<{ source: string; label: string; device?: string | null; temperature_c: number }> | null;
  pci?: Array<{
    slot: string;
    vendor?: string | null;
    vendor_id?: string | null;
    device_id?: string | null;
    driver?: string | null;
    description?: string | null;
  }> | null;
  network?: {
    status?: string;
    manager?: string;
    editable?: boolean;
    interpretation?: string;
    interfaces?: Array<{
      name: string; state?: string | null; mac?: string | null; mtu?: number | null;
      role?: string; carries_default_route?: boolean; connection?: string | null;
      manager_state?: string | null;
      addresses?: Array<{ address: string; prefix?: number; family?: string; scope?: string }>;
    }>;
    default_routes?: Array<{ family?: string; gateway?: string | null; device?: string | null; source?: string | null; metric?: number | null; protocol?: string | null }>;
    resolvers?: Array<{ scope: string; servers: string[] }>;
    observations?: {
      addressed_timing_ports?: string[];
      declared_timing_ports?: number;
      timing_ports_visible_here?: number;
      management_without_default_route?: string[];
      note?: string;
    };
  } | null;
  topology?: {
    status?: string;
    declared_nodes?: string[];
    links?: Array<{
      from: string;
      to: string;
      from_port: string;
      to_port: string;
      speed_mbps?: number | null;
      carrier?: boolean;
      verified?: boolean;
      problems?: string[];
    }>;
    verified_links?: number;
    management_excluded?: string[];
    discovery?: { available?: boolean; reason?: string };
    interpretation?: string;
  } | null;
  provenance?: string;
};

const GB = 1024 * 1024 * 1024;

function bytes(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value >= GB) return `${(value / GB).toFixed(1)} GB`;
  return `${(value / (1024 * 1024)).toFixed(0)} MB`;
}

function kb(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return "—";
  return bytes(value * 1024);
}

function duration(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h ${minutes}m`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function pct(value?: number | null) {
  return value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(1)}%`;
}

/** Severity bands for a meter; deliberately conservative for a timing appliance. */
function severity(value?: number | null, warn = 75, critical = 90) {
  if (value == null || !Number.isFinite(value)) return "";
  if (value >= critical) return "critical";
  if (value >= warn) return "warning";
  return "";
}

function Meter({ value, warn, critical }: { value?: number | null; warn?: number; critical?: number }) {
  const width = value == null || !Number.isFinite(value) ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className="sys-meter">
      <div className={`sys-meter-fill ${severity(value, warn, critical)}`} style={{ width: `${width}%` }} />
    </div>
  );
}

export function SystemObservatory({ system, updatedAt }: { system: SystemPayload | null; updatedAt?: number | null }) {
  if (!system) {
    return (
      <section className="panel">
        <div className="panel-heading">
          <div><span className="section-kicker">HOST</span><h2>System Observatory</h2></div>
        </div>
        <div className="empty-analysis">Waiting for <code>/api/system</code></div>
      </section>
    );
  }

  const host = system.host ?? {};
  const cpu = system.cpu ?? {};
  const memory = system.memory ?? {};
  const storage = system.storage ?? [];
  const thermal = [...(system.thermal ?? [])].sort((a, b) => b.temperature_c - a.temperature_c);
  const pci = system.pci ?? [];
  const topology = system.topology ?? {};
  const network = system.network ?? {};
  const links = topology.links ?? [];
  const hottest = thermal[0];
  // Group the PCI inventory by driver so seven identical timing adapters read as
  // one fleet rather than fourteen rows.
  const byDriver = new Map<string, number>();
  for (const device of pci) {
    const key = device.driver ?? "(no driver)";
    byDriver.set(key, (byDriver.get(key) ?? 0) + 1);
  }
  const timingCards = pci.filter((device) => device.driver === "mlx5_core");

  return (
    <>
      <section className="panel">
        <div className="panel-heading">
          <div><span className="section-kicker">HOST IDENTITY</span><h2>{host.hostname ?? "unknown host"}</h2></div>
          <span className="panel-meta">
            {updatedAt ? `updated ${new Date(updatedAt * 1000).toLocaleTimeString()}` : "read-only"}
          </span>
        </div>
        <div className="sys-identity">
          <div><small>Operating system</small><b>{host.os ?? "—"}</b></div>
          <div><small>Kernel</small><b>{host.kernel ?? "—"}</b></div>
          <div><small>Uptime</small><b>{duration(host.uptime_s)}</b></div>
          <div><small>Booted</small><b>{host.boot_time ? new Date(host.boot_time * 1000).toLocaleString() : "—"}</b></div>
        </div>
      </section>

      <div className="sys-grid">
        <section className="panel">
          <div className="panel-heading">
            <div><span className="section-kicker">COMPUTE</span><h2><Cpu size={15} /> Processor</h2></div>
            <span className="panel-meta">
              {cpu.cores ?? "—"} cores · {cpu.threads ?? "—"} threads
            </span>
          </div>
          <div className="sys-stat-row"><span>Model</span><strong>{cpu.model ?? "—"}</strong></div>
          <div className="sys-stat-row">
            <span>Busy</span>
            <strong>{pct(cpu.busy_pct)}{cpu.sampled_over_s ? ` over ${cpu.sampled_over_s}s` : ""}</strong>
          </div>
          <Meter value={cpu.busy_pct} />
          <div className="sys-stat-row">
            <span>Load average</span>
            <strong>{cpu.load_average?.length ? cpu.load_average.map((item) => item.toFixed(2)).join(" · ") : "—"}</strong>
          </div>
          <div className="sys-stat-row">
            <span>Frequency</span>
            <strong>
              {cpu.mhz_current ? `${cpu.mhz_current.toFixed(0)} MHz` : "—"}
              {cpu.mhz_maximum ? ` of ${cpu.mhz_maximum.toFixed(0)} MHz` : ""}
            </strong>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div><span className="section-kicker">MEMORY</span><h2><MemoryStick size={15} /> RAM and swap</h2></div>
            <span className="panel-meta">{kb(memory.total_kb)} installed</span>
          </div>
          <div className="sys-stat-row"><span>Used</span><strong>{kb(memory.used_kb)} · {pct(memory.used_pct)}</strong></div>
          <Meter value={memory.used_pct} />
          <div className="sys-stat-row"><span>Available</span><strong>{kb(memory.available_kb)}</strong></div>
          <div className="sys-stat-row"><span>Cached / buffers</span><strong>{kb(memory.cached_kb)} / {kb(memory.buffers_kb)}</strong></div>
          <div className="sys-stat-row">
            <span>Swap</span>
            <strong>{memory.swap_total_kb ? `${kb(memory.swap_used_kb)} of ${kb(memory.swap_total_kb)}` : "none"}</strong>
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="panel-heading">
          <div><span className="section-kicker">CAPACITY</span><h2><HardDrive size={15} /> Filesystems</h2></div>
          <span className="panel-meta">{storage.length} mounted</span>
        </div>
        {storage.length ? (
          <table className="sys-table">
            <thead><tr><th>Mount</th><th>Type</th><th>Used</th><th>Total</th><th>Utilisation</th></tr></thead>
            <tbody>
              {storage.map((item) => (
                <tr key={item.mount}>
                  <td><code>{item.mount}</code></td>
                  <td>{item.fstype ?? "—"}</td>
                  <td>{bytes(item.used_bytes)}</td>
                  <td>{bytes(item.total_bytes)}</td>
                  <td className="sys-meter-cell">
                    <Meter value={item.used_pct} warn={80} critical={92} />
                    <span>{pct(item.used_pct)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="empty-analysis">No local filesystems reported</div>}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div><span className="section-kicker">THERMAL</span><h2><Thermometer size={15} /> Sensors</h2></div>
          <span className="panel-meta">
            {hottest ? `hottest ${hottest.temperature_c.toFixed(1)} °C · ${hottest.label}` : "no sensors"}
          </span>
        </div>
        {thermal.length ? (
          <div className="sys-thermal">
            {thermal.map((item) => (
              <div key={item.source} className={`sys-thermal-cell ${severity(item.temperature_c, 90, 100)}`}>
                <small>{item.label}</small>
                <b>{item.temperature_c.toFixed(1)} °C</b>
                <Meter value={item.temperature_c} warn={90} critical={100} />
              </div>
            ))}
          </div>
        ) : <div className="empty-analysis">No thermal sensors exposed</div>}
        <div className="dyn-evidence-note">
          <ShieldCheck size={14} />
          <span>Adapter sensors are attributed to their PCI device because identical cards expose identical sensor names.</span>
        </div>
      </section>


      <section className="panel">
        <div className="panel-heading">
          <div><span className="section-kicker">NETWORK</span><h2><Network size={15} /> Addressing and routing</h2></div>
          <span className="quality-badge">{network.editable === false ? "READ ONLY" : (network.status ?? "").toUpperCase()}</span>
        </div>
        {(network.interfaces ?? []).length ? (
          <table className="sys-table">
            <thead><tr><th>Interface</th><th>Role</th><th>State</th><th>Addresses</th><th>MTU</th><th>Connection</th></tr></thead>
            <tbody>
              {(network.interfaces ?? []).map((item) => (
                <tr key={item.name}>
                  <td>
                    <code>{item.name}</code>
                    {item.carries_default_route ? <span className="net-badge">default route</span> : null}
                  </td>
                  <td>{item.role}</td>
                  <td className={item.state === "UP" ? "" : "sys-problem"}>{item.state ?? "—"}</td>
                  <td>
                    {item.addresses?.length
                      ? item.addresses.map((entry) => (
                          <div key={entry.address}><code>{entry.address}/{entry.prefix}</code></div>
                        ))
                      : <span className="sys-problem">no address</span>}
                  </td>
                  <td>{item.mtu ?? "—"}</td>
                  <td>{item.connection ?? "—"}{item.manager_state ? ` · ${item.manager_state}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="empty-analysis">No host-namespace interfaces reported</div>}
        <div className="sys-grid">
          <div>
            {(network.default_routes ?? []).map((route) => (
              <div className="sys-stat-row" key={`${route.family}-${route.gateway}`}>
                <span>Default route ({route.family})</span>
                <strong>via <code>{route.gateway}</code> on <code>{route.device}</code>{route.metric != null ? ` · metric ${route.metric}` : ""}{route.protocol ? ` · ${route.protocol}` : ""}</strong>
              </div>
            ))}
            {(network.resolvers ?? []).map((entry) => (
              <div className="sys-stat-row" key={entry.scope}>
                <span>Resolvers ({entry.scope})</span>
                <strong>{entry.servers.map((server) => <code key={server}>{server}</code>)}</strong>
              </div>
            ))}
          </div>
          <div>
            <div className="sys-stat-row"><span>Manager</span><strong>{network.manager ?? "unknown"}</strong></div>
            <div className="sys-stat-row">
              <span>Timing ports</span>
              <strong>{network.observations?.declared_timing_ports ?? 0} declared · {network.observations?.timing_ports_visible_here ?? 0} visible here</strong>
            </div>
            {network.observations?.addressed_timing_ports?.length ? (
              <div className="sys-stat-row"><span>Addressed timing ports</span><strong className="sys-problem">{network.observations.addressed_timing_ports.join(", ")}</strong></div>
            ) : null}
          </div>
        </div>
        {network.observations?.note ? (
          <div className="dyn-evidence-note"><ShieldCheck size={14} /><span>{network.observations.note}</span></div>
        ) : null}
        {network.interpretation ? (
          <div className="dyn-evidence-note"><ShieldCheck size={14} /><span>{network.interpretation}</span></div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div><span className="section-kicker">CABLING</span><h2><Network size={15} /> Declared cascade versus link state</h2></div>
          <span className="quality-badge">
            {links.length ? `${topology.verified_links ?? 0}/${links.length} VERIFIED` : (topology.status ?? "WAITING").toUpperCase()}
          </span>
        </div>
        {links.length ? (
          <div className="sys-chain">
            {links.map((link) => (
              <div key={`${link.from}-${link.to}`} className={`sys-chain-link ${link.verified ? "" : "warning"}`}>
                <div className="sys-chain-nodes"><b>{link.from}</b><span>→</span><b>{link.to}</b></div>
                <small><code>{link.from_port}</code> → <code>{link.to_port}</code></small>
                <div className="sys-chain-meta">
                  <span>{link.speed_mbps ? `${(link.speed_mbps / 1000).toFixed(0)}G` : "speed —"}</span>
                  <span>{link.carrier ? "carrier" : "no carrier"}</span>
                </div>
                {link.problems?.length ? <small className="sys-problem">{link.problems.join("; ")}</small> : null}
              </div>
            ))}
          </div>
        ) : <div className="empty-analysis">No declared topology to verify</div>}
        {topology.management_excluded?.length ? (
          <div className="sys-stat-row">
            <span>Management excluded</span>
            <strong>{topology.management_excluded.map((name) => <code key={name}>{name}</code>)}</strong>
          </div>
        ) : null}
        <div className="dyn-evidence-note">
          <ShieldCheck size={14} />
          <span>{topology.discovery?.available ? topology.interpretation : topology.discovery?.reason}</span>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div><span className="section-kicker">HARDWARE</span><h2>PCI inventory</h2></div>
          <span className="panel-meta">{pci.length} devices · {timingCards.length} timing functions</span>
        </div>
        <div className="sys-driver-summary">
          {[...byDriver.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([driver, count]) => (
            <div key={driver}><small>{driver}</small><b>{count}</b></div>
          ))}
        </div>
        {timingCards.length ? (
          <table className="sys-table">
            <thead><tr><th>Slot</th><th>Vendor</th><th>Driver</th><th>Description</th></tr></thead>
            <tbody>
              {timingCards.map((device) => (
                <tr key={device.slot}>
                  <td><code>{device.slot}</code></td>
                  <td>{device.vendor ?? "—"}</td>
                  <td><code>{device.driver ?? "—"}</code></td>
                  <td>{device.description ?? (`${device.vendor_id ?? ""} ${device.device_id ?? ""}`.trim() || "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        {system.provenance ? (
          <div className="dyn-evidence-note"><ShieldCheck size={14} /><span>{system.provenance}</span></div>
        ) : null}
      </section>
    </>
  );
}
