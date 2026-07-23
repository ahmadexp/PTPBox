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
  "observer_only": false,
  "agent_version": "1.4.0"
}
```

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

The raw payload preserves invalid samples but marks them `valid: false`. For
this direct-cable lab, a negative path delay or a delay above 1 ms indicates a
driver timestamp failure. Such samples are counted separately and excluded
from RMS and charts; their original values remain available through the API.
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
    "sanity_freq_limit_ppb": 200000
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

## Servo and holdover control

### `GET /api/servo`

Returns the supported on-box servo implementations and the current per-clock
state. The safe built-in choices are `pi`, `linreg`, and `nullf`.

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

Use `target: "all"` for BC2 through the final OC. Changing the servo requires a
brief restart of only the selected `ptp4l` instance; the agent and PHC sampler
do not restart. `nullf` deliberately commands zero frequency correction and is
intended for SyncE-backed diagnostics, not ordinary oscillator discipline.

## Experiments

### `POST /api/experiments/start`

Stages experiment metadata under `PTPBOX_STATE_DIR/experiment.json`.

```json
{
  "type": "step",
  "target": "BC7",
  "amplitude_ns": 1000,
  "duration_s": 120,
  "servo": { "kp": 0.7, "ki": 0.3, "step_threshold_ns": 0 }
}
```

This endpoint records the experiment definition. Hardware stimulus injection is
kept separate from the HTTP agent until a target-specific actuator is installed.

## Lifecycle control

### `POST /api/control`

```json
{ "action": "status" }
```

Allowed actions are `start`, `stop`, `restart`, and `status`. Servo transitions
use `/api/servo/control` so their request is validated and atomically staged
before the internal fixed helper verb is invoked. The agent invokes the fixed
installed helper through `sudo -n`. Unsupported actions return `400`;
missing integration returns `503`; lifecycle conflicts return `409`.

## Static application

All other GET paths are resolved beneath `PTPBOX_WEB_ROOT`. Unknown client-side
routes fall back to `standalone/index.html`. Resolved asset paths are checked
against the web root to prevent path traversal.
