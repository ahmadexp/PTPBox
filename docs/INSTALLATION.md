# Installation and operations

PTPBox supports a zero-privilege observer deployment and a full host deployment
that can create namespaces and run LinuxPTP processes.

## Requirements

### Runtime host

- Linux with network namespace support; Ubuntu 22.04 or newer is recommended
- Python 3.11 or newer
- LinuxPTP 4.x (`ptp4l` and optionally `pmc`)
- `iproute2` (`ip` and `tc`), `util-linux` (`nsenter`), `ethtool`, `systemd`,
  and `sudo`
- `mstflint` when ConnectX firmware clock mode must be inspected or changed
- Two timing-capable ports per boundary-clock stage
- A separate management interface that is never assigned to a PTP namespace

### Build workstation or host

- Node.js 22.13 or newer
- npm

The built host bundle has no Node.js runtime dependency. The Python agent serves
the static application directly.

## Clone and validate

```bash
git clone https://github.com/ahmadexp/PTPBox.git PTPBox-Web
cd PTPBox-Web
npm ci
make check
```

## Observer mode

Observer mode discovers interfaces, drivers, line rates, PHCs, timestamping,
network namespaces, LinuxPTP processes, and readable log files. It can also
stage configuration and experiment metadata. It cannot move a NIC or start a
privileged timing process.

```bash
npm run build:standalone

PTPBOX_ROOT=/path/to/experiment-root \
PTPBOX_STATE_DIR=/path/to/experiment-root/runtime \
PTPBOX_WEB_ROOT="$PWD/dist-standalone" \
PTPBOX_BIND=0.0.0.0 \
PTPBOX_PORT=8090 \
python3 agent/ptpbox_agent.py
```

Open `http://<host>:8090`.

`PTPBOX_ROOT` is where the agent looks for `BC1`, `BC2`, … log directories. It
can point at the original PTPBox checkout or a new data directory.

## Full installation

### 1. Discover hardware

```bash
ip -brief link
for nic in /sys/class/net/*; do
  name=${nic##*/}
  [[ $name == lo ]] || ethtool -T "$name" 2>/dev/null | sed -n '1,18p'
done
```

Record the management interface first. Verify that the SSH session uses that
interface and that it is not part of the timing chain.

After changing ConnectX hardware, also verify `REAL_TIME_CLOCK_ENABLE=1` and
perform the supported firmware reset described in the
[hardware guide](HARDWARE.md#connectx-6-dx-real-time-clock-mode). A normal
LinuxPTP lock does not prove that two independently exposed port PHCs share a
continuous hardware time domain.

### 2. Define the topology

Edit [`agent/topology.json`](../agent/topology.json). Each node represents one
dual-port NIC/card in its own namespace and needs an ingress and egress
interface. The first node runs the grandmaster-facing process, the last node
runs the ordinary-clock endpoint, and every intermediate NIC runs one two-port
`ptp4l` boundary clock. No local PHC synchronization loop is added.

```json
{
  "nodes": [
    { "name": "BC1", "ingress": "enp25s0f0np0", "egress": "enp25s0f1np1" },
    { "name": "BC2", "ingress": "enp26s0f0np0", "egress": "enp26s0f1np1" }
  ],
  "management_interfaces": ["enp179s0f0"],
  "domain": 24
}
```

> [!CAUTION]
> `start` moves every declared PTP interface into a namespace. A wrong mapping
> can disconnect the host. Use an out-of-band console for the first activation.

### 3. Build and install

```bash
npm run build:standalone

sudo \
  PTPBOX_USER="$(id -un)" \
  PTPBOX_ROOT=/path/to/experiment-root \
  bash scripts/install-host.sh
```

The installer:

1. copies the dependency-free web agent to `/opt/ptpbox-web`;
2. installs `ptpboxctl` as `/usr/local/sbin/ptpboxctl`, the
   `/usr/local/sbin/ptpbox-kalman-servo` PHC worker, the raw LinuxPTP
   event-monitor collector, and the measurement-only PPS comparator;
3. copies the topology to `/etc/ptpbox/topology.json`;
4. links `/etc/ptpbox/config.json` to the operator-owned staged configuration;
5. creates a systemd unit running as the operator account;
6. validates a sudoers policy for fixed `start`, `stop`, `restart`, `status`,
   validated `servo`, bounded `fault`, and bounded `identify` operations only;
7. prepares AppArmor-compatible LinuxPTP configuration storage;
8. adds a scoped AppArmor local include for multi-PHC boundary clocks and
   per-namespace management sockets when Ubuntu's `ptp4l` profile is present;
9. installs a systemd-tmpfiles rule so `/run/netns` and `/run/ptpbox` are
   recreated after every reboot, before the agent starts;
10. starts the web service on port 8090.

The service starts after `network.target`, not `network-online.target`.
Timing interfaces intentionally have no IP configuration, so waiting for every
NetworkManager connection to become routable would deadlock host startup.

### 4. Verify the control plane

These commands do not move interfaces:

```bash
systemctl status ptpbox-agent --no-pager
ptpboxctl discover
sudo ptpboxctl status
python3 - <<'PY'
from urllib.request import urlopen
print(urlopen("http://127.0.0.1:8090/healthz").read().decode())
PY
```

### 5. Start the physical cascade

After reviewing `/etc/ptpbox/topology.json` one final time:

```bash
sudo ptpboxctl start
sudo ptpboxctl status
```

Or use the live **Start cascade / Stop cascade** control in the web UI once the
topology and sudo policy are installed. The control tracks the real process
state and does not require another password prompt.

Logs are written under `/var/log/ptpbox`. Runtime process state and generated
LinuxPTP configuration are stored under `/run/ptpbox` and `/etc/linuxptp`
respectively. `/etc/linuxptp` is required by Ubuntu's packaged AppArmor policy.
Every intermediate namespace runs one `ptp4l` process with its ingress and
egress ports. The controller enters the named network namespace with `nsenter`
so each process can still reach its host-visible management socket under
`/run/ptpbox`. LinuxPTP selects the upstream port as the client and serves time
from the downstream port as a true boundary clock. One read-only event-monitor
collector per receiving stage preserves raw exchange timestamps. The controller
records each port's timestamp provider in `/run/ptpbox/phcs.json`; the agent
reads the selected NIC PHCs through the `clock` group and compares them to BC1
without modifying them.

## Servo selection and measured holdover

Open **Configuration → Clock discipline** to select a downstream clock (or all
downstream clocks) and choose one of:

- **PI controller** — LinuxPTP's standard proportional-integral servo;
- **Linear regression** — LinuxPTP's adaptive regression servo;
- **Kalman** — a PTPBox two-state phase/frequency estimator. LinuxPTP remains
  the hardware-timestamped observation engine in `free_running` mode while the
  dedicated worker applies a bounded PHC frequency correction. Measurement
  noise, oscillator process noise, phase time constant, and innovation gate are
  configurable from the Observatory;
- **Adaptive Kalman** — adds oscillator drift and adaptive measurement-noise
  estimation, including controlled reacquisition after a persistent phase
  transition;
- **IMM** — runs quiet, dynamic, and holdover estimators in parallel and mixes
  them using measured innovation likelihoods;
- **Null frequency** — forces zero frequency correction for a SyncE-backed
  diagnostic setup.

**Enter holdover** restarts only the selected clock with `free_running 1`.
LinuxPTP keeps receiving PTP messages and logging raw master offsets, while the
PHC comparison sampler keeps measuring every clock at the applied Sync cadence. The UI derives
holdover drift from those real PHC samples. **Resume servo** applies the chosen
implementation and shows acquisition through tracking and lock states. PI,
linear regression, and null frequency use LinuxPTP's native controller path.
Every PTPBox Kalman mode intentionally keeps LinuxPTP `free_running 1` to
prevent competing clock control and reports its own acquisition, estimate
uncertainty, innovation gate, rejection count, and lock state.

This is clock-servo holdover, not a simulated graph and not a stopped timing
process. A servo transition causes a brief restart of the selected `ptp4l`
instance because LinuxPTP does not switch `clock_servo` dynamically.

## Bounded active identification

Open **Cascade dynamics** after selecting a running Kalman, adaptive-Kalman, or
IMM servo. The controlled-identification panel can inject a finite multisine
frequency correction while all PHC and LinuxPTP monitoring remains active.
Set the target, composite peak in ppb, duration, raw-offset abort limit, and
one to eight frequencies.

The web agent validates the request, the root controller repeats every limit
against the applied sample rate, and the PHC worker automatically disables the
instrument on expiry or excessive offset. The default 25 ppb / 180 s / 5 µs
settings are intentionally conservative starting points, not universal safe
limits. Use this only on an isolated lab cascade.

## Durable measurement runs

Open **Metrology** and press **Start run** before a trial. The agent stores the
applied configuration and every raw PHC comparison in
`$PTPBOX_STATE_DIR/experiments.sqlite3` using SQLite WAL mode. Stopping the run
does not delete data. Each row in the run ledger has a direct CSV export.

The live stability curves require enough contiguous data for their selected
averaging interval. A long-τ point that says `learning` is waiting for real
samples; the agent does not extrapolate it.

## Message authentication

The Resilience and Configuration pages can stage LinuxPTP Authentication TLVs.
Before enabling them, create the Security Association file named by
`security.authentication.sa_file` (the default is
`/etc/linuxptp/ptpbox-sa.cfg`) as root and restrict its permissions. Key
material is never accepted from or returned to the browser. Authentication
requires two-step operation; apply is rejected otherwise.

Consult the installed LinuxPTP version's Security Association syntax before
creating keys:

```bash
man ptp4l
man ts2phc
```

PTPBox refuses to start an authenticated process when the configured SA file is
missing.

## Bounded fault experiments

The Resilience lab can add delay, jitter, or loss to one declared upstream
egress. It never accepts a free-form interface name. Every active fault has a
1–3600 second expiry and is also removed by **Clear now** or `ptpboxctl stop`.
Use this only on the isolated timing chain, never on the management interface.

## Stop and restore

`stop` terminates PTP processes but leaves namespaces and interface ownership in
place for quick restart:

```bash
sudo ptpboxctl stop
```

`teardown` stops processes, returns interfaces to the root namespace, and
removes the PTP namespaces. It deliberately requires a full root shell and is
not exposed to the web sudo policy:

```bash
sudo /usr/local/sbin/ptpboxctl teardown
```

## Upgrade

```bash
git pull --ff-only
npm ci
make check
sudo PTPBOX_USER="$(id -un)" PTPBOX_ROOT=/path/to/experiment-root \
  bash scripts/install-host.sh
```

The installer is idempotent. It replaces application files and the unit, keeps
the topology and experiment root explicit, then restarts the service.

## Uninstall

Stop and restore the data plane first if it is active:

```bash
sudo /usr/local/sbin/ptpboxctl teardown
sudo bash scripts/uninstall-host.sh
```

The uninstaller removes the service, web bundle, helper, and sudo rule. It
preserves topology data, logs, captures, and the source checkout.

## Environment reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `PTPBOX_USER` | invoking sudo user | Account that runs the web agent |
| `PTPBOX_ROOT` | operator home + `/PTPBox` | Log, runtime-config, and experiment root |
| `PTPBOX_STATE_DIR` | `$PTPBOX_ROOT/runtime` | Staged config and experiment state |
| `PTPBOX_ALBUM_DIR` | `$PTPBOX_STATE_DIR/album` | Shared graph PNGs and their atomic manifest |
| `PTPBOX_WEB_ROOT` | agent-local `static` | Standalone web bundle |
| `PTPBOX_BIND` | `0.0.0.0` | Agent listen address |
| `PTPBOX_PORT` | `8090` | Agent listen port |
| `PTPBOX_ALLOW_ORIGIN` | `*` | CORS origin for separate UI hosting |
| `PTPBOX_TOPOLOGY` | `/etc/ptpbox/topology.json` | Controller topology path |

## Troubleshooting

### UI shows hardware-model mode

- Open `/api/status` and check that the agent is reachable.
- Confirm port 8090 is allowed on the management network.
- Check `journalctl -u ptpbox-agent -n 100 --no-pager`.
- A reachable agent with no active measurements still uses model traces while
  showing real inventory; this is intentional.

### UI says “waiting for raw LinuxPTP sample”

- Run `sudo ptpboxctl status` and confirm every expected `ptp4l` and
  path-monitor worker is alive.
- Inspect `journalctl -u ptpbox-agent -n 100 --no-pager` and
  `/var/log/ptpbox/BC*-*.log`.
- Confirm the receiving ports progress to LinuxPTP state `s1` and then `s2`.
- Query `/api/phc`, `/api/telemetry`, and `/api/path-events` separately. PHC
  comparison, servo logs, and packet-path events have independent freshness;
  one can remain live while another source is acquiring.
- On AppArmor hosts, rerun the installer after an upgrade so the local policy
  includes `/run/ptpbox/monitor-*`.

### `observer_only` is true

The web service is working but `/usr/local/sbin/ptpboxctl` or its sudo rule is
not installed. Re-run the full installer.

### A card exposes two different `/dev/ptpX` devices

PTPBox reports both providers but does not synchronize them locally. If the
adapter does not share or hardware-synchronize those clocks, its egress can
diverge from the ingress. That divergence is part of the measured hardware
behavior and should not be mistaken for a browser or telemetry error.

### PHC comparison says permission denied

The installed service uses `SupplementaryGroups=clock`. Confirm every
`/dev/ptp*` device is group-readable by `clock`, then reinstall or restart the
service so it receives the supplementary group.

### Legacy `ptptool` fails to load

The modern control path does not require the precompiled legacy binary. It uses
LinuxPTP output, sysfs, `ethtool`, and Python standard-library parsing.
