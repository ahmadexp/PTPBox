#!/usr/bin/env python3
"""Apply a validated temperature/ageing model to a PHC during holdover.

Every other servo in PTPBox is sample-driven: it waits for a PTP offset and
reacts. Holdover removes exactly that input, so this worker is driven by a timer
instead. Each tick it reads the adapter's die temperature, asks the model what
frequency correction the oscillator needs now, and slews toward it.

The model is chosen elsewhere (``ptpbox_holdover_control.evaluate``) and is only
armed if it beat frozen holdover on a held-out stretch of the locked window. This
worker refuses to invent one: given no model file it exits rather than guess,
which leaves the clock in ordinary frozen-frequency holdover.

On exit the last applied frequency is deliberately left in place. Zeroing it
would step the oscillator at the moment the operator is trying to measure it.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable

# The model, its clamps, and the smoothing width live in one place so the worker
# and the evaluator cannot drift apart.
AGENT_DIR = Path(os.environ.get("PTPBOX_AGENT_DIR", "/opt/ptpbox-web/agent"))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))
try:
    import ptpbox_holdover_control as control
except ModuleNotFoundError as error:  # pragma: no cover - install-time problem
    raise SystemExit(
        f"cannot import ptpbox_holdover_control from {AGENT_DIR}: {error}. "
        "Set PTPBOX_AGENT_DIR or reinstall the agent."
    ) from error

CLOCKFD = 3
FD_TO_CLOCKID = 0xFFFFFFFF
ADJ_FREQUENCY = 0x0002


class Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class Timex(ctypes.Structure):
    _fields_ = [
        ("modes", ctypes.c_uint), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long), ("status", ctypes.c_int),
        ("constant", ctypes.c_long), ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
        ("time", Timeval), ("tick", ctypes.c_long), ("ppsfreq", ctypes.c_long),
        ("jitter", ctypes.c_long), ("shift", ctypes.c_int), ("stabil", ctypes.c_long),
        ("jitcnt", ctypes.c_long), ("calcnt", ctypes.c_long), ("errcnt", ctypes.c_long),
        ("stbcnt", ctypes.c_long), ("tai", ctypes.c_int),
        ("padding", ctypes.c_int * 11),
    ]


class PhcAdjuster:
    """Minimal clock_adjtime wrapper in LinuxPTP's ppb convention.

    Deliberately duplicated from the Kalman servo helper: the helpers install as
    bare executables under /usr/local/sbin, so neither can import the other.
    """

    def __init__(self, device: Path) -> None:
        self.device = device
        self.fd = os.open(device, os.O_RDWR)
        self.clockid = ((~self.fd) << 3) | FD_TO_CLOCKID
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.clock_adjtime.argtypes = [ctypes.c_int, ctypes.POINTER(Timex)]
        self.libc.clock_adjtime.restype = ctypes.c_int

    def close(self) -> None:
        os.close(self.fd)

    def _apply(self, value: Timex) -> None:
        if self.libc.clock_adjtime(self.clockid, ctypes.byref(value)) < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(self.device))

    def kernel_frequency_ppb(self) -> float:
        value = Timex()
        self._apply(value)
        return float(value.freq) / 65.536

    def set_servo_frequency_ppb(self, correction_ppb: float) -> None:
        value = Timex()
        value.modes = ADJ_FREQUENCY
        # The servo correction has the opposite sign to the kernel adjustment.
        value.freq = round(-correction_ppb * 65.536)
        self._apply(value)


def read_temperature(path: Path) -> float | None:
    """Read one hwmon temperature input, in millidegrees, as degrees C."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return float(raw) / 1000.0
    except ValueError:
        return None


def load_model(path: Path) -> control.Model:
    payload = json.loads(path.read_text(encoding="utf-8"))
    model = payload.get("model") if "model" in payload else payload
    kind = str(model.get("kind", ""))
    if kind not in control.KINDS:
        raise SystemExit(f"unsupported holdover model kind: {kind!r}")
    span = model.get("temperature_range_c") or [0.0, 0.0]
    return control.Model(
        kind=kind,
        intercept_ppb=float(model.get("intercept_ppb", 0.0)),
        tempco_ppb_per_c=float(model.get("tempco_ppb_per_c", 0.0)),
        drift_ppb_per_s=float(model.get("drift_ppb_per_s", 0.0)),
        reference_temperature_c=float(model.get("reference_temperature_c", 0.0)),
        reference_time=float(model.get("reference_time", 0.0)),
        temperature_min_c=float(span[0]),
        temperature_max_c=float(span[1]),
    )


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def run(
    model: control.Model,
    adjuster: Any,
    temperature: Callable[[], float | None],
    state_path: Path,
    node: str,
    interval_s: float,
    max_seconds: float,
    max_slew_ppb_per_s: float,
    stop: list[bool],
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Tick until stopped, the budget expires, or the model's horizon is passed."""
    released_at = now()
    # Anchor on what the servo left behind, so the first tick is continuous with
    # the discipline that was just removed.
    initial = -adjuster.kernel_frequency_ppb()
    model = control.Model(
        model.kind, model.intercept_ppb, model.tempco_ppb_per_c, model.drift_ppb_per_s,
        model.reference_temperature_c, released_at,
        model.temperature_min_c, model.temperature_max_c,
    )
    compensator = control.Compensator(model, released_at, initial, max_slew_ppb_per_s)
    status: dict[str, Any] = {}
    ticks = 0
    while not stop[0]:
        moment = now()
        elapsed = moment - released_at
        if max_seconds > 0 and elapsed > max_seconds:
            break
        status = compensator.tick(moment, temperature())
        try:
            adjuster.set_servo_frequency_ppb(status["applied_ppb"])
            status["applied"] = True
        except OSError as error:
            status["applied"] = False
            status["error"] = str(error)
        ticks += 1
        payload = {
            "node": node,
            "servo": "holdover-compensator",
            "model": model.as_dict(),
            "released_at": released_at,
            "observed_at": moment,
            "ticks": ticks,
            "initial_correction_ppb": initial,
            **status,
        }
        atomic_json(state_path, payload)
        print(json.dumps(payload, separators=(",", ":")), flush=True)
        if stop[0]:
            break
        sleep(interval_s)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply temperature-compensated holdover to a PHC")
    parser.add_argument("--node", required=True)
    parser.add_argument("--phc", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path,
                        help="JSON model written by the evaluator when it armed")
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--temperature-file", required=True, type=Path,
                        help="hwmon tempN_input for this adapter")
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--max-seconds", type=float, default=control.MAX_DRIFT_HORIZON_S)
    parser.add_argument("--max-slew-ppb-per-s", type=float, default=control.MAX_SLEW_PPB_PER_S)
    args = parser.parse_args()

    for name in ("interval_s", "max_slew_ppb_per_s"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if not args.model.exists():
        raise SystemExit(
            f"no armed holdover model at {args.model}; leaving the clock in frozen holdover"
        )

    model = load_model(args.model)
    stop = [False]

    def request_stop(_signal: int, _frame: Any) -> None:
        stop[0] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    adjuster = PhcAdjuster(args.phc)
    try:
        run(model, adjuster, lambda: read_temperature(args.temperature_file),
            args.state, args.node, args.interval_s, args.max_seconds,
            args.max_slew_ppb_per_s, stop)
    finally:
        # Leave the last correction applied on purpose; see the module docstring.
        adjuster.close()


if __name__ == "__main__":
    main()
