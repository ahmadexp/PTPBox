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
- an estimated settling time when the fitted poles are stable;
- Bode magnitude and unwrapped phase below the sampling Nyquist frequency;
- the positive- and negative-frequency Nyquist trajectory;
- exact second-order Jury/Schur conditions for the fitted digital denominator;
- a Routh–Hurwitz table for the bilinear-mapped continuous equivalent.

The identified relationship is

\[
y[k]=a_1y[k-1]+a_2y[k-2]+b_1u[k-1]+b_2u[k-2]+c
\]

and the plotted transfer function is

\[
G(z)=\frac{b_1z^{-1}+b_2z^{-2}}
{1-a_1z^{-1}-a_2z^{-2}}.
\]

Here \(u\) is the measured servo frequency correction and \(y\) is the raw PHC
phase offset. The Bode view is therefore useful for resolved bandwidth,
resonance, attenuation, and phase-lag comparisons between captured operating
regimes. The Nyquist view shows the same complex response and its closest
approach to \(-1\), but labels that point as a geometric reference only.
Operational closed-loop data does not, by itself, identify the complete
open-loop transfer \(L(z)\), so PTPBox does not report an encirclement verdict,
gain margin, or phase margin from this model.

For the fitted denominator \(P(z)=z^2-a_1z-a_2\), the primary sampled-data
verdict evaluates the exact second-order Jury conditions:

\[
P(1)>0,\quad P(-1)>0,\quad |a_2|<1.
\]

The UI also maps the denominator through
\(z=(1+sT/2)/(1-sT/2)\). The resulting continuous polynomial is

\[
\frac{T^2}{4}(1+a_1-a_2)s^2 + T(1+a_2)s + (1-a_1-a_2),
\]

which supplies the displayed Routh array. This is an interpretable cross-check,
not a replacement for the direct digital pole/Jury result. MIT OpenCourseWare
provides concise references for [frequency-response and Nyquist
analysis](https://ocw.mit.edu/courses/16-06-principles-of-automatic-control-fall-2012/pages/lecture-notes/)
and the [Routh–Hurwitz
criterion](https://ocw.mit.edu/courses/2-004-dynamics-and-control-ii-spring-2008/resources/lecture_25/);
Notre Dame's linear-systems text documents the
[Jury test for discrete systems](https://www3.nd.edu/~lemmon/courses/linear-systems/lecture-book/linsys-book-2024.pdf).

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

### Attractor reconstruction and evidence gates

The Attractor Observatory starts from one scalar observable: the raw endpoint
PHC offset relative to BC1. It uses at most the newest 384 finite samples,
centers and standardizes them, and reconstructs delay vectors

\[
\mathbf{x}_i =
[x_i,x_{i-\tau},\ldots,x_{i-(m-1)\tau}].
\]

The delay \(\tau\) is the first local minimum of average mutual information over
the bounded available lag range. If no finite-record minimum exists, the engine
uses the first autocorrelation crossing below \(1/e\), or the available lag
with the smallest absolute autocorrelation. This implements the reconstruction
criterion of [Fraser and Swinney](https://doi.org/10.1103/PhysRevA.33.1134)
within the finite live record.

Embedding dimension is selected with the false-nearest-neighbor test of
[Kennel, Brown, and Abarbanel](https://doi.org/10.1103/PhysRevA.45.3403).
For \(m=1..5\), each point's nearest temporally separated neighbor is examined
after adding the next delayed coordinate. A neighbor is false when either the
new-coordinate distance ratio exceeds 10 or the expanded distance exceeds
twice the scalar signal standard deviation. Temporally adjacent candidates are
excluded with a Theiler window. The smallest \(m\geq2\) below 5% is selected;
10% is the looser evidence gate reported to the UI.

The displayed orbit is the first two coordinates, with the third coordinate
retained as depth metadata. An 18×18 occupancy grid locates separated local
density maxima; visits within a 0.72σ reconstructed-state radius define
**recurrent-core candidates** and their coverage. Successive local maxima of the
observable provide the return pairs \((x_n,x_{n+1})\).

The local-divergence curve follows the small-record approach of
[Rosenstein, Collins, and De Luca](https://doi.org/10.1016/0167-2789(93)90009-P):
each state is paired with its closest temporally separated neighbor, the mean
log separation is followed forward, and the best bounded early-time positive
linear segment is reported with its slope, fit interval, pair count, and
\(R^2\). This is a finite-record local-divergence estimate, not an asserted
asymptotic Lyapunov exponent.

The UI shows “candidate attractor” only when five independently inspectable
conditions all hold:

1. the selected embedding has at most 10% false nearest neighbors;
2. one or more recurrent cores cover at least 8% of reconstructed states;
3. the separate Grassberger–Procaccia dimension estimate converges across its
   final three embeddings; and
4. the early-time divergence slope is positive with \(R^2\geq0.8\).
5. Bayesian online change detection reports a stationary window with newest
   change probability below 0.2.

Anything weaker is labeled reconstruction ready, recurrent structure, or
inconclusive. Takens' embedding theorem motivates the reconstruction
([original chapter](https://doi.org/10.1007/BFb0091924)); finite noisy PHC
records do not satisfy every theorem assumption, so the label deliberately
remains a candidate. The analysis performs no interpolation, never adjusts a
clock, and always reports `live_changes: 0`.

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

### Fractal scaling diagnostics

PTPBox reports three different estimators because “fractal dimension” is not one
interchangeable scalar.

#### Grassberger–Procaccia correlation dimension

The endpoint phase is standardized and reconstructed with delay coordinates at
embedding dimensions \(m=2,3,4,5\). The delay is the first autocorrelation
crossing below \(1/e\), or the smallest available absolute autocorrelation when
no crossing exists. Temporally adjacent vector pairs are excluded with a
Theiler window of twice that delay.

For each embedding, the correlation sum is evaluated over 20 logarithmically
spaced radii:

\[
C_m(r)=\frac{1}{N_\mathrm{pairs}}\sum_{i<j}
\mathbf{1}\left(\lVert x_i-x_j\rVert\leq r\right)
\]

PTPBox searches contiguous finite-data scaling intervals with
\(0.015\leq C_m(r)\leq0.8\), at least five radii, a physically admissible
positive slope, and high linear-fit quality. The local slope

\[
D_2 \approx \frac{d\log C_m(r)}{d\log r}
\]

is reported with \(R^2\), the selected radius range, usable pair count, delay,
Theiler window, and all four embedding estimates. `converged` is true only when
the last three estimates stabilize within the explicit tolerance. This follows
the correlation-dimension method introduced by
[Grassberger and Procaccia](https://doi.org/10.1103/PhysRevLett.50.346).

#### Higuchi graph dimension

Higuchi curve lengths are calculated for integer intervals from \(k=1\) through
\(\min(48,N/4)\). A linear fit of \(\log L(k)\) against \(\log(1/k)\) yields
the endpoint trace dimension \(D_H\), along with \(R^2\), every plotted length,
and the fitted range. This is the roughness dimension of the sampled
phase-versus-index graph—not the dimension of a reconstructed attractor. The
implementation follows [Higuchi's original method](https://www.ism.ac.jp/~higuchi/index_e/papers/PhysicaD-1988.pdf).

#### Multifractal detrended fluctuation analysis

MF-DFA integrates centered endpoint phase, divides it into forward and backward
segments at ten logarithmically spaced scales, removes a linear trend per
segment, and evaluates \(q=-4,-2,0,2,4\):

\[
F_q(s)\sim s^{h(q)}
\]

The reported width is
\(\Delta h=\max h(q)-\min h(q)\). PTPBox repeats the analysis on six
deterministically shuffled surrogates, which preserve the value distribution
but destroy temporal ordering. The result includes observed width, mean
surrogate width, their signed difference, every \(h(q)\), scale points, and fit
quality. This follows
[Kantelhardt et al.](https://doi.org/10.1016/S0378-4371(02)01383-3).

Minimum records are 32 samples for Higuchi, 64 for correlation dimension, and
128 for MF-DFA. All three use at most the newest 1024 raw endpoint samples,
perform no interpolation, and never write a clock. Finite records, noise,
periodic forcing, nonstationarity, scaling-window choice, and estimator bias can
all produce non-integer values. The Observatory therefore never equates these
estimates with proof of chaos, exact self-similarity, or a strange attractor.

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
