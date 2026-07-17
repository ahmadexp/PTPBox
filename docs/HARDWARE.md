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
sharing one PHC. PTPBox detects this and omits unnecessary cross-PHC discipline.

## Reference host

The current reference machine exposes sixteen physical ports across NVIDIA,
Intel E810, and Intel ixgbe drivers. Fourteen ports form the timing lab; two are
reserved for management and spare use.

| Stage | Ingress | Egress | Driver | PHC behavior |
| --- | --- | --- | --- | --- |
| BC1 | `enp25s0f0np0` | `enp25s0f1np1` | `mlx5_core` | distinct PHCs |
| BC2 | `enp26s0f0np0` | `enp26s0f1np1` | `ice` | shared provider `ptp1` |
| BC3 | `enp27s0f0np0` | `enp27s0f1np1` | `mlx5_core` | distinct PHCs |
| BC4 | `enp28s0f0np0` | `enp28s0f1np1` | `mlx5_core` | distinct PHCs |
| BC5 | `enp103s0f0np0` | `enp103s0f1np1` | `mlx5_core` | distinct PHCs |
| BC6 | `enp104s0f0np0` | `enp104s0f1np1` | `mlx5_core` | distinct PHCs |
| BC7 | `enp105s0f0np0` | `enp105s0f1np1` | `mlx5_core` | distinct PHCs |
| Management | `enp179s0f0` | — | `ixgbe` | excluded |
| Spare | `enp179s0f1` | — | `ixgbe` | excluded |

Interface names are examples, not portable defaults. PCI enumeration can change
after firmware, BIOS, or hardware changes.

## Physical cabling

The expected chain connects each node's egress to the next node's ingress:

```text
reference → BC1 egress ─ cable ─ BC2 ingress
             BC2 egress ─ cable ─ BC3 ingress
             BC3 egress ─ cable ─ BC4 ingress
             BC4 egress ─ cable ─ BC5 ingress
             BC5 egress ─ cable ─ BC6 ingress
             BC6 egress ─ cable ─ BC7 ingress → endpoint
```

Use direct attach or optics supported consistently by each pair. Mixed 50G and
100G links are fine when both ends negotiate the same speed.

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
- [ ] Confirm LinuxPTP 4.x is installed.
- [ ] Keep a physical or BMC console open.
- [ ] Start with `ptpboxctl status`, then `start`.
- [ ] Inspect `/var/log/ptpbox` before beginning a long experiment.

## Budget systems

PTPBox also works with multi-port appliances based on Intel i210/i225-class
controllers. Fewer ports simply produce a shorter cascade. A four-node setup
still demonstrates compounding offset, servo interaction, and holdover behavior.
