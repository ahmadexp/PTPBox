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
  "agent_version": "1.0.0"
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

### `GET /api/telemetry`

The agent scans `PTPBOX_ROOT/BC*/**.log`, reads only the tail of each file, and
extracts LinuxPTP offset, frequency adjustment, and mean path delay.

```json
{
  "timestamp": 1784327816.64,
  "mode": "live",
  "measured_clocks": 1,
  "clocks": [
    {
      "id": "BC2",
      "logs": 2,
      "measurement": {
        "offset_ns": 42,
        "frequency_ppb": -12.4,
        "mean_path_delay_ns": 241,
        "source": "BC2/BC2-OC-1.log",
        "observed_at": 1784327800.0
      }
    }
  ]
}
```

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
  "delay_mechanism": "P2P",
  "log_sync_interval": -4,
  "two_step": true,
  "hardware_timestamping": true,
  "servo": {
    "type": "pi",
    "kp": 0.7,
    "ki": 0.3,
    "step_threshold_ns": 20,
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
  "servo": { "kp": 0.7, "ki": 0.3, "step_threshold_ns": 20 }
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
