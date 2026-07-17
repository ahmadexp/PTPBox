# Installation and operations

PTPBox supports a zero-privilege observer deployment and a full host deployment
that can create namespaces and run LinuxPTP processes.

## Requirements

### Runtime host

- Linux with network namespace support; Ubuntu 22.04 or newer is recommended
- Python 3.11 or newer
- LinuxPTP 4.x (`ptp4l`, `phc2sys`, and optionally `pmc`)
- `iproute2`, `ethtool`, `systemd`, and `sudo`
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

### 2. Define the topology

Edit [`agent/topology.json`](../agent/topology.json). Each node needs an ingress
and egress interface. The first node runs the grandmaster-facing process, the
last node runs the ordinary-clock endpoint, and intermediate nodes run both
roles with PHC discipline when their ports use different hardware clocks.

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
2. installs `ptpboxctl` as `/usr/local/sbin/ptpboxctl`;
3. copies the topology to `/etc/ptpbox/topology.json`;
4. links `/etc/ptpbox/config.json` to the operator-owned staged configuration;
5. creates a systemd unit running as the operator account;
6. validates a sudoers policy for `start`, `stop`, `restart`, and `status` only;
7. starts the web service on port 8090.

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

Or use the web control once the topology and sudo policy are installed.

Logs are written under `/var/log/ptpbox`. Runtime process state and generated
LinuxPTP configuration are stored under `/run/ptpbox`.

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

### `observer_only` is true

The web service is working but `/usr/local/sbin/ptpboxctl` or its sudo rule is
not installed. Re-run the full installer.

### A port has timestamping but no separate `/dev/ptpX`

Some multi-port adapters share one PHC. PTPBox checks the hardware timestamp
provider index and skips redundant `phc2sys` discipline when both ports use the
same clock.

### Legacy `ptptool` fails to load

The modern control path does not require the precompiled legacy binary. It uses
LinuxPTP output, sysfs, `ethtool`, and Python standard-library parsing.
