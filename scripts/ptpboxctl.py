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
TOPOLOGY_FILE = Path(os.environ.get("PTPBOX_TOPOLOGY", "/etc/ptpbox/topology.json"))
CONFIG_FILE = Path(os.environ.get("PTPBOX_CONFIG", "/etc/ptpbox/config.json"))
PIDS_FILE = STATE_DIR / "processes.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "domain": 24,
    "transport": "L2",
    "delay_mechanism": "P2P",
    "log_sync_interval": -4,
    "two_step": True,
    "hardware_timestamping": True,
    "servo": {
        "type": "pi",
        "kp": 0.7,
        "ki": 0.3,
        "step_threshold_ns": 20,
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
    return {"ok": True, "configured_namespaces": configured, "interfaces": len(assigned)}


def render_ptp_config(role: str, path: Path) -> None:
    values = config()
    servo = values["servo"]
    transport = {"L2": "L2", "UDPv4": "UDPv4", "UDPv6": "UDPv6"}[values["transport"]]
    role_line = "serverOnly 1" if role == "server" else "clientOnly 1"
    text = f"""[global]
domainNumber {int(values['domain'])}
network_transport {transport}
delay_mechanism {values['delay_mechanism']}
time_stamping {'hardware' if values['hardware_timestamping'] else 'software'}
twoStepFlag {1 if values['two_step'] else 0}
logSyncInterval {int(values['log_sync_interval'])}
clock_servo {servo['type']}
pi_proportional_const {float(servo['kp'])}
pi_integral_const {float(servo['ki'])}
step_threshold {float(servo['step_threshold_ns']) / 1_000_000_000:.9f}
first_step_threshold {float(servo['first_step_threshold_ns']) / 1_000_000_000:.9f}
sanity_freq_limit {int(servo['sanity_freq_limit_ppb'])}
logging_level 6
use_syslog 0
verbose 1
{role_line}
"""
    path.write_text(text, encoding="utf-8")


def timestamp_provider(namespace: str, interface: str) -> int | None:
    result = command(["ip", "netns", "exec", namespace, "ethtool", "-T", interface], check=False)
    for line in result.stdout.splitlines():
        if "Hardware timestamp provider index:" in line:
            try:
                return int(line.rsplit(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def spawn(label: str, args: list[str], processes: list[dict[str, Any]]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{label}.log"
    handle = log_path.open("ab", buffering=0)
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
    setup()
    topo = topology()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    server_config = STATE_DIR / "ptp4l-server.conf"
    client_config = STATE_DIR / "ptp4l-client.conf"
    render_ptp_config("server", server_config)
    render_ptp_config("client", client_config)
    values = config()
    servo = values["servo"]
    processes: list[dict[str, Any]] = []
    try:
        first = topo["nodes"][0]
        spawn("BC1-GM", ["ip", "netns", "exec", first["name"], "ptp4l", "-f", str(server_config), "-i", first["egress"], "-m", "-q"], processes)
        for node in topo["nodes"][1:-1]:
            spawn(f"{node['name']}-OC", ["ip", "netns", "exec", node["name"], "ptp4l", "-f", str(client_config), "-i", node["ingress"], "-m", "-q"], processes)
            source_phc = timestamp_provider(node["name"], node["ingress"])
            sink_phc = timestamp_provider(node["name"], node["egress"])
            if source_phc is None or sink_phc is None or source_phc != sink_phc:
                spawn(
                    f"{node['name']}-PHC",
                    [
                        "ip", "netns", "exec", node["name"], "phc2sys",
                        "-s", node["ingress"], "-c", node["egress"], "-O", "0",
                        "-P", str(servo["kp"]), "-I", str(servo["ki"]),
                        "-S", str(float(servo["step_threshold_ns"]) / 1_000_000_000),
                        "-F", str(float(servo["first_step_threshold_ns"]) / 1_000_000_000),
                        "-m", "-q",
                    ],
                    processes,
                )
            spawn(f"{node['name']}-GM", ["ip", "netns", "exec", node["name"], "ptp4l", "-f", str(server_config), "-i", node["egress"], "-m", "-q"], processes)
        last = topo["nodes"][-1]
        spawn(f"{last['name']}-OC", ["ip", "netns", "exec", last["name"], "ptp4l", "-f", str(client_config), "-i", last["ingress"], "-m", "-q"], processes)
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
