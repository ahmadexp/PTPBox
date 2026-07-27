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


# ---------------------------------------------------------------------------
# Distribution tails
#
# The agent is dependency-free, so the incomplete beta and gamma functions that
# F, chi-square, and t tails need are implemented here rather than imported.
# ---------------------------------------------------------------------------

def _log_gamma(value: float) -> float:
    return math.lgamma(value)


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Lentz's method for the continued fraction of the incomplete beta."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for iteration in range(1, 300):
        m2 = 2 * iteration
        aa = iteration * (b - iteration) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        aa = -(a + iteration) * (qab + iteration) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return result


def _beta_incomplete(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(_log_gamma(a + b) - _log_gamma(a) - _log_gamma(b) + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def f_survival(statistic: float, numerator_df: float, denominator_df: float) -> float | None:
    """P(F > statistic); the p-value for a variance-ratio test."""
    if statistic <= 0 or numerator_df <= 0 or denominator_df <= 0:
        return None
    x = denominator_df / (denominator_df + numerator_df * statistic)
    return max(0.0, min(1.0, _beta_incomplete(denominator_df / 2.0, numerator_df / 2.0, x)))


def t_two_sided(statistic: float, degrees: float) -> float | None:
    """Two-sided p-value for Student's t."""
    if degrees <= 0:
        return None
    x = degrees / (degrees + statistic * statistic)
    return max(0.0, min(1.0, _beta_incomplete(degrees / 2.0, 0.5, x)))


def _gamma_upper(shape: float, x: float) -> float:
    """Regularised upper incomplete gamma Q(shape, x)."""
    if x <= 0:
        return 1.0
    if x < shape + 1.0:
        # Series for the lower tail, then complement.
        total, term = 1.0 / shape, 1.0 / shape
        for index in range(1, 300):
            term *= x / (shape + index)
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        return max(0.0, min(1.0, 1.0 - total * math.exp(-x + shape * math.log(x) - _log_gamma(shape))))
    # Continued fraction for the upper tail.
    tiny = 1e-30
    b = x + 1.0 - shape
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for index in range(1, 300):
        an = -index * (index - shape)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return max(0.0, min(1.0, h * math.exp(-x + shape * math.log(x) - _log_gamma(shape))))


def chi_square_survival(statistic: float, degrees: float) -> float | None:
    if statistic < 0 or degrees <= 0:
        return None
    return _gamma_upper(degrees / 2.0, statistic / 2.0)


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Step-up false-discovery-rate adjustment for a family of comparisons."""
    count = len(p_values)
    if not count:
        return []
    order = sorted(range(count), key=lambda index: p_values[index])
    adjusted = [0.0] * count
    running = 1.0
    for rank, index in enumerate(reversed(order), start=1):
        position = count - rank + 1
        running = min(running, p_values[index] * count / position)
        adjusted[index] = min(1.0, running)
    return adjusted


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


def _block_bootstrap_slope(
    temperature: Sequence[float],
    frequency: Sequence[float],
    block: int,
    draws: int = 240,
    seed: int = 12345,
) -> tuple[float, float] | None:
    """Percentile interval for the slope, resampling contiguous blocks.

    Serial correlation makes an ordinary bootstrap far too optimistic, so whole
    blocks are resampled to preserve the local dependence structure. A
    deterministic generator keeps repeated snapshots reproducible.
    """
    count = len(temperature)
    if count < 40 or block < 2:
        return None
    blocks = max(2, count // block)
    state = seed
    slopes: list[float] = []
    for _ in range(draws):
        xs: list[float] = []
        ys: list[float] = []
        for _ in range(blocks):
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            start = state % max(1, count - block)
            xs.extend(temperature[start:start + block])
            ys.extend(frequency[start:start + block])
        fit = _polynomial_fit(xs, ys, 1)
        if fit:
            slopes.append(fit["coefficients"][1])
    if len(slopes) < 20:
        return None
    slopes.sort()
    low = slopes[int(0.025 * (len(slopes) - 1))]
    high = slopes[int(0.975 * (len(slopes) - 1))]
    return (low, high)


def _brown_forsythe(groups: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Equal-variance test on absolute deviations from each group median.

    The median form is used rather than Levene's mean form because residual
    distributions here are not reliably symmetric.
    """
    usable = [list(group) for group in groups if len(group) >= 3]
    if len(usable) < 2:
        return {"status": "insufficient-groups"}
    transformed: list[list[float]] = []
    for group in usable:
        ordered = sorted(group)
        middle = len(ordered) // 2
        median = ordered[middle] if len(ordered) % 2 else 0.5 * (ordered[middle - 1] + ordered[middle])
        transformed.append([abs(value - median) for value in group])
    total = [value for group in transformed for value in group]
    grand = _mean(total)
    k = len(transformed)
    n = len(total)
    between = sum(len(group) * (_mean(group) - grand) ** 2 for group in transformed)
    within = sum((value - _mean(group)) ** 2 for group in transformed for value in group)
    if within <= EPSILON or n - k <= 0:
        return {"status": "degenerate"}
    statistic = (between / (k - 1)) / (within / (n - k))
    p_value = f_survival(statistic, k - 1, n - k)
    return {
        "status": "ready",
        "statistic": statistic,
        "numerator_df": k - 1,
        "denominator_df": n - k,
        "p_value": p_value,
        "equal_variance": None if p_value is None else p_value > 0.05,
    }


def _kruskal_wallis(groups: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Distribution-free alternative when normality cannot be assumed."""
    usable = [list(group) for group in groups if len(group) >= 3]
    if len(usable) < 2:
        return {"status": "insufficient-groups"}
    pooled = sorted((value, index) for index, group in enumerate(usable) for value in group)
    ranks: dict[int, list[float]] = {index: [] for index in range(len(usable))}
    position = 0
    while position < len(pooled):
        end = position
        while end + 1 < len(pooled) and pooled[end + 1][0] == pooled[position][0]:
            end += 1
        average = (position + end) / 2.0 + 1.0
        for offset in range(position, end + 1):
            ranks[pooled[offset][1]].append(average)
        position = end + 1
    n = len(pooled)
    statistic = 12.0 / (n * (n + 1)) * sum(len(r) * (_mean(r) - (n + 1) / 2.0) ** 2 for r in ranks.values()) if n > 1 else 0.0
    degrees = len(usable) - 1
    return {
        "status": "ready",
        "statistic": statistic,
        "degrees_of_freedom": degrees,
        "p_value": chi_square_survival(statistic, degrees),
    }


def _leading_mode(matrix: list[list[float]]) -> tuple[float, list[float]] | None:
    """Largest eigenvalue and vector by power iteration."""
    size = len(matrix)
    if size < 2:
        return None
    vector = [1.0 / math.sqrt(size)] * size
    value = 0.0
    for _ in range(400):
        product = [sum(matrix[row][col] * vector[col] for col in range(size)) for row in range(size)]
        norm = math.sqrt(sum(item * item for item in product))
        if norm <= EPSILON:
            return None
        nxt = [item / norm for item in product]
        if max(abs(nxt[index] - vector[index]) for index in range(size)) < 1e-12:
            vector = nxt
            value = norm
            break
        vector, value = nxt, norm
    return value, vector


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    def rank(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda index: values[index])
        out = [0.0] * len(values)
        position = 0
        while position < len(order):
            end = position
            while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
                end += 1
            average = (position + end) / 2.0 + 1.0
            for offset in range(position, end + 1):
                out[order[offset]] = average
            position = end + 1
        return out
    return _pearson(rank(left), rank(right))


def fleet_comparison(
    paired: dict[str, Sequence[tuple[float, float, float]]],
    nodes: dict[str, dict[str, Any]],
    slot_order: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compare the clocks against one another.

    The question is not whether the cards have different mean corrections, which
    they trivially do because they are different oscillators with different
    offsets. It is whether their *slopes* differ, which is a test for homogeneity
    of regression slopes rather than a one-way analysis of means.
    """
    usable = {
        name: [(temp, freq) for _, temp, freq in rows]
        for name, rows in paired.items()
        if nodes.get(name, {}).get("status") == "ready"
    }
    if len(usable) < 2:
        return {"status": "insufficient-clocks", "clocks": len(usable)}

    # Pooled model with a single common slope versus one slope per clock. The
    # extra parameters are the per-clock slope deviations.
    pooled_rss = 0.0
    separate_rss = 0.0
    total_samples = 0
    per_clock: dict[str, dict[str, Any]] = {}
    residual_groups: list[list[float]] = []
    all_temperature: list[float] = []
    all_frequency: list[float] = []
    centred_pairs: list[tuple[str, float, float]] = []
    for name, rows in usable.items():
        temps = [row[0] for row in rows]
        freqs = [row[1] for row in rows]
        fit = _polynomial_fit(temps, freqs, 1)
        if not fit:
            continue
        separate_rss += fit["rss"]
        total_samples += len(rows)
        residual_groups.append(fit["residuals"])
        effective = nodes[name]["serial_correlation"]["effective_samples"]
        block = max(2, int(len(rows) / max(2.0, effective)))
        per_clock[name] = {
            "slope_ppb_per_c": fit["coefficients"][1],
            "standard_error_ppb_per_c": nodes[name]["ols"]["standard_error_ppb_per_c"],
            "effective_samples": effective,
            "bootstrap_95_ppb_per_c": _block_bootstrap_slope(temps, freqs, block),
            "block_length": block,
            "mean_temperature_c": _mean(temps),
        }
        # Centre each clock's data so the pooled fit tests slope equality, not
        # the offset differences that trivially exist between oscillators.
        temp_mean, freq_mean = _mean(temps), _mean(freqs)
        for index in range(len(rows)):
            centred_pairs.append((name, temps[index] - temp_mean, freqs[index] - freq_mean))
        all_temperature.extend(temps)
        all_frequency.extend(freqs)

    if len(per_clock) < 2:
        return {"status": "insufficient-clocks", "clocks": len(per_clock)}

    common = _polynomial_fit([row[1] for row in centred_pairs], [row[2] for row in centred_pairs], 1)
    if common:
        pooled_rss = common["rss"]
    groups = len(per_clock)
    # Naive degrees of freedom would treat every correlated sample as
    # independent, so scale by the mean autocorrelation discount.
    inflation = total_samples / max(1.0, sum(item["effective_samples"] for item in per_clock.values()))
    numerator_df = groups - 1
    denominator_df = max(1.0, (total_samples - 2 * groups) / max(1.0, inflation))
    statistic = None
    p_value = None
    if pooled_rss > separate_rss and separate_rss > EPSILON and numerator_df > 0:
        statistic = ((pooled_rss - separate_rss) / numerator_df) / (separate_rss / max(1.0, total_samples - 2 * groups))
        statistic /= max(1.0, inflation)
        p_value = f_survival(statistic, numerator_df, denominator_df)

    # Pairwise slope differences with a false-discovery-rate adjustment.
    names = sorted(per_clock)
    raw: list[dict[str, Any]] = []
    for left_index in range(len(names)):
        for right_index in range(left_index + 1, len(names)):
            left, right = names[left_index], names[right_index]
            a, b = per_clock[left], per_clock[right]
            se_a, se_b = a["standard_error_ppb_per_c"], b["standard_error_ppb_per_c"]
            if se_a is None or se_b is None:
                continue
            pooled_se = math.sqrt(se_a * se_a + se_b * se_b)
            if pooled_se <= EPSILON:
                continue
            difference = a["slope_ppb_per_c"] - b["slope_ppb_per_c"]
            t_statistic = difference / pooled_se
            degrees = max(1.0, a["effective_samples"] + b["effective_samples"] - 4.0)
            raw.append({
                "left": left, "right": right, "difference_ppb_per_c": difference,
                "t_statistic": t_statistic,
                # An exact 0.0 is the strongest possible evidence, so it must not
                # be coalesced away as if the test had failed to run.
                "p_value": p_pair if (p_pair := t_two_sided(t_statistic, degrees)) is not None else 1.0,
            })
    adjusted = benjamini_hochberg([item["p_value"] for item in raw])
    for index, item in enumerate(raw):
        item["p_adjusted"] = adjusted[index]
        item["differs"] = adjusted[index] < 0.05

    # How much of the frequency motion is shared across the chassis rather than
    # specific to one card.
    aligned = min((len(rows) for rows in usable.values()), default=0)
    common_mode: dict[str, Any] = {"status": "insufficient-overlap"}
    if aligned >= 30 and len(usable) >= 2:
        series = [[row[1] for row in rows[-aligned:]] for rows in usable.values()]
        size = len(series)
        correlation = [[1.0] * size for _ in range(size)]
        for row in range(size):
            for col in range(row + 1, size):
                value = _pearson(series[row], series[col]) or 0.0
                correlation[row][col] = correlation[col][row] = value
        mode = _leading_mode(correlation)
        if mode:
            value, vector = mode
            common_mode = {
                "status": "ready",
                "clocks": list(usable.keys()),
                "leading_eigenvalue": value,
                "explained_share": value / size,
                "loadings": {name: vector[index] for index, name in enumerate(usable.keys())},
                "aligned_samples": aligned,
                "interpretation": (
                    "A dominant first mode means the clocks move together, which is what a "
                    "shared chassis thermal and ambient environment produces; it does not by "
                    "itself separate common heating from the cascade's own servo coupling"
                ),
            }

    # Does temperature follow physical slot order? An airflow gradient shows up
    # as a monotone relationship rather than a random spread.
    gradient: dict[str, Any] = {"status": "unavailable"}
    if slot_order:
        pairs = [
            (slot_order[name], nodes[name]["temperature"]["mean_c"])
            for name in per_clock if name in slot_order
        ]
        if len(pairs) >= 4:
            rho = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
            gradient = {
                "status": "ready",
                "spearman_rho": rho,
                "samples": len(pairs),
                "interpretation": "A strong monotone relationship with slot order suggests an airflow gradient rather than per-card variation",
            }

    return {
        "status": "ready",
        "clocks": len(per_clock),
        "per_clock": per_clock,
        "slope_homogeneity": {
            "test": "homogeneity of regression slopes (ANCOVA interaction)",
            "f_statistic": statistic,
            "numerator_df": numerator_df,
            "denominator_df": denominator_df,
            "p_value": p_value,
            "slopes_differ": bool(p_value is not None and p_value < 0.05),
            "autocorrelation_inflation": inflation,
            "interpretation": (
                "Rejecting equality means one fleet-wide coefficient does not describe every "
                "card. Degrees of freedom are discounted for serial correlation, without which "
                "any difference would look significant."
            ),
        },
        "pairwise": raw,
        "residual_variance": _brown_forsythe(residual_groups),
        "rank_test": _kruskal_wallis(residual_groups),
        "common_mode": common_mode,
        "slot_gradient": gradient,
        "not_applicable": {
            "manova": (
                "MANOVA models several dependent variables measured on one unit. Here a single "
                "dependent variable, the applied correction, is measured on separate clocks, so "
                "the multivariate question is answered by the common-mode decomposition instead."
            ),
            "one_way_anova_on_means": (
                "Comparing mean corrections only confirms that different oscillators sit at "
                "different frequency offsets, which is expected and uninformative."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Temperature-compensated holdover
#
# During holdover the PHC is left free-running and accumulates phase at the
# oscillator's own frequency error. If that error moves with temperature by a
# known coefficient, continuing to apply a temperature-driven correction should
# cancel part of the drift.
#
# The coefficient sign needs care. The analysis above regresses the *applied
# correction* on temperature, and the correction cancels the oscillator error, so
# the oscillator's own coefficient is the negation of that slope.
#
# Compensation is only worth arming when the coefficient is trustworthy. A
# confounded estimate injects error rather than removing it, so the evaluator
# reports both what the measured coefficient would achieve and what the best
# possible coefficient could achieve. If the second is small the idea cannot help
# this oscillator, whatever coefficient is used.
# ---------------------------------------------------------------------------

MIN_COMPENSATION_BENEFIT_PCT = 10.0

# A die has thermal mass, so its temperature cannot change materially between
# consecutive samples. Whole-degree sensors nonetheless flap by a full step
# between reads, and that dither is quantisation noise rather than heat. Left in
# place it lets a fitted coefficient cancel drift by exploiting alternation at
# the sampling frequency, which manufactures an improvement that no physical
# compensator could reproduce. Smooth the regressor before modelling.
COMPENSATION_SMOOTHING_SAMPLES = 5


def _integrate(rates: Sequence[float], times: Sequence[float]) -> list[float]:
    """Trapezoidal phase from a frequency-error series in ppb (ns per s)."""
    phase = [0.0]
    for index in range(1, len(rates)):
        step = times[index] - times[index - 1]
        phase.append(phase[-1] + 0.5 * (rates[index] + rates[index - 1]) * step)
    return phase


def _series_statistics(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"rms_ns": None, "peak_abs_ns": None, "final_ns": None}
    return {
        "rms_ns": math.sqrt(sum(value * value for value in values) / len(values)),
        "peak_abs_ns": max(abs(value) for value in values),
        "final_ns": values[-1],
    }


def holdover_compensation(
    samples: Sequence[tuple[float, float, float]],
    correction_tempco_ppb_per_c: float | None = None,
    coefficient_verdict: str = "unknown",
) -> dict[str, Any]:
    """Evaluate temperature-compensated holdover against a recorded free run.

    ``samples`` are (time, temperature_c, phase_ns) from a holdover record, where
    phase is the accumulated time error from the release baseline.

    Nothing here changes a clock. It answers whether compensation would have
    reduced the drift that was actually observed, which is the question that has
    to be settled before arming it on hardware.
    """
    ordered = sorted((float(t), float(temp), float(phase)) for t, temp, phase in samples)
    if len(ordered) < 30:
        return {"status": "learning", "samples": len(ordered),
                "reason": "need at least 30 holdover samples with temperature"}
    times = [row[0] for row in ordered]
    temperature = [row[1] for row in ordered]
    phase = [row[2] for row in ordered]
    span = times[-1] - times[0]
    if span <= EPSILON:
        return {"status": "unavailable", "reason": "zero-length record"}

    # Differentiate phase to get the free-running frequency error, then remove the
    # release-instant value so only the change during holdover is modelled.
    rates: list[float] = []
    for index in range(len(ordered)):
        if index == 0:
            step = times[1] - times[0]
            rates.append((phase[1] - phase[0]) / step if step > EPSILON else 0.0)
        elif index == len(ordered) - 1:
            step = times[-1] - times[-2]
            rates.append((phase[-1] - phase[-2]) / step if step > EPSILON else 0.0)
        else:
            step = times[index + 1] - times[index - 1]
            rates.append((phase[index + 1] - phase[index - 1]) / step if step > EPSILON else 0.0)

    # Suppress sensor dither before it can be mistaken for thermal motion.
    window = max(1, min(COMPENSATION_SMOOTHING_SAMPLES, len(temperature) // 4))
    smoothed: list[float] = []
    for index in range(len(temperature)):
        low = max(0, index - window // 2)
        high = min(len(temperature), low + window)
        smoothed.append(_mean(temperature[low:high]))
    reference_temperature = smoothed[0]
    delta_temperature = [value - reference_temperature for value in smoothed]

    def residual_phase(oscillator_tempco: float) -> list[float]:
        """Phase left after cancelling the modelled thermal frequency term."""
        corrected = [rates[index] - oscillator_tempco * delta_temperature[index] for index in range(len(rates))]
        return _integrate(corrected, times)

    measured = _series_statistics(phase)

    # The oscillator coefficient is the negation of the correction coefficient.
    predictive: dict[str, Any] = {"status": "unavailable", "reason": "no coefficient supplied"}
    if correction_tempco_ppb_per_c is not None:
        oscillator_tempco = -float(correction_tempco_ppb_per_c)
        residual = residual_phase(oscillator_tempco)
        stats = _series_statistics(residual)
        improvement = None
        if measured["rms_ns"] and measured["rms_ns"] > EPSILON and stats["rms_ns"] is not None:
            improvement = 100.0 * (1.0 - stats["rms_ns"] / measured["rms_ns"])
        predictive = {
            "status": "ready",
            "oscillator_tempco_ppb_per_c": oscillator_tempco,
            "correction_tempco_ppb_per_c": float(correction_tempco_ppb_per_c),
            **stats,
            "improvement_pct": improvement,
            "harmful": bool(improvement is not None and improvement < 0.0),
        }

    # Best achievable coefficient, fitted in sample. This is an upper bound, not
    # a usable coefficient: it is chosen with knowledge of the very record it is
    # scored against.
    oracle: dict[str, Any] = {"status": "unavailable"}
    leverage = sum(value * value for value in delta_temperature)
    if leverage > EPSILON:
        # Minimise the residual phase RMS over the coefficient by a short scan
        # bracketing the least-squares rate solution.
        rate_fit = sum(rates[index] * delta_temperature[index] for index in range(len(rates))) / leverage
        best = None
        for step in range(-20, 21):
            candidate = rate_fit * (1.0 + step * 0.1)
            stats = _series_statistics(residual_phase(candidate))
            if stats["rms_ns"] is None:
                continue
            if best is None or stats["rms_ns"] < best[1]["rms_ns"]:
                best = (candidate, stats)
        if best:
            candidate, stats = best
            improvement = None
            if measured["rms_ns"] and measured["rms_ns"] > EPSILON:
                improvement = 100.0 * (1.0 - stats["rms_ns"] / measured["rms_ns"])
            oracle = {
                "status": "ready",
                "oscillator_tempco_ppb_per_c": candidate,
                **stats,
                "improvement_pct": improvement,
                "note": "fitted on the same record it is scored against; an upper bound on achievable benefit, not a coefficient to deploy",
            }

    achievable = oracle.get("improvement_pct") if oracle.get("status") == "ready" else None
    coefficient_trusted = coefficient_verdict == "supported"
    worth_arming = bool(
        coefficient_trusted
        and predictive.get("status") == "ready"
        and not predictive.get("harmful")
        and (predictive.get("improvement_pct") or 0.0) >= MIN_COMPENSATION_BENEFIT_PCT
    )
    if achievable is not None and achievable < MIN_COMPENSATION_BENEFIT_PCT:
        recommendation = (
            f"Do not compensate. Even the best possible coefficient recovers only "
            f"{achievable:.1f}% of the observed wander, so this drift is not "
            f"temperature-driven over the range seen."
        )
    elif not coefficient_trusted:
        recommendation = (
            "Do not arm yet. The temperature range in the locked record cannot support a "
            "trustworthy coefficient, and compensating with a confounded one injects error. "
            "Run a forced thermal cycle first."
        )
    elif worth_arming:
        recommendation = "Compensation is supported by the record and would reduce holdover wander."
    else:
        recommendation = "Coefficient is trustworthy but the modelled benefit is below the arming threshold."

    return {
        "status": "ready",
        "samples": len(ordered),
        "record_span_s": span,
        "temperature": {
            "reference_c": reference_temperature,
            "minimum_c": min(temperature),
            "maximum_c": max(temperature),
            "span_c": max(temperature) - min(temperature),
            "smoothing_samples": window,
            "smoothed_span_c": max(smoothed) - min(smoothed),
            "note": (
                "The regressor is smoothed because whole-degree sensors dither by a full step "
                "between reads, which is quantisation noise and not thermal motion a compensator "
                "could track"
            ),
        },
        "measured_free_run": measured,
        "compensated_predictive": predictive,
        "compensated_best_possible": oracle,
        "arming": {
            "coefficient_verdict": coefficient_verdict,
            "coefficient_trusted": coefficient_trusted,
            "benefit_threshold_pct": MIN_COMPENSATION_BENEFIT_PCT,
            "worth_arming": worth_arming,
            "recommendation": recommendation,
        },
        "actuation": {
            "implemented": False,
            "mechanism": (
                "Live compensation would keep applying a bounded clock_adjtime frequency "
                "correction during holdover instead of leaving the PHC free-running, driven by "
                "the live reading, which requires the root-owned servo worker rather than this "
                "read-only analysis"
            ),
        },
        "live_changes": 0,
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
    slot_order = {name: index for index, name in enumerate(sorted(paired))}
    return {
        "nodes": nodes,
        "fleet": fleet_comparison(paired, nodes, slot_order),
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
