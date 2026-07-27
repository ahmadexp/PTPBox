#!/usr/bin/env python3
"""Dedicated raw PHC acquisition process, isolated from heavy web analysis."""

from __future__ import annotations

import signal
import sqlite3
import threading
import time

import ptpbox_agent as agent
from ptpbox_phc_store import append_sample


def collect(stop: threading.Event) -> None:
    deadline = time.monotonic()
    consecutive_failures = 0
    while not stop.is_set():
        requested_rate = agent.configured_phc_sample_rate_hz()
        period = 1.0 / requested_rate
        delivered = False
        sample = agent.take_phc_sample()
        if sample is not None:
            temperatures = agent.clock_temperatures()
            try:
                append_sample(agent.PHC_STORE_FILE, sample, temperatures)
                agent.experiment_store().record_phc(sample, temperatures)
                delivered = True
            except (OSError, sqlite3.Error) as exc:
                print(f"PTPBox PHC store write failed: {exc}", flush=True)
        else:
            print("PTPBox PHC sample unavailable", flush=True)

        # A sustained inability to acquire or store is not something this loop can
        # repair: exhausted descriptors, a revoked store path, or a disappearing
        # PHC all persist.  Exit so the supervisor restarts a clean process
        # instead of leaving a live service that silently delivers nothing.
        consecutive_failures = 0 if delivered else consecutive_failures + 1
        if consecutive_failures >= max(10, int(round(requested_rate * 10))):
            print(
                f"PTPBox PHC collector delivered nothing for {consecutive_failures} "
                "consecutive cycles; exiting for supervisor restart",
                flush=True,
            )
            raise SystemExit(1)

        deadline += period
        now = time.monotonic()
        if deadline < now - period:
            deadline = now
        stop.wait(max(0.0, deadline - now))


def main() -> None:
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    print(f"PTPBox PHC collector writing {agent.PHC_STORE_FILE}", flush=True)
    collect(stop)


if __name__ == "__main__":
    main()
