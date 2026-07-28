"""Temperature-compensated holdover control.

While a stage is locked, the frequency correction its servo applies is the
negation of that oscillator's own fractional frequency error. That makes the
locked window a labelled training set: it says what correction the oscillator
needed at a given die temperature and a given age. A holdover controller can fit
that relationship and keep applying the predicted correction after PTP is taken
away, instead of freezing the last value and letting the oscillator walk.

The reason this module is mostly guardrail is that the naive version of the idea
does not survive contact with the hardware. On the reference host the apparent
per-card temperature coefficient is confounded with ageing (temperature and
elapsed time correlate above 0.7), the sensors quantise to whole degrees, and the
passive temperature span is a couple of degrees. Fitting a coefficient on that
and applying it makes holdover worse, not better. So nothing here trusts a
coefficient because it was fitted. A model is armed only if it beats frozen
holdover when forecasting a stretch of the locked window it never saw, which is
the same extrapolation problem holdover actually poses.

Sign convention matches the servo worker and LinuxPTP: ``correction_ppb`` is the
servo correction, not the kernel frequency, so a prediction can be handed
straight to ``PhcAdjuster.set_servo_frequency_ppb``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

EPSILON = 1e-12

# A model has to forecast a stretch it never saw. One split is not enough: on the
# reference host the winner flips between drift, temperature, and nothing
# depending purely on where a single split lands, so the decision is taken over
# rolling origins and a candidate must win consistently, not once.
WALK_FORWARD_FOLDS = 5
# Fraction of folds a candidate must win outright before it may be armed.
MIN_FOLD_AGREEMENT = 0.8
MIN_TRAIN_SAMPLES = 40
MIN_HOLDOUT_SAMPLES = 12

# Whole-degree sensors dither between adjacent codes. Fitting that dither once
# produced a fake 100% improvement, so temperature is always smoothed, and by
# the same window during training and prediction.
SMOOTHING_SAMPLES = 5

# Temperature terms are only admissible if temperature actually moved.
MIN_TEMPERATURE_SPAN_C = 2.0
MIN_DISTINCT_LEVELS = 3

# A compensated model must beat frozen holdover by a real margin, not noise.
MIN_BENEFIT_PCT = 15.0

# Guards on what may reach the clock.
MAX_CORRECTION_PPB = 5_000.0
MAX_SLEW_PPB_PER_S = 5.0
# A linear ageing term is only credible near the window it was fitted on, and a
# linear temperature term must not be extrapolated far past observed readings.
MAX_DRIFT_HORIZON_S = 900.0
TEMPERATURE_EXTRAPOLATION_MARGIN_C = 2.0

KINDS = ("frozen", "drift", "temperature", "temperature-drift")


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 3:
        return None
    mean_left, mean_right = _mean(left), _mean(right)
    covariance = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    var_left = sum((a - mean_left) ** 2 for a in left)
    var_right = sum((b - mean_right) ** 2 for b in right)
    if var_left <= EPSILON or var_right <= EPSILON:
        return None
    return covariance / math.sqrt(var_left * var_right)


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting."""
    size = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-14:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / divisor
            if factor == 0.0:
                continue
            for index in range(column, size + 1):
                augmented[row][index] -= factor * augmented[column][index]
    result = [0.0] * size
    for row in reversed(range(size)):
        total = augmented[row][size] - sum(augmented[row][col] * result[col] for col in range(row + 1, size))
        result[row] = total / augmented[row][row]
    return result


def smooth(values: Sequence[float], window: int = SMOOTHING_SAMPLES) -> list[float]:
    """Trailing moving average, so smoothing never sees the future.

    A centred window would leak later samples into earlier predictions, which
    the live controller cannot do.
    """
    if window <= 1:
        return [float(value) for value in values]
    out: list[float] = []
    for index in range(len(values)):
        start = max(0, index - window + 1)
        chunk = values[start : index + 1]
        out.append(sum(chunk) / len(chunk))
    return out


@dataclass(frozen=True)
class Model:
    """A frequency-correction forecast, in the servo's sign convention."""

    kind: str
    intercept_ppb: float = 0.0
    tempco_ppb_per_c: float = 0.0
    drift_ppb_per_s: float = 0.0
    reference_temperature_c: float = 0.0
    reference_time: float = 0.0
    temperature_min_c: float = 0.0
    temperature_max_c: float = 0.0

    def predict_ppb(self, at_time: float, temperature_c: float | None) -> float:
        value = self.intercept_ppb
        if self.drift_ppb_per_s:
            elapsed = at_time - self.reference_time
            # Never extrapolate the ageing ramp past the horizon it was fitted for.
            elapsed = max(-MAX_DRIFT_HORIZON_S, min(MAX_DRIFT_HORIZON_S, elapsed))
            value += self.drift_ppb_per_s * elapsed
        if self.tempco_ppb_per_c and temperature_c is not None:
            low = self.temperature_min_c - TEMPERATURE_EXTRAPOLATION_MARGIN_C
            high = self.temperature_max_c + TEMPERATURE_EXTRAPOLATION_MARGIN_C
            clamped = max(low, min(high, temperature_c))
            value += self.tempco_ppb_per_c * (clamped - self.reference_temperature_c)
        return max(-MAX_CORRECTION_PPB, min(MAX_CORRECTION_PPB, value))

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "intercept_ppb": self.intercept_ppb,
            "tempco_ppb_per_c": self.tempco_ppb_per_c,
            "drift_ppb_per_s": self.drift_ppb_per_s,
            "reference_temperature_c": self.reference_temperature_c,
            "temperature_range_c": [self.temperature_min_c, self.temperature_max_c],
        }


def _fit(kind: str, times: Sequence[float], temperatures: Sequence[float],
         corrections: Sequence[float]) -> Model | None:
    """Least squares for one candidate, on centred regressors for conditioning."""
    reference_time = times[-1]
    reference_temperature = _mean(temperatures)
    low, high = min(temperatures), max(temperatures)

    if kind == "frozen":
        # The baseline every compensated model has to beat: hold the last
        # correction. Averaging the tail rather than taking a single sample keeps
        # one noisy final reading from defining the whole holdover.
        tail = corrections[-min(len(corrections), SMOOTHING_SAMPLES) :]
        return Model("frozen", intercept_ppb=_mean(tail),
                     reference_time=reference_time,
                     reference_temperature_c=reference_temperature,
                     temperature_min_c=low, temperature_max_c=high)

    columns: list[list[float]] = [[1.0] * len(times)]
    names: list[str] = ["intercept"]
    if "temperature" in kind:
        columns.append([t - reference_temperature for t in temperatures])
        names.append("tempco")
    if "drift" in kind:
        columns.append([t - reference_time for t in times])
        names.append("drift")

    size = len(columns)
    matrix = [[sum(columns[r][i] * columns[c][i] for i in range(len(times)))
               for c in range(size)] for r in range(size)]
    vector = [sum(columns[r][i] * corrections[i] for i in range(len(times))) for r in range(size)]
    solution = _solve(matrix, vector)
    if solution is None:
        return None
    named = dict(zip(names, solution))
    return Model(
        kind,
        intercept_ppb=named.get("intercept", 0.0),
        tempco_ppb_per_c=named.get("tempco", 0.0),
        drift_ppb_per_s=named.get("drift", 0.0),
        reference_temperature_c=reference_temperature,
        reference_time=reference_time,
        temperature_min_c=low,
        temperature_max_c=high,
    )


def _forecast_rms_ppb(model: Model, times: Sequence[float],
                      temperatures: Sequence[float], corrections: Sequence[float]) -> float:
    total = 0.0
    for index in range(len(times)):
        error = model.predict_ppb(times[index], temperatures[index]) - corrections[index]
        total += error * error
    return math.sqrt(total / len(times)) if times else float("inf")


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def rolling_origins(count: int, folds: int = WALK_FORWARD_FOLDS) -> list[tuple[slice, slice]]:
    """Expanding-window splits: train on the past, forecast the next block.

    Each fold mimics one release: fit on everything up to a moment, then predict
    forward. Using several origins is what stops the verdict from depending on
    where a single arbitrary split happened to fall.
    """
    usable = count - MIN_TRAIN_SAMPLES
    if usable < MIN_HOLDOUT_SAMPLES:
        return []
    block = max(MIN_HOLDOUT_SAMPLES, usable // max(1, folds))
    out: list[tuple[slice, slice]] = []
    start = MIN_TRAIN_SAMPLES
    while start + block <= count and len(out) < folds:
        out.append((slice(0, start), slice(start, start + block)))
        start += block
    return out


@dataclass
class Evaluation:
    """What the selector decided, and why."""

    status: str
    armed_kind: str | None = None
    model: Model | None = None
    reason: str = ""
    frozen_rms_ppb: float | None = None
    best_rms_ppb: float | None = None
    benefit_pct: float | None = None
    folds: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "armed_kind": self.armed_kind,
            "model": self.model.as_dict() if self.model else None,
            "reason": self.reason,
            "frozen_rms_ppb": self.frozen_rms_ppb,
            "best_rms_ppb": self.best_rms_ppb,
            "benefit_pct": self.benefit_pct,
            "folds": self.folds,
            "candidates": self.candidates,
            "diagnostics": self.diagnostics,
            "interpretation": (
                f"Candidates forecast {self.folds} rolling origins of the locked "
                "window, each one standing in for a release. A model is armed only "
                f"if its median gain over frozen holdover reaches "
                f"{MIN_BENEFIT_PCT:.0f}% and it wins at least "
                f"{MIN_FOLD_AGREEMENT * 100:.0f}% of folds. A single split is not "
                "enough: the winner changes with where the split falls."
            ),
        }


def evaluate(
    samples: Iterable[tuple[float, float | None, float]],
    folds: int = WALK_FORWARD_FOLDS,
    min_benefit_pct: float = MIN_BENEFIT_PCT,
    min_fold_agreement: float = MIN_FOLD_AGREEMENT,
) -> Evaluation:
    """Choose a holdover model from a locked window.

    ``samples`` are ``(time, temperature_c, correction_ppb)`` taken while the
    stage was locked. Samples without a temperature are dropped, since every
    candidate needs a common training set to be comparable.
    """
    rows = sorted(
        (float(t), float(temp), float(corr))
        for t, temp, corr in samples
        if temp is not None and all(math.isfinite(float(v)) for v in (t, temp, corr))
    )
    if len(rows) < MIN_TRAIN_SAMPLES + MIN_HOLDOUT_SAMPLES:
        return Evaluation("learning", reason=(
            f"need {MIN_TRAIN_SAMPLES + MIN_HOLDOUT_SAMPLES} locked samples with "
            f"temperature, have {len(rows)}"))

    times = [row[0] for row in rows]
    # Smoothing spans the splits deliberately: the live controller also carries a
    # warm filter across release, so scoring must not credit a cold start.
    temperatures = smooth([row[1] for row in rows])
    corrections = [row[2] for row in rows]

    splits = rolling_origins(len(rows), folds)
    if not splits:
        return Evaluation("learning", reason="record is too short to form a rolling origin")

    span = max(temperatures) - min(temperatures)
    levels = len({round(value, 3) for value in temperatures})
    temperature_admissible = span >= MIN_TEMPERATURE_SPAN_C and levels >= MIN_DISTINCT_LEVELS
    diagnostics = {
        "samples": len(rows),
        "folds": len(splits),
        "record_span_s": times[-1] - times[0],
        "temperature_span_c": span,
        "distinct_levels": levels,
        "temperature_time_correlation": _pearson(times, temperatures),
        "temperature_admissible": temperature_admissible,
    }

    # Score every candidate on every origin.
    per_kind: dict[str, list[float]] = {}
    fitted: dict[str, Model] = {}
    for kind in KINDS:
        if "temperature" in kind and not temperature_admissible:
            continue
        scores: list[float] = []
        for train, test in splits:
            model = _fit(kind, times[train], temperatures[train], corrections[train])
            if model is None:
                scores = []
                break
            rms = _forecast_rms_ppb(model, times[test], temperatures[test], corrections[test])
            if not math.isfinite(rms):
                scores = []
                break
            scores.append(rms)
            # Keep the last fold's fit: it is trained on the most data and is
            # therefore the one that would actually be armed.
            fitted[kind] = model
        if scores:
            per_kind[kind] = scores

    if "frozen" not in per_kind:
        return Evaluation("unavailable", reason="frozen baseline could not be fitted",
                          diagnostics=diagnostics, folds=len(splits))

    frozen_scores = per_kind["frozen"]
    frozen_median = _median(frozen_scores)

    candidates: list[dict[str, Any]] = []
    for kind, scores in per_kind.items():
        gains = [
            (f - c) / f * 100.0 if f > EPSILON else 0.0
            for f, c in zip(frozen_scores, scores)
        ]
        wins = sum(1 for gain in gains if gain >= min_benefit_pct)
        candidates.append({
            "kind": kind,
            "holdout_rms_ppb": _median(scores),
            "benefit_pct": _median(gains) if kind != "frozen" else 0.0,
            "worst_fold_benefit_pct": min(gains) if gains else None,
            "folds_won": wins,
            "fold_agreement": wins / len(scores) if scores else 0.0,
            "per_fold_rms_ppb": scores,
            **(fitted[kind].as_dict() if kind in fitted else {}),
        })
    candidates.sort(key=lambda item: item["holdout_rms_ppb"])

    # A candidate must be consistently better, not better on average.
    eligible = [
        item for item in candidates
        if item["kind"] != "frozen"
        and item["benefit_pct"] >= min_benefit_pct
        and item["fold_agreement"] >= min_fold_agreement
    ]
    if not eligible:
        best = min((item for item in candidates if item["kind"] != "frozen"),
                   key=lambda item: -item["benefit_pct"], default=None)
        detail = (
            f"best {best['kind']} gained a median {best['benefit_pct']:+.1f}% and won "
            f"{best['folds_won']}/{len(frozen_scores)} folds"
            if best else "only the frozen baseline could be fitted"
        )
        reason = (
            f"no candidate beat frozen holdover by {min_benefit_pct:.0f}% across "
            f"{min_fold_agreement * 100:.0f}% of {len(splits)} rolling origins; {detail}"
        )
        if not temperature_admissible:
            reason += (
                f"; temperature terms inadmissible, span {span:.1f} degC over "
                f"{levels} distinct levels"
            )
        return Evaluation("refused", armed_kind=None, model=fitted.get("frozen"),
                          reason=reason, frozen_rms_ppb=frozen_median,
                          best_rms_ppb=candidates[0]["holdout_rms_ppb"],
                          benefit_pct=best["benefit_pct"] if best else None,
                          folds=len(splits), candidates=candidates,
                          diagnostics=diagnostics)

    winner = eligible[0]
    return Evaluation(
        "ready", armed_kind=winner["kind"], model=fitted[winner["kind"]],
        reason=(
            f"{winner['kind']} gained a median {winner['benefit_pct']:.1f}% over frozen "
            f"holdover and won {winner['folds_won']}/{len(splits)} rolling origins "
            f"(worst {winner['worst_fold_benefit_pct']:+.1f}%)"
        ),
        frozen_rms_ppb=frozen_median, best_rms_ppb=winner["holdout_rms_ppb"],
        benefit_pct=winner["benefit_pct"], folds=len(splits),
        candidates=candidates, diagnostics=diagnostics,
    )


class Compensator:
    """Applies a selected model during holdover, with bounded output.

    Holdover is time-driven, not sample-driven: once PTP is gone there are no
    offsets to react to, so the controller ticks on a timer, reads temperature,
    and emits a forecast. Output is slew-limited so a sensor glitch cannot step
    the frequency.
    """

    def __init__(self, model: Model, released_at: float, initial_ppb: float,
                 max_slew_ppb_per_s: float = MAX_SLEW_PPB_PER_S) -> None:
        self.model = model
        self.released_at = released_at
        self.max_slew_ppb_per_s = max_slew_ppb_per_s
        self.applied_ppb = initial_ppb
        self.last_tick: float | None = None
        self._temperatures: list[float] = []

    def observe_temperature(self, temperature_c: float | None) -> float | None:
        """Feed the same trailing filter the model was trained through."""
        if temperature_c is None or not math.isfinite(temperature_c):
            return self._temperatures[-1] if self._temperatures else None
        self._temperatures.append(float(temperature_c))
        del self._temperatures[:-SMOOTHING_SAMPLES]
        return sum(self._temperatures) / len(self._temperatures)

    def tick(self, now: float, temperature_c: float | None) -> dict[str, Any]:
        smoothed = self.observe_temperature(temperature_c)
        target = self.model.predict_ppb(now, smoothed)
        if self.last_tick is None:
            # Release must be seamless: whatever the servo last applied is what
            # the clock keeps for this tick. If the model disagrees with the
            # servo, converging to it is what the slew limit is for; stepping
            # there immediately would jolt the oscillator at the one instant the
            # operator is trying to measure.
            allowed = 0.0
        else:
            allowed = self.max_slew_ppb_per_s * max(0.0, now - self.last_tick)
        delta = target - self.applied_ppb
        clipped = abs(delta) > allowed
        if clipped:
            delta = math.copysign(allowed, delta)
        self.applied_ppb = max(-MAX_CORRECTION_PPB, min(MAX_CORRECTION_PPB, self.applied_ppb + delta))
        self.last_tick = now
        return {
            "elapsed_s": now - self.released_at,
            "temperature_c": temperature_c,
            "smoothed_temperature_c": smoothed,
            "target_ppb": target,
            "applied_ppb": self.applied_ppb,
            "slew_limited": clipped,
            "converged": abs(target - self.applied_ppb) <= EPSILON,
            "kind": self.model.kind,
        }
