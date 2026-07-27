#!/usr/bin/env python3
"""Read-only host resource and hardware inventory for the System Observatory.

Everything here reads `/proc`, `/sys`, and mount statistics only. Nothing in
this module needs root, spawns a shell, writes to the filesystem, or touches a
PTP hardware clock, so it can be served on the same unprivileged path as the
rest of the observation API.

Root paths are parameters rather than constants so the parsers can be exercised
against a synthetic tree in tests.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

PROC = Path("/proc")
SYS = Path("/sys")

# Vendors relevant to a timing appliance; anything else is reported by raw id
# rather than guessed at.
PCI_VENDORS = {
    "0x15b3": "Mellanox/NVIDIA",
    "0x8086": "Intel",
    "0x1022": "AMD",
    "0x10de": "NVIDIA",
    "0x1d0f": "Amazon",
    "0x14e4": "Broadcom",
    "0x1077": "QLogic",
    "0x1af4": "Red Hat/Virtio",
}

# Only real, locally-backed filesystems are interesting for capacity.
SKIP_FSTYPES = {
    "proc", "sysfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "pstore",
    "securityfs", "debugfs", "tracefs", "configfs", "fusectl", "bpf",
    "autofs", "hugetlbfs", "mqueue", "binfmt_misc", "nsfs", "squashfs",
    "efivarfs", "ramfs", "rpc_pipefs",
}

_CPU_SAMPLE: dict[str, Any] = {}


def _text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, UnicodeDecodeError):
        return default


def _int(path: Path) -> int | None:
    raw = _text(path)
    try:
        return int(raw)
    except ValueError:
        return None


def host_info(proc: Path = PROC, etc: Path = Path("/etc")) -> dict[str, Any]:
    uptime_raw = _text(proc / "uptime")
    uptime_s: float | None = None
    if uptime_raw:
        try:
            uptime_s = float(uptime_raw.split()[0])
        except (ValueError, IndexError):
            uptime_s = None
    pretty = ""
    for line in _text(etc / "os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = line.partition("=")[2].strip().strip('"')
            break
    return {
        "hostname": socket.gethostname(),
        "kernel": _text(proc / "sys" / "kernel" / "osrelease") or None,
        "os": pretty or None,
        "uptime_s": uptime_s,
        "boot_time": (time.time() - uptime_s) if uptime_s is not None else None,
    }


def cpu_info(proc: Path = PROC, sysfs: Path = SYS) -> dict[str, Any]:
    model = None
    threads = 0
    physical: set[tuple[str, str]] = set()
    socket_id = ""
    for line in _text(proc / "cpuinfo").splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        # "processor" is the authoritative per-thread record; counting any other
        # key as well double-counts each logical CPU.
        if key == "processor":
            threads += 1
            socket_id = ""
        elif key == "model name":
            model = model or value
        elif key == "physical id":
            socket_id = value
        elif key == "core id":
            # A core is only unique within its socket.
            physical.add((socket_id, value))
    load: list[float] = []
    parts = _text(proc / "loadavg").split()
    for item in parts[:3]:
        try:
            load.append(float(item))
        except ValueError:
            pass
    freq_dir = sysfs / "devices" / "system" / "cpu" / "cpu0" / "cpufreq"
    current = _int(freq_dir / "scaling_cur_freq")
    minimum = _int(freq_dir / "cpuinfo_min_freq")
    maximum = _int(freq_dir / "cpuinfo_max_freq")
    return {
        "model": model,
        "threads": threads or None,
        "cores": len(physical) or None,
        "load_average": load or None,
        "mhz_current": (current / 1000.0) if current else None,
        "mhz_minimum": (minimum / 1000.0) if minimum else None,
        "mhz_maximum": (maximum / 1000.0) if maximum else None,
    }


def cpu_utilization(proc: Path = PROC, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Busy percentage from two /proc/stat readings.

    The delta is taken against the previous call rather than sleeping inside a
    request, so the first call after start reports null instead of blocking.
    """
    store = _CPU_SAMPLE if state is None else state
    fields: list[int] = []
    for line in _text(proc / "stat").splitlines():
        if line.startswith("cpu "):
            for item in line.split()[1:]:
                try:
                    fields.append(int(item))
                except ValueError:
                    break
            break
    if not fields:
        return {"busy_pct": None, "sampled_over_s": None}
    total = sum(fields)
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    now = time.monotonic()
    previous_total = store.get("total")
    previous_idle = store.get("idle")
    previous_at = store.get("at")
    store["total"], store["idle"], store["at"] = total, idle, now
    if previous_total is None or total <= previous_total:
        return {"busy_pct": None, "sampled_over_s": None}
    delta_total = total - previous_total
    delta_idle = idle - previous_idle
    busy = 100.0 * (1.0 - (delta_idle / delta_total)) if delta_total else None
    return {
        "busy_pct": None if busy is None else max(0.0, min(100.0, busy)),
        "sampled_over_s": None if previous_at is None else round(now - previous_at, 3),
    }


def memory_info(proc: Path = PROC) -> dict[str, Any]:
    values: dict[str, int] = {}
    for line in _text(proc / "meminfo").splitlines():
        key, _, rest = line.partition(":")
        item = rest.strip().split(" ")[0]
        try:
            values[key.strip()] = int(item)
        except ValueError:
            continue
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    swap_total = values.get("SwapTotal")
    swap_free = values.get("SwapFree")
    used = (total - available) if (total is not None and available is not None) else None
    return {
        "total_kb": total,
        "available_kb": available,
        "used_kb": used,
        "used_pct": (100.0 * used / total) if (used is not None and total) else None,
        "buffers_kb": values.get("Buffers"),
        "cached_kb": values.get("Cached"),
        "swap_total_kb": swap_total,
        "swap_used_kb": (swap_total - swap_free) if (swap_total is not None and swap_free is not None) else None,
    }


def storage_info(proc: Path = PROC) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for line in _text(proc / "mounts").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mount, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        if fstype in SKIP_FSTYPES or mount in seen:
            continue
        # /run matters because the volatile PHC store lives there, but its
        # per-service credential, user, and namespace submounts are views of the
        # same tmpfs and only add noise.
        if fstype == "tmpfs" and mount != "/run":
            continue
        seen.add(mount)
        try:
            stat = os.statvfs(mount)
        except OSError:
            continue
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        if total <= 0:
            continue
        used = total - (stat.f_bfree * stat.f_frsize)
        out.append({
            "mount": mount,
            "device": device,
            "fstype": fstype,
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": free,
            "used_pct": 100.0 * used / total,
        })
    out.sort(key=lambda item: item["mount"])
    return out


def thermal_info(sysfs: Path = SYS) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    thermal = sysfs / "class" / "thermal"
    try:
        zones = sorted(thermal.glob("thermal_zone*"))
    except OSError:
        zones = []
    for zone in zones:
        milli = _int(zone / "temp")
        if milli is None:
            continue
        out.append({
            "source": zone.name,
            "label": _text(zone / "type") or zone.name,
            "temperature_c": milli / 1000.0,
        })
    hwmon = sysfs / "class" / "hwmon"
    try:
        monitors = sorted(hwmon.glob("hwmon*"))
    except OSError:
        monitors = []
    for monitor in monitors:
        name = _text(monitor / "name") or monitor.name
        # Several identical adapters expose the same sensor name, so resolve the
        # owning device to tell a 97 C card from an 86 C one.
        device = None
        link = monitor / "device"
        try:
            if link.exists():
                resolved = Path(os.path.realpath(link))
                device = resolved.name
                if not re.match(r"^[0-9a-f]{4}:", device) and resolved.parent.name:
                    device = resolved.parent.name
        except OSError:
            device = None
        for sensor in sorted(monitor.glob("temp*_input")):
            milli = _int(sensor)
            if milli is None:
                continue
            label = _text(sensor.parent / sensor.name.replace("_input", "_label"))
            parts = [name, label, f"({device})" if device else ""]
            out.append({
                "source": f"{monitor.name}/{sensor.name}",
                "device": device,
                "label": " ".join(part for part in parts if part),
                "temperature_c": milli / 1000.0,
            })
    return out


def pci_devices(sysfs: Path = SYS) -> list[dict[str, Any]]:
    """PCI inventory from sysfs, with optional human names from lspci."""
    names: dict[str, str] = {}
    if shutil.which("lspci"):
        try:
            completed = subprocess.run(
                ["lspci", "-mm"], capture_output=True, text=True, timeout=5, check=False
            )
            for line in completed.stdout.splitlines():
                fields = re.findall(r'"([^"]*)"|(\S+)', line)
                flat = [a or b for a, b in fields]
                if len(flat) >= 4:
                    names[flat[0]] = f"{flat[2]} {flat[3]}".strip()
        except (OSError, subprocess.SubprocessError):
            names = {}
    out: list[dict[str, Any]] = []
    root = sysfs / "bus" / "pci" / "devices"
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        slot = entry.name
        vendor = _text(entry / "vendor")
        driver = None
        link = entry / "driver"
        if link.is_symlink() or link.exists():
            try:
                driver = os.path.basename(os.path.realpath(link))
            except OSError:
                driver = None
        short = slot.split(":", 1)[1] if ":" in slot else slot
        out.append({
            "slot": slot,
            "vendor_id": vendor or None,
            "vendor": PCI_VENDORS.get(vendor, vendor or None),
            "device_id": _text(entry / "device") or None,
            "class_id": _text(entry / "class") or None,
            "driver": driver,
            "description": names.get(short) or names.get(slot),
        })
    return out


def snapshot(
    interfaces: list[dict[str, Any]] | None = None,
    topology: dict[str, Any] | None = None,
    proc: Path = PROC,
    sysfs: Path = SYS,
) -> dict[str, Any]:
    """Assemble the System Observatory payload."""
    return {
        "timestamp": time.time(),
        "host": host_info(proc=proc),
        "cpu": {**cpu_info(proc=proc, sysfs=sysfs), **cpu_utilization(proc=proc)},
        "memory": memory_info(proc=proc),
        "storage": storage_info(proc=proc),
        "thermal": thermal_info(sysfs=sysfs),
        "pci": pci_devices(sysfs=sysfs),
        "topology": topology_verification(interfaces or [], topology or {}),
        "provenance": (
            "read-only /proc, /sys, and mount statistics; no privileged call, "
            "no clock access, and no filesystem write"
        ),
    }


def topology_verification(
    interfaces: list[dict[str, Any]],
    topology: dict[str, Any],
) -> dict[str, Any]:
    """Check the declared cascade against observed link state.

    This verifies what the host can see without touching the data plane. It is
    deliberately not called discovery: resolving which port is cabled to which
    peer needs the raw-frame prober, which requires the timing ports back in the
    host namespace and therefore a torn-down cascade.
    """
    by_name = {str(item.get("name")): item for item in interfaces}
    management = {str(name) for name in topology.get("management_interfaces", [])}
    links: list[dict[str, Any]] = []
    nodes = topology.get("nodes") or []
    for index, node in enumerate(nodes):
        name = str(node.get("name"))
        egress = str(node.get("egress") or "")
        downstream = nodes[index + 1] if index + 1 < len(nodes) else None
        ingress = str((downstream or {}).get("ingress") or "")
        left, right = by_name.get(egress), by_name.get(ingress)
        expected = [item for item in (left, right) if item]
        speeds = {item.get("speed_mbps") for item in expected if item.get("speed_mbps")}
        problems: list[str] = []
        for label, item, port in (("egress", left, egress), ("ingress", right, ingress)):
            if not port:
                continue
            if item is None:
                problems.append(f"{label} {port} not present")
            elif not item.get("carrier"):
                problems.append(f"{label} {port} has no carrier")
        if len(speeds) > 1:
            problems.append(f"speed mismatch {sorted(speeds)}")
        if downstream is None:
            continue
        links.append({
            "from": name,
            "to": str(downstream.get("name")),
            "from_port": egress,
            "to_port": ingress,
            "speed_mbps": (sorted(speeds)[0] if len(speeds) == 1 else None),
            "carrier": all(item.get("carrier") for item in expected) if expected else False,
            "verified": not problems,
            "problems": problems,
        })
    excluded = sorted(name for name in management if name in by_name)
    return {
        "status": "ready" if nodes else "no-topology",
        "declared_nodes": [str(node.get("name")) for node in nodes],
        "links": links,
        "verified_links": sum(1 for link in links if link["verified"]),
        "management_excluded": excluded,
        "discovery": {
            "available": False,
            "reason": (
                "peer discovery sends raw experimental-EtherType frames and needs the "
                "timing ports in the host namespace, so it requires a torn-down cascade "
                "and root: run scripts/probe-cabling.py"
            ),
        },
        "interpretation": (
            "Link state and declared mapping are verified against the host view. "
            "This is not physical peer discovery."
        ),
    }
