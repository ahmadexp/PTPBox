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
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(os.environ.get("PTPBOX_ROOT", Path.home() / "PTPBox"))
STATE_DIR = Path(os.environ.get("PTPBOX_STATE_DIR", ROOT / "runtime"))
CONFIG_FILE = Path(os.environ.get("PTPBOX_CONFIG", STATE_DIR / "config.json"))
SERVO_REQUEST_FILE = STATE_DIR / "servo-request.json"
SERVO_STATE_FILE = Path(os.environ.get("PTPBOX_SERVO_STATE", "/run/ptpbox/servo-state.json"))
CONTROL = Path(os.environ.get("PTPBOX_CONTROL", "/usr/local/sbin/ptpboxctl"))
WEB_ROOT = Path(os.environ.get("PTPBOX_WEB_ROOT", Path(__file__).parent / "static"))
LOG_DIR = Path(os.environ.get("PTPBOX_LOG_DIR", "/var/log/ptpbox"))
TOPOLOGY_FILE = Path(os.environ.get("PTPBOX_TOPOLOGY", Path(__file__).with_name("topology.json")))
PHC_MAP_FILE = Path(os.environ.get("PTPBOX_PHC_MAP", "/run/ptpbox/phcs.json"))
ALLOW_ORIGIN = os.environ.get("PTPBOX_ALLOW_ORIGIN", "*")
TELEMETRY_MAX_BYTES = 2_000_000
TELEMETRY_MAX_SAMPLES = 4096
TELEMETRY_STALE_AFTER_SECONDS = 5.0
TELEMETRY_MAX_PATH_DELAY_NS = 1_000_000.0
PHC_HISTORY_MAX_SAMPLES = 900
PHC_STALE_AFTER_SECONDS = 3.0
PHC_CROSS_TIMESTAMP_SAMPLES = 9
SUPPORTED_SERVOS = {"pi", "linreg", "nullf"}
LOG_PATTERN = re.compile(
    r"offset\s+(?P<offset>-?\d+(?:\.\d+)?)\s+"
    r"(?:(?P<servo_state>s\d+)\s+)?freq\s+(?P<freq>[+-]?\d+(?:\.\d+)?)\s+"
    r"path delay\s+(?P<delay>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
LOG_TIME_PATTERN = re.compile(r"\[(?P<seconds>\d+(?:\.\d+)?)\]")
LOG_SESSION_PATTERN = re.compile(r"selected /dev/ptp\d+ as PTP clock", re.IGNORECASE)

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
    },
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
        if not re.search(r"\b(ptp4l|phc2sys|ts2phc)\b", line):
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
        "raw": True,
        "smoothing": "none",
        "method": "common-system cross timestamps with interpolated BC1 reference",
    }


def phc_sampler_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        started = time.monotonic()
        record_phc_sample()
        stop.wait(max(0.0, 1.0 - (time.monotonic() - started)))


def load_config() -> dict[str, Any]:
    try:
        value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else DEFAULT_CONFIG.copy()
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_CONFIG))


def validate_config(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(value.get("domain"), int) or not 0 <= value["domain"] <= 127:
        errors.append("domain must be an integer from 0 through 127")
    if value.get("transport") not in {"L2", "UDPv4", "UDPv6"}:
        errors.append("transport must be L2, UDPv4, or UDPv6")
    if value.get("delay_mechanism") not in {"P2P", "E2E"}:
        errors.append("delay_mechanism must be P2P or E2E")
    servo = value.get("servo")
    if not isinstance(servo, dict):
        errors.append("servo settings are required")
    else:
        if servo.get("type") not in SUPPORTED_SERVOS:
            errors.append("servo.type must be pi, linreg, or nullf")
        for key in ("kp", "ki"):
            if not isinstance(servo.get(key), (int, float)) or not 0 <= float(servo[key]) <= 10:
                errors.append(f"servo.{key} must be between 0 and 10")
        if not isinstance(servo.get("step_threshold_ns"), (int, float)) or servo["step_threshold_ns"] < 0:
            errors.append("servo.step_threshold_ns must be non-negative")
    return errors


def save_config(value: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    pending = CONFIG_FILE.with_suffix(".json.tmp")
    pending.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    pending.replace(CONFIG_FILE)


def load_servo_state() -> dict[str, Any]:
    value = load_json(SERVO_STATE_FILE, {})
    return value if isinstance(value, dict) else {}


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
            start = max(0, size - TELEMETRY_MAX_BYTES)
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
        "servo_control": load_servo_state(),
        "raw": True,
        "smoothing": "none",
        "measurement_source": "kernel cross-timestamped PHC comparison",
        "history_seconds": history_seconds,
    }


def status() -> dict[str, Any]:
    ports = interfaces()
    processes = running_processes()
    return {
        "hostname": socket.gethostname(),
        "linuxptp": linuxptp_version(),
        "interfaces": len(ports),
        "ptp_interfaces": sum(port.hardware_timestamping for port in ports),
        "namespaces": namespaces(),
        "processes": processes,
        "running": bool(processes),
        "servo_control": load_servo_state(),
        "observer_only": os.geteuid() != 0 and not CONTROL.exists(),
        "root": str(ROOT),
        "agent_version": "1.6.0",
        "timestamp": time.time(),
    }


def control(action: str) -> tuple[int, dict[str, Any]]:
    if action not in {"start", "stop", "restart", "status", "servo"}:
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


class Handler(BaseHTTPRequestHandler):
    server_version = "PTPBoxAgent/1.0"

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
                self.send_json({"error": "enabled must be boolean and type must be pi, linreg, or nullf"}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            pending = SERVO_REQUEST_FILE.with_suffix(".json.tmp")
            pending.write_text(json.dumps({"target": target, "enabled": enabled, "type": servo_type}, indent=2) + "\n", encoding="utf-8")
            pending.replace(SERVO_REQUEST_FILE)
            code, response = control("servo")
            self.send_json(response, code)
            return
        if route == "/api/experiments/start":
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            experiment = {"id": f"run-{int(time.time())}", "state": "staged", "created_at": time.time(), **body}
            (STATE_DIR / "experiment.json").write_text(json.dumps(experiment, indent=2) + "\n", encoding="utf-8")
            self.send_json(experiment, HTTPStatus.CREATED)
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
    sampler.start()
    print(f"PTPBox agent listening on http://{args.bind}:{args.port} (root={ROOT})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_sampler.set()
        sampler.join(timeout=2)
        server.server_close()


if __name__ == "__main__":
    main()
