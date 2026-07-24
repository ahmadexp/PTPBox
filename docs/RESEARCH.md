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

- overlapping Allan deviation (ADEV), the general two-sample stability view;
- modified Allan deviation (MDEV), which can distinguish white from flicker
  phase modulation through its averaging response;
- overlapping Hadamard deviation (HDEV), which rejects linear frequency drift;
- parabolic deviation (PDEV), a least-squares/parabolic-counter statistic with
  improved short-\(\tau\) use of phase samples;
- total deviation (TOTDEV), using endpoint reflection to improve long-\(\tau\)
  use of a finite record;
- Theo1, for improved confidence and reach at long averaging times;
- time deviation (TDEV), derived from MDEV;
- maximum time interval error (MTIE);
- RMS time interval error (TIE RMS).

ADEV, MDEV, HDEV, PDEV, TOTDEV, and Theo1 are dimensionless
fractional-frequency deviations. TDEV, MTIE, and TIE RMS are returned in
nanoseconds. Theo1 is evaluated only for even \(m \ge 10\) and is plotted at
the NIST effective averaging time \(\tau=0.75m\tau_0\). Every result includes
its averaging interval and usable-term count. The UI does not draw unsupported
long-\(\tau\) points.

The workbench also reports record span, linear-detrended phase RMS,
peak-to-peak phase, least-squares fractional-frequency bias, frequency drift,
minimum ADEV, and local MDEV log-slope noise candidates. A slope label is a
diagnostic candidate, not proof of one power-law noise process. Confidence
intervals are deliberately absent: a defensible interval requires the
noise-dependent equivalent degrees of freedom, so PTPBox does not substitute a
pair-count heuristic.

The implementation follows the definitions and reporting discipline in
[NIST SP 1065](https://www.nist.gov/publications/handbook-frequency-stability-analysis)
and the terminology standardized by
[IEEE 1139-2022](https://standards.ieee.org/ieee/1139/7585/). PDEV follows the
parabolic-variance definition introduced by Vernotte, Lenczner, Bourgeois, and
Rubiola in [*The Parabolic Variance (PVAR), a Wavelet Variance Based on the
Least-Square Fit*](https://members.femto-st.fr/sites/femto-st.fr.michel-lenczner/files/content/papers/VerLen2015-2.pdf).
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

## Dynamic cascade metrology

### Clock stability versus transfer stability

The rolling atlas evaluates clock ADEV/MDEV and first-difference FTU/ADEVS in
overlapping windows. This preserves time localization: a lock transition, path
regime, or oscillator warm-up is not silently averaged into one stationary
curve.

For a residual phase record \(r_k\), PTPBox reports:

\[
\mathrm{TIE}_{\mathrm{rms}}(\tau)=
\sqrt{\left\langle(r_{k+m}-r_k)^2\right\rangle},
\qquad
\mathrm{FTU}(\tau)=\frac{\mathrm{TIE}_{\mathrm{rms}}(\tau)}
{\tau\cdot10^9}
\]

and applies the two-sample Allan equation to \(m\)-sample averages of \(r_k\)
for ADEVS, retaining nanosecond units. ADEVS is sensitive to a linear trend
that ordinary phase-derived ADEV rejects.

These names do not qualify the observable. Direct BC1-to-endpoint PHC
difference combines oscillator and path behavior. Because adjacent-hop PHC
differences come from those same BC1 cross timestamps, their sum telescopes
exactly to the endpoint; subtracting it would create an algebraic zero rather
than an independently measured closure. `qualified_residual` therefore remains
false until a loopback, common-edge, calibrated PPS/TIC, or another independent
residual is supplied.

### Cross-spectral cascade modes

Aligned adjacent-hop channels are linearly detrended and analyzed with
overlapping Hann-window Welch cross spectra. At each frequency bin the engine
returns:

- per-hop PSD, phase, adjacent-hop magnitude-squared coherence, incremental
  gain, and cumulative gain;
- the dominant eigenvector of the Hermitian cross-spectral matrix and its share
  of total spatial power; and
- four log-frequency coherent bands with average spatial loadings and energy
  shares.

This decomposition is inspired by multiresolution coherent spatio-temporal
analysis but is not presented as the reference mrCOSTS implementation. Passive
gain above 0 dB means the recorded cascade amplified motion at that frequency;
`formal_string_stability` remains false without an independent input and
coherence-qualified input/output estimate.

### Servo, estimator, and observability diagnostics

The hybrid-state view derives the empirical transition matrix, state shares,
dwell distributions, offset/correction RMS, and a local first-order phase pole
for acquisition, lock, holdover, and unknown states. For a Kalman-family servo,
the consistency screen reports scalar normalized innovation squared (NIS),
the fraction inside the 95% \(\chi^2_1\) gate, lag-one innovation
autocorrelation, and accepted-update share.

Separately, the normalized ARX information-matrix eigenvalues expose rank,
condition number, and input variance. A fitted model may look stable while its
parameters remain weakly observable; the UI therefore shows fit and
identifiability as separate evidence.

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

### Active closed-loop identification and robust-control screens

Operational correction and phase data are correlated through feedback, so a
direct output/input ratio is biased. The active instrument adds a deterministic
random-phase multisine *after* a selected PTPBox Kalman-family servo. Each
sample records the independent excitation \(d\), base controller correction,
actual applied correction \(u\), and PHC phase observation \(y\).

The controller enforces:

- 0.1–500 ppb composite peak correction;
- 30–900 second duration;
- one to eight tones from 0.002 Hz to 45% of the configured sample rate;
- a 100–100,000 ns raw-offset abort limit; and
- automatic expiry or immediate operator stop.

Instrumental cross spectra estimate the plant as
\(\hat G=S_{yd}/S_{ud}\), avoiding ordinary closed-loop correlation bias at
excited frequencies. The separately observed controller contribution yields
\(\hat C\), \(\hat L=\hat G\hat C\), and:

\[
S=\frac{1}{1+L},\qquad T=\frac{L}{1+L},\qquad KS=\frac{C}{1+L}.
\]

PTPBox publishes these curves, the identified Nyquist locus, empirical
segment-to-segment plant scatter, a balanced disk-margin screen, and a
frequency-dependent multiplicative uncertainty/IQC-style separation test only
at bins passing both excitation-to-input and excitation-to-output coherence
gates. `low-evidence` is a valid result; the UI does not promote a margin from
unexcited bins.

## Holdover reachability and independent clock attribution

The reachability screen fits phase, frequency, and frequency drift to the
recent endpoint record, then propagates residual-derived phase and frequency
dispersion into a 95% forecast tube. For ±100 ns, ±1 µs, and ±10 µs masks it
reports the first horizon where the modeled violation probability reaches 5%.
The calibration state remains `unvalidated-live-forecast` until repeated
holdover trials demonstrate empirical coverage.

N-cornered analysis solves the pairwise variance system
\(\sigma_{ij}^{2}\approx\sigma_i^2+\sigma_j^2\) with non-negative estimates.
It assumes negligible inter-clock correlation, so PTPBox gates its
interpretation while the cascade clocks discipline one another. The result
becomes eligible only in independently free-running holdover or with a
qualified common-edge comparison.

## Timing OAM and path regimes

Timing OAM reports constant time error (mean TE), dynamic time-error RMS,
P95/max absolute TE, peak-to-peak TE, and measured hop-by-hop cTE accumulation.
Reference masks are operator thresholds rather than automatic profile
certification.

The event monitor emits Sync and Delay records separately. The regime analyzer
pairs the nearest opposite-direction record for the same node within five
seconds, preserves both sequence IDs, and robustly classifies baseline,
congested, forward-heavy, and reverse-heavy observations from round-trip delay
and directional imbalance. The imbalance remains an apparent observable, not
calibrated asymmetry.

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

### Higher-order, topological, and directed diagnostics

- **Bicoherence** uses a normalized direct bispectrum across overlapping,
  tapered segments. It screens quadratic phase coupling
  \(f_1+f_2\rightarrow f_3\), not its physical cause.
- **Topological dynamics** constructs a normalized three-coordinate delay
  embedding and computes Vietoris–Rips \(\beta_0\) and \(\beta_1\) curves by
  finite-field boundary rank. Persistent loops are candidate geometry and
  still require stationary windows and surrogate confirmation.
- **Multiscale sample entropy** coarse-grains the record at powers of two and
  reports finite-count-corrected sample entropy with \(m=2,r=0.2\sigma\).
- **Predictive direction** compares lag-one target-only and target-plus-source
  regressions. The logged variance reduction is explicitly `causal: false`;
  the shared BC1 reference and physical cascade are confounders.

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
