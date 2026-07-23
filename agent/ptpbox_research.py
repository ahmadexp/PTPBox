#!/usr/bin/env python3
"""Dependency-free metrology and advanced-control primitives for PTPBox.

The module deliberately keeps every calculation auditable and portable to the
reference appliance.  It does not adjust a clock or touch a network device.
Control decisions produced here are recommendations until the guarded
``ptpboxctl`` path applies them.
"""

from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


EPSILON = 1e-12


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def variance(values: Sequence[float], sample: bool = False) -> float:
    if len(values) < (2 if sample else 1):
        return 0.0
    center = mean(values)
    denominator = len(values) - 1 if sample else len(values)
    return sum((value - center) ** 2 for value in values) / denominator


def median_absolute_deviation(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    center = statistics.median(values)
    return statistics.median(abs(value - center) for value in values)


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def transpose(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    return [list(column) for column in zip(*matrix)] if matrix else []


def matrix_multiply(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> list[list[float]]:
    right_t = transpose(right)
    return [[sum(a * b for a, b in zip(row, column)) for column in right_t] for row in left]


def matrix_vector(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [sum(value * item for value, item in zip(row, vector)) for row in matrix]


def identity(size: int) -> list[list[float]]:
    return [[1.0 if row == column else 0.0 for column in range(size)] for row in range(size)]


def inverse(matrix: Sequence[Sequence[float]], ridge: float = 0.0) -> list[list[float]]:
    size = len(matrix)
    if not size or any(len(row) != size for row in matrix):
        raise ValueError("matrix must be square")
    augmented = [
        [
            *(float(value) + (ridge if row == column else 0.0) for column, value in enumerate(matrix[row])),
            *identity(size)[row],
        ]
        for row in range(size)
    ]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < EPSILON:
            raise ValueError("matrix is singular")
        if pivot != column:
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[column])
            ]
    return [row[size:] for row in augmented]


def solve(matrix: Sequence[Sequence[float]], vector: Sequence[float], ridge: float = 0.0) -> list[float]:
    return matrix_vector(inverse(matrix, ridge=ridge), vector)


def least_squares(rows: Sequence[Sequence[float]], values: Sequence[float], ridge: float = 1e-9) -> list[float]:
    if not rows or len(rows) != len(values):
        raise ValueError("least-squares rows and values must be non-empty and aligned")
    transposed = transpose(rows)
    normal = matrix_multiply(transposed, rows)
    target = matrix_vector(transposed, values)
    return solve(normal, target, ridge=ridge)


def covariance_matrix(channels: Sequence[Sequence[float]], shrinkage: float = 0.08) -> list[list[float]]:
    if not channels:
        return []
    length = min((len(channel) for channel in channels), default=0)
    if length < 2:
        return [[0.0 for _ in channels] for _ in channels]
    centered = []
    for channel in channels:
        samples = list(channel[-length:])
        center = mean(samples)
        centered.append([value - center for value in samples])
    raw = [
        [
            sum(a * b for a, b in zip(left, right)) / max(1, length - 1)
            for right in centered
        ]
        for left in centered
    ]
    diagonal_mean = mean([raw[index][index] for index in range(len(raw))])
    strength = max(0.0, min(1.0, shrinkage))
    return [
        [
            (1.0 - strength) * raw[row][column]
            + strength * (diagonal_mean if row == column else 0.0)
            for column in range(len(raw))
        ]
        for row in range(len(raw))
    ]


def symmetric_eigenvalues(matrix: Sequence[Sequence[float]], iterations: int = 80) -> list[float]:
    """Return eigenvalues of a real symmetric matrix using Jacobi rotations."""
    size = len(matrix)
    if not size:
        return []
    work = [list(map(float, row)) for row in matrix]
    for _ in range(iterations * max(1, size * size)):
        row, column, largest = 0, 0, 0.0
        for left in range(size):
            for right in range(left + 1, size):
                if abs(work[left][right]) > largest:
                    row, column, largest = left, right, abs(work[left][right])
        if largest < 1e-10:
            break
        angle = 0.5 * math.atan2(2.0 * work[row][column], work[column][column] - work[row][row])
        cosine, sine = math.cos(angle), math.sin(angle)
        old_rr, old_cc, old_rc = work[row][row], work[column][column], work[row][column]
        work[row][row] = cosine * cosine * old_rr - 2.0 * sine * cosine * old_rc + sine * sine * old_cc
        work[column][column] = sine * sine * old_rr + 2.0 * sine * cosine * old_rc + cosine * cosine * old_cc
        work[row][column] = work[column][row] = 0.0
        for index in range(size):
            if index in {row, column}:
                continue
            old_ir, old_ic = work[index][row], work[index][column]
            work[index][row] = work[row][index] = cosine * old_ir - sine * old_ic
            work[index][column] = work[column][index] = sine * old_ir + cosine * old_ic
    return sorted((work[index][index] for index in range(size)), reverse=True)


def _tau_factors(length: int) -> list[int]:
    factors: list[int] = []
    factor = 1
    while factor <= max(1, length // 4):
        factors.append(factor)
        factor *= 2
    return factors


def rolling_ranges(values: Sequence[float], window: int) -> list[float]:
    if window <= 0 or window > len(values):
        return []
    minima: deque[int] = deque()
    maxima: deque[int] = deque()
    ranges: list[float] = []
    for index, value in enumerate(values):
        while minima and values[minima[-1]] >= value:
            minima.pop()
        while maxima and values[maxima[-1]] <= value:
            maxima.pop()
        minima.append(index)
        maxima.append(index)
        cutoff = index - window
        while minima and minima[0] <= cutoff:
            minima.popleft()
        while maxima and maxima[0] <= cutoff:
            maxima.popleft()
        if index >= window - 1:
            ranges.append(values[maxima[0]] - values[minima[0]])
    return ranges


def stability_metrics(phase_ns: Sequence[float], sample_period_s: float) -> dict[str, list[dict[str, float | int | None]]]:
    """Compute overlapping stability statistics from equally spaced phase data.

    ADEV, MDEV, and HDEV are returned as dimensionless fractional-frequency
    deviations. TDEV, MTIE, and Theo1 retain nanosecond units.
    """
    samples = [float(value) for value in phase_ns if math.isfinite(value)]
    if len(samples) < 4 or not math.isfinite(sample_period_s) or sample_period_s <= 0:
        return {name: [] for name in ("adev", "mdev", "tdev", "hdev", "mtie", "theo1")}
    result: dict[str, list[dict[str, float | int | None]]] = {
        name: [] for name in ("adev", "mdev", "tdev", "hdev", "mtie", "theo1")
    }
    for factor in _tau_factors(len(samples)):
        tau = factor * sample_period_s
        second = [
            samples[index + 2 * factor] - 2.0 * samples[index + factor] + samples[index]
            for index in range(len(samples) - 2 * factor)
        ]
        if second:
            adev = math.sqrt(sum(value * value for value in second) / (2.0 * len(second))) * 1e-9 / tau
            result["adev"].append(
                {"tau_s": tau, "value": adev, "pairs": len(second), "confidence": min(0.99, 1.0 - 1.0 / math.sqrt(len(second) + 1))}
            )
        if len(samples) >= 3 * factor:
            modified_terms = [
                sum(
                    samples[index + 2 * factor + inner]
                    - 2.0 * samples[index + factor + inner]
                    + samples[index + inner]
                    for inner in range(factor)
                )
                for index in range(len(samples) - 3 * factor + 1)
            ]
            if modified_terms:
                mdev = (
                    math.sqrt(sum(value * value for value in modified_terms) / (2.0 * len(modified_terms)))
                    * 1e-9
                    / (factor * tau)
                )
                result["mdev"].append(
                    {"tau_s": tau, "value": mdev, "pairs": len(modified_terms), "confidence": min(0.99, 1.0 - 1.0 / math.sqrt(len(modified_terms) + 1))}
                )
                result["tdev"].append(
                    {"tau_s": tau, "value": tau * mdev * 1e9 / math.sqrt(3.0), "pairs": len(modified_terms), "confidence": min(0.99, 1.0 - 1.0 / math.sqrt(len(modified_terms) + 1))}
                )
            third = [
                samples[index + 3 * factor]
                - 3.0 * samples[index + 2 * factor]
                + 3.0 * samples[index + factor]
                - samples[index]
                for index in range(len(samples) - 3 * factor)
            ]
            if third:
                hdev = math.sqrt(sum(value * value for value in third) / (6.0 * len(third))) * 1e-9 / tau
                result["hdev"].append(
                    {"tau_s": tau, "value": hdev, "pairs": len(third), "confidence": min(0.99, 1.0 - 1.0 / math.sqrt(len(third) + 1))}
                )
        windows = rolling_ranges(samples, factor + 1)
        if windows:
            result["mtie"].append(
                {"tau_s": tau, "value": max(windows), "pairs": len(windows), "confidence": None}
            )
        # Theo1 is useful at long tau because it uses phase pairs distributed
        # across each 2m window instead of discarding most of the record.
        if factor >= 2 and len(samples) > 2 * factor:
            terms: list[float] = []
            inner_step = max(1, math.ceil(factor / 64))
            inner_indices = list(range(0, factor, inner_step))
            for start in range(len(samples) - 2 * factor):
                for inner in inner_indices:
                    weight = 1.0 / max(0.5, factor - inner - 0.5)
                    delta = (
                        samples[start]
                        - samples[start + inner + 1]
                        + samples[start + 2 * factor]
                        - samples[start + 2 * factor - inner - 1]
                    )
                    terms.append(weight * delta * delta)
            if terms:
                theo1 = math.sqrt(sum(terms) / (0.75 * (len(samples) - 2 * factor) * len(inner_indices)))
                result["theo1"].append(
                    {"tau_s": tau, "value": theo1, "pairs": len(terms), "confidence": min(0.99, 1.0 - 1.0 / math.sqrt(len(terms) + 1))}
                )
    return result


@dataclass(frozen=True)
class Observation:
    left: str
    right: str
    difference_ns: float
    sigma_ns: float
    source: str


def factor_graph_fusion(nodes: Sequence[str], observations: Sequence[Observation], reference: str) -> dict[str, Any]:
    """Weighted least-squares fusion for direct, hop, PPS, and PTP offsets."""
    unknown = [node for node in nodes if node != reference]
    index = {node: position for position, node in enumerate(unknown)}
    size = len(unknown)
    if not size:
        return {"reference": reference, "nodes": {reference: {"offset_ns": 0.0, "sigma_ns": 0.0}}, "residuals": []}
    normal = [[0.0 for _ in range(size)] for _ in range(size)]
    target = [0.0 for _ in range(size)]
    used: list[Observation] = []
    for observation in observations:
        if observation.left not in nodes or observation.right not in nodes:
            continue
        sigma = max(0.01, abs(observation.sigma_ns))
        weight = 1.0 / (sigma * sigma)
        row = [0.0 for _ in range(size)]
        if observation.right != reference:
            row[index[observation.right]] += 1.0
        if observation.left != reference:
            row[index[observation.left]] -= 1.0
        if not any(row):
            continue
        for left in range(size):
            target[left] += weight * row[left] * observation.difference_ns
            for right in range(size):
                normal[left][right] += weight * row[left] * row[right]
        used.append(observation)
    if not used:
        return {"reference": reference, "nodes": {}, "residuals": [], "status": "waiting"}
    try:
        covariance = inverse(normal, ridge=1e-9)
        estimates = matrix_vector(covariance, target)
    except ValueError:
        return {"reference": reference, "nodes": {}, "residuals": [], "status": "rank-deficient"}
    values = {reference: 0.0, **{node: estimates[position] for node, position in index.items()}}
    residuals = [
        {
            "source": observation.source,
            "edge": f"{observation.left}→{observation.right}",
            "residual_ns": values[observation.right] - values[observation.left] - observation.difference_ns,
            "normalized": (
                values[observation.right] - values[observation.left] - observation.difference_ns
            ) / max(observation.sigma_ns, 0.01),
        }
        for observation in used
    ]
    return {
        "reference": reference,
        "nodes": {
            node: {
                "offset_ns": values[node],
                "sigma_ns": 0.0 if node == reference else math.sqrt(max(0.0, covariance[index[node]][index[node]])),
            }
            for node in nodes
        },
        "residuals": residuals,
        "chi_square": sum(item["normalized"] ** 2 for item in residuals),
        "degrees_of_freedom": max(0, len(used) - size),
        "status": "solved",
    }


class AdaptiveKalman3:
    """Three-state phase/frequency/drift estimator with adaptive measurement noise."""

    def __init__(
        self,
        measurement_noise_ns: float = 200.0,
        process_noise_ppb_s: float = 2.0,
        drift_noise_ppb_s2: float = 0.05,
        innovation_gate_sigma: float = 6.0,
    ) -> None:
        self.state = [0.0, 0.0, 0.0]
        self.covariance = [
            [measurement_noise_ns**2 * 100.0, 0.0, 0.0],
            [0.0, max(625.0, process_noise_ppb_s**2 * 16.0), 0.0],
            [0.0, 0.0, max(1.0, drift_noise_ppb_s2**2 * 100.0)],
        ]
        self.base_measurement_variance = measurement_noise_ns**2
        self.measurement_variance = measurement_noise_ns**2
        self.process_noise = process_noise_ppb_s**2
        self.drift_noise = drift_noise_ppb_s2**2
        self.gate = innovation_gate_sigma
        self.last_time: float | None = None
        self.samples = 0
        self.accepted = 0
        self.rejected = 0
        self.consecutive_rejections = 0

    def update(self, measurement_ns: float, timestamp: float, applied_correction_ppb: float = 0.0) -> dict[str, Any]:
        if self.last_time is None:
            self.state[0] = measurement_ns
            dt = 0.0
        else:
            dt = timestamp - self.last_time
            if not 0.0001 <= dt <= 120.0:
                self.last_time = timestamp
                return self.snapshot(measurement_ns, 0.0, False, "invalid-interval", dt)
            transition = [
                [1.0, dt, 0.5 * dt * dt],
                [0.0, 1.0, dt],
                [0.0, 0.0, 1.0],
            ]
            self.state = matrix_vector(transition, self.state)
            self.state[0] -= applied_correction_ppb * dt
            process = [
                [self.process_noise * dt**3 / 3.0, self.process_noise * dt**2 / 2.0, 0.0],
                [self.process_noise * dt**2 / 2.0, self.process_noise * dt, 0.0],
                [0.0, 0.0, self.drift_noise * dt],
            ]
            predicted = matrix_multiply(matrix_multiply(transition, self.covariance), transpose(transition))
            self.covariance = [
                [predicted[row][column] + process[row][column] for column in range(3)]
                for row in range(3)
            ]
        self.last_time = timestamp
        innovation = measurement_ns - self.state[0]
        innovation_variance = self.covariance[0][0] + self.measurement_variance
        sigma = math.sqrt(max(EPSILON, innovation_variance))
        accepted = self.samples < 3 or abs(innovation) <= self.gate * sigma
        self.samples += 1
        if not accepted:
            self.rejected += 1
            self.consecutive_rejections += 1
            self.measurement_variance = min(
                self.base_measurement_variance * 100.0,
                0.97 * self.measurement_variance + 0.03 * innovation * innovation,
            )
            # A hard innovation gate protects against isolated timestamp
            # spikes, but a real frequency step can otherwise leave the
            # estimator permanently outside its own gate. After three
            # consecutive misses, re-anchor phase with inflated uncertainty;
            # the bounded controller then reacquires without stepping time.
            if self.consecutive_rejections >= 3:
                self.state[0] = measurement_ns
                self.covariance[0][0] = max(
                    self.covariance[0][0],
                    self.measurement_variance * 4.0,
                )
                self.covariance[1][1] = max(
                    self.covariance[1][1],
                    self.process_noise * 64.0,
                )
                self.covariance[2][2] = max(
                    self.covariance[2][2],
                    self.drift_noise * 64.0,
                )
                self.consecutive_rejections = 0
                return self.snapshot(measurement_ns, innovation, True, "reacquiring", dt)
            return self.snapshot(measurement_ns, innovation, False, "innovation-gated", dt)
        gain = [self.covariance[row][0] / innovation_variance for row in range(3)]
        prior = [row[:] for row in self.covariance]
        self.state = [value + gain[index] * innovation for index, value in enumerate(self.state)]
        self.covariance = [
            [prior[row][column] - gain[row] * prior[0][column] for column in range(3)]
            for row in range(3)
        ]
        self.measurement_variance = max(
            self.base_measurement_variance * 0.1,
            min(self.base_measurement_variance * 100.0, 0.97 * self.measurement_variance + 0.03 * innovation * innovation),
        )
        self.accepted += 1
        self.consecutive_rejections = 0
        return self.snapshot(measurement_ns, innovation, True, "locked" if self.accepted >= 5 else "acquiring", dt)

    def snapshot(self, measurement: float, innovation: float, accepted: bool, state: str, dt: float) -> dict[str, Any]:
        return {
            "state": state,
            "measurement_ns": measurement,
            "measurement_accepted": accepted,
            "phase_estimate_ns": self.state[0],
            "frequency_estimate_ppb": self.state[1],
            "drift_estimate_ppb_s": self.state[2],
            "phase_sigma_ns": math.sqrt(max(0.0, self.covariance[0][0])),
            "frequency_sigma_ppb": math.sqrt(max(0.0, self.covariance[1][1])),
            "drift_sigma_ppb_s": math.sqrt(max(0.0, self.covariance[2][2])),
            "innovation_ns": innovation,
            "adaptive_measurement_noise_ns": math.sqrt(self.measurement_variance),
            "sample_interval_s": dt,
            "sample_count": self.samples,
            "accepted_count": self.accepted,
            "rejected_count": self.rejected,
        }


class InteractingMultipleModel:
    """Quiet/dynamic/holdover IMM around three adaptive Kalman models."""

    def __init__(self, measurement_noise_ns: float = 200.0) -> None:
        self.models = [
            AdaptiveKalman3(measurement_noise_ns, 0.4, 0.01),
            AdaptiveKalman3(measurement_noise_ns, 6.0, 0.2),
            AdaptiveKalman3(measurement_noise_ns * 2.0, 1.5, 0.05),
        ]
        self.names = ["quiet", "dynamic", "holdover"]
        self.probabilities = [0.72, 0.23, 0.05]
        self.transition = [
            [0.97, 0.025, 0.005],
            [0.08, 0.90, 0.02],
            [0.03, 0.02, 0.95],
        ]

    def update(self, measurement_ns: float, timestamp: float, applied_correction_ppb: float = 0.0, holdover: bool = False) -> dict[str, Any]:
        prior = [
            sum(self.probabilities[source] * self.transition[source][target] for source in range(3))
            for target in range(3)
        ]
        snapshots = [
            model.update(measurement_ns, timestamp, applied_correction_ppb)
            for model in self.models
        ]
        likelihoods = []
        for snapshot in snapshots:
            sigma = max(0.1, float(snapshot["adaptive_measurement_noise_ns"]))
            normalized = float(snapshot["innovation_ns"]) / sigma
            likelihoods.append(math.exp(-0.5 * min(80.0, normalized * normalized)) / sigma)
        if holdover:
            likelihoods[2] *= 50.0
        evidence = sum(probability * likelihood for probability, likelihood in zip(prior, likelihoods))
        self.probabilities = [
            probability * likelihood / max(EPSILON, evidence)
            for probability, likelihood in zip(prior, likelihoods)
        ]
        phase = sum(probability * float(snapshot["phase_estimate_ns"]) for probability, snapshot in zip(self.probabilities, snapshots))
        frequency = sum(probability * float(snapshot["frequency_estimate_ppb"]) for probability, snapshot in zip(self.probabilities, snapshots))
        drift = sum(probability * float(snapshot["drift_estimate_ppb_s"]) for probability, snapshot in zip(self.probabilities, snapshots))
        active = max(range(3), key=self.probabilities.__getitem__)
        return {
            **snapshots[active],
            "phase_estimate_ns": phase,
            "frequency_estimate_ppb": frequency,
            "drift_estimate_ppb_s": drift,
            "regime": self.names[active],
            "model_probabilities": dict(zip(self.names, self.probabilities)),
        }


def temperature_holdover_model(
    timestamps: Sequence[float],
    phase_ns: Sequence[float],
    temperatures_c: Sequence[float],
    horizon_s: float = 300.0,
) -> dict[str, Any]:
    length = min(len(timestamps), len(phase_ns), len(temperatures_c))
    if length < 8:
        return {"status": "learning", "samples": length}
    times = [value - timestamps[-length] for value in timestamps[-length:]]
    phases = list(phase_ns[-length:])
    temperatures = list(temperatures_c[-length:])
    rows = []
    rates = []
    for index in range(1, length):
        dt = max(1e-6, times[index] - times[index - 1])
        temperature_rate = (temperatures[index] - temperatures[index - 1]) / dt
        rows.append([1.0, temperatures[index], temperatures[index] ** 2, temperature_rate])
        rates.append((phases[index] - phases[index - 1]) / dt)
    coefficients = least_squares(rows, rates, ridge=1e-5)
    predictions = [sum(coefficient * value for coefficient, value in zip(coefficients, row)) for row in rows]
    residual_sigma = math.sqrt(variance([actual - predicted for actual, predicted in zip(rates, predictions)]))
    current_temperature = temperatures[-1]
    current_rate = rows[-1][-1]
    predicted_frequency = (
        coefficients[0]
        + coefficients[1] * current_temperature
        + coefficients[2] * current_temperature**2
        + coefficients[3] * current_rate
    )
    return {
        "status": "ready",
        "samples": length,
        "temperature_c": current_temperature,
        "temperature_rate_c_s": current_rate,
        "predicted_frequency_ppb": predicted_frequency,
        "predicted_phase_ns": phases[-1] + predicted_frequency * horizon_s,
        "horizon_s": horizon_s,
        "one_sigma_ns": residual_sigma * math.sqrt(max(1.0, horizon_s)),
        "coefficients": coefficients,
    }


def identify_arx(input_values: Sequence[float], output_values: Sequence[float], sample_period_s: float) -> dict[str, Any]:
    length = min(len(input_values), len(output_values))
    if length < 12:
        return {"status": "learning", "samples": length}
    inputs = list(input_values[-length:])
    outputs = list(output_values[-length:])
    rows = [
        [outputs[index - 1], outputs[index - 2], inputs[index - 1], inputs[index - 2], 1.0]
        for index in range(2, length)
    ]
    target = outputs[2:]
    coefficients = least_squares(rows, target, ridge=1e-6)
    a1, a2, b1, b2, bias = coefficients
    discriminant = complex(a1 * a1 + 4.0 * a2, 0.0) ** 0.5
    poles = [(a1 + discriminant) / 2.0, (a1 - discriminant) / 2.0]
    spectral_radius = max(abs(pole) for pole in poles)
    settling = math.inf if spectral_radius >= 1.0 else -4.0 * sample_period_s / math.log(max(EPSILON, spectral_radius))
    fitted = [sum(coefficient * value for coefficient, value in zip(coefficients, row)) for row in rows]
    residuals = [actual - predicted for actual, predicted in zip(target, fitted)]
    total = sum((value - mean(target)) ** 2 for value in target)
    r_squared = 1.0 - sum(value * value for value in residuals) / max(EPSILON, total)
    return {
        "status": "stable" if spectral_radius < 1.0 else "unstable",
        "samples": length,
        "coefficients": {"a1": a1, "a2": a2, "b1": b1, "b2": b2, "bias": bias},
        "poles": [{"real": pole.real, "imag": pole.imag, "magnitude": abs(pole)} for pole in poles],
        "spectral_radius": spectral_radius,
        "settling_time_s": settling if math.isfinite(settling) else None,
        "dc_gain": (b1 + b2) / max(EPSILON, 1.0 - a1 - a2),
        "r_squared": r_squared,
        "residual_sigma_ns": math.sqrt(variance(residuals)),
    }


def safe_bayesian_tune(
    phase_ns: Sequence[float],
    sample_period_s: float,
    current_kp: float,
    current_ki: float,
) -> dict[str, Any]:
    """Replay-safe constrained tuning over a deterministic PI candidate set.

    An RBF Gaussian process ranks the candidate surface after an initial space-
    filling set. Every score comes from replay; no live gain is changed here.
    """
    samples = list(phase_ns)
    if len(samples) < 20:
        return {"status": "learning", "samples": len(samples)}
    candidates = [(kp / 10.0, ki / 20.0) for kp in range(2, 13, 2) for ki in range(1, 17, 3)]

    def replay(kp: float, ki: float) -> dict[str, float | bool]:
        correction = 0.0
        integral = 0.0
        errors: list[float] = []
        peak = 0.0
        for measurement in samples:
            residual = measurement - correction
            integral = max(-200_000.0, min(200_000.0, integral + residual * sample_period_s))
            correction += (kp * residual + ki * integral) * min(1.0, sample_period_s)
            errors.append(residual)
            peak = max(peak, abs(residual))
        rms = math.sqrt(mean([value * value for value in errors]))
        tail = math.sqrt(mean([value * value for value in errors[-max(5, len(errors) // 5):]]))
        stable = math.isfinite(rms) and peak <= max(20_000.0, 8.0 * percentile([abs(value) for value in samples], 0.95))
        score = rms + 0.35 * tail + 0.02 * peak
        return {"rms_ns": rms, "tail_rms_ns": tail, "peak_ns": peak, "score": score, "safe": stable}

    # Start with a space-filling design, then spend a bounded replay budget on
    # expected-improvement selections from a small RBF Gaussian process.  This
    # makes the recommendation reproducible and keeps candidate exploration
    # off the live clock.
    seed_indices = sorted({
        0,
        len(candidates) - 1,
        len(candidates) // 2,
        len(candidates) // 3,
        2 * len(candidates) // 3,
        min(range(len(candidates)), key=lambda index: (candidates[index][0] - current_kp) ** 2 + (candidates[index][1] - current_ki) ** 2),
    })
    evaluations = [
        {**{"kp": candidates[index][0], "ki": candidates[index][1]}, **replay(*candidates[index])}
        for index in seed_indices
    ]
    evaluated = set(seed_indices)
    replay_budget = min(20, len(candidates))

    def normalized(candidate: tuple[float, float]) -> tuple[float, float]:
        return candidate[0] / 1.2, candidate[1] / 0.8

    def kernel(left: tuple[float, float], right: tuple[float, float], length_scale: float = 0.34) -> float:
        distance_squared = sum((a - b) ** 2 for a, b in zip(normalized(left), normalized(right)))
        return math.exp(-0.5 * distance_squared / (length_scale * length_scale))

    while len(evaluations) < replay_budget and len(evaluated) < len(candidates):
        observed_candidates = [(float(item["kp"]), float(item["ki"])) for item in evaluations]
        observed_scores = [float(item["score"]) for item in evaluations]
        score_center = mean(observed_scores)
        score_scale = max(EPSILON, math.sqrt(variance(observed_scores)))
        normalized_scores = [(score - score_center) / score_scale for score in observed_scores]
        covariance = [
            [
                kernel(left, right) + (1e-6 if row == column else 0.0)
                for column, right in enumerate(observed_candidates)
            ]
            for row, left in enumerate(observed_candidates)
        ]
        try:
            precision = inverse(covariance)
            alpha = matrix_vector(precision, normalized_scores)
        except ValueError:
            break
        incumbent = min(normalized_scores)
        acquisition: list[tuple[float, int]] = []
        for index, candidate in enumerate(candidates):
            if index in evaluated:
                continue
            covariance_vector = [kernel(candidate, observed) for observed in observed_candidates]
            predicted_mean = sum(value * coefficient for value, coefficient in zip(covariance_vector, alpha))
            projected = matrix_vector(precision, covariance_vector)
            predicted_variance = max(1e-9, 1.0 - sum(value * mapped for value, mapped in zip(covariance_vector, projected)))
            predicted_sigma = math.sqrt(predicted_variance)
            improvement = incumbent - predicted_mean
            z_score = improvement / predicted_sigma
            normal_pdf = math.exp(-0.5 * z_score * z_score) / math.sqrt(2.0 * math.pi)
            normal_cdf = 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))
            expected_improvement = improvement * normal_cdf + predicted_sigma * normal_pdf
            acquisition.append((expected_improvement, index))
        if not acquisition:
            break
        next_index = max(acquisition)[1]
        evaluated.add(next_index)
        kp_value, ki_value = candidates[next_index]
        evaluations.append({**{"kp": kp_value, "ki": ki_value}, **replay(kp_value, ki_value)})

    safe = [item for item in evaluations if item["safe"]]
    if not safe:
        return {"status": "no-safe-candidate", "samples": len(samples), "evaluations": evaluations}
    best = min(safe, key=lambda item: float(item["score"]))
    current = replay(current_kp, current_ki)
    return {
        "status": "recommended",
        "samples": len(samples),
        "method": "constrained replay + RBF Gaussian-process expected improvement",
        "recommendation": best,
        "baseline": {"kp": current_kp, "ki": current_ki, **current},
        "predicted_improvement_pct": max(0.0, 100.0 * (float(current["score"]) - float(best["score"])) / max(EPSILON, float(current["score"]))),
        "safe_candidates": len(safe),
        "evaluated_candidates": len(evaluations),
        "candidate_space": len(candidates),
        "live_changes": 0,
        "frontier": sorted(safe, key=lambda item: float(item["score"]))[:8],
    }


def bayesian_change_points(values: Sequence[float], hazard: float = 1 / 120.0, max_run: int = 256) -> dict[str, Any]:
    """Bounded Bayesian online change-point detection with a Gaussian model."""
    samples = [float(value) for value in values if math.isfinite(value)]
    if len(samples) < 4:
        return {"probabilities": [], "change_points": [], "status": "learning"}
    differences = [samples[index] - samples[index - 1] for index in range(1, len(samples))]
    scale = max(1e-3, 1.4826 * median_absolute_deviation(differences) / math.sqrt(2.0))
    run_probabilities = [1.0]
    means = [samples[0]]
    counts = [1]
    probabilities: list[float] = [1.0]
    changes: list[int] = []
    for index, value in enumerate(samples[1:], 1):
        likelihoods = []
        for run_index, run_mean in enumerate(means):
            predictive_sigma = scale * math.sqrt(1.0 + 1.0 / max(1, counts[run_index]))
            normalized = (value - run_mean) / predictive_sigma
            likelihoods.append(math.exp(-0.5 * min(80.0, normalized * normalized)) / predictive_sigma)
        growth = [run_probabilities[run] * likelihoods[run] * (1.0 - hazard) for run in range(len(run_probabilities))]
        # A new run starts from a deliberately broad, heavy-tailed base prior.
        # Reusing each existing run's likelihood here would algebraically pin
        # the posterior change probability to the hazard at every sample.
        base_likelihood = 1.0 / max(1.0, 20.0 * scale)
        change = sum(run_probabilities) * hazard * base_likelihood
        updated = [change, *growth][:max_run]
        total = sum(updated)
        updated = [probability / max(EPSILON, total) for probability in updated]
        next_means = [value]
        next_counts = [1]
        for run_index in range(min(len(means), max_run - 1)):
            count = counts[run_index] + 1
            next_means.append(means[run_index] + (value - means[run_index]) / count)
            next_counts.append(count)
        run_probabilities, means, counts = updated, next_means, next_counts
        probabilities.append(updated[0])
        if updated[0] > max(0.35, hazard * 20.0):
            changes.append(index)
    return {
        "probabilities": probabilities,
        "change_points": changes,
        "latest_probability": probabilities[-1],
        "status": "change" if changes and changes[-1] >= len(samples) - 3 else "stable",
    }


def recurrence_analysis(channels: Sequence[Sequence[float]], max_points: int = 96) -> dict[str, Any]:
    if not channels:
        return {"status": "waiting", "matrix": []}
    length = min(len(channel) for channel in channels)
    if length < 8:
        return {"status": "learning", "matrix": [], "samples": length}
    point_count = min(length, max_points)
    indices = [
        round(index * (length - 1) / max(1, point_count - 1))
        for index in range(point_count)
    ]
    vectors = [[float(channel[index]) for channel in channels] for index in indices]
    centers = [mean([vector[channel] for vector in vectors]) for channel in range(len(channels))]
    scales = [
        max(1e-6, math.sqrt(variance([vector[channel] for vector in vectors])))
        for channel in range(len(channels))
    ]
    normalized = [
        [(value - centers[channel]) / scales[channel] for channel, value in enumerate(vector)]
        for vector in vectors
    ]
    distances = [
        math.sqrt(sum((left - right) ** 2 for left, right in zip(normalized[row], normalized[column])))
        for row in range(len(normalized))
        for column in range(row)
    ]
    threshold = percentile(distances, 0.12)
    matrix = [
        "".join(
            "1" if math.sqrt(sum((left - right) ** 2 for left, right in zip(normalized[row], normalized[column]))) <= threshold else "0"
            for column in range(len(normalized))
        )
        for row in range(len(normalized))
    ]
    recurrent = sum(character == "1" for row in matrix for character in row)
    diagonal_points = 0
    diagonal_lines = 0
    size = len(matrix)
    for offset in range(-size + 1, size):
        run = 0
        for row in range(size):
            column = row + offset
            if 0 <= column < size and matrix[row][column] == "1":
                run += 1
            else:
                if run >= 2:
                    diagonal_points += run
                    diagonal_lines += 1
                run = 0
        if run >= 2:
            diagonal_points += run
            diagonal_lines += 1
    return {
        "status": "ready",
        "matrix": matrix,
        "samples": size,
        "threshold_sigma": threshold,
        "recurrence_rate": recurrent / max(1, size * size),
        "determinism": diagonal_points / max(1, recurrent),
        "diagonal_lines": diagonal_lines,
    }


def replay_bifurcation_analysis(
    phase_ns: Sequence[float],
    sample_period_s: float,
    current_kp: float,
    current_ki: float,
    parameter_steps: int = 46,
    active_controller: str = "pi",
) -> dict[str, Any]:
    """Sweep a PI gain multiplier through a captured endpoint phase record.

    The result is a model-based *bifurcation-style* diagram, not a claim that a
    physical bifurcation was observed.  Each parameter column contains extrema
    from the settled tail of an offline replay.  No candidate is applied to a
    PHC and the response remains explicitly distinguishable from a controlled
    hardware sweep.
    """
    samples = [float(value) for value in phase_ns if math.isfinite(value)][-384:]
    if len(samples) < 32:
        return {
            "status": "learning",
            "samples": len(samples),
            "points": [],
            "summaries": [],
            "live_changes": 0,
        }
    if abs(current_kp) + abs(current_ki) < EPSILON:
        return {
            "status": "unavailable",
            "samples": len(samples),
            "points": [],
            "summaries": [],
            "reason": "The configured PI gains are both zero.",
            "live_changes": 0,
        }

    center = statistics.median(samples)
    forcing = [value - center for value in samples]
    forcing_envelope = max(1.0, percentile([abs(value) for value in forcing], 0.95))
    hard_limit = max(20_000.0, forcing_envelope * 12.0)
    steps = max(12, min(80, int(parameter_steps)))
    gain_scales = [0.25 + index * (2.5 - 0.25) / (steps - 1) for index in range(steps)]
    points: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for gain_scale in gain_scales:
        correction = 0.0
        integral = 0.0
        settled: list[float] = []
        peak = 0.0
        divergent = False
        # Repeating the measured record lets initial controller state decay
        # before the final pass is sampled at a consistent forcing phase.
        for replay_pass in range(4):
            for measurement in forcing:
                residual = measurement - correction
                if not math.isfinite(residual) or abs(residual) > hard_limit * 8.0:
                    divergent = True
                    break
                integral = max(
                    -200_000.0,
                    min(200_000.0, integral + residual * sample_period_s),
                )
                correction += (
                    gain_scale * (current_kp * residual + current_ki * integral)
                    * min(1.0, sample_period_s)
                )
                peak = max(peak, abs(residual))
                if replay_pass == 3:
                    settled.append(residual)
            if divergent:
                break

        tail_length = max(16, min(96, len(settled) // 3))
        tail = settled[-tail_length:] if settled else []
        tail_rms = math.sqrt(mean([value * value for value in tail])) if tail else math.inf
        stable = (
            not divergent
            and math.isfinite(tail_rms)
            and peak <= hard_limit
            and tail_rms <= max(4.0 * forcing_envelope, 500.0)
        )
        extrema = [
            tail[index]
            for index in range(1, len(tail) - 1)
            if (tail[index] - tail[index - 1]) * (tail[index + 1] - tail[index]) <= 0.0
            and (
                abs(tail[index] - tail[index - 1])
                + abs(tail[index + 1] - tail[index])
            ) >= max(0.05, forcing_envelope * 0.002)
        ]
        if len(extrema) < 3 and tail:
            extrema = [
                tail[round(index * (len(tail) - 1) / 7)]
                for index in range(8)
            ]
        if len(extrema) > 20:
            extrema = [
                extrema[round(index * (len(extrema) - 1) / 19)]
                for index in range(20)
            ]

        if divergent:
            extrema = [-hard_limit, hard_limit]
        ordered = sorted(extrema)
        branch_tolerance = max(
            0.5,
            (percentile(ordered, 0.95) - percentile(ordered, 0.05)) * 0.055,
        )
        branches: list[float] = []
        for value in ordered:
            if not branches or abs(value - branches[-1]) > branch_tolerance:
                branches.append(value)
            else:
                branches[-1] = (branches[-1] + value) / 2.0
        branch_count = min(16, len(branches))
        regime = (
            "divergent"
            if not stable
            else "single-band"
            if branch_count <= 2
            else "multi-band"
            if branch_count <= 8
            else "broadband"
        )
        for branch_index, value in enumerate(extrema):
            points.append(
                {
                    "gain_scale": gain_scale,
                    "residual_ns": max(-hard_limit, min(hard_limit, value)),
                    "stable": stable,
                    "regime": regime,
                    "branch": branch_index,
                    "clipped": divergent or abs(value) >= hard_limit,
                }
            )
        summaries.append(
            {
                "gain_scale": gain_scale,
                "kp": current_kp * gain_scale,
                "ki": current_ki * gain_scale,
                "stable": stable,
                "regime": regime,
                "branch_count": branch_count,
                "tail_rms_ns": tail_rms if math.isfinite(tail_rms) else None,
                "peak_ns": peak if math.isfinite(peak) else None,
            }
        )

    first_transition = next(
        (item["gain_scale"] for item in summaries if not item["stable"]),
        None,
    )
    stable_through = None
    for item in summaries:
        if not item["stable"]:
            break
        stable_through = item["gain_scale"]
    current = min(summaries, key=lambda item: abs(item["gain_scale"] - 1.0))
    finite_values = [
        abs(float(point["residual_ns"]))
        for point in points
        if not point["clipped"]
    ]
    display_limit = max(
        25.0,
        forcing_envelope * 1.25,
        percentile(finite_values, 0.99) * 1.08 if finite_values else 0.0,
    )
    display_limit = min(hard_limit, display_limit)
    return {
        "status": "ready",
        "samples": len(samples),
        "parameter": "PI gain scale",
        "parameter_min": gain_scales[0],
        "parameter_max": gain_scales[-1],
        "current_gain_scale": 1.0,
        "base_gains": {"kp": current_kp, "ki": current_ki},
        "active_controller": active_controller,
        "baseline_is_live": active_controller == "pi",
        "points": points,
        "summaries": summaries,
        "current": current,
        "stable_through_gain": stable_through,
        "first_transition_gain": first_transition,
        "display_limit_ns": display_limit,
        "forcing_envelope_ns": forcing_envelope,
        "method": "settled extrema from bounded offline PI replay",
        "provenance": "captured endpoint PHC phase; centered and replayed without writing a clock",
        "interpretation": (
            "A response-branch screening map. A true hardware bifurcation "
            "requires a controlled gain sweep with settled observations at every step."
        ),
        "live_changes": 0,
    }


def koopman_dmd(channels: Sequence[Sequence[float]]) -> dict[str, Any]:
    if not channels:
        return {"status": "waiting"}
    length = min(len(channel) for channel in channels)
    if length < max(8, len(channels) + 2):
        return {"status": "learning", "samples": length}
    centered = []
    for channel in channels:
        values = list(channel[-length:])
        channel_mean = mean(values)
        centered.append([value - channel_mean for value in values])
    left = [channel[:-1] for channel in centered]
    right = [channel[1:] for channel in centered]
    gram = matrix_multiply(left, transpose(left))
    cross = matrix_multiply(right, transpose(left))
    operator = matrix_multiply(cross, inverse(gram, ridge=1e-6))
    # Singular values of A (sqrt eigenvalues of A'A) are robust even when the
    # real-valued operator has complex conjugate eigenpairs.
    singular = [math.sqrt(max(0.0, value)) for value in symmetric_eigenvalues(matrix_multiply(transpose(operator), operator))]
    one_step = [
        matrix_vector(operator, [channel[index] for channel in left])
        for index in range(length - 1)
    ]
    residuals = [
        right[channel][index] - one_step[index][channel]
        for index in range(length - 1)
        for channel in range(len(channels))
    ]
    return {
        "status": "ready",
        "samples": length,
        "operator": operator,
        "singular_values": singular,
        "spectral_norm": singular[0] if singular else 0.0,
        "residual_sigma_ns": math.sqrt(variance(residuals)),
        "interpretation": "contracting" if singular and singular[0] < 1.0 else "amplifying",
    }


def ensemble_clock(ids: Sequence[str], channels: Sequence[Sequence[float]]) -> dict[str, Any]:
    length = min((len(channel) for channel in channels), default=0)
    if not ids or length < 4 or len(ids) != len(channels):
        return {"status": "learning", "samples": length}
    covariance = covariance_matrix([channel[-length:] for channel in channels], shrinkage=0.12)
    try:
        precision = inverse(covariance, ridge=1e-6)
        raw = matrix_vector(precision, [1.0] * len(ids))
        total = sum(raw)
        weights = [max(0.0, value / max(EPSILON, total)) for value in raw]
        normalized_total = sum(weights)
        weights = [value / max(EPSILON, normalized_total) for value in weights]
    except ValueError:
        weights = [1.0 / len(ids)] * len(ids)
    current = [channel[-1] for channel in channels]
    virtual_offset = sum(weight * value for weight, value in zip(weights, current))
    return {
        "status": "ready",
        "samples": length,
        "virtual_offset_ns": virtual_offset,
        "weights": dict(zip(ids, weights)),
        "one_sigma_ns": math.sqrt(max(0.0, sum(weights[row] * covariance[row][column] * weights[column] for row in range(len(ids)) for column in range(len(ids))))),
    }


def error_budget(
    node_ids: Sequence[str],
    direct_uncertainty: dict[str, float],
    servo_rms: dict[str, float],
    path_jitter: dict[str, float],
    holdover_sigma: dict[str, float],
    hop_channels: Sequence[Sequence[float]] = (),
) -> dict[str, Any]:
    nodes: dict[str, Any] = {}
    for node in node_ids:
        components = {
            "cross_timestamp": max(0.0, direct_uncertainty.get(node, 0.0)),
            "servo": max(0.0, servo_rms.get(node, 0.0)),
            "path": max(0.0, path_jitter.get(node, 0.0)),
            "holdover": max(0.0, holdover_sigma.get(node, 0.0)),
        }
        squared = {name: value * value for name, value in components.items()}
        total_squared = sum(squared.values())
        nodes[node] = {
            "rss_ns": math.sqrt(total_squared),
            "components_ns": components,
            "contribution_pct": {
                name: 100.0 * value / max(EPSILON, total_squared)
                for name, value in squared.items()
            },
        }
    cascade: dict[str, Any] | None = None
    if hop_channels:
        aligned = min((len(channel) for channel in hop_channels), default=0)
        if aligned >= 3:
            covariance = covariance_matrix(
                [list(channel[-aligned:]) for channel in hop_channels],
                shrinkage=0.08,
            )
            independent_variance = sum(covariance[index][index] for index in range(len(covariance)))
            correlated_variance = sum(sum(row) for row in covariance)
            cascade = {
                "hop_count": len(covariance),
                "samples": aligned,
                "independent_sigma_ns": math.sqrt(max(0.0, independent_variance)),
                "correlated_sigma_ns": math.sqrt(max(0.0, correlated_variance)),
                "cross_covariance_ns2": correlated_variance - independent_variance,
                "covariance_ns2": covariance,
            }
    return {
        "nodes": nodes,
        "cascade": cascade,
        "method": "component RSS per clock; covariance propagation across measured hop errors",
    }


class ExperimentStore:
    """SQLite/WAL recorder for raw samples, events, configuration, and results."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    stopped_at REAL,
                    metadata_json TEXT NOT NULL,
                    config_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS samples (
                    experiment_id TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    cycle_id TEXT NOT NULL,
                    clock_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    offset_ns REAL,
                    hop_offset_ns REAL,
                    uncertainty_ns REAL,
                    path_delay_ns REAL,
                    frequency_ppb REAL,
                    temperature_c REAL,
                    valid INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS samples_experiment_time
                    ON samples(experiment_id, observed_at);
                CREATE TABLE IF NOT EXISTS events (
                    experiment_id TEXT,
                    observed_at REAL NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def active(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE state='running' ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def start(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        identifier = f"run-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(now))}"
        name = str(payload.get("name") or f"{str(payload.get('kind') or payload.get('type') or 'capture').title()} {identifier[-6:]}")
        kind = str(payload.get("kind") or payload.get("type") or "capture")
        metadata = {key: value for key, value in payload.items() if key not in {"name", "kind", "type"}}
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE experiments SET state='completed', stopped_at=? WHERE state='running'",
                (now,),
            )
            connection.execute(
                "INSERT INTO experiments VALUES (?, ?, ?, 'running', ?, NULL, ?, ?)",
                (identifier, name[:120], kind[:64], now, json.dumps(metadata), json.dumps(config)),
            )
        return self.get(identifier) or {}

    def stop(self, identifier: str | None = None) -> dict[str, Any] | None:
        active = self.active() if identifier is None else self.get(identifier)
        if not active:
            return None
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE experiments SET state='completed', stopped_at=? WHERE id=? AND state='running'",
                (now, active["id"]),
            )
        return self.get(str(active["id"]))

    def get(self, identifier: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT e.*,
                       (SELECT COUNT(*) FROM samples s WHERE s.experiment_id=e.id) AS sample_count,
                       (SELECT COUNT(*) FROM events v WHERE v.experiment_id=e.id) AS event_count
                FROM experiments e WHERE e.id=?
                """,
                (identifier,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        item["config"] = json.loads(item.pop("config_json"))
        return item

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM experiments ORDER BY started_at DESC LIMIT ?",
                (max(1, min(200, limit)),),
            ).fetchall()
        return [item for row in rows if (item := self.get(str(row["id"]))) is not None]

    def record_phc(self, sample: dict[str, Any], temperatures: dict[str, float] | None = None) -> None:
        active = self.active()
        if not active:
            return
        temperatures = temperatures or {}
        rows = [
            (
                active["id"],
                float(clock["observed_at"]),
                str(sample["sample_id"]),
                str(clock["id"]),
                "phc-cross-timestamp",
                clock.get("offset_ns"),
                clock.get("previous_hop_offset_ns"),
                clock.get("comparison_uncertainty_ns"),
                None,
                None,
                temperatures.get(str(clock["id"])),
                1 if clock.get("valid") else 0,
                json.dumps(clock, separators=(",", ":")),
            )
            for clock in sample.get("clocks", [])
        ]
        with self._lock, self._connect() as connection:
            connection.executemany(
                "INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def event(self, category: str, severity: str, message: str, payload: dict[str, Any] | None = None) -> None:
        active = self.active()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    active["id"] if active else None,
                    time.time(),
                    category[:64],
                    severity[:32],
                    message[:500],
                    json.dumps(payload or {}, separators=(",", ":")),
                ),
            )

    def export_csv(self, identifier: str) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "experiment_id",
                "observed_at",
                "cycle_id",
                "clock_id",
                "source",
                "offset_ns",
                "hop_offset_ns",
                "uncertainty_ns",
                "path_delay_ns",
                "frequency_ppb",
                "temperature_c",
                "valid",
            ]
        )
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT experiment_id, observed_at, cycle_id, clock_id, source,
                       offset_ns, hop_offset_ns, uncertainty_ns, path_delay_ns,
                       frequency_ppb, temperature_c, valid
                FROM samples WHERE experiment_id=? ORDER BY observed_at, clock_id
                """,
                (identifier,),
            )
            writer.writerows(rows)
        return output.getvalue()


class RollingResearchEngine:
    """Incremental analysis cache shared by all Observatory research views."""

    def __init__(self, max_samples: int = 7200) -> None:
        self.samples: deque[dict[str, Any]] = deque(maxlen=max_samples)
        self.temperatures: deque[tuple[float, dict[str, float]]] = deque(maxlen=max_samples)
        self._lock = threading.Lock()

    def add(self, sample: dict[str, Any], temperatures: dict[str, float] | None = None) -> None:
        with self._lock:
            self.samples.append(sample)
            self.temperatures.append((float(sample.get("observed_at", time.time())), temperatures or {}))

    def snapshot(
        self,
        telemetry_clocks: Sequence[dict[str, Any]],
        sample_rate_hz: float,
        kp: float,
        ki: float,
        active_controller: str = "pi",
    ) -> dict[str, Any]:
        with self._lock:
            samples = list(self.samples)
            temperatures = list(self.temperatures)
        node_ids = [str(clock.get("id")) for clock in telemetry_clocks]
        if not node_ids and samples:
            node_ids = [str(clock["id"]) for clock in samples[-1].get("clocks", [])]
        series: dict[str, list[float]] = {node: [] for node in node_ids}
        uncertainty: dict[str, list[float]] = {node: [] for node in node_ids}
        hop_series: dict[str, list[float]] = {node: [] for node in node_ids[1:]}
        timestamps: list[float] = []
        for sample in samples:
            by_id = {str(clock["id"]): clock for clock in sample.get("clocks", [])}
            if not all(node in by_id and by_id[node].get("valid") and by_id[node].get("offset_ns") is not None for node in node_ids):
                continue
            timestamps.append(float(sample["observed_at"]))
            for node in node_ids:
                series[node].append(float(by_id[node]["offset_ns"]))
                uncertainty[node].append(float(by_id[node].get("comparison_uncertainty_ns") or 0.0))
                if node in hop_series and by_id[node].get("previous_hop_offset_ns") is not None:
                    hop_series[node].append(float(by_id[node]["previous_hop_offset_ns"]))
        period = 1.0 / max(0.01, sample_rate_hz)
        endpoint = node_ids[-1] if node_ids else None
        endpoint_series = series.get(endpoint, []) if endpoint else []
        hop_channels = [hop_series[node] for node in node_ids[1:] if hop_series.get(node)]
        aligned_hop_length = min((len(channel) for channel in hop_channels), default=0)
        hop_channels = [channel[-aligned_hop_length:] for channel in hop_channels] if aligned_hop_length else []
        stability = stability_metrics(endpoint_series, period)
        changes = bayesian_change_points(endpoint_series)
        recurrence = recurrence_analysis(hop_channels)
        bifurcation = replay_bifurcation_analysis(
            endpoint_series,
            period,
            kp,
            ki,
            active_controller=active_controller,
        )
        koopman = koopman_dmd(hop_channels)
        ensemble_ids = [node for node in node_ids[1:] if series.get(node)]
        ensemble_channels = [series[node] for node in ensemble_ids]
        aligned_ensemble_length = min((len(channel) for channel in ensemble_channels), default=0)
        ensemble_channels = [channel[-aligned_ensemble_length:] for channel in ensemble_channels] if aligned_ensemble_length else []
        ensemble = ensemble_clock(ensemble_ids, ensemble_channels)
        auto_tune = safe_bayesian_tune(endpoint_series, period, kp, ki)
        inputs = []
        outputs = []
        endpoint_clock = next((clock for clock in telemetry_clocks if str(clock.get("id")) == endpoint), None)
        if endpoint_clock:
            ptp_samples = [
                sample for sample in endpoint_clock.get("samples", [])
                if sample.get("valid") and sample.get("frequency_ppb") is not None and sample.get("offset_ns") is not None
            ]
            inputs = [float(sample["frequency_ppb"]) for sample in ptp_samples]
            outputs = [float(sample["offset_ns"]) for sample in ptp_samples]
        system_id = identify_arx(inputs, outputs, period)
        observations: list[Observation] = []
        if samples and node_ids:
            latest = {str(clock["id"]): clock for clock in samples[-1].get("clocks", [])}
            for node in node_ids[1:]:
                clock = latest.get(node)
                if clock and clock.get("valid") and clock.get("offset_ns") is not None:
                    observations.append(
                        Observation(
                            node_ids[0],
                            node,
                            float(clock["offset_ns"]),
                            max(0.1, float(clock.get("comparison_uncertainty_ns") or 1.0)),
                            f"PHC {clock.get('cross_timestamp_method') or 'cross timestamp'}",
                        )
                    )
            for index, node in enumerate(node_ids[1:], 1):
                clock = latest.get(node)
                if clock and clock.get("previous_hop_offset_ns") is not None:
                    observations.append(
                        Observation(
                            node_ids[index - 1],
                            node,
                            float(clock["previous_hop_offset_ns"]),
                            max(0.2, float(clock.get("comparison_uncertainty_ns") or 2.0) * math.sqrt(2.0)),
                            "adjacent PHC factor",
                        )
                    )
        servo_rms = {
            str(clock.get("id")): float(clock.get("rms_ns") or 0.0)
            for clock in telemetry_clocks
        }
        path_jitter = {}
        for clock in telemetry_clocks:
            delays = [
                float(sample["mean_path_delay_ns"])
                for sample in clock.get("samples", [])
                if sample.get("valid") and sample.get("mean_path_delay_ns") is not None
            ]
            path_jitter[str(clock.get("id"))] = math.sqrt(variance(delays)) if delays else 0.0
        direct_uncertainty = {
            node: statistics.median(values) if values else 0.0
            for node, values in uncertainty.items()
        }
        holdover_models = {}
        for node in node_ids:
            node_temperatures = [
                float(values[node])
                for _timestamp, values in temperatures
                if node in values
            ]
            if node_temperatures and series.get(node):
                length = min(len(node_temperatures), len(series[node]), len(timestamps))
                holdover_models[node] = temperature_holdover_model(
                    timestamps[-length:],
                    series[node][-length:],
                    node_temperatures[-length:],
                )
        holdover_sigma = {
            node: float(model.get("one_sigma_ns") or 0.0)
            for node, model in holdover_models.items()
        }
        return {
            "generated_at": time.time(),
            "sample_count": len(samples),
            "aligned_sample_count": len(timestamps),
            "sample_rate_hz": sample_rate_hz,
            "endpoint": endpoint,
            "stability": stability,
            "fusion": factor_graph_fusion(node_ids, observations, node_ids[0]) if node_ids else {"status": "waiting"},
            "ensemble": ensemble,
            "change_detection": changes,
            "recurrence": recurrence,
            "bifurcation": bifurcation,
            "koopman": koopman,
            "system_identification": system_id,
            "auto_tune": auto_tune,
            "temperature_holdover": holdover_models,
            "error_budget": error_budget(
                node_ids,
                direct_uncertainty,
                servo_rms,
                path_jitter,
                holdover_sigma,
                hop_channels,
            ),
            "provenance": {
                "phase": "raw kernel PHC cross timestamps",
                "servo": "raw LinuxPTP log samples",
                "smoothing": "none; rolling statistics are explicitly windowed",
            },
        }
