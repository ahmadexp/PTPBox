# Hardware and topology guide

PTPBox works best when every timing port supports hardware transmit timestamps,
hardware receive timestamps, and a raw hardware clock.

## Capability check

```bash
ip -brief link
ethtool -T enp25s0f0np0
```

Look for:

```text
hardware-transmit
hardware-receive
hardware-raw-clock
Hardware timestamp provider index: N
```

The provider index matters. Two ports can expose hardware timestamping while
sharing one PHC. PTPBox records both providers for observation but never adds a
cross-PHC discipline loop. Shared or hardware-synchronized NIC clocks propagate
time naturally; genuinely distinct clocks remain visible in the measurements.

## Reference host

The current reference machine exposes sixteen physical ports across seven
NVIDIA ConnectX-6 Dx timing adapters and one Intel X550 management adapter.
Fourteen 100G ports form the timing lab; two ports are reserved for management
and spare use.

| Cascade stage | Physical clock | Ingress | Egress | Driver | PHC behavior |
| --- | --- | --- | --- | --- | --- |
| 1 | BC1 | `enp25s0f0np0` | `enp25s0f1np1` | `mlx5_core` | distinct devices, hardware-aligned RTC |
| 2 | BC2 | `enp26s0f0np0` | `enp26s0f1np1` | `mlx5_core` | distinct devices, hardware-aligned RTC |
| 3 | BC3 | `enp105s0f0np0` | `enp105s0f1np1` | `mlx5_core` | distinct devices, hardware-aligned RTC |
| 4 | BC4 | `enp104s0f0np0` | `enp104s0f1np1` | `mlx5_core` | distinct devices, hardware-aligned RTC |
| 5 | BC5 | `enp103s0f0np0` | `enp103s0f1np1` | `mlx5_core` | distinct devices, hardware-aligned RTC |
| 6 | BC6 | `enp27s0f0np0` | `enp27s0f1np1` | `mlx5_core` | distinct devices, hardware-aligned RTC |
| 7 | BC7 | `enp28s0f0np0` | `enp28s0f1np1` | `mlx5_core` | distinct devices, hardware-aligned RTC |
| — | Management | `enp179s0f0` | — | `ixgbe` | excluded |
| — | Spare | `enp179s0f1` | — | `ixgbe` | excluded |

Interface names are examples, not portable defaults. PCI enumeration can change
after firmware, BIOS, or hardware changes.

### ConnectX-6 Dx real-time clock mode

Every ConnectX-6 Dx timing card must use one real-time clock domain across its
functions. NVIDIA documents `REAL_TIME_CLOCK_ENABLE` as a device-wide NV_CONFIG
setting and requires a firmware reset after it changes. A card can otherwise
lock its ingress servo while its egress timestamps remain phase-discontinuous.

For each physical card, query the PF0 PCI address and enable the setting if
needed. Stop PTP before resetting the adapter, and keep management on a
different PCI device:

```bash
sudo mstconfig -d 0000:1a:00.0 query | grep REAL_TIME_CLOCK_ENABLE
sudo mstconfig -y -d 0000:1a:00.0 set REAL_TIME_CLOCK_ENABLE=1
sudo mstfwreset -d 0000:1a:00.0 query
sudo mstfwreset -d 0000:1a:00.0 reset -l 3 -t 0 --sync 0 -y
```

Use only a reset level reported as supported by `mstfwreset query`; some
firmware revisions require a full power cycle. See NVIDIA's
[time-stamping guide](https://docs.nvidia.com/networking/display/mlnxofedv23100540/time-stamping)
and [real-time clock guide](https://docs.nvidia.com/networking/display/NVIDIA5TTechnologyUserManualv10/Real-Time%2BClock).

## Physical cabling

The reference machine is physically wired as a ring. A raw experimental-frame
probe verified every peer on 18 July 2026. The controller follows six links as a
cascade and leaves the return link inactive, avoiding a PTP timing loop:

```text
GM: BC1 egress ─100G──> BC2 ingress
    BC2 egress ─100G──> BC3 ingress
    BC3 egress ─100G──> BC4 ingress
    BC4 egress ─100G──> BC5 ingress
    BC5 egress ─100G──> BC6 ingress
    BC6 egress ─100G──> BC7 ingress :OC

inactive return: BC7 egress ──100G──> BC1 ingress
```

Use direct attach or optics supported consistently by each pair. Every timing
link in the current reference setup negotiates at 100G.

To rediscover wiring after a hardware change, first stop and tear down the
cascade so all timing ports are back in the host namespace. Run the probe only
on an isolated lab fabric: it sends a few broadcast frames with IEEE's local
experimental EtherType and never modifies interface configuration.

```bash
sudo ptpboxctl teardown
sudo python3 scripts/probe-cabling.py --topology agent/topology.json
```

The result lists bidirectional cable peers and any unresolved ports. Review the
physical result, update `agent/topology.json`, then start the cascade again.

## Generate an inventory

After installing the helper, or directly from the checkout:

```bash
python3 scripts/ptpboxctl.py discover | python3 -m json.tool
```

For a fuller record:

```bash
for nic_path in /sys/class/net/*; do
  nic=${nic_path##*/}
  [[ $nic == lo ]] && continue
  echo "== $nic =="
  ethtool -i "$nic" 2>/dev/null | sed -n '1,6p'
  ethtool -T "$nic" 2>/dev/null | sed -n '1,18p'
done
```

## Topology schema

```json
{
  "nodes": [
    {
      "name": "BC1",
      "ingress": "enp25s0f0np0",
      "egress": "enp25s0f1np1"
    }
  ],
  "management_interfaces": ["enp179s0f0", "enp179s0f1"],
  "subnet_prefix": "192.168",
  "domain": 24
}
```

The current Layer-2 controller does not assign addresses from `subnet_prefix`;
the field is retained for future OOB and UDP profile support.

## First-activation checklist

- [ ] Capture `ip -brief address` and the active SSH route.
- [ ] Mark every management and out-of-band interface as excluded.
- [ ] Confirm every declared timing interface exists.
- [ ] Confirm both ends of each cable report carrier.
- [ ] Confirm timestamp provider indices and shared-PHC pairs.
- [ ] Confirm `/run/ptpbox/phcs.json` selects BC1 egress and each receiver ingress.
- [ ] Confirm LinuxPTP 4.x is installed.
- [ ] Keep a physical or BMC console open.
- [ ] Start with `ptpboxctl status`, then `start`.
- [ ] Inspect `/var/log/ptpbox` before beginning a long experiment.

## Budget systems

PTPBox also works with multi-port appliances based on Intel i210/i225-class
controllers. Fewer ports simply produce a shorter cascade. A four-node setup
still demonstrates compounding offset, servo interaction, and holdover behavior.
