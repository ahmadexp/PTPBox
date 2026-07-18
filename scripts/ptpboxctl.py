#!/usr/bin/env python3
"""Privileged, narrowly scoped lifecycle manager for a PTPBox cascade."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


STATE_DIR = Path(os.environ.get("PTPBOX_CONTROL_STATE", "/run/ptpbox"))
LOG_DIR = Path(os.environ.get("PTPBOX_LOG_DIR", "/var/log/ptpbox"))
LINUXPTP_CONFIG_DIR = Path(os.environ.get("PTPBOX_LINUXPTP_CONFIG_DIR", "/etc/linuxptp"))
TOPOLOGY_FILE = Path(os.environ.get("PTPBOX_TOPOLOGY", "/etc/ptpbox/topology.json"))
CONFIG_FILE = Path(os.environ.get("PTPBOX_CONFIG", "/etc/ptpbox/config.json"))
PIDS_FILE = STATE_DIR / "processes.json"
PHC_MAP_FILE = STATE_DIR / "phcs.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "domain": 24,
    "transport": "L2",
    "delay_mechanism": "E2E",
    "log_sync_interval": 0,
    "two_step": True,
    "hardware_timestamping": True,
    "servo": {
        "type": "pi",
        "kp": 0.7,
        "ki": 0.3,
        "step_threshold_ns": 0,
        "first_step_threshold_ns": 20_000,
        "sanity_freq_limit_ppb": 200_000,
    },
}


def command(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {' '.join(args)}")
    return result


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("this action requires root")


def load_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def topology() -> dict[str, Any]:
    value = load_json(TOPOLOGY_FILE)
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), list) or len(value["nodes"]) < 2:
        raise ValueError(f"invalid or missing topology: {TOPOLOGY_FILE}")
    for node in value["nodes"]:
        if not all(isinstance(node.get(key), str) and node[key] for key in ("name", "ingress", "egress")):
            raise ValueError("every topology node requires name, ingress, and egress")
    return value


def config() -> dict[str, Any]:
    value = load_json(CONFIG_FILE)
    if not isinstance(value, dict):
        value = DEFAULT_CONFIG
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    if isinstance(value, dict):
        merged.update({key: item for key, item in value.items() if key != "servo"})
        if isinstance(value.get("servo"), dict):
            merged["servo"].update(value["servo"])
    return merged


def namespace_exists(name: str) -> bool:
    return command(["ip", "netns", "list"], check=False).stdout.splitlines() and any(line.split()[0] == name for line in command(["ip", "netns", "list"], check=False).stdout.splitlines())


def interface_in_namespace(name: str, interface: str) -> bool:
    return command(["ip", "netns", "exec", name, "ip", "link", "show", "dev", interface], check=False).returncode == 0


def setup() -> dict[str, Any]:
    require_root()
    topo = topology()
    assigned = {item for node in topo["nodes"] for item in (node["ingress"], node["egress"])}
    management = set(topo.get("management_interfaces", []))
    overlap = assigned & management
    if overlap:
        raise ValueError(f"refusing to move management interfaces: {', '.join(sorted(overlap))}")

    missing = [item for item in assigned if not Path("/sys/class/net", item).exists() and not any(interface_in_namespace(node["name"], item) for node in topo["nodes"] if namespace_exists(node["name"]))]
    if missing:
        raise ValueError(f"interfaces not found: {', '.join(sorted(missing))}")

    configured: list[str] = []
    for node in topo["nodes"]:
        name = node["name"]
        if not namespace_exists(name):
            command(["ip", "netns", "add", name])
        for interface in (node["ingress"], node["egress"]):
            if not interface_in_namespace(name, interface):
                command(["ip", "link", "set", "dev", interface, "netns", name])
            command(["ip", "netns", "exec", name, "ip", "link", "set", "dev", interface, "up"])
        command(["ip", "netns", "exec", name, "ip", "link", "set", "lo", "up"])
        configured.append(name)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    phcs = write_phc_inventory(topo)
    return {"ok": True, "configured_namespaces": configured, "interfaces": len(assigned), "phcs": phcs}


def render_ptp_config(
    role: str,
    path: Path,
    boundary_jbod: bool = False,
    ingress: str | None = None,
    egress: str | None = None,
    uds_label: str | None = None,
) -> None:
    values = config()
    servo = values["servo"]
    transport = {"L2": "L2", "UDPv4": "UDPv4", "UDPv6": "UDPv6"}[values["transport"]]
    if role == "boundary" and (not ingress or not egress):
        raise ValueError("a boundary clock requires ingress and egress interfaces")
    # The physical chain has deliberate direction. Static port roles prevent a
    # downstream free-running clock from being selected in reverse while an
    # upstream link is starting or faulted.
    role_line = (
        "serverOnly 1\npriority1 1"
        if role == "server"
        else "clientOnly 1"
        if role == "client"
        else "BMCA noop\nclientOnly 1"
    )
    jbod_line = "boundary_clock_jbod 1" if boundary_jbod else ""
    port_sections = f"\n[{ingress}]\n\n[{egress}]\nserverOnly 1\n" if role == "boundary" else ""
    uds_lines = (
        f"uds_address /run/ptpbox/ptp4l-{uds_label}\n"
        f"uds_ro_address /run/ptpbox/ptp4l-{uds_label}-ro"
        if uds_label
        else ""
    )
    text = f"""[global]
domainNumber {int(values['domain'])}
network_transport {transport}
delay_mechanism {values['delay_mechanism']}
time_stamping {'hardware' if values['hardware_timestamping'] else 'software'}
twoStepFlag {1 if values['two_step'] else 0}
logSyncInterval {int(values['log_sync_interval'])}
summary_interval {int(values['log_sync_interval'])}
tx_timestamp_timeout 100
clock_servo {servo['type']}
pi_proportional_const {float(servo['kp'])}
pi_integral_const {float(servo['ki'])}
step_threshold {float(servo['step_threshold_ns']) / 1_000_000_000:.9f}
first_step_threshold {float(servo['first_step_threshold_ns']) / 1_000_000_000:.9f}
sanity_freq_limit {int(servo['sanity_freq_limit_ppb'])}
logging_level 6
use_syslog 0
verbose 1
{uds_lines}
{role_line}
{jbod_line}
{port_sections}
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o644)


def timestamp_provider(namespace: str, interface: str) -> int | None:
    result = command(["ip", "netns", "exec", namespace, "ethtool", "-T", interface], check=False)
    for line in result.stdout.splitlines():
        if "Hardware timestamp provider index:" in line:
            try:
                return int(line.rsplit(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def write_phc_inventory(topo: dict[str, Any]) -> list[dict[str, Any]]:
    """Persist the read-only PHC measurement map for the observation agent."""
    inventory: list[dict[str, Any]] = []
    for index, node in enumerate(topo["nodes"]):
        ingress_index = timestamp_provider(node["name"], node["ingress"])
        egress_index = timestamp_provider(node["name"], node["egress"])
        ingress_phc = f"ptp{ingress_index}" if ingress_index is not None else None
        egress_phc = f"ptp{egress_index}" if egress_index is not None else None
        inventory.append(
            {
                "id": node["name"],
                "namespace": node["name"],
                "ingress": node["ingress"],
                "egress": node["egress"],
                "ingress_phc": ingress_phc,
                "egress_phc": egress_phc,
                # The first card supplies time on its egress. Every following
                # card is measured at the ingress PHC disciplined by ptp4l.
                "measurement_phc": egress_phc if index == 0 else ingress_phc,
                "shared_phc": ingress_index is not None and ingress_index == egress_index,
            }
        )
    PHC_MAP_FILE.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    PHC_MAP_FILE.chmod(0o644)
    return inventory


def prioritize_timestamp_workers() -> list[int]:
    """Apply LinuxPTP's documented ICE worker mitigation when present."""
    prioritized: list[int] = []
    for comm_path in Path("/proc").glob("[0-9]*/comm"):
        try:
            name = comm_path.read_text(encoding="utf-8").strip()
            pid = int(comm_path.parent.name)
        except (OSError, ValueError):
            continue
        if not name.startswith("ice-ptp-"):
            continue
        result = command(["chrt", "-r", "--pid", "30", str(pid)], check=False)
        if result.returncode == 0:
            prioritized.append(pid)
    return prioritized


def spawn(label: str, args: list[str], processes: list[dict[str, Any]]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{label}.log"
    handle = log_path.open("ab", buffering=0)
    log_path.chmod(0o644)
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(0.12)
    if process.poll() is not None:
        handle.close()
        raise RuntimeError(f"{label} exited during startup; inspect {log_path}")
    processes.append({"label": label, "pid": process.pid, "command": args, "log": str(log_path)})


def start() -> dict[str, Any]:
    require_root()
    current = status()
    if current["running"]:
        return {"ok": True, "message": "cascade is already running", **current}
    setup_result = setup()
    prioritize_timestamp_workers()
    topo = topology()
    phcs_by_id = {item["id"]: item for item in setup_result.get("phcs", [])}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    processes: list[dict[str, Any]] = []
    try:
        for index, node in enumerate(topo["nodes"]):
            first = index == 0
            last = index == len(topo["nodes"]) - 1
            role = "server" if first else "client" if last else "boundary"
            label = f"{node['name']}-{'GM' if first else 'OC' if last else 'BC'}"
            node_config = LINUXPTP_CONFIG_DIR / f"ptpbox-{node['name'].lower()}.conf"
            inventory = phcs_by_id.get(node["name"], {})
            # LinuxPTP requires this flag when two interface names expose
            # different provider indices. On the reference Mellanox cards the
            # providers are hardware-synchronized; no host-side clock loop is
            # started or required for their operation.
            boundary_jbod = not first and not last and not bool(inventory.get("shared_phc"))
            render_ptp_config(
                role,
                node_config,
                boundary_jbod=boundary_jbod,
                ingress=node["ingress"] if role == "boundary" else None,
                egress=node["egress"] if role == "boundary" else None,
                uds_label=node["name"].lower(),
            )
            interfaces = [node["egress"]] if first else [node["ingress"]] if last else [node["ingress"], node["egress"]]
            args = ["ip", "netns", "exec", node["name"], "ptp4l", "-f", str(node_config)]
            # Intermediate ports are declared in their config so the egress can
            # carry the static server role. Endpoints use the simpler CLI form.
            if role != "boundary":
                for interface in interfaces:
                    args.extend(["-i", interface])
            args.extend(["-m", "-q"])
            spawn(label, args, processes)
    except Exception:
        for item in reversed(processes):
            try:
                os.killpg(item["pid"], signal.SIGTERM)
            except OSError:
                pass
        raise
    PIDS_FILE.write_text(json.dumps(processes, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "running": True, "processes": processes}


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status() -> dict[str, Any]:
    processes = load_json(PIDS_FILE, [])
    if not isinstance(processes, list):
        processes = []
    for item in processes:
        item["alive"] = isinstance(item.get("pid"), int) and process_alive(item["pid"])
    return {"running": bool(processes) and all(item.get("alive") for item in processes), "processes": processes, "namespaces": [node["name"] for node in topology()["nodes"] if namespace_exists(node["name"])]}


def stop() -> dict[str, Any]:
    require_root()
    processes = load_json(PIDS_FILE, [])
    stopped: list[str] = []
    if isinstance(processes, list):
        for item in reversed(processes):
            pid = item.get("pid")
            if isinstance(pid, int) and process_alive(pid):
                try:
                    os.killpg(pid, signal.SIGTERM)
                    stopped.append(item.get("label", str(pid)))
                except OSError:
                    pass
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and any(isinstance(item.get("pid"), int) and process_alive(item["pid"]) for item in processes):
            time.sleep(0.1)
        for item in processes:
            pid = item.get("pid")
            if isinstance(pid, int) and process_alive(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
    PIDS_FILE.unlink(missing_ok=True)
    return {"ok": True, "running": False, "stopped": stopped}


def teardown() -> dict[str, Any]:
    require_root()
    stop()
    topo = topology()
    restored: list[str] = []
    for node in reversed(topo["nodes"]):
        name = node["name"]
        if not namespace_exists(name):
            continue
        for interface in (node["ingress"], node["egress"]):
            if interface_in_namespace(name, interface):
                command(["ip", "netns", "exec", name, "ip", "link", "set", "dev", interface, "netns", "1"])
                command(["ip", "link", "set", "dev", interface, "up"], check=False)
                restored.append(interface)
        command(["ip", "netns", "del", name])
    return {"ok": True, "restored_interfaces": restored}


def discover() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for interface in sorted(Path("/sys/class/net").iterdir(), key=lambda item: item.name):
        if interface.name == "lo":
            continue
        ptp_dir = interface / "device" / "ptp"
        try:
            phcs = [item.name for item in ptp_dir.iterdir()]
        except OSError:
            phcs = []
        items.append({"name": interface.name, "state": (interface / "operstate").read_text().strip(), "phc": phcs[0] if phcs else None})
    return {"interfaces": items}


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the PTPBox namespace cascade")
    parser.add_argument("action", choices=["discover", "setup", "start", "stop", "restart", "status", "teardown"])
    args = parser.parse_args()
    try:
        if args.action == "discover":
            result = discover()
        elif args.action == "setup":
            result = setup()
        elif args.action == "start":
            result = start()
        elif args.action == "stop":
            result = stop()
        elif args.action == "restart":
            stop()
            result = start()
        elif args.action == "status":
            result = status()
        else:
            result = teardown()
        print(json.dumps(result))
    except (OSError, RuntimeError, ValueError, PermissionError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
