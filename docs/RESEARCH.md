# Research and algorithm guide

PTPBox is both a synchronization controller and a measurement instrument. The
two roles are deliberately separated:

- `ptp4l` or a selected PTPBox worker disciplines a receiving PHC;
- the PHC sampler, path collectors, PPS comparator, run recorder, and research
  engine observe without changing a clock;
- replay optimization produces a recommendation, never an exploratory live
  control action.

This guide records what each advanced instrument computes, which raw data it
uses, and what conclusions it cannot support.

## Data provenance first

| Source | Acquisition | Clock-changing? | Main consumers |
| --- | --- | --- | --- |
| Direct PHC comparison | `PTP_SYS_OFFSET_EXTENDED` against `CLOCK_MONOTONIC_RAW`, shortest of 9 brackets, BC1 interpolated to each target epoch | No | Overview, metrology, covariance, state space, experiments |
| LinuxPTP servo log | Native signed master offset, path delay, frequency adjustment, and state | Only the separately selected servo controls the PHC | Servo RMS, Kalman observation, holdover, system ID |
| LinuxPTP event monitor | Management TLVs containing `t1`/`t2`/`t3`/`t4`, correction fields, and sequence IDs | No | Path microscope |
| Common PPS edge | PHC EXTS event timestamps from one externally fanned PPS | No; comparison mode is forced `measure_only` | Independent PHC comparison, factor graph |
| Hardware status | DPLL generic netlink, devlink JSON, hwmon, PHC sysfs | No | Resilience and thermal holdover |

Large timestamp integers are kept as integer arithmetic in the agent and as
decimal strings in JSON where JavaScript binary floating point could lose a
nanosecond.

## Stability metrology

The engine accepts equally spaced phase-error samples \(x_k\) in nanoseconds
and evaluates power-of-two averaging factors. It returns:

- overlapping Allan deviation (ADEV);
- modified Allan deviation (MDEV);
- time deviation (TDEV);
- Hadamard deviation (HDEV);
- maximum time interval error (MTIE);
- Theo1 for improved use of a finite record at long averaging time.

ADEV, MDEV, and HDEV are dimensionless fractional-frequency deviations.
TDEV, MTIE, and Theo1 are returned in nanoseconds. Every result includes its
averaging interval and usable-pair count. The UI does not draw unsupported
long-τ points.

The implementation follows the definitions and reporting discipline in
[NIST SP 1065](https://www.nist.gov/publications/handbook-frequency-stability-analysis).
It is an online diagnostic implementation, not a replacement for a calibrated
metrology package when formal uncertainty accreditation is required.

## Redundant clock fusion

Each observation is expressed as a difference constraint:

\[
z_{ij} = x_j - x_i + v_{ij}, \qquad
w_{ij} = \frac{1}{\sigma_{ij}^2}
\]

The factor-graph workbench anchors BC1, builds the weighted normal equations,
and solves for every other clock offset. Inputs may include direct BC1
cross-timestamp comparisons, adjacent-hop deltas, LinuxPTP observations, and a
common-edge PPS comparison. The result includes:

- fused offset and marginal 1σ per clock;
- residual and normalized residual per factor;
- χ² and degrees of freedom;
- explicit `waiting` or `rank-deficient` states.

The graph exposes contradictory measurements; it does not hide them with a
visual average and it never drives a PHC.

## Ensemble time and correlated error

The diagnostic ensemble estimates a shrinkage covariance matrix \(C\) and
starts from inverse-covariance weights:

\[
w \propto C^{-1}\mathbf{1}
\]

Negative weights are clipped, the remainder is normalized, and a ridge protects
the small live matrix from singularity. This is a robust virtual comparison
reference inspired by weighted clock ensembles such as
[NIST AT1](https://www.nist.gov/pml/time-and-frequency-division/time-services/utcnist-time-scale/how-utcnist-works).
It is not advertised as UTC and is not installed as the cascade grandmaster.

For the cascade error budget, per-clock cross-timestamp, servo, path, and
holdover terms are combined by root-sum-square. Adjacent-hop error is also
propagated through its complete covariance:

\[
\sigma_\text{cascade}^2 = \mathbf{1}^{T} C_\text{hop}\mathbf{1}
\]

The UI reports this correlated result beside the independent assumption
\(\sqrt{\mathrm{trace}(C_\text{hop})}\). Their difference shows whether common
motion reinforces or cancels downstream error.

## Servo family

### Native LinuxPTP modes

- `pi`: the standard proportional-integral controller;
- `linreg`: LinuxPTP's adaptive linear-regression controller;
- `nullf`: zero frequency correction for a separately syntonized diagnostic
  setup.

Their definitions and configuration boundaries come from the
[LinuxPTP configuration reference](https://www.linuxptp.org/documentation/default/).

### Classic Kalman

The classic estimator uses phase and fractional-frequency state. LinuxPTP stays
in `free_running` mode to provide hardware-timestamped observations without
competing for the PHC. The worker gates implausible innovations and applies a
bounded frequency correction through `clock_adjtime`.

### Adaptive three-state Kalman

The adaptive model uses:

\[
\mathbf{x} =
\begin{bmatrix}
\text{phase ns} & \text{frequency ppb} & \text{drift ppb/s}
\end{bmatrix}^{T}
\]

The transition integrates frequency into phase and drift into frequency.
Innovation energy adapts the measurement covariance. A single large innovation
is rejected; a persistent phase transition causes a controlled re-anchor after
three consecutive rejects so the estimator cannot permanently starve itself.

### Interacting multiple model

IMM maintains three adaptive filters:

- quiet oscillator;
- dynamic/acquiring oscillator;
- holdover oscillator.

A Markov transition matrix mixes the prior states. Each model's innovation
likelihood updates its posterior probability, and the combined estimate is a
probability-weighted state. The displayed mode is an estimator regime, not a
substitute for LinuxPTP port state or a DPLL hardware lock indication.

## Holdover prediction

When aligned temperature samples exist, the engine fits a regularized model
over phase, frequency, drift, temperature, and temperature rate. It projects a
configurable horizon and reports predicted phase, frequency, and 1σ residual
uncertainty. With no hardware temperature sensor it returns `learning` or
`unavailable`; it does not infer temperature from clock behavior.

Holdover itself is real: the selected servo is stopped while LinuxPTP packet
reception and direct PHC monitoring continue.

## System identification

The ARX instrument fits a regularized discrete-time autoregressive model with a
control input derived from measured frequency correction. It reports:

- coefficients and poles;
- spectral radius;
- residual standard deviation;
- \(R^2\);
- an estimated settling time when the fitted poles are stable.

This is a local empirical model of the captured operating regime. It is useful
for comparing controller settings but does not constitute a formal robust
stability proof.

## Replay-safe Bayesian tuning

The tuner evaluates a bounded grid of PI gains against captured offset data. A
candidate is eligible only when replay stays within peak and stability
constraints. After safe seed evaluations, an RBF Gaussian process models the
score and expected improvement selects additional candidates.

The response includes the baseline, recommendation, safe frontier, evaluated
count, predicted improvement, and:

```json
{ "live_changes": 0 }
```

The button in the UI stages the recommendation for review. Applying it remains
a separate guarded operator action. The GP/EI design follows the framework in
[Snoek, Larochelle, and Adams](https://papers.nips.cc/paper_files/paper/2012/hash/05311655a15b75fab86956663e1819cd-Abstract.html).

## Regime and nonlinear-dynamics diagnostics

### Bayesian online change-point detection

A bounded run-length posterior tracks the probability that the generating
regime changed at the newest sample. The implementation uses a broad base prior,
a Gaussian observation model, and a configurable hazard. It follows
[Adams and MacKay](https://arxiv.org/abs/0710.3742). A high probability is
evidence of a transition, not an automatic statement of root cause.

### Recurrence quantification

Aligned hop-change vectors are normalized per channel. The recurrence threshold
is selected from their pairwise distance distribution. The engine returns the
binary matrix, recurrence rate, diagonal-line count, and determinism. Diagonal
structure can indicate repeatable evolution; it is not proof of chaos or a
deterministic attractor.

### Replay bifurcation map

The bifurcation workbench uses PI gain scale \(g\) as its continuation
parameter. It evaluates 46 evenly spaced values from \(g=0.25\) to \(g=2.50\)
against the captured endpoint PHC phase record. At each value, the replay:

1. centers the raw endpoint phase with a robust median;
2. runs four passes through the same captured forcing record;
3. retains only the final settled tail;
4. extracts local extrema as response branches; and
5. marks candidates that exceed the bounded replay envelope.

Both configured gains scale together:

\[
K_p(g)=gK_p,\qquad K_i(g)=gK_i
\]

The response ordinate is settled replay phase residual in nanoseconds. The
result includes every plotted point, per-gain branch count, tail RMS, peak,
regime, the first replay-bound crossing, base gains, active endpoint controller,
whether the 1.00× PI baseline is actually live, sample count, method, and
provenance. It always includes:

```json
{ "live_changes": 0 }
```

This is a model-based response-branch screening diagram, not evidence that the
hardware crossed a mathematical bifurcation. Bifurcation means a qualitative
change in dynamics as a system parameter varies; a physical claim therefore
requires a controlled on-hardware gain sweep, sufficient dwell at each gain,
and settled measurements. The distinction follows the standard definition in
[Guckenheimer](https://doi.org/10.4249/scholarpedia.1517). The original
recurrence plot remains beside the sweep because recurrence can reveal
transitions while still not proving a deterministic attractor.

### Koopman / dynamic mode decomposition

For centered snapshots \(X\) and their one-step successors \(X'\), the engine
fits:

\[
A = X'X^{T}(XX^{T} + \lambda I)^{-1}
\]

It reports the operator, singular-value amplification spectrum, and one-step
residual. This follows the snapshot philosophy introduced by
[Schmid](https://doi.org/10.1017/S0022112010001217). The plotted singular values
describe the fitted local map; they are not controller gain or phase margins.

## Packet-path interpretation

For an end-to-end exchange:

\[
d_f^\text{apparent} = t_2 - t_1,\qquad
d_r^\text{apparent} = t_4 - t_3
\]

With transmitter clock \(A\), receiver clock \(B\), phase offset \(\theta\),
forward delay \(d_f\), and reverse delay \(d_r\):

\[
(t_2-t_1)-(t_4-t_3) = 2\theta + d_f-d_r
\]

Therefore the event monitor can show an apparent directional residual but
cannot independently estimate one-way path asymmetry. A common PPS edge, a
calibrated external time interval counter, or another independent phase
reference is needed to separate those terms.

## Resilience boundaries

- Profile presets check only implemented configuration fields. They return
  `certification: false`.
- DPLL and SyncE appear only when the Linux kernel exposes their state through
  the DPLL subsystem; PTP `s2` lock is never reused as a proxy.
- Authentication references a root-owned LinuxPTP Security Association file.
  Keys are not transported through the web API.
- Netem faults resolve the interface from the installed topology, affect one
  namespace egress, and always have an expiry.
- PPS common-edge mode is forced to `measure_only` so it cannot compete with a
  `ts2phc` discipline loop.

See the [architecture](ARCHITECTURE.md), [API](API.md), and
[experiment guide](EXPERIMENTS.md) for implementation and operating details.
