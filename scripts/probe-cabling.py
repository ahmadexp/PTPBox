#!/usr/bin/env python3
"""Identify direct PTPBox cable peers with short experimental Ethernet frames."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import select
import socket
import time
from collections import Counter
from pathlib import Path
from typing import Any


ETHERTYPE = 0x88B5  # IEEE 802 local experimental EtherType.
MAGIC = b"PTPBOX-CABLE-PROBE\0"


def load_interfaces(path: Path) -> list[str]:
    topology: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    management = set(topology.get("management_interfaces", []))
    interfaces = [
        interface
        for node in topology.get("nodes", [])
        for interface in (node.get("ingress"), node.get("egress"))
        if isinstance(interface, str) and interface and interface not in management
    ]
    if not interfaces:
        raise ValueError(f"no timing interfaces found in {path}")
    if len(interfaces) != len(set(interfaces)):
        raise ValueError("topology contains a timing interface more than once")
    return interfaces


def mac_bytes(interface: str) -> bytes:
    value = Path("/sys/class/net", interface, "address").read_text(encoding="utf-8").strip()
    return bytes.fromhex(value.replace(":", ""))


def probe(interfaces: list[str], count: int, timeout: float) -> dict[str, Any]:
    protocol = socket.htons(ETHERTYPE)
    sockets: dict[str, socket.socket] = {}
    try:
        for interface in interfaces:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, protocol)
            sock.bind((interface, 0))
            sock.setblocking(False)
            sockets[interface] = sock

        directed: dict[str, Counter[str]] = {interface: Counter() for interface in interfaces}
        for source, sender in sockets.items():
            for sock in sockets.values():
                while True:
                    try:
                        sock.recv(2048)
                    except BlockingIOError:
                        break
            token = secrets.token_bytes(12)
            payload = MAGIC + token + source.encode("ascii") + b"\0"
            frame = b"\xff" * 6 + mac_bytes(source) + ETHERTYPE.to_bytes(2, "big") + payload
            for _ in range(count):
                sender.send(frame)
                time.sleep(0.015)

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                ready, _, _ = select.select(list(sockets.values()), [], [], max(0.0, deadline - time.monotonic()))
                if not ready:
                    break
                for receiver in ready:
                    try:
                        packet = receiver.recv(2048)
                    except BlockingIOError:
                        continue
                    if len(packet) < 14 or packet[12:14] != ETHERTYPE.to_bytes(2, "big"):
                        continue
                    if not packet[14:].startswith(MAGIC + token):
                        continue
                    destination = next(name for name, sock in sockets.items() if sock is receiver)
                    if destination != source:
                        directed[source][destination] += 1

        links: list[dict[str, Any]] = []
        paired: set[str] = set()
        for source in interfaces:
            if source in paired or not directed[source]:
                continue
            peer, forward = directed[source].most_common(1)[0]
            reverse = directed.get(peer, Counter()).get(source, 0)
            if reverse:
                links.append({"a": source, "b": peer, "forward_frames": forward, "reverse_frames": reverse})
                paired.update((source, peer))

        return {
            "ok": len(paired) == len(interfaces),
            "ethertype": f"0x{ETHERTYPE:04x}",
            "links": links,
            "unresolved": [interface for interface in interfaces if interface not in paired],
            "directed_observations": {source: dict(peers) for source, peers in directed.items() if peers},
        }
    finally:
        for sock in sockets.values():
            sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology", type=Path, default=Path("/etc/ptpbox/topology.json"))
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=0.20)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("raw cable probing requires root")
    if not 1 <= args.count <= 20:
        parser.error("--count must be between 1 and 20")
    if not 0.05 <= args.timeout <= 2.0:
        parser.error("--timeout must be between 0.05 and 2 seconds")
    print(json.dumps(probe(load_interfaces(args.topology), args.count, args.timeout), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
