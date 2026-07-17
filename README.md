# PTPBox Precision Time Lab

PTPBox turns a multi-NIC Linux host into an observable PTP cascade. This modern
control room makes the physical topology legible, charts error accumulation at
each hop, runs repeatable servo experiments, inventories PHCs and timestamping
capabilities, and stages guarded configuration changes.

The original shell-based PTPBox project remains the inspiration and hardware
foundation: [Time-Appliances-Project/Incubation-Projects](https://github.com/Time-Appliances-Project/Incubation-Projects/tree/master/Software/PTPBox).

## Product surfaces

- **Overview** — live cascade topology, clock state, offset growth, RMS, MTIE,
  and selected-clock servo details.
- **Analytics** — multi-trace offset explorer, density view, and per-hop error
  budget.
- **Experiments** — step, wander, holdover, and gain-sweep recipes with tunable
  PI parameters.
- **Interfaces** — physical NIC, driver, line-rate, bus, PHC, and hardware
  timestamping inventory.
- **Configuration** — protocol, profile, message rate, servo, and guardrail
  settings with a staged safe-apply review.
- **Event log** — clock-state, servo, measurement, and operator events.

## Architecture

The UI is a React application with two build targets:

1. `npm run build` produces the deployable Sites/Cloudflare build.
2. `npm run build:standalone` produces a static bundle that the dependency-free
   Python agent serves directly on the PTPBox host.

`agent/ptpbox_agent.py` runs without root for inventory and log observation.
Privileged namespace and LinuxPTP lifecycle operations are isolated in
`scripts/ptpboxctl.py`; the optional host installer exposes only fixed lifecycle
subcommands through sudo.

## Local development

```bash
npm install
npm run dev
```

The UI probes `http://<current-host>:8090/api/status`. When the host agent is not
available it automatically enters an explicit, deterministic hardware-model
mode so every view remains useful for demonstrations.

## Build and verify

```bash
npm test
npm run build:standalone
python3 -m py_compile agent/ptpbox_agent.py scripts/ptpboxctl.py
bash -n scripts/install-host.sh
```

## Host preview without root

Build the standalone bundle, copy `agent/` and `dist-standalone/` to the host,
then run:

```bash
PTPBOX_ROOT=$HOME/PTPBox \
PTPBOX_WEB_ROOT=$HOME/PTPBox-Web/dist-standalone \
python3 $HOME/PTPBox-Web/agent/ptpbox_agent.py
```

Open `http://<ptpbox-host>:8090`. Observation works immediately. Lifecycle
controls remain unavailable until the optional privileged helper is installed.

## Full host integration

After inspecting `agent/topology.json` and confirming the management interfaces
are excluded, build the standalone bundle and run:

```bash
sudo bash scripts/install-host.sh
```

The installer places the web bundle and agent under `/opt/ptpbox-web`, installs
the controller as `/usr/local/sbin/ptpboxctl`, validates a narrow sudoers rule,
and starts `ptpbox-agent.service` on port 8090.

Moving NICs into namespaces interrupts traffic on those ports. The controller
refuses to move any interface listed under `management_interfaces`, but the
topology must still be reviewed on each host before the first `setup` or
`start`.
