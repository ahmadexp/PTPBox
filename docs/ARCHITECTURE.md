# Architecture

PTPBox separates product UI, unprivileged observation, and privileged data-plane
control. That separation keeps the common workflow safe while preserving the
ability to run a real multi-namespace PTP cascade.

## Components

### Precision Observatory

The React application in `app/` is a client-side instrument UI. It renders:

- the cascade and selected-clock detail;
- live or modeled offset traces on Canvas;
- stability and per-hop error analysis;
- experiment design, selectable servo control, and measured holdover;
- interface/PHC inventory;
- guarded configuration review;
- event and session summaries.

It probes `http://<browser-host>:8090/api/status`. A query-string override is
available for development: `?agent=http://192.0.2.10:8090`.

The same component has two build targets:

- Vinext/Cloudflare output for the hosted demo;
- a Vite static bundle for the on-box Python agent.

### Host agent

`agent/ptpbox_agent.py` uses only the Python standard library. It runs as the
operator account and reads:

- `/sys/class/net` for host-namespace link, driver, bus, MAC, speed, and PHC data;
- `ethtool -T` when sysfs does not expose a distinct PHC;
- `ip netns list` for namespace state;
- `ps` for active `ptp4l` processes;
- `/run/ptpbox/phcs.json` for the controller-verified NIC-to-PHC map and the
  timing-interface metadata captured inside each namespace;
- mapped `/dev/ptp*` clocks for cadence-matched, read-only kernel cross-timestamp
  comparisons;
- raw LinuxPTP client logs in `/var/log/ptpbox`, with a legacy fallback below
  `PTPBOX_ROOT/BC*`, for offset, frequency adjustment, path delay, and servo
  state.

It also serves the standalone application and stages JSON configuration under
`PTPBOX_STATE_DIR`.

The browser requests an initial raw window and then polls incrementally with a
`since` cursor. Direct PHC comparisons and LinuxPTP diagnostics retain their
native timestamps. Missing samples are rendered as gaps; no moving average,
time-series interpolation, or synthetic fill is applied in live mode.

### Lifecycle helper

`scripts/ptpboxctl.py` owns the privileged operations:

- validate interface and management-interface assignments;
- create/delete network namespaces;
- move/restore interfaces;
- start/stop LinuxPTP processes;
- generate role-specific `ptp4l` configuration;
- run one two-port boundary-clock process for every intermediate NIC;
- apply PI, linear-regression, PTPBox Kalman, or null-frequency discipline to
  one receiver or every downstream clock;
- for Kalman mode, keep `ptp4l` in non-disciplining `free_running` mode, feed
  its hardware-timestamped offset samples into a two-state phase/frequency
  estimator, reject statistically inconsistent innovations, and apply only the
  bounded correction to the mapped PHC;
- enter LinuxPTP `free_running` holdover while keeping both PTP diagnostics and
  the independent PHC comparison sampler alive;
- validate PHC periodic-output, external-timestamp, pin, and channel
  capabilities before an enabled PPS experiment;
- generate one explicit `ts2phc` topology that programs a selected PHC as PPS
  out (or accepts generic external PPS) and selected PHCs as PPS in;
- write the authoritative PHC measurement map for the unprivileged agent;
- keep PPS control disabled by default; in the normal cascade the NICs
  synchronize only through `ptp4l` over the physical chain;
- track child processes and logs.

Every daemon receives a unique management socket below `/run/ptpbox`; network
namespaces do not isolate Unix-domain socket paths. On AppArmor-enabled Ubuntu
hosts, the installer adds a local profile include for those sockets, inherited
PTPBox logs, and the multi-PHC JBOD clock-switch notification path.

The reference host uses end-to-end delay, as did the original PTPBox. The
generated LinuxPTP configuration matches `summary_interval` to the Sync
interval. LinuxPTP therefore emits one signed master-offset sample per update
instead of aggregating multiple updates into unsigned RMS summaries.
`freq_est_interval` follows the same exponent so non-disciplining holdover and
Kalman observation still emit one sample at every applied Sync cadence.
BC1 has an explicit BMCA priority advantage. Intermediate ingress and egress
ports also have static client/server roles, so a downstream free-running clock
cannot be elected in reverse while an upstream link starts or faults.
On Intel ICE hardware, the controller applies LinuxPTP's documented real-time
priority 30 to the driver's timestamp workers. The reference cascade uses the
original project's one Sync per second cadence to avoid overdriving a shared
multi-port timestamp engine.

The web sudo policy permits only `start`, `stop`, `restart`, `status`, and
`servo` with no additional arguments. The agent validates and atomically stages
the servo request before invoking that fixed verb. `setup` and `teardown` remain
manual root operations.
The observation service uses `KillMode=process`, so restarting or upgrading the
web agent does not terminate the separately tracked timing processes.

The service shares the host filesystem mount view. Named network namespaces are
`nsfs` mounts under `/run/netns`; hiding them in a short-lived service mount
namespace would make a later web-agent restart lose the handles. The API still
runs as an unprivileged account, and its only root path is the exact-command
sudo allowlist. During upgrades from older sandboxed units, the controller can
borrow a surviving managed `ptp4l` process's mount view so the cascade remains
controllable without a data-plane restart.

## Data plane

The reference host's physically verified seven-node sequence is:

```text
BC1 → BC2 → BC3 → BC4 → BC5 → BC6 → BC7
GM       boundary clocks                    OC
```

The final BC7-to-BC1 cable closes the physical ring but carries no PTP process;
it is the deliberate logical break that prevents a timing loop.

Each node receives two physical ports. PTP is transported directly over Layer 2
by default, so the data-plane interfaces do not require IP addressing.

For intermediate nodes:

1. one `ptp4l` instance owns both the ingress and egress ports;
2. LinuxPTP selects the upstream port as client and the downstream port as
   server, propagating the grandmaster dataset through a real boundary clock;
3. the observation agent reads the NIC's measurement PHC and compares it to
   BC1 without adjusting either clock.

This is the original PTPBox real-time model. A dual-port adapter that shares or
hardware-synchronizes its port clocks naturally propagates time. If a card
exposes genuinely independent PHCs, their divergence remains visible instead
of being concealed by a host-side control loop.

The PHC sampler opens each mapped `/dev/ptp*` read-only and uses the Linux
`PTP_SYS_OFFSET_EXTENDED` ioctl. Each call requests nine kernel-bracketed
PHC/system pairs and keeps the pair with the shortest pre/post interval, the
same estimator used by LinuxPTP. `CLOCK_MONOTONIC_RAW` supplies a common,
step-free system reference. BC1 is sampled before and after the targets; its
PHC-to-system offset is interpolated to each target's exact measurement epoch
before the large integer clock offsets are subtracted. This removes read-order
latency without disciplining any clock.

If available, the agent prefers `PTP_SYS_OFFSET_PRECISE`; older kernels fall
back through extended `CLOCK_REALTIME` measurements to a userspace midpoint.
The API identifies the selected method, shortest kernel bracket, and a
conservative comparison-error bound for every sample. The UI keeps PHC
comparison dispersion separate from LinuxPTP servo RMS.

## Control flow

```mermaid
sequenceDiagram
    actor Operator
    participant UI as Observatory
    participant A as Agent (operator)
    participant C as ptpboxctl (root)
    participant N as Namespaces / NICs
    participant P as LinuxPTP
    participant K as Kalman worker (root)

    Operator->>UI: Review topology and settings
    UI->>A: POST /api/config/apply
    A->>A: Validate and atomically stage JSON
    Operator->>UI: Start cascade
    UI->>A: POST /api/control {start}
    A->>C: sudo -n ptpboxctl start
    C->>C: Validate topology and management exclusions
    C->>N: Create namespaces and move declared ports
    C->>P: Start ptp4l with fixed argv
    P-->>A: LinuxPTP logs
    A->>N: Read mapped PHCs without adjustment
    A-->>UI: PHC comparisons, servo telemetry, process state
    Operator->>UI: Apply Kalman to BC7
    UI->>A: POST /api/servo/control
    A->>C: sudo -n ptpboxctl servo
    C->>P: Restart BC7 with free_running 1
    C->>K: Start worker for mapped BC7 PHC
    P-->>K: Raw hardware-timestamped offset / delay
    K->>K: Estimate phase, frequency, and covariance
    K->>N: Apply bounded PHC frequency correction
    K-->>A: Estimate, uncertainty, gate, and lock telemetry
    Operator->>UI: Enter holdover on BC7
    UI->>A: POST /api/servo/control
    A->>C: sudo -n ptpboxctl servo
    C->>P: Restart BC7 with free_running 1
    P-->>A: Sync offsets continue; PHC is not adjusted
    A-->>UI: Raw drift while monitoring stays live
```

## State and files

| Location | Owner | Lifetime | Contents |
| --- | --- | --- | --- |
| `PTPBOX_ROOT/runtime` | operator | durable | staged config, current experiment metadata |
| `/etc/ptpbox/topology.json` | root | durable | authoritative interface mapping |
| `/etc/ptpbox/config.json` | symlink | durable | points to staged operator config |
| `/run/ptpbox` | root | boot | managed process IDs, servo state, Kalman estimates, and read-only PHC/PPS map |
| `/etc/linuxptp/ptpbox-*.conf` | root | regenerated on start | AppArmor-compatible `ptp4l` and optional `ts2phc` config |
| `/var/log/ptpbox` | root | durable | one log per managed process |
| `/opt/ptpbox-web` | root | deployment | agent and static UI |

Configuration writes use a temporary sibling followed by an atomic replace.
Process spawning uses argument arrays rather than a shell.

## Telemetry modes

### Live

The agent is reachable and direct PHC reads are fresh. The UI replaces modeled
series with observed PHC differences while retaining LinuxPTP frequency, delay,
and servo state as separate diagnostics.

### Observer

The agent is reachable and presents real hardware/process state, but the
cascade is not producing measurements. The UI uses deterministic model traces
and labels the session accordingly.

### Hosted model

The browser cannot reach a private agent. All host data and traces come from the
deterministic demonstration model. No control operation is attempted.

## Security boundaries

- The HTTP service is not a general remote shell.
- Configuration is validated and serialized as JSON.
- `ptpboxctl` never executes user-provided shell text.
- The controller refuses overlap between assigned and management interfaces.
- The systemd service is unprivileged and receives only the `clock`
  supplementary group for read-only PHC device and PPS sysfs observation.
- Root control is restricted to five exact `ptpboxctl` command lines; the HTTP
  agent cannot supply controller arguments or shell text.
- Public exposure requires a separate authenticated TLS reverse proxy.

See [`SECURITY.md`](../SECURITY.md) for deployment policy.
