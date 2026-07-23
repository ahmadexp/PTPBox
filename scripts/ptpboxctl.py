#!/usr/bin/env python3
"""Privileged, narrowly scoped lifecycle manager for a PTPBox cascade."""

from __future__ import annotations

import argparse
import json
import os
import re
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
SERVO_STATE_FILE = STATE_DIR / "servo-state.json"
SERVO_REQUEST_FILE = CONFIG_FILE.with_name("servo-request.json")
SUPPORTED_SERVOS = {"pi", "linreg", "nullf"}
PPS_CONFIG_FILE = LINUXPTP_CONFIG_DIR / "ptpbox-ts2phc.conf"

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
    "pps": {
        "enabled": False,
        "source": "BC1",
        "sinks": [],
        "output_pin": 0,
        "input_pin": 0,
        "channel": 0,
        "polarity": "rising",
        "pulse_width_ns": 100_000_000,
        "perout_phase_ns": 0,
        "extts_correction_ns": 0,
        "ts2phc": {
            "servo": "pi",
            "kp": 0.7,
            "ki": 0.3,
            "step_threshold_ns": 0,
            "first_step_threshold_ns": 20_000,
            "holdover_seconds": 0,
            "stable_threshold_ns": 100,
            "stable_samples": 10,
            "logging_level": 6,
        },
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


def enter_namespace_mount_context() -> None:
    """Use a mount view that contains the named network-namespace handles.

    ``ip netns exec`` creates a private mount namespace for its child.  That
    view keeps every named namespace handle alive even if the observation
    service is upgraded from an older, filesystem-sandboxed unit.  Prefer a
    surviving managed ptp4l process during that migration; on a clean boot the
    service and PID 1 share the host mount view and no switch is necessary.
    """
    if os.geteuid() != 0:
        return
    if os.environ.get("PTPBOX_MOUNT_CONTEXT") == "1":
        return

    processes = load_json(PIDS_FILE, [])
    if isinstance(processes, list):
        for item in processes:
            pid = item.get("pid") if isinstance(item, dict) else None
            if not isinstance(pid, int) or not process_alive(pid):
                continue
            mount_namespace = Path(f"/proc/{pid}/ns/mnt")
            if not mount_namespace.exists():
                continue
            nsenter = "/usr/bin/nsenter" if Path("/usr/bin/nsenter").exists() else "/bin/nsenter"
            if not Path(nsenter).exists():
                raise RuntimeError("nsenter is required to retain the managed network namespace context")
            env = "/usr/bin/env" if Path("/usr/bin/env").exists() else "/bin/env"
            os.execv(
                nsenter,
                [
                    nsenter,
                    f"--mount={mount_namespace}",
                    "--",
                    env,
                    "PTPBOX_MOUNT_CONTEXT=1",
                    sys.executable,
                    str(Path(__file__).resolve()),
                    *sys.argv[1:],
                ],
            )

    # A fresh start has no managed process to borrow from.  Escape an older
    # service sandbox so namespace setup happens in the persistent host view.
    try:
        current = os.readlink("/proc/self/ns/mnt")
        host = os.readlink("/proc/1/ns/mnt")
    except OSError:
        return
    if current == host:
        return
    nsenter = "/usr/bin/nsenter" if Path("/usr/bin/nsenter").exists() else "/bin/nsenter"
    if not Path(nsenter).exists():
        raise RuntimeError("nsenter is required to control host network namespaces from the sandboxed agent")
    env = "/usr/bin/env" if Path("/usr/bin/env").exists() else "/bin/env"
    os.execv(
        nsenter,
        [
            nsenter,
            "--mount=/proc/1/ns/mnt",
            "--",
            env,
            "PTPBOX_MOUNT_CONTEXT=1",
            sys.executable,
            str(Path(__file__).resolve()),
            *sys.argv[1:],
        ],
    )


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

    def merge(target: dict[str, Any], update: dict[str, Any]) -> None:
        for key, item in update.items():
            if isinstance(item, dict) and isinstance(target.get(key), dict):
                merge(target[key], item)
            else:
                target[key] = item

    merge(merged, value)
    return merged


def servo_state(topo: dict[str, Any] | None = None) -> dict[str, Any]:
    topo = topo or topology()
    saved = load_json(SERVO_STATE_FILE, {})
    saved_nodes = saved.get("nodes", {}) if isinstance(saved, dict) else {}
    default_type = str(config()["servo"]["type"])
    if default_type not in SUPPORTED_SERVOS:
        default_type = "pi"
    nodes: dict[str, dict[str, Any]] = {}
    for index, node in enumerate(topo["nodes"]):
        name = node["name"]
        item = saved_nodes.get(name, {}) if isinstance(saved_nodes, dict) else {}
        servo_type = item.get("type", default_type) if isinstance(item, dict) else default_type
        if servo_type not in SUPPORTED_SERVOS:
            servo_type = default_type
        enabled = bool(item.get("enabled", True)) if isinstance(item, dict) else True
        if index == 0:
            nodes[name] = {"mode": "reference", "enabled": False, "type": None, "changed_at": None, "holdover_started": None}
        else:
            nodes[name] = {
                "mode": "active" if enabled else "holdover",
                "enabled": enabled,
                "type": servo_type,
                "changed_at": item.get("changed_at") if isinstance(item, dict) else None,
                "holdover_started": item.get("holdover_started") if isinstance(item, dict) and not enabled else None,
            }
    return {"updated_at": saved.get("updated_at") if isinstance(saved, dict) else None, "nodes": nodes}


def save_servo_state(value: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    value["updated_at"] = time.time()
    SERVO_STATE_FILE.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    SERVO_STATE_FILE.chmod(0o644)


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
    servo_override: dict[str, Any] | None = None,
    free_running: bool = False,
) -> None:
    values = config()
    servo = {**values["servo"], **(servo_override or {})}
    if servo["type"] not in SUPPORTED_SERVOS:
        raise ValueError(f"unsupported servo type: {servo['type']}")
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
free_running {1 if free_running else 0}
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


def namespace_interface_details(namespace: str, interface: str, phc: str | None) -> dict[str, Any]:
    """Capture interface metadata while the timing port lives in its namespace."""
    link_result = command(["ip", "netns", "exec", namespace, "ip", "-j", "link", "show", "dev", interface], check=False)
    try:
        link_items = json.loads(link_result.stdout)
        link = link_items[0] if isinstance(link_items, list) and link_items else {}
    except json.JSONDecodeError:
        link = {}

    ethtool_result = command(["ip", "netns", "exec", namespace, "ethtool", interface], check=False)
    speed_match = re.search(r"^\s*Speed:\s*(\d+)\s*Mb/s", ethtool_result.stdout, re.MULTILINE)
    driver_result = command(["ip", "netns", "exec", namespace, "ethtool", "-i", interface], check=False)
    driver_fields = {
        key.strip(): value.strip()
        for line in driver_result.stdout.splitlines()
        if ":" in line
        for key, value in [line.split(":", 1)]
    }
    flags = link.get("flags", []) if isinstance(link, dict) else []
    return {
        "name": interface,
        "namespace": namespace,
        "state": str(link.get("operstate", "UNKNOWN")).upper(),
        "carrier": isinstance(flags, list) and "LOWER_UP" in flags,
        "speed_mbps": int(speed_match.group(1)) if speed_match else None,
        "mac": str(link.get("address", "")),
        "driver": driver_fields.get("driver"),
        "bus": driver_fields.get("bus-info"),
        "phc": phc,
        "hardware_timestamping": phc is not None,
    }


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
                "ingress_interface": namespace_interface_details(node["name"], node["ingress"], ingress_phc),
                "egress_interface": namespace_interface_details(node["name"], node["egress"], egress_phc),
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


def phc_capability(phc: str, field: str) -> int:
    value = Path("/sys/class/ptp", phc, field)
    try:
        return int(value.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def pps_device(inventory: dict[str, dict[str, Any]], node: str) -> str:
    phc = inventory.get(node, {}).get("measurement_phc")
    if not isinstance(phc, str) or not re.fullmatch(r"ptp\d+", phc):
        raise ValueError(f"{node} has no measurement PHC for PPS")
    device = f"/dev/{phc}"
    if not Path(device).exists():
        raise ValueError(f"PPS device is unavailable for {node}: {device}")
    return device


def validate_pps_hardware(values: dict[str, Any], inventory: dict[str, dict[str, Any]]) -> tuple[str | None, list[str]]:
    pps = values["pps"]
    source_name = pps["source"]
    sink_names = pps["sinks"]
    channel = int(pps["channel"])
    output_pin = int(pps["output_pin"])
    input_pin = int(pps["input_pin"])
    source_device: str | None = None

    if source_name != "external":
        source_device = pps_device(inventory, source_name)
        source_phc = source_device.removeprefix("/dev/")
        if phc_capability(source_phc, "n_periodic_outputs") <= channel:
            raise ValueError(f"{source_name} PHC does not expose periodic-output channel {channel}")
        if phc_capability(source_phc, "n_programmable_pins") <= output_pin:
            raise ValueError(f"{source_name} PHC does not expose PPS output pin {output_pin}")

    sink_devices: list[str] = []
    for name in sink_names:
        device = pps_device(inventory, name)
        phc = device.removeprefix("/dev/")
        if phc_capability(phc, "n_external_timestamps") <= channel:
            raise ValueError(f"{name} PHC does not expose external-timestamp channel {channel}")
        if phc_capability(phc, "n_programmable_pins") <= input_pin:
            raise ValueError(f"{name} PHC does not expose PPS input pin {input_pin}")
        sink_devices.append(device)
    return source_device, sink_devices


def render_ts2phc_config(
    path: Path,
    values: dict[str, Any],
    inventory: dict[str, dict[str, Any]],
) -> tuple[str | None, list[str]]:
    """Render a hardware-backed PPS distribution and ts2phc servo."""
    pps = values["pps"]
    ts2phc = pps["ts2phc"]
    source_device, sink_devices = validate_pps_hardware(values, inventory)
    sections: list[str] = []
    if source_device:
        sections.append(
            f"""[{source_device}]
ts2phc.master 1
ts2phc.channel {int(pps['channel'])}
ts2phc.pin_index {int(pps['output_pin'])}
"""
        )
    for device in sink_devices:
        sections.append(
            f"""[{device}]
ts2phc.channel {int(pps['channel'])}
ts2phc.extts_polarity {pps['polarity']}
ts2phc.extts_correction {int(pps['extts_correction_ns'])}
ts2phc.pin_index {int(pps['input_pin'])}
"""
        )
    perout_phase = f"ts2phc.perout_phase {int(pps['perout_phase_ns'])}\n" if source_device else ""
    text = f"""[global]
use_syslog 0
verbose 1
logging_level {int(ts2phc['logging_level'])}
clock_servo {ts2phc['servo']}
pi_proportional_const {float(ts2phc['kp'])}
pi_integral_const {float(ts2phc['ki'])}
step_threshold {float(ts2phc['step_threshold_ns']) / 1_000_000_000:.9f}
first_step_threshold {float(ts2phc['first_step_threshold_ns']) / 1_000_000_000:.9f}
servo_offset_threshold {int(ts2phc['stable_threshold_ns'])}
servo_num_offset_values {int(ts2phc['stable_samples'])}
ts2phc.holdover {int(ts2phc['holdover_seconds'])}
ts2phc.pulsewidth {int(pps['pulse_width_ns'])}
{perout_phase}

{''.join(sections)}"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o644)
    return source_device, sink_devices


def start_pps(values: dict[str, Any], inventory: dict[str, dict[str, Any]], processes: list[dict[str, Any]]) -> None:
    pps = values["pps"]
    if not pps["enabled"]:
        return
    source_device, sink_devices = render_ts2phc_config(PPS_CONFIG_FILE, values, inventory)
    args = ["ts2phc", "-f", str(PPS_CONFIG_FILE)]
    if source_device is None:
        args.extend(["-s", "generic"])
    args.extend(["-m", "-q"])
    spawn("PPS-ts2phc", args, processes)
    processes[-1].update(
        {
            "kind": "ts2phc",
            "pps_source": pps["source"],
            "pps_sinks": list(pps["sinks"]),
            "source_device": source_device,
            "sink_devices": sink_devices,
        }
    )


def start() -> dict[str, Any]:
    require_root()
    current = status()
    if current["running"]:
        return {"ok": True, "message": "cascade is already running", **current}
    setup_result = setup()
    prioritize_timestamp_workers()
    topo = topology()
    phcs_by_id = {item["id"]: item for item in setup_result.get("phcs", [])}
    control_state = servo_state(topo)
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
            node_servo = control_state["nodes"][node["name"]]
            render_ptp_config(
                role,
                node_config,
                boundary_jbod=boundary_jbod,
                ingress=node["ingress"] if role == "boundary" else None,
                egress=node["egress"] if role == "boundary" else None,
                uds_label=node["name"].lower(),
                servo_override={"type": node_servo["type"]} if node_servo["type"] else None,
                free_running=not node_servo["enabled"] and not first,
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
            processes[-1].update({"node": node["name"], "servo": node_servo})
        start_pps(config(), phcs_by_id, processes)
    except Exception:
        for item in reversed(processes):
            try:
                os.killpg(item["pid"], signal.SIGTERM)
            except OSError:
                pass
        raise
    PIDS_FILE.write_text(json.dumps(processes, indent=2) + "\n", encoding="utf-8")
    save_servo_state(control_state)
    return {"ok": True, "running": True, "processes": processes, "servo": control_state}


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_process(item: dict[str, Any], timeout: float = 4.0) -> None:
    pid = item.get("pid")
    if not isinstance(pid, int) or not process_alive(pid):
        return
    os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process_alive(pid):
        time.sleep(0.05)
    if process_alive(pid):
        os.killpg(pid, signal.SIGKILL)


def servo_apply() -> dict[str, Any]:
    """Apply a validated servo/holdover request without stopping observation."""
    require_root()
    request = load_json(SERVO_REQUEST_FILE)
    if not isinstance(request, dict):
        raise ValueError("missing servo request")
    target = request.get("target")
    enabled = request.get("enabled")
    servo_type = request.get("type")
    if not isinstance(target, str) or not isinstance(enabled, bool) or servo_type not in SUPPORTED_SERVOS:
        raise ValueError("servo request requires a valid target, enabled flag, and servo type")

    topo = topology()
    receivers = [node["name"] for node in topo["nodes"][1:]]
    if target != "all" and target not in receivers:
        raise ValueError(f"invalid servo target: {target}")
    targets = receivers if target == "all" else [target]
    if not enabled:
        targets = list(reversed(targets))

    processes = load_json(PIDS_FILE, [])
    if not isinstance(processes, list) or not processes:
        raise RuntimeError("cascade is not running")
    state = servo_state(topo)
    changed: list[str] = []
    for name in targets:
        index = next((i for i, item in enumerate(processes) if item.get("node") == name or str(item.get("label", "")).startswith(f"{name}-")), None)
        if index is None:
            raise RuntimeError(f"no managed ptp4l process for {name}")
        item = processes[index]
        if not isinstance(item.get("command"), list) or not item.get("label"):
            raise RuntimeError(f"invalid process record for {name}")
        node_index = next(i for i, node in enumerate(topo["nodes"]) if node["name"] == name)
        node = topo["nodes"][node_index]
        last = node_index == len(topo["nodes"]) - 1
        role = "client" if last else "boundary"
        inventory_items = load_json(PHC_MAP_FILE, [])
        if not isinstance(inventory_items, list):
            inventory_items = []
        inventory = next((entry for entry in inventory_items if isinstance(entry, dict) and entry.get("id") == name), {})
        node_config = LINUXPTP_CONFIG_DIR / f"ptpbox-{name.lower()}.conf"
        render_ptp_config(
            role,
            node_config,
            boundary_jbod=not last and not bool(inventory.get("shared_phc")),
            ingress=node["ingress"] if role == "boundary" else None,
            egress=node["egress"] if role == "boundary" else None,
            uds_label=name.lower(),
            servo_override={"type": servo_type},
            free_running=not enabled,
        )
        stop_process(item)
        replacement: list[dict[str, Any]] = []
        spawn(str(item["label"]), list(item["command"]), replacement)
        changed_at = time.time()
        node_state = {
            "mode": "active" if enabled else "holdover",
            "enabled": enabled,
            "type": servo_type,
            "changed_at": changed_at,
            "holdover_started": None if enabled else changed_at,
        }
        replacement[0].update({"node": name, "servo": node_state})
        processes[index] = replacement[0]
        state["nodes"][name] = node_state
        PIDS_FILE.write_text(json.dumps(processes, indent=2) + "\n", encoding="utf-8")
        save_servo_state(state)
        changed.append(name)
    return {"ok": True, "running": True, "changed": changed, "servo": state}


def status() -> dict[str, Any]:
    processes = load_json(PIDS_FILE, [])
    if not isinstance(processes, list):
        processes = []
    for item in processes:
        item["alive"] = isinstance(item.get("pid"), int) and process_alive(item["pid"])
    topo = topology()
    return {
        "running": bool(processes) and all(item.get("alive") for item in processes),
        "processes": processes,
        "namespaces": [node["name"] for node in topo["nodes"] if namespace_exists(node["name"])],
        "servo": servo_state(topo),
    }


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
    PHC_MAP_FILE.unlink(missing_ok=True)
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
    enter_namespace_mount_context()
    parser = argparse.ArgumentParser(description="Manage the PTPBox namespace cascade")
    parser.add_argument("action", choices=["discover", "setup", "start", "stop", "restart", "status", "servo", "teardown"])
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
        elif args.action == "servo":
            result = servo_apply()
        else:
            result = teardown()
        print(json.dumps(result))
    except (OSError, RuntimeError, ValueError, PermissionError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
