# Agent API

The host agent listens on port 8090 by default. Responses are JSON, use
`Cache-Control: no-store`, and include permissive CORS headers for local lab
development.

> [!WARNING]
> The built-in server is intended for a trusted lab network. Put an authenticated
> TLS reverse proxy in front of it before crossing a security boundary.

## Health

### `GET /healthz`

```json
{ "ok": true, "timestamp": 1784327816.56 }
```

## Host status

### `GET /api/status`

Returns LinuxPTP version, NIC/PHC counts, namespaces, active timing processes,
and whether privileged lifecycle control is installed.

```json
{
  "hostname": "PTPBox",
  "linuxptp": "4.4",
  "interfaces": 16,
  "ptp_interfaces": 16,
  "namespaces": [],
  "processes": [],
  "running": false,
  "pps": {
    "enabled": false,
    "running": false,
    "source": "BC1",
    "sinks": [],
    "servo": "pi",
    "nodes": {
      "BC1": {
        "role": "source",
        "state": "ready",
        "capable": true,
        "device": "/dev/ptp1",
        "pin": {
          "name": "mlx5_pps0",
          "function": "none",
          "channel": 0
        }
      }
    }
  },
  "advanced_capabilities": {
    "dpll": false,
    "synce": false,
    "path_monitor": true,
    "temperature": true,
    "pps_common_edge": false
  },
  "profile_compliance": {
    "profile": "G.8275.1 Telecom",
    "compliant": true,
    "certification": false
  },
  "observer_only": false,
  "agent_version": "2.0.0"
}
```

`pps.nodes` is hardware-backed. It combines the staged role with the PHC's
sysfs PPS capabilities, current pin function, managed `ts2phc` process state,
device, connector, and channel. Node state is one of `ready`, `active`,
`starting`, `stopped`, `external`, or `unavailable`.

## Interface inventory

### `GET /api/interfaces`

```json
{
  "interfaces": [
    {
      "name": "enp25s0f0np0",
      "state": "UP",
      "carrier": true,
      "speed_mbps": 100000,
      "mac": "00:00:00:00:00:00",
      "driver": "mlx5_core",
      "bus": "0000:19:00.0",
      "phc": "ptp0",
      "hardware_timestamping": true,
      "namespace": "BC1",
      "assignment": "BC1 / INACTIVE IN"
    }
  ],
  "timestamp": 1784327816.62
}
```

The lifecycle controller records timing-port metadata after moving each adapter
into its namespace. The unprivileged agent merges that snapshot with the live
host-namespace management ports, so the response and status counts still cover
all physical interfaces while the cascade is running.

## Telemetry

### `GET /api/phc`

Returns read-only PHC comparisons at the applied PTP Sync cadence: 0.5, 1, 2,
4, or 8 Hz. The `sample_rate_hz` response field reports the active cadence.
The first mapped NIC is the reference. Every PHC is cross timestamped against `CLOCK_MONOTONIC_RAW` using
the shortest of nine kernel-bracketed samples. BC1 is measured before and after
the targets and interpolated to each target's measurement epoch. `offset_ns` is
the cumulative difference from BC1; `previous_hop_offset_ns` is the delta from
the preceding NIC. The sampler never sets, steps, or adjusts a clock. Clients
can use `since` for incremental updates; the Observatory polls this lightweight
endpoint at the reported cadence while full LinuxPTP parsing stays on a slower
supervisory path.

```json
{
  "reference": "BC1",
  "reference_phc": "ptp2",
  "method": "common-system cross timestamps with interpolated BC1 reference",
  "sample_rate_hz": 8.0,
  "raw": true,
  "smoothing": "none",
  "clocks": [
    {
      "id": "BC2",
      "phc": "ptp1",
      "measurement": {
        "offset_ns": 31.4,
        "previous_hop_offset_ns": 31.4,
        "read_span_ns": 710,
        "comparison_uncertainty_ns": 715,
        "cross_timestamp_method": "PTP_SYS_OFFSET_EXTENDED(CLOCK_MONOTONIC_RAW), best of 9",
        "observed_at": 1784327800.0,
        "raw": true,
        "valid": true,
        "error": null
      }
    }
  ]
}
```

### `GET /api/telemetry`

The agent reads raw client-side LinuxPTP logs from `PTPBOX_LOG_DIR` (normally
`/var/log/ptpbox`) with a legacy fallback to `PTPBOX_ROOT/BC*`. It extracts
every offset, frequency adjustment, mean path delay, servo state, and LinuxPTP
source timestamp without smoothing or interpolation. The response also embeds
the direct PHC measurement and incremental `phc_samples` for each clock so the
UI can plot clock differences independently from servo diagnostics.

Query parameters:

- `history`: requested window in seconds, clamped to 5–900;
- `since`: return only samples newer than this Unix timestamp;
- `limit`: maximum raw samples per clock, capped at 4096.

```json
{
  "timestamp": 1784327816.64,
  "mode": "live",
  "phc_mode": "live",
  "measurement_source": "kernel cross-timestamped PHC comparison",
  "measured_clocks": 1,
  "fresh_clocks": 1,
  "raw": true,
  "smoothing": "none",
  "clocks": [
    {
      "id": "BC2",
      "role": "boundary",
      "ingress": "enp26s0f0np0",
      "egress": "enp26s0f1np1",
      "logs": 2,
      "window_sample_count": 1920,
      "window_locked_sample_count": 1912,
      "rms_ns": 18.7,
      "measurement_phc": "ptp1",
      "phc_rms_ns": 45.1,
      "phc_measurement": {
        "offset_ns": 42,
        "previous_hop_offset_ns": 42,
        "phc": "ptp1",
        "observed_at": 1784327800.0,
        "raw": true,
        "valid": true
      },
      "phc_samples": [],
      "measurement": {
        "offset_ns": 42,
        "frequency_ppb": -12.4,
        "mean_path_delay_ns": 241,
        "servo_state": "s2",
        "source": "BC2-OC.log",
        "observed_at": 1784327800.0,
        "raw": true,
        "valid": true,
        "validation_error": null
      },
      "samples": []
    }
  ]
}
```

`mode` describes LinuxPTP log freshness; `phc_mode` independently describes
direct PHC-read freshness. `samples` and `phc_samples` can be empty on an
incremental request even while their latest measurements and window statistics
remain populated.

The response also includes `servo_control.nodes`. Each downstream node reports
its selected `type`, whether discipline is `enabled`, its `mode` (`active` or
`holdover`), and the Unix timestamp at which holdover began.

For an active Kalman clock, the offset and path delay remain the untouched
LinuxPTP observation. `measurement.frequency_ppb` reports the correction that
PTPBox actually applied, `measurement.linuxptp_frequency_ppb` preserves
LinuxPTP's non-disciplining rate estimate, and `control_source` is
`ptpbox-kalman`.

The raw payload preserves invalid samples but marks them `valid: false`.
Non-finite offsets and absolute path-delay estimates above 1 ms are rejected.
A small negative LinuxPTP path-delay estimate is retained during acquisition:
it can result from inter-clock phase being larger than the physical link delay
and is not, by itself, evidence of a driver timestamp failure. Invalid samples
are counted separately and excluded from RMS and charts; their original values
remain available through the API.
Servo `rms_ns` uses only valid locked (`s2`) samples. Acquisition samples remain
in `samples` but cannot inflate the steady-state stability metric.

## Configuration

### `GET /api/config`

Returns the staged configuration or safe defaults.

### `POST /api/config/apply`

Validates and atomically stages a complete configuration document.

```json
{
  "profile": "G.8275.1 Telecom",
  "domain": 24,
  "transport": "L2",
  "delay_mechanism": "E2E",
  "log_sync_interval": 0,
  "two_step": true,
  "hardware_timestamping": true,
  "servo": {
    "type": "pi",
    "kp": 0.7,
    "ki": 0.3,
    "step_threshold_ns": 0,
    "first_step_threshold_ns": 20000,
    "sanity_freq_limit_ppb": 200000,
    "kalman": {
      "measurement_noise_ns": 200.0,
      "process_noise_ppb": 10.0,
      "phase_time_constant_s": 4.0,
      "innovation_gate_sigma": 6.0,
      "drift_noise_ppb_s2": 0.05
    }
  },
  "security": {
    "authentication": {
      "enabled": false,
      "spp": 0,
      "active_key_id": 1,
      "sa_file": "/etc/linuxptp/ptpbox-sa.cfg",
      "allow_unauth": 0
    }
  },
  "pps": {
    "enabled": false,
    "source": "BC1",
    "sinks": ["BC2", "BC3", "BC4", "BC5", "BC6", "BC7"],
    "output_pin": 0,
    "input_pin": 0,
    "channel": 0,
    "polarity": "rising",
    "pulse_width_ns": 100000000,
    "perout_phase_ns": 0,
    "extts_correction_ns": 0,
    "comparison": {
      "enabled": false,
      "measure_only": true,
      "reference": "BC2",
      "history": 256
    },
    "ts2phc": {
      "servo": "pi",
      "kp": 0.7,
      "ki": 0.3,
      "step_threshold_ns": 0,
      "first_step_threshold_ns": 20000,
      "holdover_seconds": 0,
      "stable_threshold_ns": 100,
      "stable_samples": 10,
      "logging_level": 6
    }
  }
}
```

Success is `200` with `staged: true`. Validation failures return `422` with a
`details` array.

`log_sync_interval` is the signed base-2 exponent defined by IEEE 1588 and used
directly by LinuxPTP: the effective Sync frequency is `2^-log_sync_interval`
hertz. PTPBox accepts `1` through `-3`, corresponding to 0.5, 1, 2, 4, and
8 Hz. The Observatory's operator slider moves in 0.5 Hz steps from 0.5 through
10 Hz, but always displays and applies the nearest protocol-representable rate;
it never labels an unrepresentable request as an on-wire frequency.

Staging does not alter a running process. The Observatory's guarded “Apply to
cascade” flow follows a successful stage with `POST /api/control` using the
`restart` action so every managed `ptp4l` instance reads the same new interval.
If PPS is enabled, the controller first validates periodic-output,
external-timestamp, programmable-pin, channel, and `/dev/ptp*` availability,
then generates `/etc/linuxptp/ptpbox-ts2phc.conf` and starts one tracked
`ts2phc` process. With PPS disabled, it neither changes PHC pins nor starts
`ts2phc`.

Profile presets validate the transport, delay mechanism, domain range, and
two-step compatibility that PTPBox implements. The API sets
`certification: false`: passing these checks is not a claim of complete
conformance testing. The G.8275.1 preset uses domains 24–43, G.8275.2 uses
44–63, 802.1AS uses domain 0, and the C37.238 preset uses the profile exception
domain 254. LinuxPTP Authentication TLVs require two-step operation and a
root-owned Security Association file below `/etc/linuxptp`; its key material is
never returned by the API.

## Servo and holdover control

### `GET /api/servo`

Returns the supported on-box servo implementations and the current per-clock
state. Choices are `pi`, `linreg`, `nullf`, `kalman`, `adaptive-kalman`, and
`imm`.

### `POST /api/servo/control`

Applies a servo to one downstream clock or every receiver. Setting `enabled`
to `false` enters measured holdover: LinuxPTP continues receiving Sync messages
and reporting raw offsets, but the generated configuration uses
`free_running 1` so it does not adjust the PHC. The independent PHC comparison
sampler continues at the applied Sync cadence.

```json
{
  "target": "BC7",
  "enabled": false,
  "type": "pi"
}
```

Resume BC7 under the adaptive linear-regression servo with:

```json
{
  "target": "BC7",
  "enabled": true,
  "type": "linreg"
}
```

Run BC7 under the PTPBox Kalman servo with:

```json
{
  "target": "BC7",
  "enabled": true,
  "type": "kalman"
}
```

Every PTPBox Kalman mode uses the raw LinuxPTP master offset as its observation.
`ptp4l` runs with `free_running 1`, so it cannot compete with the dedicated
controller for the PHC. Classic `kalman` estimates phase and frequency.
`adaptive-kalman` adds oscillator drift and online measurement-noise
adaptation. `imm` mixes quiet, dynamic, and holdover models and returns the
posterior regime probabilities. All modes gate outliers, re-anchor after a
persistent phase transition, clamp the applied `clock_adjtime` frequency
correction, and expose estimates, uncertainty, innovations, sample counts,
rejections, correction, and lock state in each clock's `kalman` object.

Use `target: "all"` for BC2 through the final OC. Changing the servo requires a
brief restart of only the selected `ptp4l` instance; the agent and PHC sampler
do not restart. `nullf` deliberately commands zero frequency correction and is
intended for SyncE-backed diagnostics, not ordinary oscillator discipline.

## Research and metrology

### `GET /api/research?history=900`

Returns one aligned analysis snapshot. `history` is clamped to 30–7200 seconds.
The main objects are:

| Object | Contents |
| --- | --- |
| `stability` | Per-clock ADEV, MDEV, TDEV, HDEV, MTIE, and Theo1 curves with τ and pair counts |
| `fusion` | Fused offsets, 1σ uncertainty, residuals, χ², and degrees of freedom |
| `ensemble` | Covariance-regularized clock weights, virtual offset, and 1σ |
| `error_budget` | Per-clock components and covariance-aware cascade uncertainty |
| `temperature_holdover` | Forecast horizon, phase, frequency, and 1σ when sensors are aligned |
| `system_identification` | ARX coefficients, poles, spectral radius, fit, residual, and settling time |
| `auto_tune` | Replay-only GP/EI PI recommendation, frontier, candidate counts, and `live_changes: 0` |
| `change_detection` | Bounded BOCPD probability and detected change indices |
| `recurrence` | Binary recurrence matrix, recurrence rate, determinism, and threshold |
| `bifurcation` | Offline PI gain-scale sweep with settled extrema, response bands, replay bounds, provenance, and `live_changes: 0` |
| `koopman` | Fitted snapshot operator, singular values, residual σ, and amplification label |
| `capabilities` | Hardware-derived DPLL, SyncE, devlink, temperature, path-monitor, and PPS status |
| `profiles` | Applied configuration checks; explicitly not standards certification |
| `experiments` | Recent durable runs and the active run |

Calculations report `waiting` or `learning` until enough real samples exist.
No research endpoint adjusts a clock.

Adaptive-Kalman and IMM controller state is returned per clock in
`GET /api/telemetry` under `clocks[].kalman`, because it is the state of the
actual selected servo rather than a parallel research-only estimate.

### `GET /api/capabilities?refresh=1`

Returns capability-gated hardware truth. `refresh=1` bypasses the short cache.
An unavailable DPLL or SyncE API is reported as unsupported with a reason; PTP
lock is never used as a substitute.

### `GET /api/profiles`

Returns the active profile preset, implemented configuration checks, supported
presets, validation scope, and `certification: false`.

## Raw path events

### `GET /api/path-events?limit=128`

Returns up to 2048 preserved LinuxPTP slave-event-monitor records. A record
contains the hop, Sync and Delay sequence IDs, `t1`, `t2`, `t3`, `t4`,
correction fields, receipt time, and derived apparent timestamp residuals.
Timestamp values remain decimal strings to avoid JSON floating-point loss.

Sync and Delay sequence IDs are tracked independently. The apparent
forward/reverse residual is not labeled path asymmetry because two
unsynchronized PHCs contribute twice their phase difference.

## Hardware capability status

### `GET /api/status`

In addition to process state, `pps.comparison` reports the common-edge EXTS
comparator when it is configured. This mode requires one external PPS connected
to at least two PHC input pins and remains `measure_only`; it does not start a
competing `ts2phc` servo.

## Experiments

### `POST /api/experiments/start`

Creates a durable run in `PTPBOX_STATE_DIR/experiments.sqlite3`, snapshots the
applied configuration, and starts recording every raw PHC comparison in WAL
mode.

```json
{
  "type": "step",
  "target": "BC7",
  "amplitude_ns": 1000,
  "duration_s": 120,
  "servo": { "kp": 0.7, "ki": 0.3, "step_threshold_ns": 0 }
}
```

Starting a new run completes any currently active run. Hardware stimulus remains
a separate, guarded action.

### `POST /api/experiments/stop`

```json
{ "id": "run-20260723-153000" }
```

`id` is optional; without it the active run is completed.

### `GET /api/experiments`

Returns the active run and the most recent 100 runs with sample/event counts,
metadata, captured configuration, and lifecycle timestamps.

### `GET /api/experiments/<run-id>/export`

Downloads raw samples as CSV. Run IDs are validated and database paths are
never accepted from the request.

## Bounded fault control

### `POST /api/fault/control`

```json
{
  "target": "BC3",
  "enabled": true,
  "delay_us": 250,
  "jitter_us": 50,
  "loss_pct": 0,
  "duration_s": 30
}
```

The controller applies `tc netem` only to the declared node's downstream
egress. At least one impairment must be non-zero. Delay and jitter are capped at
one second, loss at 100%, and duration at one hour. The qdisc is removed on
expiry, explicit clear, or cascade stop.

## Lifecycle control

### `POST /api/control`

```json
{ "action": "status" }
```

Allowed HTTP lifecycle actions are `start`, `stop`, `restart`, and `status`.
Servo and fault transitions use their dedicated endpoints so requests are
validated and atomically staged before the internal fixed helper verb is
invoked. The agent invokes the installed helper through `sudo -n`. Unsupported
actions return `400`; missing integration returns `503`; lifecycle conflicts
return `409`.

## Static application

All other GET paths are resolved beneath `PTPBOX_WEB_ROOT`. Unknown client-side
routes fall back to `standalone/index.html`. Resolved asset paths are checked
against the web root to prevent path traversal.
