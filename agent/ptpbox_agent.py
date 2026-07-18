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
LOG_PATTERN = re.compile(
    r"offset\s+(?P<offset>-?\d+(?:\.\d+)?)\s+"
    r"(?:(?P<servo_state>s\d+)\s+)?freq\s+(?P<freq>[+-]?\d+(?:\.\d+)?)\s+"
    r"path delay\s+(?P<delay>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
LOG_TIME_PATTERN = re.compile(r"\[(?P<seconds>\d+(?:\.\d+)?)\]")

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


LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.clock_gettime.argtypes = [ctypes.c_int, ctypes.POINTER(Timespec)]
LIBC.clock_gettime.restype = ctypes.c_int
PHC_HISTORY: deque[dict[str, Any]] = deque(maxlen=PHC_HISTORY_MAX_SAMPLES)
PHC_HISTORY_LOCK = threading.Lock()


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
    found: list[Interface] = []
    for interface in sorted(Path("/sys/class/net").iterdir(), key=lambda item: item.name):
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
        found.append(
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
            )
        )
    return found


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


def read_phc_ns(device: str) -> int:
    """Read a Linux PHC without changing its time or frequency."""
    fd = os.open(device, os.O_RDONLY)
    try:
        # Linux's FD_TO_CLOCKID macro for dynamic POSIX clocks.
        clock_id = ((~fd) << 3) | 3
        value = Timespec()
        if LIBC.clock_gettime(clock_id, ctypes.byref(value)) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), device)
        return int(value.tv_sec) * 1_000_000_000 + int(value.tv_nsec)
    finally:
        os.close(fd)


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
    """Compare every measured NIC PHC to BC1 using midpoint reads."""
    inventory = phc_inventory()
    if not inventory:
        return None
    reference = inventory[0]
    reference_device = f"/dev/{reference['measurement_phc']}"
    observed_at = time.time()
    sample_id = f"phc:{time.time_ns()}"
    clocks: list[dict[str, Any]] = []
    previous_offset: int | None = None
    for index, item in enumerate(inventory):
        device = f"/dev/{item['measurement_phc']}"
        try:
            if index == 0:
                read_phc_ns(reference_device)
                offset = 0
                read_span = 0
            else:
                reference_before = read_phc_ns(reference_device)
                target = read_phc_ns(device)
                reference_after = read_phc_ns(reference_device)
                offset = target - ((reference_before + reference_after) // 2)
                read_span = reference_after - reference_before
            hop_offset = None if previous_offset is None else offset - previous_offset
            clocks.append(
                {
                    "id": item["id"],
                    "phc": item["measurement_phc"],
                    "offset_ns": float(offset),
                    "previous_hop_offset_ns": float(hop_offset) if hop_offset is not None else None,
                    "read_span_ns": float(read_span),
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
        "method": "sequential PHC midpoint reads",
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
        "method": "sequential PHC midpoint reads",
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
    preferred = [LOG_DIR / f"{name}-OC.log"]
    if name == "BC1":
        preferred.append(LOG_DIR / f"{name}-GM.log")
    legacy_dir = ROOT / name
    legacy = sorted(legacy_dir.glob("*OC*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    fallback = sorted(legacy_dir.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    unique: list[Path] = []
    for path in [*preferred, *legacy, *fallback]:
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
                "window_invalid_sample_count": len(window_samples) - len(valid_window_samples),
                "rms_ns": (
                    sum(float(sample["offset_ns"]) ** 2 for sample in valid_window_samples) / len(valid_window_samples)
                ) ** 0.5 if valid_window_samples else None,
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
        "raw": True,
        "smoothing": "none",
        "measurement_source": "direct PHC comparison",
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
        "observer_only": os.geteuid() != 0 and not CONTROL.exists(),
        "root": str(ROOT),
        "agent_version": "1.3.0",
        "timestamp": time.time(),
    }


def control(action: str) -> tuple[int, dict[str, Any]]:
    if action not in {"start", "stop", "restart", "status"}:
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
            code, response = control(str(body.get("action", "")))
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
