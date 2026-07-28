#!/usr/bin/env python3
"""Score the thermal feedforward against the temperature-blind filter on real data.

This is an estimator comparison, not a closed-loop servo comparison, and the
distinction matters. A recorded offset series was produced under the servo that
was actually running; replaying a different controller open-loop would not
reproduce the offsets its own corrections would have caused. What can be measured
honestly from a recording is how well each estimator predicts phase it has not
seen yet, which is exactly the quantity the thermal term is meant to improve.

Sparse Sync is emulated by decimating the real record. Feeding every k-th sample
gives a genuine (k / rate) update interval over real offsets and real
temperatures, so the sparse regime can be tested without disturbing a live
cascade. The forecast horizon is the update interval itself, which is what a servo
must bridge between packets.

Usage:
  replay_thermal_servo.py [thermal.json]     # default: fetch from the appliance
"""

from __future__ import annotations

import importlib.util
import json
import math
import statistics
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from ptpbox_research import AdaptiveKalman3  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "agent" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TS = _load("ptpbox_thermal_servo")


def forced_model(node):
    """Build a model with the evidence gate deliberately bypassed.

    The live coefficients are all ``candidate``, so a gated model would be inert
    and measure nothing. Forcing it is the only way to characterise what the
    feedforward would do if armed, which is the point of this harness: the answer
    is what justifies keeping the gate shut.
    """
    model = TS.model_from_analysis(node)
    if model is None:
        return None
    return TS.ThermalDriftModel(model.tempco_ppb_per_c, model.tempco_sigma_ppb_per_c,
                                evidence_supported=True)

DECIMATIONS = (1, 4, 9, 16, 32)
MIN_FORECASTS = 25


def native_interval(rows) -> float:
    """Cadence of the record, measured rather than assumed.

    The PHC collector samples at its own rate, which is not the Sync rate, so
    hardcoding one mislabels every interval in the table.
    """
    gaps = [b[0] - a[0] for a, b in zip(rows, rows[1:]) if b[0] > a[0]]
    return statistics.median(gaps) if gaps else float("nan")


def reference_drift(rows, half_window_s=60.0):
    """Offline drift reference from the full-rate frequency series.

    A centred local slope is used deliberately: it sees future samples, which no
    online estimator may, so it is a fair yardstick for how close each online
    drift estimate gets.
    """
    times = [row[0] for row in rows]
    values = [row[1] for row in rows]
    out: dict[float, float] = {}
    for index, centre in enumerate(times):
        lo, hi = centre - half_window_s, centre + half_window_s
        window = [(t, v) for t, v in zip(times, values) if lo <= t <= hi]
        if len(window) < 10:
            continue
        mean_t = sum(t for t, _ in window) / len(window)
        sxx = sum((t - mean_t) ** 2 for t, _ in window)
        if sxx <= 0:
            continue
        mean_v = sum(v for _, v in window) / len(window)
        sxy = sum((t - mean_t) * (v - mean_v) for t, v in window)
        out[centre] = sxy / sxx
    return out


def replay(rows, model, decimation, use_thermal, drift_reference=None):
    """One-step-ahead phase forecast error over a decimated record.

    ``rows`` are (time, offset_ns, temperature_c) at the native cadence.
    """
    kalman = AdaptiveKalman3()
    feedforward = TS.ThermalFeedforward(model) if use_thermal else None
    errors: list[float] = []
    weights: list[float] = []
    drift_errors: list[float] = []
    previous = None

    # Temperature is polled by the collector on its own cadence and does not
    # thin out when the Sync rate does. Feeding it only at decimated instants
    # would hand the thermal term a handicap no real deployment has, and it
    # inverts the result: the slope estimate then degrades with the packet rate
    # instead of staying sharp while the filter's drift estimate loosens.
    thermal_cursor = 0
    for row in rows[::decimation]:
        timestamp, offset, temperature = row
        if feedforward is not None:
            while thermal_cursor < len(rows) and rows[thermal_cursor][0] <= timestamp:
                feedforward.observe(rows[thermal_cursor][0], rows[thermal_cursor][2])
                thermal_cursor += 1
        if previous is not None:
            horizon = timestamp - previous["time"]
            if 0.0 < horizon <= 600.0:
                # Predict forward from the last accepted estimate using phase,
                # frequency, and whichever drift the variant believes.
                drift = previous["drift"]
                predicted = (
                    previous["phase"]
                    + previous["frequency"] * horizon
                    + 0.5 * drift * horizon * horizon
                )
                errors.append(predicted - offset)
        status = kalman.update(offset, timestamp)
        if not status["measurement_accepted"]:
            continue
        drift = float(status["drift_estimate_ppb_s"])
        if feedforward is not None:
            fused = TS.fuse_drift(drift, float(status["drift_sigma_ppb_s"]),
                                  feedforward.drift_prediction())
            drift = fused["drift_ppb_s"]
            weights.append(fused["thermal_weight"])
        if drift_reference is not None and timestamp in drift_reference:
            drift_errors.append(drift - drift_reference[timestamp])
        previous = {
            "time": timestamp,
            "phase": float(status["phase_estimate_ns"]),
            "frequency": float(status["frequency_estimate_ppb"]),
            "drift": drift,
        }

    rms = math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else float("nan")
    drift_rms = (
        math.sqrt(sum(e * e for e in drift_errors) / len(drift_errors))
        if drift_errors else float("nan")
    )
    return rms, (statistics.fmean(weights) if weights else 0.0), len(errors), drift_rms


def main() -> None:
    if len(sys.argv) > 1:
        thermal = json.loads(Path(sys.argv[1]).read_text())
    else:
        url = "http://192.168.1.60:8090/api/thermal?history_seconds=1800"
        with urllib.request.urlopen(url, timeout=60) as response:
            thermal = json.load(response)

    print("Thermal feedforward vs the temperature-blind three-state filter, on real data.")
    print("Sparse Sync emulated by decimating the record. Negative change = thermal helped.")
    print("The evidence gate is FORCED OPEN here; in production these coefficients are")
    print("all 'candidate' and the feedforward stays inert.\n")
    header = (f"{'clock':6} {'interval':>9} {'forecast ns':>12} {'change':>8} "
              f"{'drift err ppb/s':>16} {'change':>8} {'weight':>8} {'n':>5}")
    print(header)
    print("-" * len(header))

    forecast_summary: dict[float, list[float]] = {}
    drift_summary: dict[float, list[float]] = {}
    for name in sorted(thermal.get("nodes") or {}):
        node = thermal["nodes"][name]
        rows = sorted(
            (float(p["elapsed_s"]), float(p["frequency_ppb"]), float(p["temperature_c"]))
            for p in (node.get("scatter") or [])
        )
        if len(rows) < 120:
            continue
        model = forced_model(node)
        if model is None:
            print(f"{name:6} no temperature coefficient available")
            continue
        base = native_interval(rows)
        reference = reference_drift(rows)
        for decimation in DECIMATIONS:
            if len(rows[::decimation]) < MIN_FORECASTS + 5:
                continue
            interval = decimation * base
            blind, _, count, blind_drift = replay(rows, model, decimation, False, reference)
            fused, weight, _, fused_drift = replay(rows, model, decimation, True, reference)
            if count < MIN_FORECASTS:
                continue
            change = (fused - blind) / blind * 100.0 if blind else float("nan")
            drift_change = (
                (fused_drift - blind_drift) / blind_drift * 100.0
                if blind_drift and math.isfinite(blind_drift) else float("nan")
            )
            forecast_summary.setdefault(interval, []).append(change)
            drift_summary.setdefault(interval, []).append(drift_change)
            print(f"{name:6} {interval:>8.0f}s {blind:>12.1f} {change:>+7.2f}% "
                  f"{blind_drift:>16.4f} {drift_change:>+7.2f}% {weight:>8.4f} {count:>5}")
        print()

    print("Median change by update interval:")
    print(f"  {'interval':>9} {'forecast':>10} {'drift estimate':>16} {'clocks':>8}")
    for interval in sorted(forecast_summary):
        f = statistics.median(forecast_summary[interval])
        d = statistics.median([x for x in drift_summary[interval] if math.isfinite(x)] or [float("nan")])
        print(f"  {interval:>8.0f}s {f:>+9.2f}% {d:>+15.2f}% {len(forecast_summary[interval]):>8}")


if __name__ == "__main__":
    main()
