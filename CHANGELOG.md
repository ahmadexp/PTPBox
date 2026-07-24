# Changelog

All notable changes will be documented in this file.

## Unreleased

- Expanded the clock-stability workbench to nine estimators on two shared
  logarithmic scales: overlapping ADEV, MDEV, HDEV, PDEV, TOTDEV, and Theo1 as
  dimensionless fractional-frequency deviations, plus TDEV, MTIE, and TIE RMS
  in nanoseconds. Corrected Theo1 to use NIST's effective
  \(\tau=0.75m\tau_0\), added reference-vector tests, detrended phase and
  frequency diagnostics, local MDEV slope candidates, and removed the former
  pair-count confidence proxy because formal coverage requires
  noise-dependent equivalent degrees of freedom.
- Added a fractal-analysis view beside recurrence and bifurcation diagnostics:
  Higuchi endpoint-trace dimension, Grassberger–Procaccia correlation dimension
  across four delay embeddings with an explicit scaling window and convergence
  test, plus MF-DFA \(h(q)\) and spectrum width against six deterministic
  shuffled surrogates. All estimates expose sample thresholds, regression
  quality, provenance, and `live_changes: 0` without claiming chaos.
- Added a nonlinear-analysis switch from recurrence quantification to a
  replay-safe PI bifurcation map: 46 gain-scale columns, settled endpoint-phase
  extrema, response-band counts, PI-baseline and first-bound markers,
  active-servo provenance, and `live_changes: 0`. The UI and research guide
  distinguish the screening diagram from a controlled physical bifurcation
  experiment.
- Added the advanced Precision Observatory: overlapping
  ADEV/MDEV/TDEV/HDEV/MTIE/Theo1, weighted factor-graph clock fusion,
  covariance-regularized ensemble time, and correlated cascade error budgets.
- Added raw LinuxPTP path microscopy with preserved `t1`/`t2`/`t3`/`t4`
  decimal timestamps, independently paired Sync and Delay sequence IDs, and an
  explicit apparent-residual interpretation that does not overclaim one-way
  path asymmetry.
- Added adaptive phase/frequency/drift Kalman and quiet/dynamic/holdover IMM
  servo modes, persistent-step reacquisition, temperature-aware holdover
  prediction, ARX loop identification, replay-only Gaussian-process PI tuning,
  recurrence quantification, Koopman/DMD, and Bayesian online change detection.
- Added a durable SQLite/WAL experiment recorder with applied configuration,
  raw sample/event capture, run ledger, and validated per-run CSV export.
- Added capability-gated DPLL, SyncE, devlink, temperature, and common-edge PPS
  status; PTP lock is never presented as proof of physical-frequency lock.
- Added profile configuration guardrails for default IEEE 1588, G.8275.1,
  G.8275.2, 802.1AS, and C37.238 presets, plus LinuxPTP Authentication TLV
  staging without API key exposure.
- Added one-hop bounded netem fault injection with topology-only targeting and
  mandatory automatic expiry.
- Switched namespace daemon launch to `nsenter --net` so NIC isolation remains
  intact while AppArmor-confined LinuxPTP processes retain working
  host-visible management sockets.
- Added live, current screenshots for Overview, Metrology, Path microscope,
  Intelligence, and Resilience plus a new social preview.
- Added a real per-clock Kalman servo alongside PI and linear regression:
  LinuxPTP supplies hardware-timestamped observations in non-disciplining mode,
  a root-owned two-state phase/frequency filter propagates covariance and gates
  outliers, and a bounded `clock_adjtime` correction disciplines the mapped PHC.
  The Observatory exposes measurement noise, oscillator process noise, phase
  time constant, innovation gate, estimates, uncertainty, and lock state while
  preserving the existing start/stop and measured-holdover workflow.
- Added a safe-off-by-default PPS and `ts2phc` control surface with selectable
  PHC or external source, per-clock PPS inputs, Mellanox pin/channel selection,
  edge, pulse, phase, correction, servo, stable-lock, step, and ToD-holdover
  settings; the controller validates real PHC capabilities before starting one
  tracked `ts2phc` process, and Overview reports each node's live sysfs PPS
  role, pin function, connector, and process state.
- Matched the read-only BC1-relative PHC sampler to the applied 0.5–8 Hz Sync
  cadence and added a lightweight incremental `/api/phc` browser stream, so
  the topology and raw PHC chart update up to eight times per second without
  multiplying full LinuxPTP log parsing load.
- Completed the top-bar command palette with instant page, clock, measurement,
  and control search; mouse and keyboard selection; direct BC focus; and exact
  jumps to servo, Sync-frequency, notification, and apply surfaces.
- Added a guarded 0.5–10 Hz synchronization-frequency slider with 0.5 Hz input
  steps, explicit IEEE 1588 power-of-two quantization, live
  `logSyncInterval` preview, staged-config hydration, range validation, and a
  managed cascade restart that applies the selected on-wire rate.
- Bounded LinuxPTP log-tail parsing to the requested sample window and hardened
  the host probe against overlapping slow inventory scans, keeping live raw
  telemetry responsive after append-only lab logs grow into tens of megabytes.
- Added a live state-space atlas that projects the six synchronized hop-change
  rates onto covariance eigenvectors, renders the PC1×PC2 trajectory and 1σ/2σ
  geometry, extracts configurable empirical Poincaré sections, plots modal time
  trends, and follows the covariance eigenvalue spectrum through time.
- Added a covariance lab for synchronized previous-hop phase-change rates with
  switchable covariance/correlation matrices, operator-selected rolling
  windows, a full pair-relationship timeline, sorted eigenvalues, explained
  trace, effective rank, dominant eigenvector loadings, and rolling eigenmode
  energy.
- Added a live multi-pendulum observation page that maps each previous-hop PHC
  residual to one rod angle, learns a robust per-hop equilibrium, detects
  coherent regime shifts with MAD-based auto-zeroing, and exposes manual zero,
  adaptive angular scale, and a per-link equilibrium ledger.
- Turned the top-bar bell into an accessible live notification center with
  unread state, current clock/servo/measurement health, direct navigation,
  mark-all-read, outside-click dismissal, and Escape-key support.
- Added per-clock and all-downstream live servo selection for LinuxPTP PI,
  adaptive linear-regression, and null-frequency implementations.
- Added measured holdover control using LinuxPTP `free_running`: PTP offset logs
  and one-hertz raw PHC comparisons continue while clock adjustment is frozen,
  and the Observatory reports elapsed holdover and fitted frequency drift.
- Preserved network-namespace control across upgrades from older systemd mount
  sandboxes by borrowing a surviving managed `ptp4l` mount view; new installs
  keep persistent `/run/netns` handles in the host mount view.

- Replaced sequential PHC midpoint reads with Linux kernel cross timestamps:
  prefer `PTP_SYS_OFFSET_PRECISE`, otherwise select the shortest of nine
  `PTP_SYS_OFFSET_EXTENDED` brackets against `CLOCK_MONOTONIC_RAW` and
  interpolate BC1 to each target epoch. The live ConnectX host reduced its
  measurement transaction from roughly 20 microseconds to 0.7 microseconds.
- Added the per-sample cross-timestamp method and conservative comparison-error
  bound to the API and Observatory provenance surfaces.
- Renumbered cascade stages in physical order (`BC1` through `BC7`) while
  preserving the verified port sequence and cabling.
- Corrected Observatory RMS surfaces to use raw LinuxPTP servo offsets, which
  are hardware-timestamped nanosecond measurements, instead of cross-device PHC
  comparison dispersion; the measurement error bound is now shown separately.
- Reused read-only PHC descriptors to reduce userspace midpoint-read latency.

- Updated the reference profile to seven ConnectX-6 Dx timing cards with all
  fourteen cascade ports at 100G, including the replacement BC2 adapter.
- Added namespace-aware live interface inventory and removed stale hard-coded
  E810, 50G, PHC, PCI, line-rate, and driver values from the Observatory.
- Added an experimental-EtherType cable peer probe for safely remapping a
  changed lab, plus ConnectX real-time-clock firmware setup and reset guidance.
- Made volatile namespace/controller paths boot-persistent through
  `systemd-tmpfiles` and avoided `network-online.target` deadlock on intentionally
  unnumbered timing ports.

- Added a live Start/Stop cascade control backed by the narrowly scoped sudo
  policy, periodic process-state refresh, and AppArmor-compatible LinuxPTP
  configuration paths on Ubuntu hosts.
- Restored the original real-time process model: one isolated dual-port NIC per
  namespace, directional OC/GM `ptp4l` processes, and no local PHC discipline
  loop.
- Added one-hertz, read-only PHC midpoint comparisons against BC1 with both
  cumulative and previous-hop differences, kept separate from LinuxPTP servo
  telemetry.
- Configured one signed LinuxPTP log sample per Sync update, persistent ICE
  timestamp-worker priority, and the original one-Sync-per-second cadence.
- Restored end-to-end delay measurement to match the original PTPBox hardware.
- Added explicit hardware-sample validation: impossible path delays remain in
  the raw API but are visibly degraded and excluded from charts and RMS.
- Kept managed LinuxPTP processes alive across observation-agent upgrades and
  restarts while preserving explicit web Stop control.
- Restored LinuxPTP's zero normal-step threshold so ordinary noise cannot force
  a locked servo back into acquisition.

### Added

- Precision Observatory UI with cascade topology, timing traces, analytics,
  experiments, interface inventory, configuration, and event surfaces.
- Deterministic hardware-model mode for portable demonstrations.
- Dependency-free Python host agent for LinuxPTP, NIC, PHC, namespace, and log
  observation.
- Safe configuration staging and experiment metadata endpoints.
- Privileged `ptpboxctl` lifecycle helper with management-interface protection.
- Shared-PHC detection for adapters whose ports use one hardware clock.
- Standalone static bundle served directly by the host agent.
- systemd installer, narrow sudoers policy, and non-destructive uninstaller.
- Product screenshots, architecture, installation, hardware, API, experiment,
  security, and contribution documentation.
- Incremental raw LinuxPTP sample delivery with source timestamps, servo state,
  freshness detection, window RMS, and explicit live/waiting/stale modes.
- Unsmoothed live charts, endpoint distribution, raw CSV export, and visible
  provenance that prevents simulation data from being mistaken for hardware.

### Modernized from the original prototype

- Replaced tmux-pane observability with a responsive web control room.
- Replaced precompiled legacy measurement dependencies with LinuxPTP-native
  logs and standard Linux discovery interfaces.
- Added explicit validation, safe apply, and observer-only operation.
