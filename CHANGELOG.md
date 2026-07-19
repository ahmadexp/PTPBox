# Changelog

All notable changes will be documented in this file.

## Unreleased

- Renumbered cascade stages in physical order (`BC1` through `BC7`) while
  preserving the verified port sequence and cabling.
- Corrected Observatory RMS surfaces to use raw LinuxPTP servo offsets, which
  are hardware-timestamped nanosecond measurements, instead of cross-device PHC
  read dispersion; the PHC sampling aperture is now shown separately.
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
