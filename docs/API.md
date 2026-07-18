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
      "hardware_timestamping": true
    }
  ],
  "timestamp": 1784327816.62
}
```

## Telemetry

### `GET /api/phc`

Returns one-hertz, read-only PHC comparisons. The first mapped NIC is the
reference. Every target is bracketed by two reference reads and compared with
their midpoint. `offset_ns` is cumulative difference from BC1;
`previous_hop_offset_ns` is the delta from the preceding NIC. The sampler never
sets, steps, or adjusts a clock.

```json
{
  "reference": "BC1",
  "reference_phc": "ptp2",
  "method": "sequential PHC midpoint reads",
  "raw": true,
  "smoothing": "none",
  "clocks": [
    {
      "id": "BC2",
      "phc": "ptp1",
      "measurement": {
        "offset_ns": 42,
        "previous_hop_offset_ns": 42,
        "read_span_ns": 2100,
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
  "measurement_source": "direct PHC comparison",
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

The raw payload preserves invalid samples but marks them `valid: false`. For
this direct-cable lab, a negative path delay or a delay above 1 ms indicates a
driver timestamp failure. Such samples are counted separately and excluded
from RMS and charts; their original values remain available through the API.

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

## Experiments

### `POST /api/experiments/start`

Stages experiment metadata under `PTPBOX_STATE_DIR/experiment.json`.

```json
{
  "type": "step",
  "target": "BC4",
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

Allowed actions are `start`, `stop`, `restart`, and `status`. The agent invokes
the fixed installed helper through `sudo -n`. Unsupported actions return `400`;
missing integration returns `503`; lifecycle conflicts return `409`.

## Static application

All other GET paths are resolved beneath `PTPBOX_WEB_ROOT`. Unknown client-side
routes fall back to `standalone/index.html`. Resolved asset paths are checked
against the web root to prevent path traversal.
