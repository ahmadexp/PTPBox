"""Thermal feedforward for the three-state and IMM servos.

The three-state filter already carries drift as a state and estimates it directly
from phase at the Sync cadence, so at 4 Hz a temperature term is redundant: the
phase change temperature predicts over one Sync interval is three to four orders
of magnitude below the offset noise the loop already rejects. Adding a fixed
feedforward gain there would be pure risk.

What changes the picture is sparsity. The drift state is only well observed when
samples are dense; as the Sync interval grows its covariance grows with it, and
the servo has to extrapolate further on a worse estimate. Temperature is an
independent observation of the same quantity that does not degrade with the packet
rate.

So the two are fused by inverse variance rather than blended by a tuned gain:

    d_fused = (d_kf/s_kf^2 + d_th/s_th^2) / (1/s_kf^2 + 1/s_th^2)

This has the property the measurements demand. When the filter's drift estimate is
sharp, its weight dominates and the thermal term contributes essentially nothing,
so enabling this at 4 Hz cannot degrade the servo. When the estimate is loose, the
thermal term earns weight in proportion to how much better it is. Nothing here
needs a tuning constant.

The temperature coefficient itself is not trusted on sight. The caller supplies
one only after it has passed the evidence gates, and supplies its standard error
with it, because a coefficient with a large error simply receives little weight,
which is the correct response rather than a refusal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

EPSILON = 1e-12

# Whole-degree sensors: the standard deviation of a uniform quantiser.
QUANTISATION_STEP_C = 1.0
QUANTISATION_SIGMA_C = QUANTISATION_STEP_C / math.sqrt(12.0)

# Match the holdover controller so a coefficient learnt in one place behaves the
# same in the other.
SMOOTHING_SAMPLES = 5

# dT/dt needs a baseline long enough to see past the quantiser.
MIN_SLOPE_SAMPLES = 8
MIN_SLOPE_SPAN_S = 20.0

# A thermal drift prediction is never allowed to dominate on its own.
MAX_THERMAL_WEIGHT = 0.9
MAX_DRIFT_PPB_PER_S = 10.0


@dataclass(frozen=True)
class ThermalDriftModel:
    """A temperature coefficient, its uncertainty, and whether evidence backs it.

    ``evidence_supported`` is not a formality. Replaying real records shows the
    fused drift estimate improving by about 5%, yet phase prediction degrading by
    18% at a 32 s update interval and 161% at 64 s, because the controller
    extrapolates drift as ½·d·T² and that square amplifies any bias in the
    coefficient. An unsupported coefficient is therefore most damaging in exactly
    the sparse regime this feedforward exists to serve, so the same gate that
    refuses it for holdover has to refuse it here.
    """

    tempco_ppb_per_c: float
    tempco_sigma_ppb_per_c: float
    evidence_supported: bool = False

    def usable(self) -> bool:
        return (
            self.evidence_supported
            and math.isfinite(self.tempco_ppb_per_c)
            and math.isfinite(self.tempco_sigma_ppb_per_c)
            and self.tempco_sigma_ppb_per_c > 0.0
            # A coefficient whose error bar covers zero carries no information
            # about the sign of the drift, so it must not steer.
            and abs(self.tempco_ppb_per_c) > self.tempco_sigma_ppb_per_c
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "tempco_ppb_per_c": self.tempco_ppb_per_c,
            "tempco_sigma_ppb_per_c": self.tempco_sigma_ppb_per_c,
            "evidence_supported": self.evidence_supported,
            "usable": self.usable(),
        }


def _slope_with_sigma(times: Sequence[float], values: Sequence[float]) -> tuple[float, float] | None:
    """Least-squares slope of temperature against time, and its standard error.

    The error comes from the quantiser rather than the residuals: with whole-degree
    readings over a couple of degrees the residual estimate is meaningless, while
    the quantiser noise is known exactly.
    """
    count = len(times)
    if count < MIN_SLOPE_SAMPLES:
        return None
    span = times[-1] - times[0]
    if span < MIN_SLOPE_SPAN_S:
        return None
    mean_t = sum(times) / count
    sxx = sum((t - mean_t) ** 2 for t in times)
    if sxx <= EPSILON:
        return None
    mean_v = sum(values) / count
    sxy = sum((times[i] - mean_t) * (values[i] - mean_v) for i in range(count))
    slope = sxy / sxx
    sigma = QUANTISATION_SIGMA_C / math.sqrt(sxx)
    return slope, sigma


class ThermalFeedforward:
    """Turns die temperature into an independent drift observation."""

    def __init__(self, model: ThermalDriftModel, window_s: float = 120.0) -> None:
        self.model = model
        self.window_s = window_s
        self._times: list[float] = []
        self._raw: list[float] = []
        self._smoothed: list[float] = []

    def observe(self, timestamp: float, temperature_c: float | None) -> None:
        if temperature_c is None or not math.isfinite(temperature_c):
            return
        self._times.append(float(timestamp))
        self._raw.append(float(temperature_c))
        tail = self._raw[-SMOOTHING_SAMPLES:]
        self._smoothed.append(sum(tail) / len(tail))
        # Keep only the trailing window, so a coefficient tracks recent thermal
        # motion instead of an average over the whole run.
        cutoff = timestamp - self.window_s
        while len(self._times) > MIN_SLOPE_SAMPLES and self._times[0] < cutoff:
            del self._times[0], self._raw[0], self._smoothed[0]

    def temperature_slope(self) -> tuple[float, float] | None:
        return _slope_with_sigma(self._times, self._smoothed)

    def drift_prediction(self) -> tuple[float, float] | None:
        """Predicted frequency drift in ppb/s, with its standard deviation.

        Returns ``None`` when either the coefficient or the slope is unusable, so
        the caller falls back to the filter's own estimate untouched.
        """
        if not self.model.usable():
            return None
        slope = self.temperature_slope()
        if slope is None:
            return None
        rate, rate_sigma = slope
        drift = self.model.tempco_ppb_per_c * rate
        if not math.isfinite(drift) or abs(drift) > MAX_DRIFT_PPB_PER_S:
            return None
        # Variance of a product of two independent uncertain quantities.
        variance = (
            (rate * self.model.tempco_sigma_ppb_per_c) ** 2
            + (self.model.tempco_ppb_per_c * rate_sigma) ** 2
        )
        return drift, math.sqrt(max(variance, EPSILON))


def fuse_drift(
    filter_drift_ppb_s: float,
    filter_sigma_ppb_s: float,
    thermal: tuple[float, float] | None,
    max_thermal_weight: float = MAX_THERMAL_WEIGHT,
) -> dict[str, Any]:
    """Inverse-variance fusion of the filter's drift and the thermal prediction.

    The weight is the whole point: it is derived, not tuned, so a dense sample
    stream drives the thermal contribution to zero on its own.
    """
    if thermal is None:
        return {"drift_ppb_s": filter_drift_ppb_s, "thermal_weight": 0.0,
                "thermal_drift_ppb_s": None, "reason": "no usable thermal prediction"}
    thermal_drift, thermal_sigma = thermal
    if not math.isfinite(filter_sigma_ppb_s) or filter_sigma_ppb_s <= 0.0:
        return {"drift_ppb_s": filter_drift_ppb_s, "thermal_weight": 0.0,
                "thermal_drift_ppb_s": thermal_drift,
                "reason": "filter drift variance is unusable"}

    filter_information = 1.0 / (filter_sigma_ppb_s ** 2)
    thermal_information = 1.0 / (thermal_sigma ** 2)
    weight = thermal_information / (filter_information + thermal_information)
    # Never let one sensor take the loop, however confident its error bar claims.
    weight = min(weight, max_thermal_weight)
    fused = (1.0 - weight) * filter_drift_ppb_s + weight * thermal_drift
    return {
        "drift_ppb_s": fused,
        "thermal_weight": weight,
        "thermal_drift_ppb_s": thermal_drift,
        "thermal_sigma_ppb_s": thermal_sigma,
        "filter_sigma_ppb_s": filter_sigma_ppb_s,
        "reason": "inverse-variance fusion",
    }


def model_from_analysis(node_analysis: dict[str, Any]) -> ThermalDriftModel | None:
    """Build a drift model from ptpbox_thermal.analyse_node output.

    The standard error is inflated by the measured autocorrelation variance
    inflation. Without that the error bar is optimistic by roughly the square root
    of the inflation, which would hand the thermal term far more weight than the
    evidence supports.
    """
    ols = node_analysis.get("ols") or {}
    tempco = ols.get("tempco_ppb_per_c")
    sigma = ols.get("standard_error_ppb_per_c")
    if tempco is None or sigma is None:
        return None
    # The analysis reports effective samples rather than the inflation directly,
    # so recover it as n/n_eff. On this host that is a factor of ten or more, and
    # ignoring it would hand the thermal term several times the weight it earned.
    serial = node_analysis.get("serial_correlation") or {}
    effective = serial.get("effective_samples")
    count = node_analysis.get("samples")
    sigma = float(sigma)
    if (isinstance(effective, (int, float)) and isinstance(count, (int, float))
            and effective > 0 and count > effective):
        sigma *= math.sqrt(float(count) / float(effective))
    verdict = (node_analysis.get("evidence") or {}).get("verdict")
    return ThermalDriftModel(float(tempco), sigma, evidence_supported=(verdict == "supported"))
