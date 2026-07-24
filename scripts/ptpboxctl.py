#!/usr/bin/env python3
"""Privileged, narrowly scoped lifecycle manager for a PTPBox cascade."""

from __future__ import annotations

import argparse
import json
import math
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
FAULT_REQUEST_FILE = CONFIG_FILE.with_name("fault-request.json")
FAULT_STATE_FILE = STATE_DIR / "fault-state.json"
IDENTIFICATION_REQUEST_FILE = CONFIG_FILE.with_name("identification-request.json")
IDENTIFICATION_STATE_FILE = STATE_DIR / "identification-state.json"
KALMAN_HELPER = Path(os.environ.get("PTPBOX_KALMAN_HELPER", "/usr/local/sbin/ptpbox-kalman-servo"))
EVENT_MONITOR_HELPER = Path(os.environ.get("PTPBOX_EVENT_MONITOR_HELPER", "/usr/local/sbin/ptpbox-event-monitor"))
PPS_COMPARE_HELPER = Path(os.environ.get("PTPBOX_PPS_COMPARE_HELPER", "/usr/local/sbin/ptpbox-pps-compare"))
PATH_EVENT_FILE = STATE_DIR / "path-events.jsonl"
SUPPORTED_SERVOS = {"pi", "linreg", "nullf", "kalman", "adaptive-kalman", "imm"}
LINUXPTP_NATIVE_SERVOS = {"pi", "linreg", "nullf"}
PPS_CONFIG_FILE = LINUXPTP_CONFIG_DIR / "ptpbox-ts2phc.conf"

DEFAULT_CONFIG: dict[str, Any] = {
    "profile": "G.8275.1 Telecom",
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
        "kalman": {
            "measurement_noise_ns": 200.0,
            "process_noise_ppb": 10.0,
            "phase_time_constant_s": 4.0,
            "innovation_gate_sigma": 6.0,
            "drift_noise_ppb_s2": 0.05,
        },
    },
    "security": {
        "authentication": {
            "enabled": False,
            "spp": 0,
            "active_key_id": 1,
            "sa_file": "/etc/linuxptp/ptpbox-sa.cfg",
            "allow_unauth": 0,
        },
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
        "comparison": {
            "enabled": False,
            "measure_only": True,
            "reference": "BC2",
            "history": 256,
        },
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
    external_kalman = servo["type"] in {"kalman", "adaptive-kalman", "imm"}
    linuxptp_servo = "nullf" if external_kalman else servo["type"]
    if linuxptp_servo not in LINUXPTP_NATIVE_SERVOS:
        raise ValueError(f"unsupported LinuxPTP servo type: {linuxptp_servo}")
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
        f"uds_ro_address /run/ptpbox/ptp4l-{uds_label}-ro\n"
        f"slave_event_monitor /run/ptpbox/monitor-{uds_label}"
        if uds_label
        else ""
    )
    profile = str(values.get("profile", "IEEE 1588 Default"))
    profile_lines = ""
    if profile in {"G.8275.1 Telecom", "G.8275.2 Telecom"}:
        profile_lines = "dataset_comparison G.8275.x\nG.8275.defaultDS.localPriority 128\nG.8275.portDS.localPriority 128"
    elif profile == "IEEE 802.1AS gPTP":
        profile_lines = "transportSpecific 0x1\nptp_dst_mac 01:80:C2:00:00:0E\np2p_dst_mac 01:80:C2:00:00:0E\nfollow_up_info 1"
    authentication = values.get("security", {}).get("authentication", {})
    authentication_lines = ""
    if isinstance(authentication, dict) and authentication.get("enabled"):
        sa_file = str(authentication["sa_file"])
        if not sa_file.startswith("/etc/linuxptp/") or ".." in Path(sa_file).parts:
            raise ValueError("security association file must be below /etc/linuxptp")
        if not Path(sa_file).is_file():
            raise ValueError(f"security association file does not exist: {sa_file}")
        authentication_lines = (
            f"spp {int(authentication['spp'])}\n"
            f"active_key_id {int(authentication['active_key_id'])}\n"
            f"sa_file {sa_file}\n"
            f"allow_unauth {int(authentication['allow_unauth'])}"
        )
    text = f"""[global]
domainNumber {int(values['domain'])}
network_transport {transport}
delay_mechanism {values['delay_mechanism']}
time_stamping {'hardware' if values['hardware_timestamping'] else 'software'}
twoStepFlag {1 if values['two_step'] else 0}
logSyncInterval {int(values['log_sync_interval'])}
summary_interval {int(values['log_sync_interval'])}
freq_est_interval {int(values['log_sync_interval'])}
tx_timestamp_timeout 100
free_running {1 if free_running or external_kalman else 0}
clock_servo {linuxptp_servo}
pi_proportional_const {float(servo['kp'])}
pi_integral_const {float(servo['ki'])}
step_threshold {float(servo['step_threshold_ns']) / 1_000_000_000:.9f}
first_step_threshold {float(servo['first_step_threshold_ns']) / 1_000_000_000:.9f}
sanity_freq_limit {int(servo['sanity_freq_limit_ppb'])}
logging_level 6
use_syslog 0
verbose 1
{profile_lines}
{authentication_lines}
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
    handle.write(f"\nPTPBox session start [{time.monotonic():.3f}]\n".encode())
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(0.12)
    if process.poll() is not None:
        handle.close()
        raise RuntimeError(f"{label} exited during startup; inspect {log_path}")
    processes.append({"label": label, "pid": process.pid, "command": args, "log": str(log_path)})


def spawn_kalman(
    node: str,
    measurement_phc: str | None,
    ptp_log: str,
    values: dict[str, Any],
    processes: list[dict[str, Any]],
    mode: str = "kalman",
) -> None:
    if not measurement_phc or not re.fullmatch(r"ptp\d+", measurement_phc):
        raise RuntimeError(f"Kalman servo requires a mapped measurement PHC for {node}")
    if not KALMAN_HELPER.exists():
        raise RuntimeError(f"Kalman servo helper is not installed: {KALMAN_HELPER}")
    servo = values["servo"]
    kalman = servo["kalman"]
    max_frequency = float(servo["sanity_freq_limit_ppb"])
    if max_frequency <= 0:
        max_frequency = 500_000.0
    state_path = STATE_DIR / f"kalman-{node.lower()}.json"
    state_path.unlink(missing_ok=True)
    args = [
        str(KALMAN_HELPER),
        "--mode",
        mode,
        "--node",
        node,
        "--phc",
        f"/dev/{measurement_phc}",
        "--log",
        ptp_log,
        "--state",
        str(state_path),
        "--measurement-noise-ns",
        str(float(kalman["measurement_noise_ns"])),
        "--process-noise-ppb",
        str(float(kalman["process_noise_ppb"])),
        "--drift-noise-ppb-s2",
        str(float(kalman["drift_noise_ppb_s2"])),
        "--phase-time-constant-s",
        str(float(kalman["phase_time_constant_s"])),
        "--innovation-gate-sigma",
        str(float(kalman["innovation_gate_sigma"])),
        "--max-frequency-ppb",
        str(max_frequency),
        "--first-step-threshold-ns",
        str(float(servo["first_step_threshold_ns"])),
        "--identification-state",
        str(IDENTIFICATION_STATE_FILE),
    ]
    spawn(f"{node}-{mode.upper()}", args, processes)
    processes[-1].update(
        {
            "kind": "kalman",
            "servo_type": mode,
            "kalman_for": node,
            "phc": measurement_phc,
            "state": str(state_path),
            "observation_log": ptp_log,
        }
    )


def spawn_event_monitor(
    node: str,
    processes: list[dict[str, Any]],
    values: dict[str, Any] | None = None,
) -> None:
    if not EVENT_MONITOR_HELPER.exists():
        return
    label = node.lower()
    values = values or config()
    transport_specific = 1 if values.get("profile") == "IEEE 802.1AS gPTP" else 0
    socket_path = STATE_DIR / f"monitor-{label}"
    socket_path.unlink(missing_ok=True)
    args = [
        str(EVENT_MONITOR_HELPER),
        "--node",
        node,
        "--socket",
        str(socket_path),
        "--server",
        str(STATE_DIR / f"ptp4l-{label}"),
        "--output",
        str(PATH_EVENT_FILE),
        "--domain",
        str(int(values["domain"])),
        "--transport-specific",
        str(transport_specific),
    ]
    spawn(f"{node}-PATH", args, processes)
    processes[-1].update(
        {
            "kind": "path-monitor",
            "node": node,
            "socket": str(socket_path),
            "event_file": str(PATH_EVENT_FILE),
            "domain": int(values["domain"]),
            "transport_specific": transport_specific,
        }
    )


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
    if ts2phc["servo"] not in LINUXPTP_NATIVE_SERVOS:
        raise ValueError(f"unsupported ts2phc servo type: {ts2phc['servo']}")
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
    comparison = pps.get("comparison", {})
    if comparison.get("enabled"):
        if pps.get("source") != "external" or not comparison.get("measure_only"):
            raise ValueError("PPS common-edge comparison requires an external source in measure-only mode")
        if pps.get("polarity") == "both":
            raise ValueError("PPS common-edge comparison requires a single edge polarity")
        if not PPS_COMPARE_HELPER.exists():
            raise RuntimeError(f"PPS comparison helper is not installed: {PPS_COMPARE_HELPER}")
        validate_pps_hardware(values, inventory)
        args = [
            str(PPS_COMPARE_HELPER),
            "--reference",
            str(comparison["reference"]),
            "--pin",
            str(int(pps["input_pin"])),
            "--channel",
            str(int(pps["channel"])),
            "--polarity",
            str(pps["polarity"]),
            "--correction-ns",
            str(int(pps["extts_correction_ns"])),
            "--state",
            str(STATE_DIR / "pps-comparison.json"),
            "--history",
            str(int(comparison["history"])),
        ]
        for node in pps["sinks"]:
            args.extend(["--clock", f"{node}={pps_device(inventory, node)}"])
        spawn("PPS-COMPARE", args, processes)
        processes[-1].update(
            {
                "kind": "pps-comparison",
                "pps_source": "external",
                "pps_sinks": list(pps["sinks"]),
                "reference": comparison["reference"],
                "measure_only": True,
            }
        )
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
    values = config()
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
            # Enter only the declared network namespace. ``ip netns exec``
            # also creates a private mount namespace, which turns host UDS
            # paths into disconnected AppArmor paths and prevents ptp4l from
            # sending slave-event-monitor TLVs back to the observatory.
            args = [
                "nsenter",
                f"--net=/run/netns/{node['name']}",
                "--",
                "ptp4l",
                "-f",
                str(node_config),
            ]
            # Intermediate ports are declared in their config so the egress can
            # carry the static server role. Endpoints use the simpler CLI form.
            if role != "boundary":
                for interface in interfaces:
                    args.extend(["-i", interface])
            args.extend(["-m", "-q"])
            spawn(label, args, processes)
            processes[-1].update({"node": node["name"], "servo": node_servo})
            ptp_log = str(processes[-1].get("log", LOG_DIR / f"{label}.log"))
            if not first and node_servo["enabled"] and node_servo["type"] in {"kalman", "adaptive-kalman", "imm"}:
                spawn_kalman(
                    node["name"],
                    inventory.get("measurement_phc"),
                    ptp_log,
                    values,
                    processes,
                    mode=node_servo["type"],
                )
            if not first:
                spawn_event_monitor(node["name"], processes, values)
        start_pps(values, phcs_by_id, processes)
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
    identification = load_json(IDENTIFICATION_STATE_FILE, {})
    if (
        isinstance(identification, dict)
        and identification.get("enabled")
        and identification.get("target") in targets
    ):
        identification.update(
            {
                "enabled": False,
                "stopped_at": time.time(),
                "reason": "target servo changed",
            }
        )
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        temporary_identification = IDENTIFICATION_STATE_FILE.with_suffix(".json.tmp")
        temporary_identification.write_text(json.dumps(identification, indent=2) + "\n", encoding="utf-8")
        temporary_identification.replace(IDENTIFICATION_STATE_FILE)
        IDENTIFICATION_STATE_FILE.chmod(0o644)

    processes = load_json(PIDS_FILE, [])
    if not isinstance(processes, list) or not processes:
        raise RuntimeError("cascade is not running")
    state = servo_state(topo)
    changed: list[str] = []
    for name in targets:
        for monitor_index in reversed(
            [
                i
                for i, process in enumerate(processes)
                if process.get("kind") == "path-monitor" and process.get("node") == name
            ]
        ):
            stop_process(processes[monitor_index])
            processes.pop(monitor_index)
        for kalman_index in reversed(
            [
                i
                for i, process in enumerate(processes)
                if process.get("kind") == "kalman" and process.get("kalman_for") == name
            ]
        ):
            stop_process(processes[kalman_index])
            processes.pop(kalman_index)
        index = next(
            (
                i
                for i, item in enumerate(processes)
                if item.get("kind") != "kalman"
                and (item.get("node") == name or str(item.get("label", "")).startswith(f"{name}-"))
            ),
            None,
        )
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
            free_running=not enabled or servo_type in {"kalman", "adaptive-kalman", "imm"},
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
        inserted = 0
        if enabled and servo_type in {"kalman", "adaptive-kalman", "imm"}:
            kalman_processes: list[dict[str, Any]] = []
            spawn_kalman(
                name,
                inventory.get("measurement_phc"),
                str(replacement[0]["log"]),
                config(),
                kalman_processes,
                mode=servo_type,
            )
            processes[index + 1:index + 1] = kalman_processes
            inserted += len(kalman_processes)
        monitor_processes: list[dict[str, Any]] = []
        spawn_event_monitor(name, monitor_processes, config())
        if monitor_processes:
            processes[index + 1 + inserted:index + 1 + inserted] = monitor_processes
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
        "fault": load_json(FAULT_STATE_FILE, {"enabled": False}),
        "identification": load_json(IDENTIFICATION_STATE_FILE, {"enabled": False}),
    }


def stop() -> dict[str, Any]:
    require_root()
    fault = load_json(FAULT_STATE_FILE, {})
    if isinstance(fault, dict) and fault.get("enabled") and isinstance(fault.get("target"), str):
        fault_node = next((node for node in topology()["nodes"] if node["name"] == fault["target"]), None)
        if fault_node:
            command(
                ["ip", "netns", "exec", fault["target"], "tc", "qdisc", "del", "dev", fault_node["egress"], "root"],
                check=False,
            )
        fault.update({"enabled": False, "cleared_at": time.time(), "message": "cleared by cascade stop"})
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        FAULT_STATE_FILE.write_text(json.dumps(fault, indent=2) + "\n", encoding="utf-8")
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
    if IDENTIFICATION_STATE_FILE.exists():
        state = load_json(IDENTIFICATION_STATE_FILE, {})
        if isinstance(state, dict):
            state.update({"enabled": False, "stopped_at": time.time(), "reason": "cascade stopped"})
            IDENTIFICATION_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
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


def fault_apply() -> dict[str, Any]:
    """Apply or clear one bounded netem fault on a declared cascade egress."""
    require_root()
    request = load_json(FAULT_REQUEST_FILE)
    if not isinstance(request, dict):
        raise ValueError("missing fault request")
    target = request.get("target")
    enabled = request.get("enabled")
    topo = topology()
    allowed = {node["name"]: node for node in topo["nodes"][:-1]}
    if target not in allowed or not isinstance(enabled, bool):
        raise ValueError("fault target must be an upstream topology clock")
    node = allowed[str(target)]
    interface = node["egress"]
    previous = load_json(FAULT_STATE_FILE, {})
    if isinstance(previous, dict) and previous.get("enabled") and previous.get("target") != target:
        previous_node = allowed.get(str(previous.get("target")))
        if previous_node:
            command(
                ["ip", "netns", "exec", str(previous["target"]), "tc", "qdisc", "del", "dev", previous_node["egress"], "root"],
                check=False,
            )
    if not enabled:
        result = command(
            ["ip", "netns", "exec", str(target), "tc", "qdisc", "del", "dev", interface, "root"],
            check=False,
        )
        state = {
            "enabled": False,
            "target": target,
            "interface": interface,
            "cleared_at": time.time(),
            "message": result.stderr.strip() if result.returncode and "No such file" not in result.stderr else "cleared",
        }
    else:
        delay_us = float(request.get("delay_us", 0.0))
        jitter_us = float(request.get("jitter_us", 0.0))
        loss_pct = float(request.get("loss_pct", 0.0))
        duration_s = float(request.get("duration_s", 30.0))
        if not 0 <= delay_us <= 1_000_000 or not 0 <= jitter_us <= 1_000_000 or not 0 <= loss_pct <= 100 or not 1 <= duration_s <= 3600:
            raise ValueError("fault parameters are outside guarded limits")
        if delay_us == 0 and jitter_us == 0 and loss_pct == 0:
            raise ValueError("fault needs delay, jitter, or loss")
        netem = ["delay", f"{delay_us:.3f}us"]
        if jitter_us:
            netem.append(f"{jitter_us:.3f}us")
        if loss_pct:
            netem.extend(["loss", f"{loss_pct:.4f}%"])
        command(["ip", "netns", "exec", str(target), "tc", "qdisc", "replace", "dev", interface, "root", "netem", *netem])
        started_at = time.time()
        state = {
            "enabled": True,
            "target": target,
            "interface": interface,
            "delay_us": delay_us,
            "jitter_us": jitter_us,
            "loss_pct": loss_pct,
            "started_at": started_at,
            "expires_at": started_at + duration_s,
        }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    FAULT_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    FAULT_STATE_FILE.chmod(0o644)
    return {"ok": True, "fault": state}


def identification_apply() -> dict[str, Any]:
    """Arm or disarm one bounded multisine correction on a PTPBox servo."""
    require_root()
    request = load_json(IDENTIFICATION_REQUEST_FILE)
    if not isinstance(request, dict):
        raise ValueError("missing identification request")
    target = request.get("target")
    enabled = request.get("enabled")
    topo = topology()
    receivers = {node["name"] for node in topo["nodes"][1:]}
    if target not in receivers or not isinstance(enabled, bool):
        raise ValueError("identification target must be a downstream clock")
    if not enabled:
        previous = load_json(IDENTIFICATION_STATE_FILE, {})
        state = {
            **(previous if isinstance(previous, dict) else {}),
            "enabled": False,
            "target": target,
            "stopped_at": time.time(),
            "reason": "stopped by operator",
        }
    else:
        control_node = servo_state(topo)["nodes"].get(str(target), {})
        if control_node.get("enabled") is not True or control_node.get("type") not in {"kalman", "adaptive-kalman", "imm"}:
            raise RuntimeError("active identification requires a running PTPBox Kalman-family servo")
        amplitude = float(request.get("amplitude_ppb", 25.0))
        duration = float(request.get("duration_s", 180.0))
        offset_limit = float(request.get("offset_limit_ns", 5_000.0))
        frequencies = request.get("frequencies_hz")
        sample_rate_hz = 2.0 ** (-int(config()["log_sync_interval"]))
        frequency_limit_hz = 0.45 * sample_rate_hz
        if (
            not 0.1 <= amplitude <= 500.0
            or not 30.0 <= duration <= 900.0
            or not 100.0 <= offset_limit <= 100_000.0
            or not isinstance(frequencies, list)
            or not 1 <= len(frequencies) <= 8
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.002 <= float(value) <= frequency_limit_hz
                for value in frequencies
            )
        ):
            raise ValueError("identification request is outside guarded limits")
        started_at = time.time()
        state = {
            "enabled": True,
            "target": target,
            "servo": control_node["type"],
            "amplitude_ppb": amplitude,
            "frequencies_hz": [float(value) for value in frequencies],
            "offset_limit_ns": offset_limit,
            "started_at": started_at,
            "expires_at": started_at + duration,
            "duration_s": duration,
            "waveform": "equal-amplitude deterministic random-phase multisine",
            "safety": "automatic offset-limit and expiry abort",
        }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = IDENTIFICATION_STATE_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(IDENTIFICATION_STATE_FILE)
    IDENTIFICATION_STATE_FILE.chmod(0o644)
    return {"ok": True, "identification": state}


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
    parser.add_argument("action", choices=["discover", "setup", "start", "stop", "restart", "status", "servo", "fault", "identify", "teardown"])
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
        elif args.action == "fault":
            result = fault_apply()
        elif args.action == "identify":
            result = identification_apply()
        else:
            result = teardown()
        print(json.dumps(result))
    except (OSError, RuntimeError, ValueError, PermissionError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
