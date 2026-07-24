#!/usr/bin/env python3
"""PTPBox Kalman-family PHC servos.

LinuxPTP supplies the hardware-timestamped phase observations while running in
``free_running`` mode. The classic two-state filter remains available for
reproducibility; adaptive three-state and interacting-multiple-model modes also
estimate oscillator drift and measurement regime.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any


for candidate in (
    Path(os.environ.get("PTPBOX_RESEARCH_MODULE", "/opt/ptpbox-web/agent")),
    Path(__file__).resolve().parents[1] / "agent",
):
    if (candidate / "ptpbox_research.py").exists():
        sys.path.insert(0, str(candidate))
        break

from ptpbox_research import AdaptiveKalman3, InteractingMultipleModel  # noqa: E402


LOG_PATTERN = re.compile(
    r"\[(?P<seconds>\d+(?:\.\d+)?)\].*?"
    r"(?:master )?offset\s+(?P<offset>-?\d+(?:\.\d+)?)\s+"
    r"(?:s\d+\s+)?freq\s+[+-]?\d+(?:\.\d+)?\s+"
    r"path delay\s+(?P<delay>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
MAX_PATH_DELAY_NS = 1_000_000.0
ADJ_FREQUENCY = 0x0002
ADJ_SETOFFSET = 0x0100
ADJ_NANO = 0x2000
FD_TO_CLOCKID = 3


class Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class Timex(ctypes.Structure):
    _fields_ = [
        ("modes", ctypes.c_uint),
        ("offset", ctypes.c_long),
        ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long),
        ("esterror", ctypes.c_long),
        ("status", ctypes.c_int),
        ("constant", ctypes.c_long),
        ("precision", ctypes.c_long),
        ("tolerance", ctypes.c_long),
        ("time", Timeval),
        ("tick", ctypes.c_long),
        ("ppsfreq", ctypes.c_long),
        ("jitter", ctypes.c_long),
        ("shift", ctypes.c_int),
        ("stabil", ctypes.c_long),
        ("jitcnt", ctypes.c_long),
        ("calcnt", ctypes.c_long),
        ("errcnt", ctypes.c_long),
        ("stbcnt", ctypes.c_long),
        ("tai", ctypes.c_int),
        ("_padding", ctypes.c_int * 11),
    ]


class PhcAdjuster:
    """Minimal clock_adjtime wrapper using LinuxPTP's ppb convention."""

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
        # LinuxPTP's servo correction has the opposite sign to the kernel
        # frequency adjustment (see clock_synchronize_locked in clock.c).
        value.freq = round(-correction_ppb * 65.536)
        self._apply(value)

    def step_phase_ns(self, phase_ns: float) -> None:
        step = -round(phase_ns)
        seconds = math.trunc(step / 1_000_000_000)
        nanoseconds = step - seconds * 1_000_000_000
        if nanoseconds < 0:
            seconds -= 1
            nanoseconds += 1_000_000_000
        value = Timex()
        value.modes = ADJ_SETOFFSET | ADJ_NANO
        value.time.tv_sec = seconds
        value.time.tv_usec = nanoseconds
        self._apply(value)


class KalmanServo:
    """Constant-frequency phase estimator with explicit control input.

    State is ``[master_offset_ns, required_servo_correction_ppb]``.  Since one
    ppb accumulates one nanosecond per second, both the transition and control
    matrices stay numerically well scaled.
    """

    def __init__(
        self,
        measurement_noise_ns: float,
        process_noise_ppb: float,
        phase_time_constant_s: float,
        max_frequency_ppb: float,
        innovation_gate_sigma: float,
        initial_correction_ppb: float = 0.0,
    ) -> None:
        self.measurement_noise_ns = measurement_noise_ns
        self.process_noise_ppb = process_noise_ppb
        self.phase_time_constant_s = phase_time_constant_s
        self.max_frequency_ppb = max_frequency_ppb
        self.innovation_gate_sigma = innovation_gate_sigma
        self.phase_ns = 0.0
        self.frequency_ppb = initial_correction_ppb
        self.p00 = measurement_noise_ns**2 * 100.0
        self.p01 = 0.0
        # A servo transition preserves the PHC's last kernel correction, which
        # is a strong initial frequency prior.  Starting with an enormous rate
        # covariance makes ordinary timestamp noise look like oscillator
        # motion and creates an avoidable underdamped phase response.
        self.p11 = max(25.0**2, process_noise_ppb**2 * 4.0)
        self.last_sample_time: float | None = None
        self.last_correction_ppb = initial_correction_ppb
        self.sample_count = 0
        self.accepted_count = 0
        self.rejected_count = 0
        self.locked_since_source_time: float | None = None

    def update(self, measurement_ns: float, sample_time: float) -> dict[str, Any]:
        if self.last_sample_time is None:
            self.phase_ns = measurement_ns
            self.last_sample_time = sample_time
            dt = 0.0
        else:
            dt = sample_time - self.last_sample_time
            if not 0.0001 <= dt <= 30.0:
                self.last_sample_time = sample_time
                return self.snapshot(measurement_ns, 0.0, False, "invalid-interval", dt)

            # x(k|k-1) = F x(k-1|k-1) + B u(k-1).  The applied correction
            # reduces the rate at which the signed LinuxPTP offset accumulates.
            self.phase_ns += dt * (self.frequency_ppb - self.last_correction_ppb)

            q = self.process_noise_ppb**2
            q00 = q * dt**3 / 3.0
            q01 = q * dt**2 / 2.0
            q11 = q * dt
            prior_p00 = self.p00 + 2.0 * dt * self.p01 + dt * dt * self.p11 + q00
            prior_p01 = self.p01 + dt * self.p11 + q01
            prior_p11 = self.p11 + q11
            self.p00, self.p01, self.p11 = prior_p00, prior_p01, prior_p11
            self.last_sample_time = sample_time

        innovation = measurement_ns - self.phase_ns
        innovation_variance = self.p00 + self.measurement_noise_ns**2
        innovation_sigma = math.sqrt(max(innovation_variance, 1e-12))
        accepted = self.sample_count < 2 or abs(innovation) <= self.innovation_gate_sigma * innovation_sigma
        self.sample_count += 1
        if not accepted:
            self.rejected_count += 1
            return self.snapshot(measurement_ns, innovation, False, "innovation-gated", dt)

        gain_phase = self.p00 / innovation_variance
        gain_frequency = self.p01 / innovation_variance
        prior_p00, prior_p01, prior_p11 = self.p00, self.p01, self.p11
        self.phase_ns += gain_phase * innovation
        self.frequency_ppb += gain_frequency * innovation
        self.p00 = max(0.0, (1.0 - gain_phase) * prior_p00)
        self.p01 = (1.0 - gain_phase) * prior_p01
        self.p11 = max(0.0, prior_p11 - gain_frequency * prior_p01)
        self.accepted_count += 1
        if self.accepted_count >= 4 and self.locked_since_source_time is None:
            self.locked_since_source_time = sample_time

        correction = self.frequency_ppb + self.phase_ns / self.phase_time_constant_s
        self.last_correction_ppb = max(-self.max_frequency_ppb, min(self.max_frequency_ppb, correction))
        return self.snapshot(measurement_ns, innovation, True, "locked" if self.accepted_count >= 4 else "acquiring", dt)

    def snapshot(
        self,
        measurement_ns: float,
        innovation_ns: float,
        accepted: bool,
        state: str,
        dt: float,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "sample_count": self.sample_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "locked_since_source_time": self.locked_since_source_time,
            "measurement_accepted": accepted,
            "measurement_ns": measurement_ns,
            "phase_estimate_ns": self.phase_ns,
            "frequency_estimate_ppb": self.frequency_ppb,
            "correction_ppb": self.last_correction_ppb,
            "innovation_ns": innovation_ns,
            "phase_sigma_ns": math.sqrt(max(self.p00, 0.0)),
            "frequency_sigma_ppb": math.sqrt(max(self.p11, 0.0)),
            "sample_interval_s": dt,
        }


class AdaptiveKalmanServo:
    """Bounded PHC controller around a three-state or IMM estimator."""

    def __init__(
        self,
        mode: str,
        measurement_noise_ns: float,
        process_noise_ppb: float,
        drift_noise_ppb_s2: float,
        phase_time_constant_s: float,
        max_frequency_ppb: float,
        innovation_gate_sigma: float,
        initial_correction_ppb: float = 0.0,
    ) -> None:
        self.mode = mode
        self.filter = (
            InteractingMultipleModel(measurement_noise_ns)
            if mode == "imm"
            else AdaptiveKalman3(
                measurement_noise_ns,
                process_noise_ppb,
                drift_noise_ppb_s2,
                innovation_gate_sigma,
            )
        )
        self.phase_time_constant_s = phase_time_constant_s
        self.max_frequency_ppb = max_frequency_ppb
        self.last_correction_ppb = initial_correction_ppb
        self.sample_count = 0
        self.locked_since_source_time: float | None = None

    def update(self, measurement_ns: float, sample_time: float) -> dict[str, Any]:
        status = self.filter.update(measurement_ns, sample_time, self.last_correction_ppb)
        self.sample_count = int(status["sample_count"])
        if status["measurement_accepted"]:
            correction = (
                float(status["frequency_estimate_ppb"])
                + float(status["phase_estimate_ns"]) / self.phase_time_constant_s
                + 0.5 * float(status.get("drift_estimate_ppb_s", 0.0)) * self.phase_time_constant_s
            )
            self.last_correction_ppb = max(-self.max_frequency_ppb, min(self.max_frequency_ppb, correction))
        if status["state"] == "locked" and self.locked_since_source_time is None:
            self.locked_since_source_time = sample_time
        return {
            **status,
            "correction_ppb": self.last_correction_ppb,
            "locked_since_source_time": self.locked_since_source_time,
        }


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    path.chmod(0o644)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def identification_excitation(
    path: Path,
    node: str,
    offset_ns: float,
    now: float,
) -> tuple[float, dict[str, Any]]:
    state = load_json(path)
    if not state.get("enabled") or state.get("target") != node:
        return 0.0, state
    expires_at = state.get("expires_at")
    offset_limit = state.get("offset_limit_ns")
    if isinstance(expires_at, (int, float)) and now >= float(expires_at):
        state.update({"enabled": False, "stopped_at": now, "reason": "duration complete"})
        atomic_json(path, state)
        return 0.0, state
    if isinstance(offset_limit, (int, float)) and abs(offset_ns) > float(offset_limit):
        state.update(
            {
                "enabled": False,
                "aborted_at": now,
                "reason": f"raw master offset exceeded {float(offset_limit):.1f} ns",
                "peak_offset_ns": abs(offset_ns),
            }
        )
        atomic_json(path, state)
        return 0.0, state
    amplitude = state.get("amplitude_ppb")
    started_at = state.get("started_at")
    frequencies = state.get("frequencies_hz")
    if (
        not isinstance(amplitude, (int, float))
        or not isinstance(started_at, (int, float))
        or not isinstance(frequencies, list)
        or not frequencies
    ):
        return 0.0, state
    elapsed = max(0.0, now - float(started_at))
    components = [
        math.sin(2.0 * math.pi * float(frequency) * elapsed + index * 2.399963229728653)
        for index, frequency in enumerate(frequencies)
        if isinstance(frequency, (int, float)) and math.isfinite(float(frequency))
    ]
    if not components:
        return 0.0, state
    # The configured amplitude is a hard composite peak bound rather than the
    # amplitude of every tone, keeping the actuator excursion predictable.
    correction = float(amplitude) * sum(components) / len(components)
    return max(-float(amplitude), min(float(amplitude), correction)), state


def parse_log_sample(line: str) -> tuple[float, float, float] | None:
    match = LOG_PATTERN.search(line)
    if not match:
        return None
    delay = float(match.group("delay"))
    # A temporarily negative E2E delay estimate is diagnostic evidence during
    # reacquisition, not grounds to starve the phase servo. Keep rejecting
    # non-finite and physically implausible magnitudes.
    if not math.isfinite(delay) or abs(delay) > MAX_PATH_DELAY_NS:
        return None
    return float(match.group("offset")), float(match.group("seconds")), delay


def follow_samples(path: Path, stop: list[bool]):
    while not stop[0] and not path.exists():
        time.sleep(0.1)
    if stop[0]:
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        while not stop[0]:
            line = handle.readline()
            if not line:
                time.sleep(0.02)
                continue
            sample = parse_log_sample(line)
            if sample is not None:
                yield sample


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PTPBox Kalman PHC servo")
    parser.add_argument("--mode", choices=["kalman", "adaptive-kalman", "imm"], default="kalman")
    parser.add_argument("--node", required=True)
    parser.add_argument("--phc", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--measurement-noise-ns", type=float, default=200.0)
    parser.add_argument("--process-noise-ppb", type=float, default=10.0)
    parser.add_argument("--drift-noise-ppb-s2", type=float, default=0.05)
    parser.add_argument("--phase-time-constant-s", type=float, default=4.0)
    parser.add_argument("--max-frequency-ppb", type=float, default=200_000.0)
    parser.add_argument("--innovation-gate-sigma", type=float, default=6.0)
    parser.add_argument("--first-step-threshold-ns", type=float, default=20_000.0)
    parser.add_argument("--identification-state", type=Path, default=Path("/run/ptpbox/identification-state.json"))
    args = parser.parse_args()

    for name in (
        "measurement_noise_ns",
        "process_noise_ppb",
        "drift_noise_ppb_s2",
        "phase_time_constant_s",
        "max_frequency_ppb",
        "innovation_gate_sigma",
    ):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")

    stop = [False]

    def request_stop(_signal: int, _frame: Any) -> None:
        stop[0] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    adjuster = PhcAdjuster(args.phc)
    initial_servo_correction = -adjuster.kernel_frequency_ppb()
    servo = (
        KalmanServo(
            args.measurement_noise_ns,
            args.process_noise_ppb,
            args.phase_time_constant_s,
            args.max_frequency_ppb,
            args.innovation_gate_sigma,
            initial_servo_correction,
        )
        if args.mode == "kalman"
        else AdaptiveKalmanServo(
            args.mode,
            args.measurement_noise_ns,
            args.process_noise_ppb,
            args.drift_noise_ppb_s2,
            args.phase_time_constant_s,
            args.max_frequency_ppb,
            args.innovation_gate_sigma,
            initial_servo_correction,
        )
    )
    stepped = False
    try:
        for offset_ns, source_time, path_delay_ns in follow_samples(args.log, stop):
            if stop[0]:
                break
            if servo.sample_count == 0 and args.first_step_threshold_ns > 0 and abs(offset_ns) > args.first_step_threshold_ns:
                adjuster.step_phase_ns(offset_ns)
                stepped = True
                offset_ns = 0.0
            status = servo.update(offset_ns, source_time)
            base_correction_ppb = float(status["correction_ppb"])
            excitation_ppb, identification = identification_excitation(
                args.identification_state,
                args.node,
                offset_ns,
                time.time(),
            )
            requested_correction_ppb = max(
                -args.max_frequency_ppb,
                min(args.max_frequency_ppb, base_correction_ppb + excitation_ppb),
            )
            if status["measurement_accepted"]:
                adjuster.set_servo_frequency_ppb(requested_correction_ppb)
                # Keep the estimator's known control input equal to the actual
                # kernel correction, including the independent instrument.
                servo.last_correction_ppb = requested_correction_ppb
            applied_correction_ppb = float(servo.last_correction_ppb)
            applied_excitation_ppb = applied_correction_ppb - base_correction_ppb
            payload = {
                "node": args.node,
                "servo": args.mode,
                "phc": str(args.phc),
                "path_delay_ns": path_delay_ns,
                "source_time": source_time,
                "observed_at": time.time(),
                "stepped": stepped,
                "measurement_noise_ns": args.measurement_noise_ns,
                "process_noise_ppb": args.process_noise_ppb,
                "drift_noise_ppb_s2": args.drift_noise_ppb_s2,
                "phase_time_constant_s": args.phase_time_constant_s,
                "innovation_gate_sigma": args.innovation_gate_sigma,
                **status,
                "servo_correction_ppb": base_correction_ppb,
                "requested_excitation_ppb": excitation_ppb,
                "excitation_ppb": applied_excitation_ppb,
                "applied_correction_ppb": applied_correction_ppb,
                "correction_ppb": applied_correction_ppb,
                "identification_active": bool(
                    identification.get("enabled")
                    and identification.get("target") == args.node
                ),
            }
            atomic_json(args.state, payload)
            print(json.dumps(payload, separators=(",", ":")), flush=True)
    finally:
        adjuster.close()


if __name__ == "__main__":
    main()
