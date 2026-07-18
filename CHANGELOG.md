# Changelog

All notable changes will be documented in this file.

## Unreleased

- Added a live Start/Stop cascade control backed by the narrowly scoped sudo
  policy, periodic process-state refresh, and AppArmor-compatible LinuxPTP
  configuration paths on Ubuntu hosts.
- Restored the original directional OC/GM process model, adding a PHC bridge
  only for split-PHC NICs and avoiding an unnecessary bridge on shared clocks.
- Configured one signed LinuxPTP log sample per Sync update, persistent ICE
  timestamp-worker priority, and the original one-Sync-per-second cadence.
- Restored end-to-end delay measurement to match the original PTPBox hardware.
- Added explicit hardware-sample validation: impossible path delays remain in
  the raw API but are visibly degraded and excluded from charts and RMS.
- Kept managed LinuxPTP processes alive across observation-agent upgrades and
  restarts while preserving explicit web Stop control.

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
