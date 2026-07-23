#!/usr/bin/env python3
"""Convert LinuxPTP slave-event-monitor TLVs into append-only JSON records."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


FIELD = re.compile(r"^\s*(?P<name>[A-Za-z][A-Za-z0-9]+)\s+(?P<value>\S+)")


def timestamp_ns(value: str) -> int:
    seconds, nanoseconds = value.split(".", 1)
    return int(seconds) * 1_000_000_000 + int(nanoseconds[:9].ljust(9, "0"))


def append_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(descriptor, body)
    finally:
        os.close(descriptor)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture raw LinuxPTP exchange timestamp TLVs")
    parser.add_argument("--node", required=True)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--server", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--domain", required=True, type=int)
    parser.add_argument("--transport-specific", type=int, choices=range(0, 16), default=0)
    args = parser.parse_args()
    if not re.fullmatch(r"BC[0-9]+", args.node):
        parser.error("--node must be a topology clock identifier")
    args.socket.parent.mkdir(parents=True, exist_ok=True)
    args.socket.unlink(missing_ok=True)
    stop = [False]
    process: subprocess.Popen[str] | None = None

    def request_stop(_number: int, _frame: Any) -> None:
        stop[0] = True
        if process and process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    process = subprocess.Popen(
        [
            "pmc",
            "-u",
            "-d",
            str(args.domain),
            "-t",
            f"{args.transport_specific:x}",
            "-i",
            str(args.socket),
            "-s",
            str(args.server),
        ],
        # Keep pmc's stdin pipe open.  With /dev/null pmc observes EOF and
        # exits before the first unsolicited slave-event-monitor message.
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    record: dict[str, Any] = {}
    kind: str | None = None
    try:
        assert process.stdout
        for line in process.stdout:
            if stop[0]:
                break
            if "SLAVE_RX_SYNC_TIMING_DATA" in line:
                kind = "sync"
                record = {}
            elif "SLAVE_DELAY_TIMING_DATA_NP" in line:
                kind = "delay"
                record = {}
            match = FIELD.match(line)
            if not match or not kind:
                continue
            name, value = match.group("name"), match.group("value")
            record[name] = value
            complete = kind == "sync" and name == "syncEventIngressTimestamp"
            complete = complete or kind == "delay" and name == "delayResponseTimestamp"
            if not complete:
                continue
            payload: dict[str, Any] = {
                "node": args.node,
                "kind": kind,
                "observed_at": time.time(),
                "sequence_id": int(record["sequenceId"]),
                "source_port_identity": record.get("sourcePortIdentity"),
                "raw": True,
            }
            correction_ns = int(record.get("totalCorrectionField", "0"))
            payload["correction_ns"] = correction_ns
            if kind == "sync":
                t1 = timestamp_ns(record["syncOriginTimestamp"])
                t2 = timestamp_ns(record["syncEventIngressTimestamp"])
                payload.update(
                    {
                        "t1_ns": str(t1),
                        "t2_ns": str(t2),
                        "forward_transit_ns": t2 - t1 - correction_ns,
                        "transit_interpretation": "apparent: includes inter-clock phase offset",
                        "scaled_cumulative_rate_offset": int(record.get("scaledCumulativeRateOffset", "0")),
                    }
                )
            else:
                t3 = timestamp_ns(record["delayOriginTimestamp"])
                t4 = timestamp_ns(record["delayResponseTimestamp"])
                payload.update(
                    {
                        "t3_ns": str(t3),
                        "t4_ns": str(t4),
                        "reverse_transit_ns": t4 - t3 - correction_ns,
                        "transit_interpretation": "apparent: includes inter-clock phase offset",
                    }
                )
            append_json(args.output, payload)
            print(json.dumps(payload, separators=(",", ":")), flush=True)
            record = {}
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        args.socket.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
