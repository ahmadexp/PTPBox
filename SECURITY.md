# Security policy

## Supported versions

Security fixes are applied to the current `main` branch until tagged releases
are introduced.

## Reporting a vulnerability

Please use GitHub private vulnerability reporting for this repository. Do not
open a public issue for authentication bypasses, command injection, unsafe
interface selection, or privilege-boundary problems.

## Privilege model

The HTTP agent runs as the configured operator account. Read-only inventory,
log parsing, and configuration staging do not require root.

The optional installer grants that account passwordless sudo for exactly:

```text
/usr/local/sbin/ptpboxctl start
/usr/local/sbin/ptpboxctl stop
/usr/local/sbin/ptpboxctl restart
/usr/local/sbin/ptpboxctl status
```

The helper accepts fixed verbs and validates topology data before moving any
interface. Arbitrary commands and arbitrary command-line paths are not allowed
by the sudoers rule.

## Deployment guidance

- Put the agent on a trusted management network.
- Use a reverse proxy with TLS and authentication outside a private lab.
- Do not expose port 8090 directly to the public internet.
- Keep management interfaces listed in `management_interfaces`.
- Review `/etc/ptpbox/topology.json` after every NIC rename or hardware change.
- Treat experiment logs and clock identities as operational data.
