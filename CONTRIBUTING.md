# Contributing to PTPBox

Thanks for helping make precision timing easier to observe and experiment with.

## Before you begin

- Search existing issues before opening a new one.
- For hardware or topology changes, include NIC models, drivers, LinuxPTP
  version, and whether ports share a PHC.
- Never assume an interface is safe to move. Management-interface protection is
  part of the feature, not an operator detail.
- Do not include private IP addressing, credentials, serial numbers, or raw logs
  that may contain sensitive host information.

## Development setup

```bash
npm ci
npm run dev
```

The UI automatically enters hardware-model mode when an agent is unavailable.
For a read-only live agent:

```bash
PTPBOX_ROOT=/path/to/PTPBox \
PTPBOX_WEB_ROOT="$PWD/dist-standalone" \
python3 agent/ptpbox_agent.py
```

## Quality gate

Run the complete local check before opening a pull request:

```bash
make check
```

This covers linting, the deployable build, rendered HTML tests, the standalone
host bundle, Python syntax, and shell syntax.

## Pull requests

- Keep each PR focused.
- Explain user impact and hardware impact separately.
- Include screenshots for visible UI changes.
- Add or update tests when behavior changes.
- Call out any command that moves interfaces, changes clock state, or requires
  additional privilege.

## Contribution license

PTPBox uses a noncommercial source-available license with all commercial rights
reserved to Ahmad Byagowi. By intentionally submitting a contribution for
inclusion in PTPBox, you agree to Section 5 of [LICENSE](LICENSE): you retain
ownership of your work and grant Ahmad Byagowi the perpetual right to use,
modify, distribute, relicense, and commercially exploit it as part of, or in
connection with, PTPBox.

Do not submit a contribution if you do not own it, lack authority to grant
those rights, or do not agree to those terms.

## Commit style

Use a short imperative subject, for example:

```text
Add shared-PHC detection to cascade startup
```

## Reporting hardware results

Useful reports include:

- topology and physical cable order;
- NIC, firmware, driver, and PHC mapping;
- `ptp4l -v` output;
- servo configuration;
- capture duration and sampling interval;
- per-hop RMS/MTIE/TDEV results;
- whether the trace is raw, filtered, or simulated.
