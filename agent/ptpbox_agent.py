#!/usr/bin/env python3
"""Small, dependency-free API for the PTPBox web console.

The agent intentionally separates read-only observation from privileged control.
It can run as an ordinary user for inventory and log telemetry. Start/stop calls
are delegated to a tightly scoped ptpboxctl sudo rule when the optional system
integration has been installed.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(os.environ.get("PTPBOX_ROOT", Path.home() / "PTPBox"))
STATE_DIR = Path(os.environ.get("PTPBOX_STATE_DIR", ROOT / "runtime"))
CONFIG_FILE = Path(os.environ.get("PTPBOX_CONFIG", STATE_DIR / "config.json"))
CONTROL = Path(os.environ.get("PTPBOX_CONTROL", "/usr/local/sbin/ptpboxctl"))
WEB_ROOT = Path(os.environ.get("PTPBOX_WEB_ROOT", Path(__file__).parent / "static"))
ALLOW_ORIGIN = os.environ.get("PTPBOX_ALLOW_ORIGIN", "*")
LOG_PATTERN = re.compile(
    r"offset\s+(?P<offset>-?\d+(?:\.\d+)?)\s+"
    r"(?:s\d+\s+)?freq\s+(?P<freq>[+-]?\d+(?:\.\d+)?)\s+"
    r"path delay\s+(?P<delay>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

DEFAULT_CONFIG: dict[str, Any] = {
    "profile": "G.8275.1 Telecom",
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


def last_log_measurement(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 64_000))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        match = LOG_PATTERN.search(line)
        if match:
            return {
                "offset_ns": float(match.group("offset")),
                "frequency_ppb": float(match.group("freq")),
                "mean_path_delay_ns": float(match.group("delay")),
                "source": str(path.relative_to(ROOT)),
                "observed_at": path.stat().st_mtime,
            }
    return None


def telemetry() -> dict[str, Any]:
    clocks: list[dict[str, Any]] = []
    for node_dir in sorted(ROOT.glob("BC[0-9]*")):
        candidates = sorted(node_dir.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
        measurement = next((value for path in candidates if (value := last_log_measurement(path))), None)
        clocks.append({"id": node_dir.name, "measurement": measurement, "logs": len(candidates)})
    measured = sum(1 for clock in clocks if clock["measurement"])
    return {"timestamp": time.time(), "clocks": clocks, "measured_clocks": measured, "mode": "live" if measured else "observer"}


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
        "agent_version": "1.0.0",
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
        route = urlparse(self.path).path
        try:
            if route == "/api/status":
                self.send_json(status())
            elif route == "/api/interfaces":
                self.send_json({"interfaces": [asdict(item) for item in interfaces()], "timestamp": time.time()})
            elif route == "/api/telemetry":
                self.send_json(telemetry())
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
    print(f"PTPBox agent listening on http://{args.bind}:{args.port} (root={ROOT})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
