#!/usr/bin/env python3
"""Read one physical PPS edge on multiple PHCs and compare event timestamps."""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
import select
import signal
import time
from collections import deque
from pathlib import Path
from typing import Any


PTP_PF_NONE = 0
PTP_PF_EXTTS = 1
PTP_ENABLE_FEATURE = 1 << 0
PTP_RISING_EDGE = 1 << 1
PTP_FALLING_EDGE = 1 << 2
PTP_STRICT_FLAGS = 1 << 3


class PtpClockTime(ctypes.Structure):
    _fields_ = [("sec", ctypes.c_int64), ("nsec", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]


class PtpExttsRequest(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 2)]


class PtpExttsEvent(ctypes.Structure):
    _fields_ = [("t", PtpClockTime), ("index", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 2)]


class PtpPinDesc(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char * 64),
        ("index", ctypes.c_uint32),
        ("func", ctypes.c_uint32),
        ("chan", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 5),
    ]


def linux_iow(number: int, structure: type[ctypes.Structure]) -> int:
    return (1 << 30) | (ctypes.sizeof(structure) << 16) | (ord("=") << 8) | number


PTP_EXTTS_REQUEST2 = linux_iow(11, PtpExttsRequest)
PTP_PIN_SETFUNC = linux_iow(7, PtpPinDesc)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    path.chmod(0o644)


def ioctl_struct(fd: int, request: int, value: ctypes.Structure) -> None:
    buffer = bytearray(ctypes.string_at(ctypes.addressof(value), ctypes.sizeof(value)))
    fcntl.ioctl(fd, request, buffer, True)


def configure(fd: int, pin: int, channel: int, flags: int) -> None:
    descriptor = PtpPinDesc()
    descriptor.index = pin
    descriptor.func = PTP_PF_EXTTS
    descriptor.chan = channel
    ioctl_struct(fd, PTP_PIN_SETFUNC, descriptor)
    request = PtpExttsRequest()
    request.index = channel
    request.flags = PTP_ENABLE_FEATURE | PTP_STRICT_FLAGS | flags
    ioctl_struct(fd, PTP_EXTTS_REQUEST2, request)


def release(fd: int, pin: int, channel: int) -> None:
    request = PtpExttsRequest()
    request.index = channel
    request.flags = 0
    try:
        ioctl_struct(fd, PTP_EXTTS_REQUEST2, request)
    except OSError:
        pass
    descriptor = PtpPinDesc()
    descriptor.index = pin
    descriptor.func = PTP_PF_NONE
    descriptor.chan = 0
    try:
        ioctl_struct(fd, PTP_PIN_SETFUNC, descriptor)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare PHCs on a common physical PPS edge")
    parser.add_argument("--clock", action="append", required=True, help="NODE=/dev/ptpN")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--pin", type=int, default=0)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--polarity", choices=["rising", "falling", "both"], default="rising")
    parser.add_argument("--correction-ns", type=int, default=0)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--history", type=int, default=256)
    args = parser.parse_args()
    clocks: dict[str, Path] = {}
    for assignment in args.clock:
        try:
            node, device = assignment.split("=", 1)
        except ValueError:
            parser.error("--clock must use NODE=/dev/ptpN")
        if not node or not device.startswith("/dev/ptp"):
            parser.error("--clock must use NODE=/dev/ptpN")
        clocks[node] = Path(device)
    if args.reference not in clocks:
        parser.error("--reference must name one configured clock")
    flags = {
        "rising": PTP_RISING_EDGE,
        "falling": PTP_FALLING_EDGE,
        "both": PTP_RISING_EDGE | PTP_FALLING_EDGE,
    }[args.polarity]
    descriptors: dict[int, str] = {}
    stop = [False]

    def request_stop(_number: int, _frame: Any) -> None:
        stop[0] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    pending: dict[int, dict[str, int]] = {}
    history: deque[dict[str, Any]] = deque(maxlen=max(8, min(4096, args.history)))
    try:
        for node, device in clocks.items():
            fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)
            try:
                configure(fd, args.pin, args.channel, flags)
            except Exception:
                os.close(fd)
                raise
            descriptors[fd] = node
        while not stop[0]:
            readable, _, _ = select.select(list(descriptors), [], [], 0.5)
            for fd in readable:
                try:
                    body = os.read(fd, ctypes.sizeof(PtpExttsEvent))
                except BlockingIOError:
                    continue
                if len(body) != ctypes.sizeof(PtpExttsEvent):
                    continue
                event = PtpExttsEvent.from_buffer_copy(body)
                timestamp_ns = int(event.t.sec) * 1_000_000_000 + int(event.t.nsec) - args.correction_ns
                edge = int(round(timestamp_ns / 1_000_000_000))
                pending.setdefault(edge, {})[descriptors[fd]] = timestamp_ns
                values = pending[edge]
                if len(values) != len(clocks):
                    continue
                reference_ns = values[args.reference]
                sample = {
                    "edge": edge,
                    "observed_at": time.time(),
                    "reference": args.reference,
                    "offsets_ns": {node: value - reference_ns for node, value in values.items()},
                    "timestamps_ns": values,
                    "raw": True,
                }
                history.append(sample)
                atomic_json(
                    args.state,
                    {
                        "mode": "live",
                        "reference": args.reference,
                        "clocks": list(clocks),
                        "pin": args.pin,
                        "channel": args.channel,
                        "polarity": args.polarity,
                        "samples": list(history),
                        "latest": sample,
                        "timestamp": time.time(),
                    },
                )
                print(json.dumps(sample, separators=(",", ":")), flush=True)
                pending.pop(edge, None)
            cutoff = int(time.time()) - 4
            for edge in [value for value in pending if value < cutoff]:
                pending.pop(edge, None)
    finally:
        for fd in list(descriptors):
            release(fd, args.pin, args.channel)
            os.close(fd)


if __name__ == "__main__":
    main()
