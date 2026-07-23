# Experiment guide

PTPBox is designed for repeatable comparisons, not just attractive live traces.
An experiment should capture topology, software versions, message profile,
servo parameters, stimulus, duration, and analysis window together.

## Recommended baseline

Before injecting a disturbance:

1. let every clock report stable lock for at least five minutes;
2. record NIC firmware, driver, LinuxPTP version, and PHC mapping;
3. capture 120 seconds of undisturbed offset;
4. save the active PTP profile and PI constants;
5. use the same physical cabling and link rates for every comparison.

## Built-in recipes

### Servo step response

Apply a discrete phase step at one boundary clock and measure:

- rise and settling time;
- overshoot and ringing;
- downstream amplification;
- lock-state transitions;
- recovery of RMS and MTIE.

The default design uses +1 μs at BC3 with 10 seconds of pre-trigger and 110
seconds post-trigger.

### Low-frequency wander

Introduce a slow phase or frequency modulation to test integral tracking and
downstream correlation. Use a capture long enough to include several complete
cycles; 20 minutes is a useful starting point.

### Holdover recovery

Use the dedicated **Holdover chamber** for a controlled servo-release trial.
It restores the selected nodes' saved servos, requires a continuous stable
dwell, zeroes each clock against the median of its final qualified PHC window,
then changes only adjustment to LinuxPTP free-running mode. PTP traffic, direct
PHC monitoring, and the durable raw recorder remain live.

During the free run compare:

- current, RMS, and peak absolute wander from the release baseline;
- time-error slope in ns/s, numerically equal to fractional-frequency error in
  ppb;
- per-node divergence and downstream amplification;
- direct-comparison uncertainty beside the observed wander;
- elapsed time to an operational time-error limit;
- reacquisition after the exact previous servo selection is restored.

The default recipe automatically resumes synchronization at the declared
duration. Use **Resume synchronization** to end early, or **Abort** during
qualification. A broken lock, stale PHC sample, or release-gate excursion resets
the dwell rather than releasing a partially synchronized chain.

### Gain sweep

Run a matrix of proportional and integral constants with an identical stimulus.
Compare settling time, overshoot, steady-state RMS, and worst downstream MTIE.

The Intelligence workbench can rank a bounded PI grid without changing live
hardware. It replays the current capture, evaluates safe seed points, fits an
RBF Gaussian-process surrogate, and selects additional candidates with expected
improvement. The winning gain pair is only staged for review. The run result
records `live_changes: 0`.

### Regime-transition trial

Use the one-hop netem chamber to add a bounded delay, jitter, or loss condition
for 30–120 seconds. Compare:

- Bayesian online change probability and detection latency;
- IMM quiet/dynamic/holdover mode probabilities;
- path-event continuity and apparent directional residual;
- factor-graph residuals and covariance-aware cascade uncertainty;
- acquisition and recovery behavior after the fault expires.

The controller removes the qdisc on expiry, explicit clear, or cascade stop.
Record the exact target, impairment, and duration in the run metadata.

### Thermal holdover

Let the system collect a long locked baseline with temperature sensors present,
enter clock-servo holdover without stopping observation, and compare the
temperature-conditioned phase forecast with the measured direct PHC drift.
Treat a missing sensor as missing data; do not use a temperature inferred from
servo behavior.

## Servo parameters

| Parameter | Effect | Trade-off |
| --- | --- | --- |
| `Kp` | Immediate frequency correction from phase error | Faster response can increase overshoot/noise sensitivity |
| `Ki` | Accumulated correction for persistent error | Strong drift rejection can reduce phase margin |
| `step_threshold` | Selects step versus slew during normal operation | Steps settle quickly but break phase continuity |
| `first_step_threshold` | Allows a larger correction during initial lock | Too low can prolong acquisition |
| `sanity_freq_limit` | Rejects implausible frequency corrections | Too tight can reject legitimate acquisition transients |

## Analysis windows

- **Current offset:** operational state, not a stability statistic.
- **RMS:** useful for comparing noise energy over an identical window.
- **P95 / peak:** captures excursions that RMS can hide.
- **MTIE:** bounds peak-to-peak time-error growth across observation intervals.
- **TDEV:** separates time stability across averaging intervals.
- **ADEV / MDEV / HDEV:** characterize fractional-frequency noise with
  different sensitivity to phase modulation, white phase noise, and linear
  frequency drift.
- **Theo1:** extends the useful long-τ region of a finite phase record.

The Metrology workbench computes all six families at power-of-two averaging
intervals and reports the usable-pair count. TDEV/MTIE/Theo1 retain nanosecond
units; ADEV/MDEV/HDEV are dimensionless fractional-frequency deviations.

## Result naming

Use a compact name that captures the manipulated variable:

```text
2026-07-17_step-bc3_kp070_ki030_run01
```

Keep raw logs immutable. Put derived CSV/plots beside them with a suffix such as
`_summary`, `_mtie`, or `_filtered`.

The built-in run recorder preserves raw PHC comparison rows and the complete
applied configuration in `runtime/experiments.sqlite3`. Export the run from the
Metrology ledger before moving it to a long-term dataset:

```text
GET /api/experiments/run-YYYYMMDD-HHMMSS/export
```

## Comparing runs

Change one independent variable at a time. If a cable, firmware, message rate,
or link speed changes, treat it as a new experiment family. Report the endpoint
metric and the per-hop contribution; an apparently improved OC result can hide
instability that moved upstream.
