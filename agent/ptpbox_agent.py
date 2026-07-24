#!/usr/bin/env python3
"""Small, dependency-free API for the PTPBox web console.

The agent intentionally separates read-only observation from privileged control.
It can run as an ordinary user for inventory and log telemetry. Start/stop calls
are delegated to a tightly scoped ptpboxctl sudo rule when the optional system
integration has been installed.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import json
import mimetypes
import os
import re
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ptpbox_research import ExperimentStore, RollingResearchEngine, path_regime_analysis  # noqa: E402


ROOT = Path(os.environ.get("PTPBOX_ROOT", Path.home() / "PTPBox"))
STATE_DIR = Path(os.environ.get("PTPBOX_STATE_DIR", ROOT / "runtime"))
CONFIG_FILE = Path(os.environ.get("PTPBOX_CONFIG", STATE_DIR / "config.json"))
SERVO_REQUEST_FILE = STATE_DIR / "servo-request.json"
SERVO_STATE_FILE = Path(os.environ.get("PTPBOX_SERVO_STATE", "/run/ptpbox/servo-state.json"))
HOLDOVER_SESSION_FILE = STATE_DIR / "holdover-session.json"
KALMAN_STATE_DIR = Path(os.environ.get("PTPBOX_KALMAN_STATE_DIR", "/run/ptpbox"))
CONTROL = Path(os.environ.get("PTPBOX_CONTROL", "/usr/local/sbin/ptpboxctl"))
WEB_ROOT = Path(os.environ.get("PTPBOX_WEB_ROOT", Path(__file__).parent / "static"))
LOG_DIR = Path(os.environ.get("PTPBOX_LOG_DIR", "/var/log/ptpbox"))
TOPOLOGY_FILE = Path(os.environ.get("PTPBOX_TOPOLOGY", Path(__file__).with_name("topology.json")))
PHC_MAP_FILE = Path(os.environ.get("PTPBOX_PHC_MAP", "/run/ptpbox/phcs.json"))
PPS_PROCESS_FILE = Path(os.environ.get("PTPBOX_PROCESS_STATE", "/run/ptpbox/processes.json"))
PATH_EVENT_FILE = Path(os.environ.get("PTPBOX_PATH_EVENTS", "/run/ptpbox/path-events.jsonl"))
FAULT_REQUEST_FILE = STATE_DIR / "fault-request.json"
FAULT_STATE_FILE = Path(os.environ.get("PTPBOX_FAULT_STATE", "/run/ptpbox/fault-state.json"))
IDENTIFICATION_REQUEST_FILE = STATE_DIR / "identification-request.json"
IDENTIFICATION_STATE_FILE = Path(os.environ.get("PTPBOX_IDENTIFICATION_STATE", "/run/ptpbox/identification-state.json"))
ALLOW_ORIGIN = os.environ.get("PTPBOX_ALLOW_ORIGIN", "*")
TELEMETRY_MAX_BYTES = 2_000_000
TELEMETRY_MAX_SAMPLES = 4096
TELEMETRY_STALE_AFTER_SECONDS = 5.0
TELEMETRY_MAX_PATH_DELAY_NS = 1_000_000.0
PHC_HISTORY_MAX_SAMPLES = 7200
PHC_STALE_AFTER_SECONDS = 3.0
PHC_CROSS_TIMESTAMP_SAMPLES = 9
RESEARCH_CACHE_SECONDS = max(1.0, float(os.environ.get("PTPBOX_RESEARCH_CACHE_SECONDS", "10")))
SUPPORTED_SERVOS = {"pi", "linreg", "nullf", "kalman", "adaptive-kalman", "imm"}
LINUXPTP_NATIVE_SERVOS = {"pi", "linreg", "nullf"}
LOG_PATTERN = re.compile(
    r"offset\s+(?P<offset>-?\d+(?:\.\d+)?)\s+"
    r"(?:(?P<servo_state>s\d+)\s+)?freq\s+(?P<freq>[+-]?\d+(?:\.\d+)?)\s+"
    r"path delay\s+(?P<delay>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
LOG_TIME_PATTERN = re.compile(r"\[(?P<seconds>\d+(?:\.\d+)?)\]")
LOG_SESSION_PATTERN = re.compile(r"(?:selected /dev/ptp\d+ as PTP clock|PTPBox session start)", re.IGNORECASE)

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

PROFILE_RULES: dict[str, dict[str, Any]] = {
    "IEEE 1588 Default": {"transport": {"L2", "UDPv4", "UDPv6"}, "delay": {"E2E", "P2P"}, "domain": (0, 127), "two_step": None},
    "G.8275.1 Telecom": {"transport": {"L2"}, "delay": {"E2E"}, "domain": (24, 43), "two_step": None},
    "G.8275.2 Telecom": {"transport": {"UDPv4", "UDPv6"}, "delay": {"E2E"}, "domain": (44, 63), "two_step": None},
    "IEEE 802.1AS gPTP": {"transport": {"L2"}, "delay": {"P2P"}, "domain": (0, 0), "two_step": True},
    "IEEE C37.238 Power": {"transport": {"L2"}, "delay": {"P2P"}, "domain": (254, 254), "two_step": None},
}


class Timespec(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


class PtpClockTime(ctypes.Structure):
    _fields_ = [("sec", ctypes.c_int64), ("nsec", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]


class PtpSysOffsetPrecise(ctypes.Structure):
    _fields_ = [
        ("device", PtpClockTime),
        ("sys_realtime", PtpClockTime),
        ("sys_monoraw", PtpClockTime),
        ("reserved", ctypes.c_uint32 * 4),
    ]


class PtpSysOffsetExtended(ctypes.Structure):
    _fields_ = [
        ("n_samples", ctypes.c_uint32),
        ("clockid", ctypes.c_int32),
        ("reserved", ctypes.c_uint32 * 2),
        ("ts", (PtpClockTime * 3) * 25),
    ]


def linux_iowr(type_: str, number: int, structure: type[ctypes.Structure]) -> int:
    """Build a Linux _IOWR request number without a compiled extension."""
    return (3 << 30) | (ctypes.sizeof(structure) << 16) | (ord(type_) << 8) | number


PTP_SYS_OFFSET_PRECISE = linux_iowr("=", 8, PtpSysOffsetPrecise)
PTP_SYS_OFFSET_EXTENDED = linux_iowr("=", 9, PtpSysOffsetExtended)
CLOCK_REALTIME = 0
CLOCK_MONOTONIC_RAW = 4


LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.clock_gettime.argtypes = [ctypes.c_int, ctypes.POINTER(Timespec)]
LIBC.clock_gettime.restype = ctypes.c_int
PHC_HISTORY: deque[dict[str, Any]] = deque(maxlen=PHC_HISTORY_MAX_SAMPLES)
PHC_HISTORY_LOCK = threading.Lock()
PHC_FDS: dict[str, int] = {}
PHC_FDS_LOCK = threading.Lock()
PHC_CROSS_TIMESTAMP_METHODS: dict[str, str] = {}
RESEARCH_ENGINE = RollingResearchEngine(PHC_HISTORY_MAX_SAMPLES)
_EXPERIMENT_STORES: dict[str, ExperimentStore] = {}
_EXPERIMENT_STORES_LOCK = threading.Lock()
HOLDOVER_LOCK = threading.RLock()
_TEMPERATURE_CACHE: tuple[float, dict[str, float]] = (0.0, {})
_CAPABILITY_CACHE: tuple[float, dict[str, Any]] = (0.0, {})
_RESEARCH_SNAPSHOT_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_RESEARCH_SNAPSHOT_REFRESHING: set[int] = set()
_RESEARCH_SNAPSHOT_CONDITION = threading.Condition()


@dataclass
class Interface:
    name: str
    state: str
    carrier: bool
    speed_mbps: int | None
    mac: str
    driver: str | None
    bus: str | None
    phc: str | None
    hardware_timestamping: bool
    namespace: str | None = None
    assignment: str | None = None


@dataclass(frozen=True)
class PhcCrossTimestamp:
    system_ns: int
    phc_minus_system_ns: int
    delay_ns: int
    method: str


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return default


def run(command: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)


def linuxptp_version() -> str:
    result = run(["ptp4l", "-v"])
    match = re.search(r"(\d+(?:\.\d+)+)", result.stdout + result.stderr)
    return match.group(1) if match else "unavailable"


def driver_for(interface: Path) -> str | None:
    try:
        return interface.joinpath("device", "driver").resolve().name
    except OSError:
        return None


def bus_for(interface: Path) -> str | None:
    try:
        return interface.joinpath("device").resolve().name
    except OSError:
        return None


def phc_for(interface: Path) -> str | None:
    ptp_dir = interface / "device" / "ptp"
    try:
        entries = sorted(ptp_dir.iterdir())
        return entries[0].name if entries else None
    except OSError:
        return None


def timestamp_info(name: str, discovered_phc: str | None) -> tuple[str | None, bool]:
    if discovered_phc:
        return discovered_phc, True
    result = run(["ethtool", "-T", name])
    output = result.stdout + result.stderr
    index = re.search(r"Hardware timestamp provider index:\s*(\d+)", output)
    capable = "hardware-raw-clock" in output and "hardware-transmit" in output and "hardware-receive" in output
    return (f"ptp{index.group(1)}" if index else None, capable)


def interfaces() -> list[Interface]:
    host_interfaces: list[Interface] = []
    topology_value = load_json(TOPOLOGY_FILE, {})
    management = topology_value.get("management_interfaces", []) if isinstance(topology_value, dict) else []
    try:
        host_paths = sorted(Path("/sys/class/net").iterdir(), key=lambda item: item.name)
    except OSError:
        host_paths = []
    for interface in host_paths:
        if interface.name == "lo":
            continue
        speed_raw = read_text(interface / "speed")
        try:
            speed = int(speed_raw)
            if speed < 0:
                speed = None
        except ValueError:
            speed = None
        phc, hardware_timestamping = timestamp_info(interface.name, phc_for(interface))
        assignment = None
        if interface.name in management:
            assignment = "MANAGEMENT" if management.index(interface.name) == 0 else "SPARE"
        host_interfaces.append(
            Interface(
                name=interface.name,
                state=read_text(interface / "operstate", "unknown").upper(),
                carrier=read_text(interface / "carrier") == "1",
                speed_mbps=speed,
                mac=read_text(interface / "address"),
                driver=driver_for(interface),
                bus=bus_for(interface),
                phc=phc,
                hardware_timestamping=hardware_timestamping,
                assignment=assignment,
            )
        )

    mapped_value = load_json(PHC_MAP_FILE, [])
    if not isinstance(mapped_value, list):
        return host_interfaces
    mapped_interfaces: list[Interface] = []
    mapped_names: set[str] = set()
    for index, item in enumerate(mapped_value):
        if not isinstance(item, dict):
            continue
        node_id = item.get("id")
        namespace = item.get("namespace")
        for direction in ("ingress", "egress"):
            name = item.get(direction)
            phc = item.get(f"{direction}_phc")
            details = item.get(f"{direction}_interface", {})
            if not isinstance(name, str) or not name or name in mapped_names:
                continue
            if not isinstance(details, dict):
                details = {}
            role = "IN" if direction == "ingress" else "OUT"
            if index == 0:
                role = "INACTIVE IN" if direction == "ingress" else "GM OUT"
            elif index == len(mapped_value) - 1:
                role = "OC IN" if direction == "ingress" else "INACTIVE OUT"
            speed_value = details.get("speed_mbps")
            mapped_interfaces.append(
                Interface(
                    name=name,
                    state=str(details.get("state", "NAMESPACE")).upper(),
                    carrier=bool(details.get("carrier", False)),
                    speed_mbps=int(speed_value) if isinstance(speed_value, (int, float)) else None,
                    mac=str(details.get("mac", "")),
                    driver=str(details["driver"]) if details.get("driver") else None,
                    bus=str(details["bus"]) if details.get("bus") else None,
                    phc=phc if isinstance(phc, str) else None,
                    hardware_timestamping=bool(details.get("hardware_timestamping", phc)),
                    namespace=namespace if isinstance(namespace, str) else None,
                    assignment=f"{node_id} / {role}" if isinstance(node_id, str) else role,
                )
            )
            mapped_names.add(name)
    return mapped_interfaces + [item for item in host_interfaces if item.name not in mapped_names]


def namespaces() -> list[str]:
    result = run(["ip", "netns", "list"])
    return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]


def running_processes() -> list[dict[str, Any]]:
    result = run(["ps", "-eo", "pid=,comm=,args="])
    processes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not re.search(r"\b(ptp4l|phc2sys|ts2phc|ptpbox-kalman-servo)\b", line):
            continue
        parts = line.strip().split(maxsplit=2)
        if len(parts) >= 2:
            processes.append({"pid": int(parts[0]), "name": parts[1], "command": parts[2] if len(parts) == 3 else parts[1]})
    return processes


def load_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def experiment_store() -> ExperimentStore:
    path = STATE_DIR / "experiments.sqlite3"
    key = str(path)
    with _EXPERIMENT_STORES_LOCK:
        store = _EXPERIMENT_STORES.get(key)
        if store is None:
            store = ExperimentStore(path)
            _EXPERIMENT_STORES[key] = store
        return store


def clock_temperatures(force: bool = False) -> dict[str, float]:
    """Read NIC-adjacent hwmon sensors and map them to topology clocks."""
    global _TEMPERATURE_CACHE
    now = time.time()
    if not force and now - _TEMPERATURE_CACHE[0] < 1.0:
        return dict(_TEMPERATURE_CACHE[1])
    values: dict[str, float] = {}
    for item in phc_inventory():
        candidates: list[Path] = []
        for direction in ("ingress_interface", "egress_interface"):
            details = item.get(direction)
            bus = details.get("bus") if isinstance(details, dict) else None
            if isinstance(bus, str) and bus:
                candidates.extend(Path("/sys/bus/pci/devices", bus, "hwmon").glob("hwmon*/temp*_input"))
        readings: list[float] = []
        for path in candidates:
            try:
                reading = float(path.read_text(encoding="utf-8").strip()) / 1000.0
                if -40.0 <= reading <= 150.0:
                    readings.append(reading)
            except (OSError, ValueError):
                continue
        if readings:
            values[str(item["id"])] = statistics.median(readings)
    _TEMPERATURE_CACHE = (now, values)
    return dict(values)


def _command_json(command: list[str], timeout: float = 2.0) -> Any:
    try:
        result = run(command, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def hardware_capabilities(force: bool = False) -> dict[str, Any]:
    """Probe optional timing hardware without turning unsupported data into zero."""
    global _CAPABILITY_CACHE
    now = time.time()
    if not force and now - _CAPABILITY_CACHE[0] < 10.0:
        return _CAPABILITY_CACHE[1]
    dpll_binary = shutil.which("dpll")
    devlink_binary = shutil.which("devlink")
    dpll_devices = _command_json([dpll_binary, "-j", "device", "show"]) if dpll_binary else None
    dpll_pins = _command_json([dpll_binary, "-j", "pin", "show"]) if dpll_binary else None
    devlink_health = _command_json([devlink_binary, "-j", "health", "show"]) if devlink_binary else None
    temperatures = clock_temperatures(force=True)
    path_events = raw_path_events(32)
    pps_common_edge = load_json(STATE_DIR / "pps-comparison.json", {})
    if not isinstance(pps_common_edge, dict):
        pps_common_edge = {}
    value = {
        "dpll": {
            "supported": dpll_devices is not None,
            "binary": bool(dpll_binary),
            "devices": dpll_devices or [],
            "pins": dpll_pins or [],
            "reason": None if dpll_devices is not None else "Kernel DPLL userspace reporting is unavailable on this host.",
        },
        "synce": {
            "supported": dpll_devices is not None and bool(dpll_pins),
            "state": "reported" if dpll_devices is not None and bool(dpll_pins) else "not-reported",
            "reason": None if dpll_devices is not None and bool(dpll_pins) else "No DPLL pin state is exposed; SyncE status is not inferred from PTP lock.",
        },
        "devlink_health": {
            "supported": devlink_health is not None,
            "reporters": devlink_health or {},
        },
        "temperature": {
            "supported": bool(temperatures),
            "nodes": temperatures,
        },
        "path_monitor": {
            "supported": bool(path_events),
            "events": len(path_events),
            "reason": None if path_events else "No LinuxPTP slave-event-monitor records have arrived yet.",
        },
        "pps_common_edge": {
            "supported": bool(pps_common_edge.get("samples")),
            "state": pps_common_edge,
        },
        "timestamp": now,
    }
    _CAPABILITY_CACHE = (now, value)
    return value


def raw_path_events(limit: int = 128) -> list[dict[str, Any]]:
    try:
        with PATH_EVENT_FILE.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = min(handle.tell(), 1_000_000)
            handle.seek(-size, os.SEEK_END)
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-max(1, min(2048, limit * 3)):]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("observed_at"), (int, float)):
            events.append(event)
    return events[-max(1, min(2048, limit)):]


def phc_fd(device: str) -> int:
    with PHC_FDS_LOCK:
        fd = PHC_FDS.get(device)
        if fd is None:
            fd = os.open(device, os.O_RDONLY)
            PHC_FDS[device] = fd
        return fd


def discard_phc_fd(device: str, fd: int) -> None:
    with PHC_FDS_LOCK:
        if PHC_FDS.get(device) == fd:
            PHC_FDS.pop(device, None)
            PHC_CROSS_TIMESTAMP_METHODS.pop(device, None)
            os.close(fd)


def read_phc_ns(device: str) -> int:
    """Read a Linux PHC without changing its time or frequency."""
    fd = phc_fd(device)
    try:
        # Linux's FD_TO_CLOCKID macro for dynamic POSIX clocks.
        clock_id = ((~fd) << 3) | 3
        value = Timespec()
        if LIBC.clock_gettime(clock_id, ctypes.byref(value)) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), device)
        return int(value.tv_sec) * 1_000_000_000 + int(value.tv_nsec)
    except OSError:
        discard_phc_fd(device, fd)
        raise


def ptp_time_ns(value: PtpClockTime) -> int:
    return int(value.sec) * 1_000_000_000 + int(value.nsec)


def precise_cross_timestamp(fd: int) -> PhcCrossTimestamp:
    buffer = bytearray(ctypes.sizeof(PtpSysOffsetPrecise))
    value = PtpSysOffsetPrecise.from_buffer(buffer)
    fcntl.ioctl(fd, PTP_SYS_OFFSET_PRECISE, buffer, True)
    system_ns = ptp_time_ns(value.sys_monoraw)
    return PhcCrossTimestamp(
        system_ns=system_ns,
        phc_minus_system_ns=ptp_time_ns(value.device) - system_ns,
        delay_ns=0,
        method="PTP_SYS_OFFSET_PRECISE",
    )


def extended_cross_timestamp(fd: int, clock_id: int) -> PhcCrossTimestamp:
    buffer = bytearray(ctypes.sizeof(PtpSysOffsetExtended))
    value = PtpSysOffsetExtended.from_buffer(buffer)
    value.n_samples = PHC_CROSS_TIMESTAMP_SAMPLES
    value.clockid = clock_id
    fcntl.ioctl(fd, PTP_SYS_OFFSET_EXTENDED, buffer, True)
    candidates: list[tuple[int, int, int]] = []
    for index in range(PHC_CROSS_TIMESTAMP_SAMPLES):
        before_ns = ptp_time_ns(value.ts[index][0])
        device_ns = ptp_time_ns(value.ts[index][1])
        after_ns = ptp_time_ns(value.ts[index][2])
        delay_ns = after_ns - before_ns
        if delay_ns >= 0:
            system_ns = (before_ns + after_ns) // 2
            candidates.append((delay_ns, system_ns, device_ns - system_ns))
    if not candidates:
        raise OSError(errno.EIO, "kernel returned no valid PHC cross timestamps")
    delay_ns, system_ns, offset_ns = min(candidates)
    clock_name = "CLOCK_MONOTONIC_RAW" if clock_id == CLOCK_MONOTONIC_RAW else "CLOCK_REALTIME"
    return PhcCrossTimestamp(
        system_ns=system_ns,
        phc_minus_system_ns=offset_ns,
        delay_ns=delay_ns,
        method=f"PTP_SYS_OFFSET_EXTENDED({clock_name}), best of {PHC_CROSS_TIMESTAMP_SAMPLES}",
    )


def midpoint_cross_timestamp(device: str) -> PhcCrossTimestamp:
    before_ns = time.clock_gettime_ns(CLOCK_MONOTONIC_RAW)
    device_ns = read_phc_ns(device)
    after_ns = time.clock_gettime_ns(CLOCK_MONOTONIC_RAW)
    system_ns = (before_ns + after_ns) // 2
    return PhcCrossTimestamp(
        system_ns=system_ns,
        phc_minus_system_ns=device_ns - system_ns,
        delay_ns=after_ns - before_ns,
        method="userspace CLOCK_MONOTONIC_RAW midpoint fallback",
    )


def read_phc_cross_timestamp(device: str) -> PhcCrossTimestamp:
    """Cross timestamp a PHC to a common system clock, without disciplining it."""
    fd = phc_fd(device)
    methods = ["precise", "extended-monoraw", "extended-realtime", "midpoint"]
    cached = PHC_CROSS_TIMESTAMP_METHODS.get(device)
    if cached in methods:
        methods.remove(cached)
        methods.insert(0, cached)
    unsupported = {errno.EINVAL, errno.ENOTTY, errno.EOPNOTSUPP, errno.ENOSYS}
    for method in methods:
        try:
            if method == "precise":
                result = precise_cross_timestamp(fd)
            elif method == "extended-monoraw":
                result = extended_cross_timestamp(fd, CLOCK_MONOTONIC_RAW)
            elif method == "extended-realtime":
                result = extended_cross_timestamp(fd, CLOCK_REALTIME)
            else:
                result = midpoint_cross_timestamp(device)
            PHC_CROSS_TIMESTAMP_METHODS[device] = method
            return result
        except OSError as exc:
            if method == cached:
                PHC_CROSS_TIMESTAMP_METHODS.pop(device, None)
            if exc.errno not in unsupported:
                if exc.errno in {errno.EBADF, errno.ENODEV, errno.ENXIO}:
                    discard_phc_fd(device, fd)
                raise
    raise OSError(errno.EOPNOTSUPP, "no PHC cross-timestamp method is available", device)


def phc_inventory() -> list[dict[str, Any]]:
    value = load_json(PHC_MAP_FILE, [])
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("measurement_phc"), str)
    ]


def take_phc_sample() -> dict[str, Any] | None:
    """Compare PHCs at a common epoch using kernel cross timestamps."""
    inventory = phc_inventory()
    if not inventory:
        return None
    reference = inventory[0]
    reference_device = f"/dev/{reference['measurement_phc']}"
    observed_at = time.time()
    sample_id = f"phc:{time.time_ns()}"
    clocks: list[dict[str, Any]] = []
    previous_offset: float | None = None
    target_measurements: dict[str, PhcCrossTimestamp | OSError] = {}
    try:
        reference_before = read_phc_cross_timestamp(reference_device)
        for item in inventory[1:]:
            device = f"/dev/{item['measurement_phc']}"
            try:
                target_measurements[item["id"]] = read_phc_cross_timestamp(device)
            except OSError as exc:
                target_measurements[item["id"]] = exc
        reference_after = read_phc_cross_timestamp(reference_device)
    except OSError as exc:
        reference_before = exc
        reference_after = exc

    for index, item in enumerate(inventory):
        try:
            if isinstance(reference_before, OSError) or isinstance(reference_after, OSError):
                raise reference_before if isinstance(reference_before, OSError) else reference_after
            if index == 0:
                measurement = reference_after
                offset = 0.0
                uncertainty = measurement.delay_ns / 2
            else:
                measurement = target_measurements[item["id"]]
                if isinstance(measurement, OSError):
                    raise measurement
                reference_interval = reference_after.system_ns - reference_before.system_ns
                if reference_interval:
                    position = (measurement.system_ns - reference_before.system_ns) / reference_interval
                    # Cancel the Unix-epoch-sized offsets as integers first.
                    # Converting either absolute value to float would quantize
                    # the result to roughly 256 ns at today's epoch.
                    offset = float(measurement.phc_minus_system_ns - reference_before.phc_minus_system_ns) - position * (
                        reference_after.phc_minus_system_ns - reference_before.phc_minus_system_ns
                    )
                    reference_delay = reference_before.delay_ns + position * (
                        reference_after.delay_ns - reference_before.delay_ns
                    )
                else:
                    offset = float(
                        measurement.phc_minus_system_ns
                        - (reference_before.phc_minus_system_ns + reference_after.phc_minus_system_ns) // 2
                    )
                    reference_delay = max(reference_before.delay_ns, reference_after.delay_ns)
                uncertainty = (measurement.delay_ns + max(0.0, reference_delay)) / 2
            hop_offset = None if previous_offset is None else offset - previous_offset
            clocks.append(
                {
                    "id": item["id"],
                    "phc": item["measurement_phc"],
                    "offset_ns": float(offset),
                    "previous_hop_offset_ns": float(hop_offset) if hop_offset is not None else None,
                    "read_span_ns": float(measurement.delay_ns),
                    "comparison_uncertainty_ns": float(uncertainty),
                    "cross_timestamp_method": measurement.method,
                    "observed_at": observed_at,
                    "sample_id": f"{sample_id}:{item['id']}",
                    "raw": True,
                    "valid": True,
                    "error": None,
                }
            )
            previous_offset = offset
        except OSError as exc:
            clocks.append(
                {
                    "id": item["id"],
                    "phc": item["measurement_phc"],
                    "offset_ns": None,
                    "previous_hop_offset_ns": None,
                    "read_span_ns": None,
                    "comparison_uncertainty_ns": None,
                    "cross_timestamp_method": None,
                    "observed_at": observed_at,
                    "sample_id": f"{sample_id}:{item['id']}",
                    "raw": True,
                    "valid": False,
                    "error": str(exc),
                }
            )
            previous_offset = None
    return {
        "observed_at": observed_at,
        "sample_id": sample_id,
        "reference": reference["id"],
        "reference_phc": reference["measurement_phc"],
        "clocks": clocks,
        "raw": True,
        "method": "common-system cross timestamps with interpolated BC1 reference",
    }


def record_phc_sample() -> dict[str, Any] | None:
    sample = take_phc_sample()
    if sample is not None:
        with PHC_HISTORY_LOCK:
            PHC_HISTORY.append(sample)
        temperatures = clock_temperatures()
        RESEARCH_ENGINE.add(sample, temperatures)
        try:
            experiment_store().record_phc(sample, temperatures)
        except (OSError, sqlite3.Error):
            pass
    return sample


def phc_telemetry(history_seconds: float = 120.0, since: float | None = None) -> dict[str, Any]:
    now = time.time()
    cutoff = now - max(5.0, min(history_seconds, 900.0))
    with PHC_HISTORY_LOCK:
        history = list(PHC_HISTORY)
    window = [sample for sample in history if float(sample["observed_at"]) >= cutoff]
    inventory = phc_inventory()
    clocks: list[dict[str, Any]] = []
    for item in inventory:
        values = [
            clock
            for sample in window
            for clock in sample["clocks"]
            if clock["id"] == item["id"]
        ]
        measurement = values[-1] if values else None
        samples = values if since is None else [sample for sample in values if float(sample["observed_at"]) > since]
        valid_offsets = [float(sample["offset_ns"]) for sample in values if sample["valid"] and sample["offset_ns"] is not None]
        clocks.append(
            {
                "id": item["id"],
                "phc": item["measurement_phc"],
                "measurement": measurement,
                "samples": samples,
                "window_sample_count": len(values),
                "rms_ns": (
                    sum(offset * offset for offset in valid_offsets) / len(valid_offsets)
                ) ** 0.5 if valid_offsets else None,
            }
        )
    fresh = sum(
        1
        for clock in clocks
        if clock["measurement"]
        and clock["measurement"]["valid"]
        and now - float(clock["measurement"]["observed_at"]) <= PHC_STALE_AFTER_SECONDS
    )
    mode = "live" if fresh else "stale" if history else "waiting"
    return {
        "timestamp": now,
        "reference": inventory[0]["id"] if inventory else None,
        "reference_phc": inventory[0]["measurement_phc"] if inventory else None,
        "clocks": clocks,
        "fresh_clocks": fresh,
        "mode": mode,
        "sample_rate_hz": configured_phc_sample_rate_hz(),
        "raw": True,
        "smoothing": "none",
        "method": "common-system cross timestamps with interpolated BC1 reference",
    }


def phc_sampler_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        started = time.monotonic()
        record_phc_sample()
        period = 1.0 / configured_phc_sample_rate_hz()
        stop.wait(max(0.0, period - (time.monotonic() - started)))


def load_config() -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return merged
    except (OSError, json.JSONDecodeError):
        return merged

    def merge(target: dict[str, Any], update: dict[str, Any]) -> None:
        for key, item in update.items():
            if isinstance(item, dict) and isinstance(target.get(key), dict):
                merge(target[key], item)
            else:
                target[key] = item

    merge(merged, value)
    return merged


def configured_phc_sample_rate_hz() -> float:
    """Match read-only PHC observation to the applied IEEE 1588 Sync cadence."""
    log_interval = load_config().get("log_sync_interval", 0)
    if isinstance(log_interval, bool) or not isinstance(log_interval, int) or not -3 <= log_interval <= 1:
        log_interval = 0
    return float(2 ** -log_interval)


def profile_compliance(value: dict[str, Any] | None = None) -> dict[str, Any]:
    config_value = value or load_config()
    name = str(config_value.get("profile", ""))
    rule = PROFILE_RULES.get(name)
    if not rule:
        return {"profile": name, "compliant": False, "checks": [], "error": "unknown profile"}
    checks = [
        {
            "name": "Transport",
            "actual": config_value.get("transport"),
            "expected": sorted(rule["transport"]),
            "pass": config_value.get("transport") in rule["transport"],
        },
        {
            "name": "Delay mechanism",
            "actual": config_value.get("delay_mechanism"),
            "expected": sorted(rule["delay"]),
            "pass": config_value.get("delay_mechanism") in rule["delay"],
        },
        {
            "name": "Domain",
            "actual": config_value.get("domain"),
            "expected": list(rule["domain"]),
            "pass": isinstance(config_value.get("domain"), int) and rule["domain"][0] <= config_value["domain"] <= rule["domain"][1],
        },
    ]
    if rule["two_step"] is not None:
        checks.append(
            {
                "name": "Two-step operation",
                "actual": config_value.get("two_step"),
                "expected": rule["two_step"],
                "pass": config_value.get("two_step") is rule["two_step"],
            }
        )
    return {
        "profile": name,
        "compliant": all(check["pass"] for check in checks),
        "checks": checks,
        "available_profiles": list(PROFILE_RULES),
        "scope": "transport, delay mechanism, domain, and two-step compatibility",
        "certification": False,
    }


def validate_config(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    profile = value.get("profile")
    if profile not in PROFILE_RULES:
        errors.append(f"profile must be one of: {', '.join(PROFILE_RULES)}")
    if not isinstance(value.get("domain"), int) or isinstance(value.get("domain"), bool) or not 0 <= value["domain"] <= 255:
        errors.append("domain must be an integer from 0 through 255")
    if value.get("transport") not in {"L2", "UDPv4", "UDPv6"}:
        errors.append("transport must be L2, UDPv4, or UDPv6")
    if value.get("delay_mechanism") not in {"P2P", "E2E"}:
        errors.append("delay_mechanism must be P2P or E2E")
    if profile in PROFILE_RULES:
        rules = PROFILE_RULES[profile]
        if value.get("transport") not in rules["transport"]:
            errors.append(f"{profile} requires transport: {', '.join(sorted(rules['transport']))}")
        if value.get("delay_mechanism") not in rules["delay"]:
            errors.append(f"{profile} requires delay mechanism: {', '.join(sorted(rules['delay']))}")
        if isinstance(value.get("domain"), int) and not rules["domain"][0] <= value["domain"] <= rules["domain"][1]:
            errors.append(f"{profile} requires domain {rules['domain'][0]} through {rules['domain'][1]}")
        if rules["two_step"] is not None and value.get("two_step") is not rules["two_step"]:
            errors.append(f"{profile} requires two_step={str(rules['two_step']).lower()}")
    log_sync_interval = value.get("log_sync_interval")
    if isinstance(log_sync_interval, bool) or not isinstance(log_sync_interval, int) or not -3 <= log_sync_interval <= 1:
        errors.append("log_sync_interval must be an integer from -3 through 1 (8 Hz through 0.5 Hz)")
    servo = value.get("servo")
    if not isinstance(servo, dict):
        errors.append("servo settings are required")
    else:
        if servo.get("type") not in SUPPORTED_SERVOS:
            errors.append("servo.type must be pi, linreg, nullf, kalman, adaptive-kalman, or imm")
        for key in ("kp", "ki"):
            if not isinstance(servo.get(key), (int, float)) or not 0 <= float(servo[key]) <= 10:
                errors.append(f"servo.{key} must be between 0 and 10")
        if not isinstance(servo.get("step_threshold_ns"), (int, float)) or servo["step_threshold_ns"] < 0:
            errors.append("servo.step_threshold_ns must be non-negative")
        kalman = servo.get("kalman")
        if not isinstance(kalman, dict):
            errors.append("servo.kalman settings are required")
        else:
            for key in ("measurement_noise_ns", "process_noise_ppb", "phase_time_constant_s", "innovation_gate_sigma", "drift_noise_ppb_s2"):
                if not isinstance(kalman.get(key), (int, float)) or isinstance(kalman.get(key), bool) or not 0 < float(kalman[key]) <= 1_000_000:
                    errors.append(f"servo.kalman.{key} must be positive and no greater than 1000000")
    security = value.get("security")
    authentication = security.get("authentication") if isinstance(security, dict) else None
    if not isinstance(authentication, dict):
        errors.append("security.authentication settings are required")
    else:
        if not isinstance(authentication.get("enabled"), bool):
            errors.append("security.authentication.enabled must be boolean")
        for key, minimum, maximum in (("spp", 0, 255), ("active_key_id", 1, 2**32 - 1), ("allow_unauth", 0, 2)):
            item = authentication.get(key)
            if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
                errors.append(f"security.authentication.{key} must be an integer from {minimum} through {maximum}")
        sa_file = authentication.get("sa_file")
        if not isinstance(sa_file, str) or not sa_file.startswith("/etc/linuxptp/") or ".." in Path(sa_file).parts:
            errors.append("security.authentication.sa_file must be an absolute path below /etc/linuxptp")
        if authentication.get("enabled") and value.get("two_step") is not True:
            errors.append("LinuxPTP Authentication TLVs require two_step=true")
    pps = value.get("pps")
    node_ids = [node["name"] for node in topology_nodes()]
    if not isinstance(pps, dict):
        errors.append("pps settings are required")
    else:
        if not isinstance(pps.get("enabled"), bool):
            errors.append("pps.enabled must be boolean")
        source = pps.get("source")
        if source != "external" and source not in node_ids:
            errors.append("pps.source must be external or a topology clock")
        sinks = pps.get("sinks")
        if not isinstance(sinks, list) or any(item not in node_ids for item in sinks) or len(set(sinks or [])) != len(sinks or []):
            errors.append("pps.sinks must contain unique topology clocks")
        elif source in sinks:
            errors.append("pps.source cannot also be a sink")
        elif pps.get("enabled") and not sinks:
            errors.append("pps.sinks must select at least one clock when PPS is enabled")
        for key in ("output_pin", "input_pin"):
            if isinstance(pps.get(key), bool) or not isinstance(pps.get(key), int) or not 0 <= pps[key] <= 31:
                errors.append(f"pps.{key} must be an integer from 0 through 31")
        if isinstance(pps.get("channel"), bool) or not isinstance(pps.get("channel"), int) or not 0 <= pps["channel"] <= 31:
            errors.append("pps.channel must be an integer from 0 through 31")
        if pps.get("polarity") not in {"rising", "falling", "both"}:
            errors.append("pps.polarity must be rising, falling, or both")
        if (
            isinstance(pps.get("pulse_width_ns"), bool)
            or not isinstance(pps.get("pulse_width_ns"), int)
            or not 1_000_000 <= pps["pulse_width_ns"] <= 990_000_000
        ):
            errors.append("pps.pulse_width_ns must be an integer from 1000000 through 990000000")
        if (
            isinstance(pps.get("perout_phase_ns"), bool)
            or not isinstance(pps.get("perout_phase_ns"), int)
            or not 0 <= pps["perout_phase_ns"] <= 999_999_999
        ):
            errors.append("pps.perout_phase_ns must be an integer from 0 through 999999999")
        if isinstance(pps.get("extts_correction_ns"), bool) or not isinstance(pps.get("extts_correction_ns"), int):
            errors.append("pps.extts_correction_ns must be an integer")
        comparison = pps.get("comparison")
        if not isinstance(comparison, dict):
            errors.append("pps.comparison settings are required")
        else:
            if not isinstance(comparison.get("enabled"), bool) or not isinstance(comparison.get("measure_only"), bool):
                errors.append("pps.comparison enabled and measure_only must be boolean")
            if comparison.get("enabled"):
                if not pps.get("enabled") or source != "external":
                    errors.append("PPS common-edge comparison requires enabled PPS with an external source")
                if pps.get("polarity") == "both":
                    errors.append("PPS common-edge comparison requires a single edge polarity (rising or falling)")
                if not comparison.get("measure_only"):
                    errors.append("PPS common-edge comparison must be measure-only so no competing ts2phc servo consumes events")
                if not isinstance(sinks, list) or len(sinks) < 2:
                    errors.append("PPS common-edge comparison requires at least two sink clocks")
                if comparison.get("reference") not in (sinks or []):
                    errors.append("pps.comparison.reference must be one of the sink clocks")
            history = comparison.get("history")
            if isinstance(history, bool) or not isinstance(history, int) or not 8 <= history <= 4096:
                errors.append("pps.comparison.history must be an integer from 8 through 4096")
        ts2phc = pps.get("ts2phc")
        if not isinstance(ts2phc, dict):
            errors.append("pps.ts2phc settings are required")
        else:
            if ts2phc.get("servo") not in LINUXPTP_NATIVE_SERVOS:
                errors.append("pps.ts2phc.servo must be pi, linreg, or nullf")
            for key in ("kp", "ki"):
                if not isinstance(ts2phc.get(key), (int, float)) or not 0 <= float(ts2phc[key]) <= 10:
                    errors.append(f"pps.ts2phc.{key} must be between 0 and 10")
            for key in ("step_threshold_ns", "first_step_threshold_ns", "holdover_seconds", "stable_threshold_ns"):
                if isinstance(ts2phc.get(key), bool) or not isinstance(ts2phc.get(key), (int, float)) or ts2phc[key] < 0:
                    errors.append(f"pps.ts2phc.{key} must be non-negative")
            if (
                isinstance(ts2phc.get("stable_samples"), bool)
                or not isinstance(ts2phc.get("stable_samples"), int)
                or not 1 <= ts2phc["stable_samples"] <= 1000
            ):
                errors.append("pps.ts2phc.stable_samples must be an integer from 1 through 1000")
            if (
                isinstance(ts2phc.get("logging_level"), bool)
                or not isinstance(ts2phc.get("logging_level"), int)
                or not 0 <= ts2phc["logging_level"] <= 7
            ):
                errors.append("pps.ts2phc.logging_level must be an integer from 0 through 7")
            if pps.get("polarity") == "both" and isinstance(ts2phc.get("holdover_seconds"), (int, float)) and ts2phc["holdover_seconds"] > 0:
                errors.append("pps.ts2phc holdover is not supported when pps.polarity is both")
    return errors


def save_config(value: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    pending = CONFIG_FILE.with_suffix(".json.tmp")
    pending.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    pending.replace(CONFIG_FILE)


def load_servo_state() -> dict[str, Any]:
    value = load_json(SERVO_STATE_FILE, {})
    return value if isinstance(value, dict) else {}


def load_kalman_status(name: str, now: float | None = None) -> dict[str, Any] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return None
    value = load_json(KALMAN_STATE_DIR / f"kalman-{name.lower()}.json")
    if not isinstance(value, dict) or value.get("node") != name or value.get("servo") not in {"kalman", "adaptive-kalman", "imm"}:
        return None
    observed_at = value.get("observed_at")
    if not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool):
        return None
    result = dict(value)
    result["fresh"] = (now or time.time()) - float(observed_at) <= max(5.0, 3.0 / configured_phc_sample_rate_hz())
    return result


def load_kalman_history(name: str, limit: int = 2048) -> list[dict[str, Any]]:
    """Read raw per-update Kalman records without using UI poll cadence."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return []
    candidates = [
        LOG_DIR / f"{name}-KALMAN.log",
        LOG_DIR / f"{name}-ADAPTIVE-KALMAN.log",
        LOG_DIR / f"{name}-IMM.log",
    ]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return []
    path = max(existing, key=lambda item: item.stat().st_mtime)
    requested = max(1, min(4096, int(limit)))
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - min(4_000_000, max(128_000, requested * 1_500)))
            handle.seek(start)
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    marker = text.rfind("PTPBox session start")
    if marker >= 0:
        text = text[marker:]
    lines = text.splitlines()
    if start and lines:
        lines = lines[1:]
    history = []
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(item, dict)
            and item.get("node") == name
            and item.get("servo") in {"kalman", "adaptive-kalman", "imm"}
            and isinstance(item.get("observed_at"), (int, float))
        ):
            history.append(item)
    return history[-requested:]


def display_path(path: Path) -> str:
    for base in (LOG_DIR, ROOT):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def parse_log_measurements(path: Path, limit: int = TELEMETRY_MAX_SAMPLES) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            # Reading the full 2 MiB safety cap for every clock on every poll
            # wastes CPU once append-only lab logs become large. Budget enough
            # tail data for the requested samples, plus generous non-sample
            # LinuxPTP lines, while keeping the absolute cap for API callers.
            requested = max(1, min(limit, TELEMETRY_MAX_SAMPLES))
            tail_bytes = min(TELEMETRY_MAX_BYTES, max(64_000, requested * 256))
            start = max(0, size - tail_bytes)
            handle.seek(start)
            text = handle.read().decode("utf-8", errors="replace")
        stat = path.stat()
    except OSError:
        return []

    # Logs are intentionally append-only across daemon restarts. If the newest
    # process has not emitted an offset yet, never let the previous process's
    # last sample masquerade as fresh merely because the file mtime advanced.
    session_markers = list(LOG_SESSION_PATTERN.finditer(text))
    if session_markers:
        text = text[session_markers[-1].start() :]

    lines = text.splitlines()
    if start and lines:
        lines = lines[1:]
    parsed: list[dict[str, Any]] = []
    for line in lines:
        match = LOG_PATTERN.search(line)
        if not match:
            continue
        source_time_match = LOG_TIME_PATTERN.search(line[: match.start()])
        path_delay = float(match.group("delay"))
        valid = 0.0 <= path_delay <= TELEMETRY_MAX_PATH_DELAY_NS
        parsed.append(
            {
                "offset_ns": float(match.group("offset")),
                "frequency_ppb": float(match.group("freq")),
                "mean_path_delay_ns": path_delay,
                "servo_state": (match.group("servo_state") or "").lower() or None,
                "source_time": float(source_time_match.group("seconds")) if source_time_match else None,
                "source": display_path(path),
                "raw": True,
                "valid": valid,
                "validation_error": None if valid else "path delay outside the 0–1 ms lab envelope",
            }
        )

    session_start = 0
    for index in range(1, len(parsed)):
        previous = parsed[index - 1]["source_time"]
        current = parsed[index]["source_time"]
        if previous is not None and current is not None and current + 1.0 < previous:
            session_start = index
    parsed = parsed[session_start:][-max(1, min(limit, TELEMETRY_MAX_SAMPLES)) :]
    source_times = [sample["source_time"] for sample in parsed if sample["source_time"] is not None]
    last_source_time = source_times[-1] if source_times else None
    for index, sample in enumerate(parsed):
        if last_source_time is not None and sample["source_time"] is not None:
            sample["observed_at"] = stat.st_mtime - max(0.0, last_source_time - sample["source_time"])
        else:
            sample["observed_at"] = stat.st_mtime - (len(parsed) - index - 1) * 0.0625
        sample["sample_id"] = f"{stat.st_ino}:{sample['source_time'] if sample['source_time'] is not None else index}"
    return parsed


def topology_nodes() -> list[dict[str, str]]:
    value = load_json(TOPOLOGY_FILE, {})
    if isinstance(value, dict) and isinstance(value.get("nodes"), list):
        nodes = [node for node in value["nodes"] if isinstance(node, dict)]
        if all(all(isinstance(node.get(key), str) for key in ("name", "ingress", "egress")) for node in nodes):
            return nodes
    return [
        {"name": path.name, "ingress": "", "egress": ""}
        for path in sorted(ROOT.glob("BC[0-9]*"), key=lambda item: int(re.sub(r"\D", "", item.name) or 0))
    ]


def clock_log_candidates(name: str) -> list[Path]:
    preferred = [LOG_DIR / f"{name}-BC.log", LOG_DIR / f"{name}-OC.log"]
    if name == "BC1":
        preferred.append(LOG_DIR / f"{name}-GM.log")
    managed = [path for path in preferred if path.is_file()]
    if managed:
        # A current boundary-clock log with no offset means "waiting", not
        # permission to resurrect samples from an older OC/GM process file.
        return [max(managed, key=lambda item: item.stat().st_mtime)]
    legacy_dir = ROOT / name
    legacy = sorted(legacy_dir.glob("*OC*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    fallback = sorted(legacy_dir.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    unique: list[Path] = []
    for path in [*legacy, *fallback]:
        if path.is_file() and path not in unique:
            unique.append(path)
    return unique


def telemetry(history_seconds: float = 120.0, since: float | None = None, limit: int = TELEMETRY_MAX_SAMPLES) -> dict[str, Any]:
    now = time.time()
    clocks: list[dict[str, Any]] = []
    nodes = topology_nodes()
    control_state = load_servo_state()
    controlled_nodes = control_state.get("nodes", {}) if isinstance(control_state, dict) else {}
    phc_payload = phc_telemetry(history_seconds, since)
    phc_by_id = {clock["id"]: clock for clock in phc_payload["clocks"]}
    cutoff = now - max(5.0, min(history_seconds, 900.0))
    for index, node in enumerate(nodes):
        candidates = clock_log_candidates(node["name"])
        all_samples: list[dict[str, Any]] = []
        for path in candidates:
            samples = parse_log_measurements(path, limit)
            if samples:
                all_samples = samples
                break
        measurement = all_samples[-1] if all_samples else None
        window_samples = [sample for sample in all_samples if sample["observed_at"] >= cutoff]
        valid_window_samples = [sample for sample in window_samples if sample["valid"]]
        locked_window_samples = [sample for sample in valid_window_samples if sample["servo_state"] == "s2"]
        node_control = controlled_nodes.get(node["name"], {}) if isinstance(controlled_nodes, dict) else {}
        kalman_status = (
            load_kalman_status(node["name"], now)
            if isinstance(node_control, dict) and node_control.get("type") in {"kalman", "adaptive-kalman", "imm"} and node_control.get("enabled")
            else None
        )
        if measurement and kalman_status and kalman_status["fresh"]:
            measurement = dict(measurement)
            measurement["linuxptp_frequency_ppb"] = measurement["frequency_ppb"]
            measurement["frequency_ppb"] = float(kalman_status.get("correction_ppb", 0.0))
            measurement["servo_state"] = "s2" if kalman_status.get("state") == "locked" else "s1"
            measurement["control_source"] = "ptpbox-kalman"
            if kalman_status.get("state") == "locked":
                locked_since = kalman_status.get("locked_since_source_time")
                locked_window_samples = [
                    sample
                    for sample in valid_window_samples
                    if not isinstance(locked_since, (int, float))
                    or isinstance(locked_since, bool)
                    or not isinstance(sample.get("source_time"), (int, float))
                    or float(sample["source_time"]) >= float(locked_since)
                ]
        samples = window_samples
        if since is not None:
            samples = [sample for sample in samples if sample["observed_at"] > since]
        role = "grandmaster" if index == 0 else "ordinary" if index == len(nodes) - 1 else "boundary"
        phc_clock = phc_by_id.get(node["name"], {})
        clocks.append(
            {
                "id": node["name"],
                "role": role,
                "ingress": node["ingress"],
                "egress": node["egress"],
                "measurement": measurement,
                "samples": samples,
                "window_sample_count": len(window_samples),
                "window_valid_sample_count": len(valid_window_samples),
                "window_locked_sample_count": len(locked_window_samples),
                "window_invalid_sample_count": len(window_samples) - len(valid_window_samples),
                "rms_ns": (
                    sum(float(sample["offset_ns"]) ** 2 for sample in locked_window_samples) / len(locked_window_samples)
                ) ** 0.5 if locked_window_samples else None,
                "logs": len(candidates),
                "measurement_phc": phc_clock.get("phc"),
                "phc_measurement": phc_clock.get("measurement"),
                "phc_samples": phc_clock.get("samples", []),
                "phc_window_sample_count": phc_clock.get("window_sample_count", 0),
                "phc_rms_ns": phc_clock.get("rms_ns"),
                "kalman": kalman_status,
            }
        )
    measured = sum(1 for clock in clocks if clock["measurement"] and clock["measurement"]["valid"])
    degraded = sum(1 for clock in clocks if clock["measurement"] and not clock["measurement"]["valid"])
    fresh = sum(
        1
        for clock in clocks
        if clock["measurement"]
        and clock["measurement"]["valid"]
        and now - float(clock["measurement"]["observed_at"]) <= TELEMETRY_STALE_AFTER_SECONDS
    )
    sample_count = sum(len(clock["samples"]) for clock in clocks)
    valid_sample_count = sum(sum(1 for sample in clock["samples"] if sample["valid"]) for clock in clocks)
    mode = "live" if fresh else "stale" if measured else "waiting"
    return {
        "timestamp": now,
        "clocks": clocks,
        "measured_clocks": measured,
        "fresh_clocks": fresh,
        "degraded_clocks": degraded,
        "sample_count": sample_count,
        "valid_sample_count": valid_sample_count,
        "invalid_sample_count": sample_count - valid_sample_count,
        "mode": mode,
        "phc_mode": phc_payload["mode"],
        "phc_reference": phc_payload["reference"],
        "phc_reference_device": phc_payload["reference_phc"],
        "phc_fresh_clocks": phc_payload["fresh_clocks"],
        "phc_method": phc_payload["method"],
        "phc_sample_rate_hz": phc_payload["sample_rate_hz"],
        "servo_control": control_state,
        "raw": True,
        "smoothing": "none",
        "measurement_source": "kernel cross-timestamped PHC comparison",
        "history_seconds": history_seconds,
    }


def load_holdover_session() -> dict[str, Any] | None:
    value = load_json(HOLDOVER_SESSION_FILE)
    return value if isinstance(value, dict) and isinstance(value.get("id"), str) else None


def save_holdover_session(value: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    value["updated_at"] = time.time()
    pending = HOLDOVER_SESSION_FILE.with_suffix(".json.tmp")
    pending.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    pending.replace(HOLDOVER_SESSION_FILE)


def _set_servo_controls(nodes: list[str], enabled: bool, servo_types: dict[str, str]) -> dict[str, Any]:
    """Apply per-node control without collapsing a mixed-servo experiment."""
    ordered = sorted(
        nodes,
        key=lambda name: int(re.sub(r"\D", "", name) or 0),
        reverse=not enabled,
    )
    results: list[dict[str, Any]] = []
    for node in ordered:
        servo_type = servo_types.get(node)
        if servo_type not in SUPPORTED_SERVOS:
            raise ValueError(f"unsupported saved servo type for {node}")
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        pending = SERVO_REQUEST_FILE.with_suffix(".json.tmp")
        pending.write_text(
            json.dumps({"target": node, "enabled": enabled, "type": servo_type}, indent=2) + "\n",
            encoding="utf-8",
        )
        pending.replace(SERVO_REQUEST_FILE)
        code, response = control("servo")
        if code != HTTPStatus.OK:
            raise RuntimeError(str(response.get("error") or f"servo transition failed for {node}"))
        results.append({"node": node, "response": response})
    return {"nodes": ordered, "enabled": enabled, "results": results}


def _holdover_lock_observation(session: dict[str, Any]) -> dict[str, Any]:
    selected = [str(node) for node in session.get("nodes", [])]
    threshold = float(session.get("stable_threshold_ns", 1_000.0))
    payload = telemetry(history_seconds=max(10.0, float(session.get("stable_dwell_s", 30.0)) + 5.0))
    by_id = {str(clock["id"]): clock for clock in payload["clocks"]}
    now = float(payload["timestamp"])
    node_states: dict[str, dict[str, Any]] = {}
    all_stable = bool(selected)
    for node in selected:
        clock = by_id.get(node)
        measurement = clock.get("measurement") if isinstance(clock, dict) else None
        phc = clock.get("phc_measurement") if isinstance(clock, dict) else None
        servo = (payload.get("servo_control") or {}).get("nodes", {}).get(node, {})
        reasons: list[str] = []
        if not isinstance(servo, dict) or not servo.get("enabled"):
            reasons.append("discipline is not active")
        if not isinstance(measurement, dict) or not measurement.get("valid"):
            reasons.append("no valid LinuxPTP offset")
        else:
            if now - float(measurement.get("observed_at") or 0.0) > TELEMETRY_STALE_AFTER_SECONDS:
                reasons.append("LinuxPTP offset is stale")
            if measurement.get("servo_state") != "s2":
                reasons.append(f"servo state is {measurement.get('servo_state') or 'unknown'}")
            offset = measurement.get("offset_ns")
            if not isinstance(offset, (int, float)) or isinstance(offset, bool) or abs(float(offset)) > threshold:
                reasons.append(f"master offset exceeds ±{threshold:g} ns")
        if not isinstance(phc, dict) or not phc.get("valid"):
            reasons.append("no valid direct PHC comparison")
        elif now - float(phc.get("observed_at") or 0.0) > PHC_STALE_AFTER_SECONDS:
            reasons.append("direct PHC comparison is stale")
        stable = not reasons
        all_stable = all_stable and stable
        node_states[node] = {
            "stable": stable,
            "reason": "; ".join(reasons) if reasons else "locked and inside release gate",
            "ptp_offset_ns": measurement.get("offset_ns") if isinstance(measurement, dict) else None,
            "phc_offset_ns": phc.get("offset_ns") if isinstance(phc, dict) else None,
            "servo_state": measurement.get("servo_state") if isinstance(measurement, dict) else None,
            "observed_at": phc.get("observed_at") if isinstance(phc, dict) else None,
        }
    return {
        "all_stable": all_stable,
        "nodes": node_states,
        "observed_at": now,
        "phc_mode": payload.get("phc_mode"),
        "measurement_method": payload.get("phc_method"),
    }


def _holdover_baselines(session: dict[str, Any], release_at: float) -> dict[str, Any]:
    selected = {str(node) for node in session.get("nodes", [])}
    window_seconds = max(5.0, min(60.0, float(session.get("stable_dwell_s", 30.0))))
    rows = experiment_store().phc_samples(str(session["experiment_id"]), release_at - window_seconds)
    grouped: dict[str, list[dict[str, Any]]] = {node: [] for node in selected}
    for row in rows:
        node = str(row.get("clock_id"))
        if node in grouped and row.get("valid") and isinstance(row.get("offset_ns"), (int, float)):
            grouped[node].append(row)
    baselines: dict[str, Any] = {}
    for node, samples in grouped.items():
        if not samples:
            raise RuntimeError(f"no valid pre-release PHC baseline for {node}")
        offsets = [float(sample["offset_ns"]) for sample in samples]
        uncertainties = [
            float(sample["uncertainty_ns"])
            for sample in samples
            if isinstance(sample.get("uncertainty_ns"), (int, float))
        ]
        baselines[node] = {
            "offset_ns": statistics.median(offsets),
            "uncertainty_ns": statistics.median(uncertainties) if uncertainties else None,
            "samples": len(offsets),
            "window_s": window_seconds,
            "last_observed_at": float(samples[-1]["observed_at"]),
        }
    return baselines


def start_holdover_session(payload: dict[str, Any]) -> dict[str, Any]:
    with HOLDOVER_LOCK:
        current = load_holdover_session()
        if current and current.get("phase") in {"synchronizing", "releasing", "holdover", "resuming"}:
            raise RuntimeError("a holdover run is already active")
        active_experiment = experiment_store().active()
        if active_experiment:
            raise RuntimeError(f"experiment {active_experiment['id']} is already recording")
        receivers = [node["name"] for node in topology_nodes()[1:]]
        requested = payload.get("nodes", receivers)
        if not isinstance(requested, list) or not requested or any(not isinstance(node, str) for node in requested):
            raise ValueError("nodes must be a non-empty list of downstream clock IDs")
        nodes = list(dict.fromkeys(str(node) for node in requested))
        if any(node not in receivers for node in nodes):
            raise ValueError("holdover nodes must be downstream topology clocks")
        stable_dwell_s = payload.get("stable_dwell_s", 30.0)
        stable_threshold_ns = payload.get("stable_threshold_ns", 1_000.0)
        duration_s = payload.get("duration_s", 300.0)
        for name, value, minimum, maximum in (
            ("stable_dwell_s", stable_dwell_s, 5.0, 600.0),
            ("stable_threshold_ns", stable_threshold_ns, 1.0, 1_000_000.0),
            ("duration_s", duration_s, 10.0, 86_400.0),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
                raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
        servo_state = load_servo_state().get("nodes", {})
        servo_types = {
            node: str((servo_state.get(node) or {}).get("type") or load_config()["servo"]["type"])
            for node in nodes
        }
        if any(servo_type not in SUPPORTED_SERVOS for servo_type in servo_types.values()):
            raise ValueError("every selected node needs a supported servo type")
        _set_servo_controls(nodes, True, servo_types)
        experiment = experiment_store().start(
            {
                "name": str(payload.get("name") or f"Holdover · {', '.join(nodes)}"),
                "kind": "holdover",
                "nodes": nodes,
                "stable_dwell_s": float(stable_dwell_s),
                "stable_threshold_ns": float(stable_threshold_ns),
                "duration_s": float(duration_s),
                "auto_release": bool(payload.get("auto_release", True)),
                "auto_resume": bool(payload.get("auto_resume", True)),
                "raw": True,
                "smoothing": "none",
            },
            load_config(),
        )
        now = time.time()
        session = {
            "version": 1,
            "id": f"holdover-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now))}",
            "phase": "synchronizing",
            "nodes": nodes,
            "servo_types": servo_types,
            "started_at": now,
            "phase_changed_at": now,
            "stable_since": None,
            "stable_dwell_s": float(stable_dwell_s),
            "stable_threshold_ns": float(stable_threshold_ns),
            "duration_s": float(duration_s),
            "auto_release": bool(payload.get("auto_release", True)),
            "auto_resume": bool(payload.get("auto_resume", True)),
            "release_at": None,
            "resume_at": None,
            "baseline": {},
            "lock": {"all_stable": False, "nodes": {}, "observed_at": now},
            "experiment_id": experiment["id"],
            "error": None,
        }
        save_holdover_session(session)
        experiment_store().event(
            "holdover",
            "info",
            f"Armed holdover run for {', '.join(nodes)}",
            {"session_id": session["id"], "phase": "synchronizing"},
        )
        return holdover_session_snapshot(refresh=True)


def release_holdover_session(force: bool = False) -> dict[str, Any]:
    with HOLDOVER_LOCK:
        session = load_holdover_session()
        if not session or session.get("phase") != "synchronizing":
            raise RuntimeError("no synchronizing holdover run is armed")
        if not session.get("ready_to_release"):
            session = refresh_holdover_session(session, allow_transitions=False)
            if session.get("phase") != "synchronizing":
                return holdover_session_snapshot(refresh=False)
        if not session.get("ready_to_release") and not force:
            raise RuntimeError("selected clocks have not completed the stable dwell")
        release_started = time.time()
        session["phase"] = "releasing"
        session["phase_changed_at"] = release_started
        session["baseline"] = _holdover_baselines(session, release_started)
        save_holdover_session(session)
        try:
            _set_servo_controls(
                [str(node) for node in session["nodes"]],
                False,
                {str(node): str(servo_type) for node, servo_type in session["servo_types"].items()},
            )
        except Exception as exc:
            session["phase"] = "error"
            session["error"] = str(exc)
            save_holdover_session(session)
            experiment_store().event("holdover", "error", "Holdover release failed", {"error": str(exc)})
            raise
        control_state = load_servo_state().get("nodes", {})
        release_at_by_node = {
            str(node): float((control_state.get(node) or {}).get("holdover_started"))
            for node in session["nodes"]
            if isinstance((control_state.get(node) or {}).get("holdover_started"), (int, float))
        }
        actual_releases = list(release_at_by_node.values())
        session["release_at"] = min(actual_releases) if actual_releases else time.time()
        session["release_at_by_node"] = release_at_by_node or {
            str(node): session["release_at"] for node in session["nodes"]
        }
        session["phase"] = "holdover"
        session["phase_changed_at"] = session["release_at"]
        session["error"] = None
        save_holdover_session(session)
        experiment_store().event(
            "holdover",
            "warning",
            "Clock adjustment frozen; direct PHC monitoring remains active",
            {
                "session_id": session["id"],
                "nodes": session["nodes"],
                "release_at": session["release_at"],
                "baseline": session["baseline"],
            },
        )
        return holdover_session_snapshot(refresh=False)


def resume_holdover_session(aborted: bool = False, automatic: bool = False) -> dict[str, Any]:
    with HOLDOVER_LOCK:
        session = load_holdover_session()
        if not session or session.get("phase") not in {"synchronizing", "releasing", "holdover", "error"}:
            raise RuntimeError("no active holdover run can be resumed")
        session["phase"] = "resuming"
        session["phase_changed_at"] = time.time()
        save_holdover_session(session)
        try:
            _set_servo_controls(
                [str(node) for node in session["nodes"]],
                True,
                {str(node): str(servo_type) for node, servo_type in session["servo_types"].items()},
            )
        except Exception as exc:
            session["phase"] = "error"
            session["error"] = str(exc)
            save_holdover_session(session)
            experiment_store().event("holdover", "error", "Servo recovery failed", {"error": str(exc)})
            raise
        session["resume_at"] = time.time()
        session["phase"] = "aborted" if aborted else "completed"
        session["phase_changed_at"] = session["resume_at"]
        session["error"] = None
        save_holdover_session(session)
        experiment_store().event(
            "holdover",
            "info",
            "Synchronization restored after holdover" if not aborted else "Holdover run aborted; synchronization restored",
            {"session_id": session["id"], "automatic": automatic},
        )
        experiment_store().stop(str(session.get("experiment_id")))
        return holdover_session_snapshot(refresh=False)


def refresh_holdover_session(
    session: dict[str, Any] | None = None,
    allow_transitions: bool = True,
) -> dict[str, Any]:
    with HOLDOVER_LOCK:
        session = session or load_holdover_session()
        if not session:
            return {}
        phase = session.get("phase")
        now = time.time()
        if phase == "synchronizing":
            observation = _holdover_lock_observation(session)
            session["lock"] = observation
            if observation["all_stable"]:
                if not isinstance(session.get("stable_since"), (int, float)):
                    session["stable_since"] = now
            else:
                session["stable_since"] = None
            stable_elapsed = now - float(session["stable_since"]) if isinstance(session.get("stable_since"), (int, float)) else 0.0
            session["ready_to_release"] = stable_elapsed >= float(session["stable_dwell_s"])
            save_holdover_session(session)
            if allow_transitions and session["ready_to_release"] and session.get("auto_release"):
                release_holdover_session()
                return load_holdover_session() or {}
        elif phase == "holdover" and isinstance(session.get("release_at"), (int, float)):
            elapsed = now - float(session["release_at"])
            if allow_transitions and session.get("auto_resume") and elapsed >= float(session.get("duration_s", 300.0)):
                resume_holdover_session(automatic=True)
                return load_holdover_session() or {}
        return session


def _linear_drift_ppb(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 2:
        return None
    origin = points[0][0]
    xs = [timestamp - origin for timestamp, _value in points]
    ys = [value for _timestamp, value in points]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((value - mean_x) ** 2 for value in xs)
    if denominator <= 0:
        return None
    return sum((xs[index] - mean_x) * (ys[index] - mean_y) for index in range(len(xs))) / denominator


def holdover_session_snapshot(refresh: bool = False, include_series: bool = True) -> dict[str, Any]:
    with HOLDOVER_LOCK:
        session = load_holdover_session()
        if not session:
            return {"active": False, "session": None, "series": [], "metrics": {}, "timestamp": time.time()}
        if refresh and session.get("phase") in {"synchronizing", "holdover"}:
            session = refresh_holdover_session(session)
        release_at = session.get("release_at")
        selected = [str(node) for node in session.get("nodes", [])]
        baseline = session.get("baseline", {})
        stable_since = session.get("stable_since")
        stable_elapsed = max(0.0, time.time() - float(stable_since)) if isinstance(stable_since, (int, float)) else 0.0
        active = session.get("phase") in {"synchronizing", "releasing", "holdover", "resuming", "error"}
        response = {
            "active": active,
            "session": session,
            "stable_elapsed_s": stable_elapsed,
            "stable_progress": min(1.0, stable_elapsed / max(1.0, float(session.get("stable_dwell_s", 30.0)))),
            "holdover_elapsed_s": max(0.0, time.time() - float(release_at)) if isinstance(release_at, (int, float)) else 0.0,
            "series": [],
            "metrics": {},
            "captured_rows": 0,
            "captured_cycles": 0,
            "display_stride": 1,
            "raw": True,
            "smoothing": "none",
            "measurement_source": "kernel cross-timestamped PHC comparison relative to pre-release median",
            "timestamp": time.time(),
        }
        if not include_series or not isinstance(release_at, (int, float)) or not session.get("experiment_id"):
            return response
        store = experiment_store()
        identifier = str(session["experiment_id"])
        release = float(release_at)
        summary_rows = store.phc_holdover_summary(identifier, release, selected)
        sampled_rows, captured_cycles, display_stride = store.phc_holdover_series(identifier, release, selected)
        metrics: dict[str, Any] = {}
        for row in summary_rows:
            node = str(row["clock_id"])
            base = baseline.get(node) if isinstance(baseline, dict) else None
            if not isinstance(base, dict) or not isinstance(base.get("offset_ns"), (int, float)):
                continue
            base_offset = float(base["offset_ns"])
            samples = int(row["samples"])
            sum_offset = float(row["sum_offset_ns"])
            sum_offset_squared = float(row["sum_offset_squared_ns2"])
            sum_time = float(row["sum_time_s"])
            sum_time_squared = float(row["sum_time_squared_s2"])
            sum_time_offset = float(row["sum_time_offset_ns_s"])
            denominator = samples * sum_time_squared - sum_time * sum_time
            drift = (
                (samples * sum_time_offset - sum_time * sum_offset) / denominator
                if samples >= 2 and denominator > 0
                else None
            )
            wander_squared = max(
                0.0,
                sum_offset_squared - 2.0 * base_offset * sum_offset + samples * base_offset * base_offset,
            )
            minimum = float(row["minimum_offset_ns"]) - base_offset
            maximum = float(row["maximum_offset_ns"]) - base_offset
            latest = row.get("latest_offset_ns")
            metrics[node] = {
                "samples": samples,
                "current_wander_ns": float(latest) - base_offset if isinstance(latest, (int, float)) else None,
                "peak_abs_wander_ns": max(abs(minimum), abs(maximum)),
                "rms_wander_ns": (wander_squared / samples) ** 0.5 if samples else None,
                "drift_ppb": drift,
                "latest_uncertainty_ns": row.get("latest_uncertainty_ns"),
            }
        cycles: dict[str, dict[str, Any]] = {}
        release_by_node = session.get("release_at_by_node", {})
        for row in sampled_rows:
            node = str(row.get("clock_id"))
            base = baseline.get(node) if isinstance(baseline, dict) else None
            node_release = (
                float(release_by_node[node])
                if isinstance(release_by_node, dict) and isinstance(release_by_node.get(node), (int, float))
                else release
            )
            if (
                node not in selected
                or not row.get("valid")
                or not isinstance(base, dict)
                or float(row["observed_at"]) < node_release
            ):
                continue
            offset = row.get("offset_ns")
            base_offset = base.get("offset_ns")
            if not isinstance(offset, (int, float)) or not isinstance(base_offset, (int, float)):
                continue
            observed_at = float(row["observed_at"])
            wander = float(offset) - float(base_offset)
            uncertainty = float(row["uncertainty_ns"]) if isinstance(row.get("uncertainty_ns"), (int, float)) else None
            cycle_id = str(row["cycle_id"])
            point = cycles.setdefault(
                cycle_id,
                {
                    "observed_at": observed_at,
                    "elapsed_s": observed_at - float(release_at),
                    "values_ns": {},
                    "uncertainty_ns": {},
                },
            )
            point["values_ns"][node] = wander
            if uncertainty is not None:
                point["uncertainty_ns"][node] = uncertainty
        raw_series = sorted(cycles.values(), key=lambda point: float(point["observed_at"]))
        response.update(
            {
                "series": raw_series,
                "metrics": metrics,
                "captured_rows": sum(int(row["samples"]) for row in summary_rows),
                "captured_cycles": captured_cycles,
                "display_stride": display_stride,
            }
        )
        return response


def holdover_session_loop(stop: threading.Event) -> None:
    while not stop.wait(1.0):
        session = load_holdover_session()
        if not session or session.get("phase") not in {"synchronizing", "holdover"}:
            continue
        try:
            refresh_holdover_session(session)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            with HOLDOVER_LOCK:
                current = load_holdover_session()
                if current and current.get("phase") in {"synchronizing", "releasing", "holdover", "resuming"}:
                    current["phase"] = "error"
                    current["error"] = str(exc)
                    save_holdover_session(current)


def _build_research_snapshot(history_seconds: float = 900.0) -> dict[str, Any]:
    telemetry_payload = telemetry(history_seconds=history_seconds, limit=TELEMETRY_MAX_SAMPLES)
    config_value = load_config()
    servo = config_value.get("servo", {})
    endpoint_id = str(telemetry_payload["clocks"][-1].get("id")) if telemetry_payload["clocks"] else ""
    control_nodes = (telemetry_payload.get("servo_control") or {}).get("nodes", {})
    active_controller = str((control_nodes.get(endpoint_id) or {}).get("type") or "pi")
    receiver_controls = [
        control_nodes.get(str(clock.get("id"))) or {}
        for clock in telemetry_payload.get("clocks", [])[1:]
    ]
    independent_clock_mode = bool(receiver_controls) and all(
        item.get("enabled") is False
        for item in receiver_controls
    )
    independent_clock_reason = (
        "all downstream PHCs are in measured holdover"
        if independent_clock_mode
        else "N-cornered separation is gated until all downstream PHCs are independently free-running"
    )
    kalman_histories = {
        str(clock.get("id")): load_kalman_history(str(clock.get("id")))
        for clock in telemetry_payload.get("clocks", [])[1:]
        if (control_nodes.get(str(clock.get("id"))) or {}).get("type") in {"kalman", "adaptive-kalman", "imm"}
    }
    snapshot = RESEARCH_ENGINE.snapshot(
        telemetry_payload["clocks"],
        float(telemetry_payload.get("phc_sample_rate_hz") or configured_phc_sample_rate_hz()),
        float(servo.get("kp", 0.7)),
        float(servo.get("ki", 0.3)),
        active_controller,
        independent_clock_mode,
        independent_clock_reason,
        kalman_histories,
    )
    path_events = raw_path_events(128)
    snapshot.setdefault("dynamics", {})["path_regimes"] = path_regime_analysis(path_events)
    snapshot.update(
        {
            "mode": telemetry_payload["phc_mode"],
            "capabilities": hardware_capabilities(),
            "profiles": profile_compliance(config_value),
            "path_microscope": {
                "events": path_events,
                "mode": "live" if path_events else "waiting",
                "provenance": "LinuxPTP slave-event-monitor TLVs; no exchange timestamps are synthesized",
            },
            "experiments": experiment_store().list(20),
            "active_experiment": experiment_store().active(),
            "security": {
                "authentication": {
                    "enabled": bool(config_value.get("security", {}).get("authentication", {}).get("enabled")),
                    "spp": config_value.get("security", {}).get("authentication", {}).get("spp"),
                    "active_key_id": config_value.get("security", {}).get("authentication", {}).get("active_key_id"),
                    "allow_unauth": config_value.get("security", {}).get("authentication", {}).get("allow_unauth"),
                    "key_material_exposed": False,
                },
            },
        }
    )
    return snapshot


def _cached_research_payload(
    payload: dict[str, Any],
    stored_at: float,
    refreshing: bool,
) -> dict[str, Any]:
    result = dict(payload)
    result["analysis_cache"] = {
        "age_s": max(0.0, time.monotonic() - stored_at),
        "max_age_s": RESEARCH_CACHE_SECONDS,
        "refreshing": refreshing,
        "request_coalescing": True,
    }
    return result


def _refresh_research_snapshot(cache_key: int, history_seconds: float) -> None:
    try:
        payload = _build_research_snapshot(history_seconds)
    except Exception as exc:  # pragma: no cover - surfaced through the next cold request
        print(f"PTPBox research refresh failed: {exc}", file=sys.stderr)
        with _RESEARCH_SNAPSHOT_CONDITION:
            _RESEARCH_SNAPSHOT_REFRESHING.discard(cache_key)
            _RESEARCH_SNAPSHOT_CONDITION.notify_all()
        return
    with _RESEARCH_SNAPSHOT_CONDITION:
        _RESEARCH_SNAPSHOT_CACHE[cache_key] = (time.monotonic(), payload)
        if len(_RESEARCH_SNAPSHOT_CACHE) > 8:
            oldest = min(
                _RESEARCH_SNAPSHOT_CACHE,
                key=lambda key: _RESEARCH_SNAPSHOT_CACHE[key][0],
            )
            if oldest != cache_key:
                _RESEARCH_SNAPSHOT_CACHE.pop(oldest, None)
        _RESEARCH_SNAPSHOT_REFRESHING.discard(cache_key)
        _RESEARCH_SNAPSHOT_CONDITION.notify_all()


def research_snapshot(history_seconds: float = 900.0) -> dict[str, Any]:
    """Share heavy analysis work across synchronized Observatory pollers."""
    cache_key = int(round(history_seconds))
    while True:
        with _RESEARCH_SNAPSHOT_CONDITION:
            cached = _RESEARCH_SNAPSHOT_CACHE.get(cache_key)
            if cached:
                stored_at, payload = cached
                expired = time.monotonic() - stored_at >= RESEARCH_CACHE_SECONDS
                if expired and cache_key not in _RESEARCH_SNAPSHOT_REFRESHING:
                    _RESEARCH_SNAPSHOT_REFRESHING.add(cache_key)
                    threading.Thread(
                        target=_refresh_research_snapshot,
                        args=(cache_key, history_seconds),
                        name=f"ptpbox-research-{cache_key}",
                        daemon=True,
                    ).start()
                return _cached_research_payload(
                    payload,
                    stored_at,
                    cache_key in _RESEARCH_SNAPSHOT_REFRESHING,
                )
            if cache_key not in _RESEARCH_SNAPSHOT_REFRESHING:
                _RESEARCH_SNAPSHOT_REFRESHING.add(cache_key)
                break
            _RESEARCH_SNAPSHOT_CONDITION.wait()

    try:
        payload = _build_research_snapshot(history_seconds)
    except Exception:
        with _RESEARCH_SNAPSHOT_CONDITION:
            _RESEARCH_SNAPSHOT_REFRESHING.discard(cache_key)
            _RESEARCH_SNAPSHOT_CONDITION.notify_all()
        raise
    stored_at = time.monotonic()
    with _RESEARCH_SNAPSHOT_CONDITION:
        _RESEARCH_SNAPSHOT_CACHE[cache_key] = (stored_at, payload)
        _RESEARCH_SNAPSHOT_REFRESHING.discard(cache_key)
        _RESEARCH_SNAPSHOT_CONDITION.notify_all()
    return _cached_research_payload(payload, stored_at, False)


def read_integer(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def phc_pps_capabilities(phc: str | None) -> dict[str, Any]:
    if not phc or not re.fullmatch(r"ptp\d+", phc):
        return {
            "available": False,
            "external_timestamp_channels": 0,
            "periodic_output_channels": 0,
            "programmable_pins": 0,
            "pins": [],
        }
    root = Path("/sys/class/ptp", phc)
    pins: list[dict[str, Any]] = []
    try:
        pin_paths = sorted((root / "pins").iterdir(), key=lambda item: item.name)
    except OSError:
        pin_paths = []
    functions = {0: "none", 1: "external-timestamp", 2: "periodic-output", 3: "physical-sync"}
    for index, path in enumerate(pin_paths):
        values = read_text(path).split()
        try:
            function = int(values[0])
            channel = int(values[1])
        except (IndexError, ValueError):
            function = -1
            channel = -1
        pins.append(
            {
                "index": index,
                "name": path.name,
                "function": functions.get(function, f"unknown-{function}"),
                "channel": channel,
            }
        )
    return {
        "available": read_integer(root / "pps_available") > 0,
        "external_timestamp_channels": read_integer(root / "n_external_timestamps"),
        "periodic_output_channels": read_integer(root / "n_periodic_outputs"),
        "programmable_pins": read_integer(root / "n_programmable_pins"),
        "pins": pins,
    }


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pps_status(processes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    pps = load_config()["pps"]
    topology = topology_nodes()
    inventory_value = load_json(PHC_MAP_FILE, [])
    inventory = {
        item["id"]: item
        for item in inventory_value
        if isinstance(inventory_value, list) and isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    managed_value = load_json(PPS_PROCESS_FILE, [])
    managed = managed_value if isinstance(managed_value, list) else []
    managed_pps = next(
        (
            item
            for item in managed
            if isinstance(item, dict)
            and (item.get("kind") in {"ts2phc", "pps-comparison"} or item.get("label") in {"PPS-ts2phc", "PPS-COMPARE"})
        ),
        None,
    )
    managed_pid = managed_pps.get("pid") if isinstance(managed_pps, dict) else None
    managed_running = isinstance(managed_pid, int) and process_is_alive(managed_pid)
    if not managed_running and processes:
        managed_running = any(
            (
                item.get("name") == "ts2phc" and "ptpbox-ts2phc.conf" in str(item.get("command", ""))
            )
            or "ptpbox-pps-compare" in str(item.get("command", ""))
            for item in processes
        )

    source = pps["source"]
    sinks = set(pps["sinks"])
    nodes: dict[str, dict[str, Any]] = {}
    for node in topology:
        name = node["name"]
        mapped = inventory.get(name, {})
        phc = mapped.get("measurement_phc") if isinstance(mapped, dict) else None
        capabilities = phc_pps_capabilities(phc if isinstance(phc, str) else None)
        role = "source" if source == name else "sink" if name in sinks else "disabled"
        pin_index = int(pps["output_pin"] if role == "source" else pps["input_pin"])
        pins = capabilities["pins"]
        pin = pins[pin_index] if 0 <= pin_index < len(pins) else None
        expected_function = "periodic-output" if role == "source" else "external-timestamp" if role == "sink" else None
        role_capable = bool(
            capabilities["available"]
            and (
                role == "disabled"
                or (
                    role == "source"
                    and capabilities["periodic_output_channels"] > int(pps["channel"])
                    and capabilities["programmable_pins"] > pin_index
                )
                or (
                    role == "sink"
                    and capabilities["external_timestamp_channels"] > int(pps["channel"])
                    and capabilities["programmable_pins"] > pin_index
                )
            )
        )
        configured = bool(pps["enabled"] and role != "disabled")
        pin_active = bool(pin and pin["function"] == expected_function and pin["channel"] == int(pps["channel"]))
        if not role_capable:
            state = "unavailable"
        elif not configured:
            state = "external" if pin and pin["function"] != "none" else "ready"
        elif managed_running and pin_active:
            state = "active"
        elif managed_running:
            state = "starting"
        else:
            state = "stopped"
        nodes[name] = {
            "role": role,
            "state": state,
            "configured": configured,
            "running": configured and managed_running,
            "capable": role_capable,
            "phc": phc,
            "device": f"/dev/{phc}" if phc else None,
            "pin": pin,
            "channel": int(pps["channel"]),
            "capabilities": capabilities,
        }
    return {
        "enabled": bool(pps["enabled"]),
        "running": managed_running,
        "source": source,
        "sinks": list(pps["sinks"]),
        "servo": pps["ts2phc"]["servo"],
        "mode": "common-edge-measurement" if pps.get("comparison", {}).get("enabled") else "ts2phc-discipline",
        "comparison": {
            **pps.get("comparison", {}),
            "state": load_json(STATE_DIR / "pps-comparison.json", {}),
        },
        "pulse_width_ns": int(pps["pulse_width_ns"]),
        "nodes": nodes,
        "timestamp": time.time(),
    }


def status() -> dict[str, Any]:
    ports = interfaces()
    processes = running_processes()
    capabilities = hardware_capabilities()
    return {
        "hostname": socket.gethostname(),
        "linuxptp": linuxptp_version(),
        "interfaces": len(ports),
        "ptp_interfaces": sum(port.hardware_timestamping for port in ports),
        "namespaces": namespaces(),
        "processes": processes,
        "running": bool(processes),
        "phc_sample_rate_hz": configured_phc_sample_rate_hz(),
        "servo_control": load_servo_state(),
        "pps": pps_status(processes),
        "advanced_capabilities": {
            name: bool(details.get("supported"))
            for name, details in capabilities.items()
            if isinstance(details, dict) and "supported" in details
        },
        "active_experiment": experiment_store().active(),
        "holdover": holdover_session_snapshot(refresh=False, include_series=False),
        "profile_compliance": profile_compliance(),
        "fault": load_json(FAULT_STATE_FILE, {"enabled": False}),
        "identification": load_json(IDENTIFICATION_STATE_FILE, {"enabled": False}),
        "observer_only": os.geteuid() != 0 and not CONTROL.exists(),
        "root": str(ROOT),
        "agent_version": "2.5.1",
        "timestamp": time.time(),
    }


def control(action: str) -> tuple[int, dict[str, Any]]:
    if action not in {"start", "stop", "restart", "status", "servo", "fault", "identify"}:
        return HTTPStatus.BAD_REQUEST, {"error": "unsupported control action"}
    if not CONTROL.exists():
        return HTTPStatus.SERVICE_UNAVAILABLE, {"error": "privileged control helper is not installed", "observer_only": True}
    command = [str(CONTROL), action]
    if os.geteuid() != 0:
        command = ["sudo", "-n", *command]
    result = run(command, timeout=20)
    if result.returncode != 0:
        return HTTPStatus.CONFLICT, {"error": result.stderr.strip() or result.stdout.strip() or "control action failed"}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"message": result.stdout.strip() or f"{action} completed"}
    return HTTPStatus.OK, payload


def fault_expiry_loop(stop: threading.Event) -> None:
    while not stop.wait(0.5):
        state = load_json(FAULT_STATE_FILE, {})
        expires_at = state.get("expires_at") if isinstance(state, dict) and state.get("enabled") else None
        if not isinstance(expires_at, (int, float)) or time.time() < float(expires_at):
            continue
        target = state.get("target")
        if not isinstance(target, str):
            continue
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        pending = FAULT_REQUEST_FILE.with_suffix(".json.tmp")
        pending.write_text(json.dumps({"target": target, "enabled": False}, indent=2) + "\n", encoding="utf-8")
        pending.replace(FAULT_REQUEST_FILE)
        control("fault")


class Handler(BaseHTTPRequestHandler):
    server_version = "PTPBoxAgent/2.5.0"

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-PTPBox-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} [{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, value: Any, code: int = HTTPStatus.OK) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str, filename: str | None = None) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, route: str) -> None:
        relative = route.lstrip("/") or "standalone/index.html"
        candidate = (WEB_ROOT / relative).resolve()
        root = WEB_ROOT.resolve()
        if root not in candidate.parents and candidate != root:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            candidate = root / "standalone" / "index.html"
        if not candidate.is_file():
            self.send_json({"error": "web application is not installed"}, HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if candidate.name != "index.html":
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
            value = json.loads(self.rfile.read(length) or b"{}")
            return value if isinstance(value, dict) else {}
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        try:
            if route == "/api/status":
                self.send_json(status())
            elif route == "/api/interfaces":
                self.send_json({"interfaces": [asdict(item) for item in interfaces()], "timestamp": time.time()})
            elif route == "/api/telemetry":
                try:
                    history_seconds = float(query.get("history", ["120"])[0])
                    since = float(query["since"][0]) if "since" in query else None
                    limit = int(query.get("limit", [str(TELEMETRY_MAX_SAMPLES)])[0])
                except (TypeError, ValueError):
                    self.send_json({"error": "history, since, and limit must be numeric"}, HTTPStatus.BAD_REQUEST)
                    return
                self.send_json(telemetry(history_seconds, since, limit))
            elif route == "/api/phc":
                try:
                    history_seconds = float(query.get("history", ["120"])[0])
                    since = float(query["since"][0]) if "since" in query else None
                except (TypeError, ValueError):
                    self.send_json({"error": "history and since must be numeric"}, HTTPStatus.BAD_REQUEST)
                    return
                self.send_json(phc_telemetry(history_seconds, since))
            elif route == "/api/config":
                self.send_json(load_config())
            elif route == "/api/servo":
                self.send_json({"supported": sorted(SUPPORTED_SERVOS), "state": load_servo_state(), "timestamp": time.time()})
            elif route == "/api/research":
                try:
                    history_seconds = float(query.get("history", ["900"])[0])
                except (TypeError, ValueError):
                    self.send_json({"error": "history must be numeric"}, HTTPStatus.BAD_REQUEST)
                    return
                self.send_json(research_snapshot(max(30.0, min(7200.0, history_seconds))))
            elif route == "/api/capabilities":
                self.send_json(hardware_capabilities(force=query.get("refresh") == ["1"]))
            elif route == "/api/path-events":
                try:
                    limit = int(query.get("limit", ["128"])[0])
                except (TypeError, ValueError):
                    self.send_json({"error": "limit must be numeric"}, HTTPStatus.BAD_REQUEST)
                    return
                self.send_json({"events": raw_path_events(limit), "timestamp": time.time()})
            elif route == "/api/profiles":
                self.send_json(profile_compliance())
            elif route == "/api/experiments":
                self.send_json({"active": experiment_store().active(), "runs": experiment_store().list(100), "timestamp": time.time()})
            elif route == "/api/holdover":
                self.send_json(holdover_session_snapshot(refresh=True))
            elif route.startswith("/api/experiments/") and route.endswith("/export"):
                identifier = route.removeprefix("/api/experiments/").removesuffix("/export").strip("/")
                if not re.fullmatch(r"run-[0-9A-Za-z-]+", identifier) or not experiment_store().get(identifier):
                    self.send_json({"error": "experiment not found"}, HTTPStatus.NOT_FOUND)
                    return
                body = experiment_store().export_csv(identifier).encode("utf-8")
                self.send_bytes(body, "text/csv; charset=utf-8", f"{identifier}.csv")
            elif route == "/healthz":
                self.send_json({"ok": True, "timestamp": time.time()})
            else:
                self.send_static(route)
        except (OSError, subprocess.SubprocessError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        body = self.read_json()
        if route == "/api/config/apply":
            errors = validate_config(body)
            if errors:
                self.send_json({"error": "validation failed", "details": errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            save_config(body)
            self.send_json({"ok": True, "staged": True, "path": str(CONFIG_FILE), "timestamp": time.time()})
            return
        if route == "/api/control":
            action = str(body.get("action", ""))
            if action not in {"start", "stop", "restart", "status"}:
                self.send_json({"error": "unsupported control action"}, HTTPStatus.BAD_REQUEST)
                return
            code, response = control(action)
            self.send_json(response, code)
            return
        if route == "/api/servo/control":
            target = body.get("target")
            enabled = body.get("enabled")
            servo_type = body.get("type")
            receiver_ids = [node["name"] for node in topology_nodes()[1:]]
            if target != "all" and target not in receiver_ids:
                self.send_json({"error": "target must be all or a downstream clock"}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if not isinstance(enabled, bool) or servo_type not in SUPPORTED_SERVOS:
                self.send_json({"error": "enabled must be boolean and type must be pi, linreg, nullf, kalman, adaptive-kalman, or imm"}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            pending = SERVO_REQUEST_FILE.with_suffix(".json.tmp")
            pending.write_text(json.dumps({"target": target, "enabled": enabled, "type": servo_type}, indent=2) + "\n", encoding="utf-8")
            pending.replace(SERVO_REQUEST_FILE)
            code, response = control("servo")
            self.send_json(response, code)
            return
        if route == "/api/holdover/control":
            action = str(body.get("action") or "")
            try:
                if action == "start":
                    response = start_holdover_session(body)
                    code = HTTPStatus.CREATED
                elif action == "release":
                    response = release_holdover_session(force=bool(body.get("force", False)))
                    code = HTTPStatus.OK
                elif action == "resume":
                    response = resume_holdover_session()
                    code = HTTPStatus.OK
                elif action == "abort":
                    response = resume_holdover_session(aborted=True)
                    code = HTTPStatus.OK
                else:
                    self.send_json({"error": "action must be start, release, resume, or abort"}, HTTPStatus.BAD_REQUEST)
                    return
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            except (OSError, sqlite3.Error, RuntimeError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
                return
            self.send_json(response, code)
            return
        if route == "/api/experiments/start":
            try:
                experiment = experiment_store().start(body, load_config())
                experiment_store().event("experiment", "info", f"Started {experiment.get('kind', 'capture')} capture", {"id": experiment.get("id")})
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
                return
            self.send_json(experiment, HTTPStatus.CREATED)
            return
        if route == "/api/experiments/stop":
            identifier = body.get("id")
            if identifier is not None and not isinstance(identifier, str):
                self.send_json({"error": "id must be a string"}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            experiment = experiment_store().stop(identifier)
            if not experiment:
                self.send_json({"error": "no running experiment"}, HTTPStatus.NOT_FOUND)
                return
            experiment_store().event("experiment", "info", f"Completed capture {experiment['id']}", {"id": experiment["id"]})
            self.send_json(experiment)
            return
        if route == "/api/fault/control":
            target = body.get("target")
            enabled = body.get("enabled")
            node_ids = [node["name"] for node in topology_nodes()[:-1]]
            if target not in node_ids or not isinstance(enabled, bool):
                self.send_json({"error": "target must be an upstream topology clock and enabled must be boolean"}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            normalized = {"target": target, "enabled": enabled}
            for key, minimum, maximum in (
                ("delay_us", 0.0, 1_000_000.0),
                ("jitter_us", 0.0, 1_000_000.0),
                ("loss_pct", 0.0, 100.0),
                ("duration_s", 1.0, 3600.0),
            ):
                value = body.get(key, 0 if key != "duration_s" else 30)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
                    self.send_json({"error": f"{key} must be between {minimum} and {maximum}"}, HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                normalized[key] = float(value)
            if enabled and normalized["delay_us"] == 0 and normalized["jitter_us"] == 0 and normalized["loss_pct"] == 0:
                self.send_json({"error": "an enabled fault needs delay, jitter, or loss"}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            pending = FAULT_REQUEST_FILE.with_suffix(".json.tmp")
            pending.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
            pending.replace(FAULT_REQUEST_FILE)
            code, response = control("fault")
            if code == HTTPStatus.OK:
                experiment_store().event("fault", "warning" if enabled else "info", f"{'Applied' if enabled else 'Cleared'} guarded netem on {target}", normalized)
            self.send_json(response, code)
            return
        if route == "/api/identification/control":
            target = body.get("target")
            enabled = body.get("enabled")
            receiver_ids = [node["name"] for node in topology_nodes()[1:]]
            if target not in receiver_ids or not isinstance(enabled, bool):
                self.send_json(
                    {"error": "target must be a downstream clock and enabled must be boolean"},
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
                return
            control_node = (load_servo_state().get("nodes", {}) or {}).get(str(target), {})
            if enabled and (
                not isinstance(control_node, dict)
                or control_node.get("enabled") is not True
                or control_node.get("type") not in {"kalman", "adaptive-kalman", "imm"}
            ):
                self.send_json(
                    {"error": "active identification requires a running PTPBox Kalman, adaptive-Kalman, or IMM servo"},
                    HTTPStatus.CONFLICT,
                )
                return
            normalized: dict[str, Any] = {"target": target, "enabled": enabled}
            if enabled:
                amplitude = body.get("amplitude_ppb", 25.0)
                duration = body.get("duration_s", 180.0)
                offset_limit = body.get("offset_limit_ns", 5_000.0)
                frequencies = body.get("frequencies_hz", [0.01, 0.025, 0.05, 0.1])
                if (
                    isinstance(amplitude, bool)
                    or not isinstance(amplitude, (int, float))
                    or not 0.1 <= float(amplitude) <= 500.0
                    or isinstance(duration, bool)
                    or not isinstance(duration, (int, float))
                    or not 30.0 <= float(duration) <= 900.0
                    or isinstance(offset_limit, bool)
                    or not isinstance(offset_limit, (int, float))
                    or not 100.0 <= float(offset_limit) <= 100_000.0
                    or not isinstance(frequencies, list)
                    or not 1 <= len(frequencies) <= 8
                ):
                    self.send_json(
                        {"error": "amplitude, duration, offset limit, or frequency list is outside the guarded range"},
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                    return
                nyquist_limit = 0.45 * configured_phc_sample_rate_hz()
                if any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not 0.002 <= float(value) <= nyquist_limit
                    for value in frequencies
                ):
                    self.send_json(
                        {"error": f"every excitation frequency must be between 0.002 Hz and {nyquist_limit:.4f} Hz"},
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                    return
                normalized.update(
                    {
                        "amplitude_ppb": float(amplitude),
                        "duration_s": float(duration),
                        "offset_limit_ns": float(offset_limit),
                        "frequencies_hz": sorted({float(value) for value in frequencies}),
                    }
                )
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            pending = IDENTIFICATION_REQUEST_FILE.with_suffix(".json.tmp")
            pending.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
            pending.replace(IDENTIFICATION_REQUEST_FILE)
            code, response = control("identify")
            if code == HTTPStatus.OK:
                experiment_store().event(
                    "identification",
                    "warning" if enabled else "info",
                    f"{'Started' if enabled else 'Stopped'} bounded servo identification on {target}",
                    normalized,
                )
            self.send_json(response, code)
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser(description="PTPBox observation and control API")
    parser.add_argument("--bind", default=os.environ.get("PTPBOX_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PTPBOX_PORT", "8090")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    stop_sampler = threading.Event()
    sampler = threading.Thread(target=phc_sampler_loop, args=(stop_sampler,), name="ptpbox-phc-sampler", daemon=True)
    fault_expirer = threading.Thread(target=fault_expiry_loop, args=(stop_sampler,), name="ptpbox-fault-expirer", daemon=True)
    holdover_manager = threading.Thread(target=holdover_session_loop, args=(stop_sampler,), name="ptpbox-holdover-manager", daemon=True)
    sampler.start()
    fault_expirer.start()
    holdover_manager.start()
    print(f"PTPBox agent listening on http://{args.bind}:{args.port} (root={ROOT})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_sampler.set()
        sampler.join(timeout=2)
        fault_expirer.join(timeout=2)
        holdover_manager.join(timeout=2)
        server.server_close()


if __name__ == "__main__":
    main()
