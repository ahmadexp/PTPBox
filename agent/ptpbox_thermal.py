#!/usr/bin/env python3
"""Oscillator frequency correction versus observed temperature.

The servo's applied frequency correction is the negation of the oscillator's
fractional-frequency error, so correction plotted against die temperature is a
measurement of the temperature coefficient. This module estimates that
coefficient and, just as importantly, decides whether the record can support the
claim at all.

Three properties of the available data drive the design:

* the hardware monitor quantises temperature to 1 degree, so the regressor
  carries roughly 0.29 degrees of quantisation noise and ordinary least squares
  is attenuated toward zero (errors-in-variables). A Deming estimate that
  accounts for noise in both variables is therefore reported beside it;
* temperature and elapsed time are strongly collinear in passive operation, so a
  fit against temperature alone silently absorbs oscillator ageing and servo
  drift. A joint fit against temperature and time separates them;
* consecutive samples are serially correlated, so the naive sample count vastly
  overstates significance. Standard errors use an effective sample size derived
  from the residual autocorrelation.

Nothing here is a controlled thermal experiment: a defensible coefficient needs
deliberate forcing over a wide range. The gates say so rather than implying a
tempco was measured.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

EPSILON = 1e-12

# A hardware monitor reporting whole degrees has uniform quantisation error over
# one degree, whose standard deviation is 1/sqrt(12).
QUANTISATION_STEP_C = 1.0
QUANTISATION_SIGMA_C = QUANTISATION_STEP_C / math.sqrt(12.0)

# Evidence thresholds. These are deliberately strict: a two degree span with a
# one degree sensor cannot support a coefficient anyone should act on.
MIN_SAMPLES = 60
MIN_SPAN_C = 5.0
MIN_DISTINCT_LEVELS = 4
MAX_TIME_COLLINEARITY = 0.9
MIN_EFFECTIVE_SAMPLES = 12.0


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean, right_mean = _mean(left), _mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_ss = sum((a - left_mean) ** 2 for a in left)
    right_ss = sum((b - right_mean) ** 2 for b in right)
    if left_ss <= EPSILON or right_ss <= EPSILON:
        return None
    return numerator / math.sqrt(left_ss * right_ss)


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


def _polynomial_fit(x: Sequence[float], y: Sequence[float], degree: int) -> dict[str, Any] | None:
    """Least-squares polynomial fit on centred x for conditioning."""
    if len(x) <= degree + 1:
        return None
    centre = _mean(x)
    shifted = [value - centre for value in x]
    size = degree + 1
    matrix = [[sum(value ** (row + col) for value in shifted) for col in range(size)] for row in range(size)]
    vector = [sum(y[index] * shifted[index] ** row for index in range(len(y))) for row in range(size)]
    coefficients = _solve(matrix, vector)
    if coefficients is None:
        return None
    predicted = [
        sum(coefficients[power] * shifted[index] ** power for power in range(size))
        for index in range(len(y))
    ]
    residuals = [y[index] - predicted[index] for index in range(len(y))]
    rss = sum(value * value for value in residuals)
    mean_y = _mean(y)
    tss = sum((value - mean_y) ** 2 for value in y)
    count = len(y)
    # Gaussian log-likelihood at the maximum, used only for model comparison.
    sigma2 = max(rss / count, EPSILON)
    log_likelihood = -0.5 * count * (math.log(2 * math.pi * sigma2) + 1.0)
    parameters = size + 1
    return {
        "degree": degree,
        "coefficients": coefficients,
        "centre_c": centre,
        "r_squared": None if tss <= EPSILON else 1.0 - rss / tss,
        "residual_sigma": math.sqrt(rss / max(1, count - size)),
        "rss": rss,
        "aic": 2 * parameters - 2 * log_likelihood,
        "bic": parameters * math.log(count) - 2 * log_likelihood,
        "residuals": residuals,
    }


def _lag_one_autocorrelation(values: Sequence[float]) -> float | None:
    if len(values) < 8:
        return None
    mean = _mean(values)
    numerator = sum((values[i] - mean) * (values[i + 1] - mean) for i in range(len(values) - 1))
    denominator = sum((value - mean) ** 2 for value in values)
    return None if denominator <= EPSILON else numerator / denominator


def _theil_sen(x: Sequence[float], y: Sequence[float], budget: int = 20000) -> float | None:
    """Median of pairwise slopes; resistant to relock outliers."""
    count = len(x)
    if count < 3:
        return None
    slopes: list[float] = []
    stride = 1 if count * (count - 1) // 2 <= budget else max(1, int(math.sqrt(count * (count - 1) / (2 * budget))) + 1)
    for i in range(0, count - 1, stride):
        for j in range(i + 1, count, stride):
            dx = x[j] - x[i]
            if abs(dx) > EPSILON:
                slopes.append((y[j] - y[i]) / dx)
    if not slopes:
        return None
    slopes.sort()
    middle = len(slopes) // 2
    return slopes[middle] if len(slopes) % 2 else 0.5 * (slopes[middle - 1] + slopes[middle])


def _deming(x: Sequence[float], y: Sequence[float], lambda_ratio: float) -> float | None:
    """Errors-in-variables slope for known error-variance ratio var(y)/var(x)."""
    if len(x) < 3:
        return None
    x_mean, y_mean = _mean(x), _mean(y)
    sxx = sum((value - x_mean) ** 2 for value in x) / (len(x) - 1)
    syy = sum((value - y_mean) ** 2 for value in y) / (len(y) - 1)
    sxy = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(len(x))) / (len(x) - 1)
    if abs(sxy) <= EPSILON:
        return None
    difference = syy - lambda_ratio * sxx
    return (difference + math.sqrt(difference * difference + 4.0 * lambda_ratio * sxy * sxy)) / (2.0 * sxy)


def _joint_temperature_time_fit(
    temperature: Sequence[float],
    elapsed: Sequence[float],
    frequency: Sequence[float],
) -> dict[str, Any] | None:
    """Separate a temperature coefficient from ageing and servo drift.

    Passive temperature is collinear with elapsed time, so a fit against
    temperature alone attributes any slow frequency walk to heat.
    """
    count = len(frequency)
    if count < 12:
        return None
    t_mean, e_mean = _mean(temperature), _mean(elapsed)
    t = [value - t_mean for value in temperature]
    e = [value - e_mean for value in elapsed]
    matrix = [
        [sum(a * a for a in t), sum(t[i] * e[i] for i in range(count))],
        [sum(t[i] * e[i] for i in range(count)), sum(a * a for a in e)],
    ]
    mean_f = _mean(frequency)
    centred = [value - mean_f for value in frequency]
    vector = [
        sum(t[i] * centred[i] for i in range(count)),
        sum(e[i] * centred[i] for i in range(count)),
    ]
    solution = _solve(matrix, vector)
    if solution is None:
        return None
    tempco, drift = solution
    predicted = [tempco * t[i] + drift * e[i] for i in range(count)]
    residuals = [centred[i] - predicted[i] for i in range(count)]
    rss = sum(value * value for value in residuals)
    tss = sum(value * value for value in centred)
    return {
        "tempco_ppb_per_c": tempco,
        "ageing_ppb_per_s": drift,
        "r_squared": None if tss <= EPSILON else 1.0 - rss / tss,
        "residual_sigma_ppb": math.sqrt(rss / max(1, count - 3)),
    }


def analyse_node(
    samples: Sequence[tuple[float, float, float]],
    node: str = "",
) -> dict[str, Any]:
    """Analyse one clock's (time, temperature_c, frequency_ppb) record."""
    ordered = sorted((float(t), float(temp), float(freq)) for t, temp, freq in samples)
    count = len(ordered)
    if count < MIN_SAMPLES:
        return {
            "node": node,
            "status": "learning",
            "samples": count,
            "reason": f"need at least {MIN_SAMPLES} paired samples, have {count}",
        }
    times = [row[0] for row in ordered]
    temperature = [row[1] for row in ordered]
    frequency = [row[2] for row in ordered]
    start = times[0]
    elapsed = [value - start for value in times]

    span = max(temperature) - min(temperature)
    levels = sorted(set(temperature))
    quantised = all(abs(value - round(value)) < 1e-9 for value in temperature)

    ols = _polynomial_fit(temperature, frequency, 1)
    if ols is None:
        return {"node": node, "status": "unavailable", "samples": count,
                "reason": "temperature is constant over the record"}
    slope = ols["coefficients"][1]
    intercept_at_mean = ols["coefficients"][0]

    # Serial correlation inflates naive significance; discount the sample count.
    rho = _lag_one_autocorrelation(ols["residuals"]) or 0.0
    rho = max(-0.99, min(0.99, rho))
    effective = count * (1.0 - rho) / (1.0 + rho) if rho > -1.0 else float(count)
    effective = max(2.0, min(float(count), effective))
    temp_ss = sum((value - _mean(temperature)) ** 2 for value in temperature)
    # Scale the standard error by the variance-inflation implied by rho.
    slope_se = None
    if temp_ss > EPSILON:
        base = ols["residual_sigma"] / math.sqrt(temp_ss)
        slope_se = base * math.sqrt(max(1.0, count / effective))
    t_statistic = None if not slope_se or slope_se <= EPSILON else slope / slope_se
    # Normal approximation; with an effective size this small a t quantile would
    # be wider still, so the interval is optimistic and labelled as such.
    ci = None if slope_se is None else (slope - 1.96 * slope_se, slope + 1.96 * slope_se)

    lambda_ratio = None
    deming_slope = None
    if QUANTISATION_SIGMA_C > 0:
        frequency_noise = _variance(ols["residuals"])
        lambda_ratio = frequency_noise / max(QUANTISATION_SIGMA_C ** 2, EPSILON)
        deming_slope = _deming(temperature, frequency, lambda_ratio)

    quadratic = _polynomial_fit(temperature, frequency, 2)
    cubic = _polynomial_fit(temperature, frequency, 3)
    models = {"linear": ols, "quadratic": quadratic, "cubic": cubic}
    ranked = [
        {"model": name, "aic": value["aic"], "bic": value["bic"],
         "r_squared": value["r_squared"], "degree": value["degree"]}
        for name, value in models.items() if value
    ]
    ranked.sort(key=lambda item: item["aic"])
    preferred = ranked[0]["model"] if ranked else "linear"

    # Hysteresis: fit the heating and cooling branches separately.
    heating: list[tuple[float, float]] = []
    cooling: list[tuple[float, float]] = []
    for index in range(1, count):
        delta = temperature[index] - temperature[index - 1]
        if delta > 0:
            heating.append((temperature[index], frequency[index]))
        elif delta < 0:
            cooling.append((temperature[index], frequency[index]))
    branch: dict[str, Any] = {"heating_samples": len(heating), "cooling_samples": len(cooling)}
    for name, data in (("heating", heating), ("cooling", cooling)):
        if len(data) >= 12:
            fit = _polynomial_fit([row[0] for row in data], [row[1] for row in data], 1)
            branch[f"{name}_slope_ppb_per_c"] = None if fit is None else fit["coefficients"][1]
        else:
            branch[f"{name}_slope_ppb_per_c"] = None
    up, down = branch.get("heating_slope_ppb_per_c"), branch.get("cooling_slope_ppb_per_c")
    branch["separation_ppb_per_c"] = None if up is None or down is None else abs(up - down)

    # Thermal lag: the die leads the sensor, so search a bounded delay.
    lag = {"best_lag_samples": None, "best_correlation": None, "zero_lag_correlation": _pearson(temperature, frequency)}
    horizon = min(60, count // 4)
    best = None
    for shift in range(0, horizon + 1):
        left = temperature[: count - shift] if shift else temperature
        right = frequency[shift:] if shift else frequency
        correlation = _pearson(left, right)
        if correlation is None:
            continue
        if best is None or abs(correlation) > abs(best[1]):
            best = (shift, correlation)
    if best:
        lag["best_lag_samples"], lag["best_correlation"] = best[0], best[1]

    joint = _joint_temperature_time_fit(temperature, elapsed, frequency)
    time_collinearity = _pearson(temperature, elapsed)

    raw_sigma = math.sqrt(_variance(frequency))
    compensated_sigma = ols["residual_sigma"]
    reduction = None
    if raw_sigma > EPSILON:
        reduction = 100.0 * (1.0 - compensated_sigma / raw_sigma)

    gates = {
        "enough_samples": count >= MIN_SAMPLES,
        "enough_span": span >= MIN_SPAN_C,
        "enough_levels": len(levels) >= MIN_DISTINCT_LEVELS,
        "not_time_confounded": time_collinearity is None or abs(time_collinearity) <= MAX_TIME_COLLINEARITY,
        "residuals_independent": abs(rho) < 0.5,
        "effective_samples_sufficient": effective >= MIN_EFFECTIVE_SAMPLES,
        "slope_significant": t_statistic is not None and abs(t_statistic) >= 2.0,
    }
    passed = sum(1 for value in gates.values() if value)
    if all(gates.values()):
        verdict = "supported"
    elif gates["enough_samples"] and passed >= 4:
        verdict = "candidate"
    else:
        verdict = "insufficient-evidence"

    unmet = [name for name, value in gates.items() if not value]
    return {
        "node": node,
        "status": "ready",
        "samples": count,
        "record_span_s": times[-1] - times[0],
        "temperature": {
            "minimum_c": min(temperature),
            "maximum_c": max(temperature),
            "span_c": span,
            "distinct_levels": len(levels),
            "quantised_to_whole_degrees": quantised,
            "quantisation_sigma_c": QUANTISATION_SIGMA_C if quantised else 0.0,
            "mean_c": _mean(temperature),
        },
        "frequency": {
            "mean_ppb": _mean(frequency),
            "sigma_ppb": raw_sigma,
            "minimum_ppb": min(frequency),
            "maximum_ppb": max(frequency),
        },
        "ols": {
            "tempco_ppb_per_c": slope,
            "frequency_at_mean_temperature_ppb": intercept_at_mean,
            "r_squared": ols["r_squared"],
            "residual_sigma_ppb": ols["residual_sigma"],
            "standard_error_ppb_per_c": slope_se,
            "t_statistic": t_statistic,
            "confidence_95_ppb_per_c": ci,
            "note": "attenuated toward zero by temperature quantisation noise",
        },
        "errors_in_variables": {
            "deming_tempco_ppb_per_c": deming_slope,
            "variance_ratio": lambda_ratio,
            "method": "Deming regression with quantisation-derived error in temperature",
        },
        "robust": {"theil_sen_tempco_ppb_per_c": _theil_sen(temperature, frequency)},
        "model_selection": {"ranked_by_aic": ranked, "preferred": preferred,
                            "note": "AT-cut quartz is cubic in temperature; a preferred linear model only means the record cannot resolve curvature"},
        "hysteresis": branch,
        "thermal_lag": lag,
        "confounding": {
            "temperature_time_correlation": time_collinearity,
            "joint_fit": joint,
            "note": "passive temperature tracks elapsed time, so a temperature-only fit absorbs oscillator ageing",
        },
        "serial_correlation": {
            "residual_lag_one": rho,
            "effective_samples": effective,
            "note": "standard errors are scaled by the variance inflation this implies",
        },
        "compensation_preview": {
            "raw_sigma_ppb": raw_sigma,
            "residual_sigma_ppb": compensated_sigma,
            "reduction_pct": reduction,
        },
        "evidence": {"gates": gates, "gates_passed": passed, "gates_total": len(gates),
                     "unmet": unmet, "verdict": verdict},
        "scatter": [
            {"temperature_c": temperature[index], "frequency_ppb": frequency[index],
             "elapsed_s": elapsed[index]}
            for index in range(0, count, max(1, count // 600))
        ],
    }


def thermal_analysis(paired: dict[str, Sequence[tuple[float, float, float]]]) -> dict[str, Any]:
    """Analyse every clock and summarise the fleet."""
    nodes = {node: analyse_node(samples, node) for node, samples in sorted(paired.items())}
    ready = [value for value in nodes.values() if value.get("status") == "ready"]
    supported = [value for value in ready if value["evidence"]["verdict"] == "supported"]
    hottest = max(
        (value for value in ready),
        key=lambda item: item["temperature"]["maximum_c"],
        default=None,
    )
    return {
        "nodes": nodes,
        "summary": {
            "analysed": len(ready),
            "supported": len(supported),
            "hottest_node": None if hottest is None else hottest["node"],
            "hottest_c": None if hottest is None else hottest["temperature"]["maximum_c"],
        },
        "method": (
            "servo frequency correction regressed on hardware-monitor die temperature; "
            "OLS with autocorrelation-scaled errors, Deming errors-in-variables, "
            "Theil-Sen robust slope, AIC-ranked polynomial order, split-branch "
            "hysteresis, lag search, and a joint temperature/time fit"
        ),
        "interpretation": (
            "The applied correction is the negated oscillator error, so its slope against "
            "temperature estimates a temperature coefficient. Passive operation supplies "
            "neither a wide range nor an independent temperature input, so a supported "
            "verdict requires deliberate thermal forcing; without it the estimate remains "
            "a candidate confounded with ageing."
        ),
        "live_changes": 0,
    }
