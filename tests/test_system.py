import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_system.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_system_test", MODULE_PATH)
assert SPEC and SPEC.loader
SYSTEM = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SYSTEM
SPEC.loader.exec_module(SYSTEM)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class HostTests(unittest.TestCase):
    def test_uptime_and_release_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "proc" / "uptime", "12345.67 98765.43\n")
            _write(root / "proc" / "sys" / "kernel" / "osrelease", "7.0.0-28-generic\n")
            _write(root / "etc" / "os-release", 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 26.04 LTS"\n')

            info = SYSTEM.host_info(proc=root / "proc", etc=root / "etc")

            self.assertAlmostEqual(12345.67, info["uptime_s"])
            self.assertEqual("7.0.0-28-generic", info["kernel"])
            self.assertEqual("Ubuntu 26.04 LTS", info["os"])

    def test_missing_files_yield_nulls_rather_than_raising(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            info = SYSTEM.host_info(proc=Path(directory), etc=Path(directory))

            self.assertIsNone(info["uptime_s"])
            self.assertIsNone(info["kernel"])
            self.assertIsNone(info["os"])


class CpuTests(unittest.TestCase):
    CPUINFO = (
        "processor\t: 0\nmodel name\t: Intel(R) Xeon(R) W-2123 CPU @ 3.60GHz\ncore id\t: 0\n\n"
        "processor\t: 1\nmodel name\t: Intel(R) Xeon(R) W-2123 CPU @ 3.60GHz\ncore id\t: 1\n\n"
        "processor\t: 2\nmodel name\t: Intel(R) Xeon(R) W-2123 CPU @ 3.60GHz\ncore id\t: 0\n\n"
    )

    def test_model_threads_cores_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / "proc" / "cpuinfo", self.CPUINFO)
            _write(root / "proc" / "loadavg", "1.05 0.98 0.91 2/1234 5678\n")
            freq = root / "sys" / "devices" / "system" / "cpu" / "cpu0" / "cpufreq"
            _write(freq / "scaling_cur_freq", "1200000\n")
            _write(freq / "cpuinfo_max_freq", "3600000\n")

            info = SYSTEM.cpu_info(proc=root / "proc", sysfs=root / "sys")

            self.assertIn("W-2123", info["model"])
            self.assertEqual(3, info["threads"])
            self.assertEqual(2, info["cores"])  # two distinct core ids
            self.assertEqual([1.05, 0.98, 0.91], info["load_average"])
            self.assertAlmostEqual(1200.0, info["mhz_current"])
            self.assertAlmostEqual(3600.0, info["mhz_maximum"])

    def test_utilisation_needs_two_samples_and_then_reports_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            # user nice system idle iowait ...
            _write(proc / "stat", "cpu  100 0 100 800 0 0 0 0 0 0\n")
            state: dict = {}

            first = SYSTEM.cpu_utilization(proc=proc, state=state)
            self.assertIsNone(first["busy_pct"], "first call cannot know a delta")

            # +100 user and +100 system is 200 busy ticks against +100 idle,
            # so 200/300 of the interval was busy.
            _write(proc / "stat", "cpu  200 0 200 900 0 0 0 0 0 0\n")
            second = SYSTEM.cpu_utilization(proc=proc, state=state)

            self.assertAlmostEqual(200.0 / 3.0, second["busy_pct"], delta=0.01)

    def test_utilisation_survives_a_counter_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            _write(proc / "stat", "cpu  500 0 500 5000 0 0 0 0 0 0\n")
            state: dict = {}
            SYSTEM.cpu_utilization(proc=proc, state=state)
            _write(proc / "stat", "cpu  1 0 1 10 0 0 0 0 0 0\n")

            self.assertIsNone(SYSTEM.cpu_utilization(proc=proc, state=state)["busy_pct"])


class MemoryTests(unittest.TestCase):
    def test_used_is_total_minus_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            _write(proc / "meminfo",
                   "MemTotal:       131072 kB\nMemAvailable:   98304 kB\n"
                   "Buffers:          1024 kB\nCached:           2048 kB\n"
                   "SwapTotal:        4096 kB\nSwapFree:         4096 kB\n")

            info = SYSTEM.memory_info(proc=proc)

            self.assertEqual(131072, info["total_kb"])
            self.assertEqual(32768, info["used_kb"])
            self.assertAlmostEqual(25.0, info["used_pct"])
            self.assertEqual(0, info["swap_used_kb"])


class ThermalTests(unittest.TestCase):
    def test_zones_and_hwmon_are_reported_in_celsius(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sysfs = Path(directory)
            _write(sysfs / "class" / "thermal" / "thermal_zone0" / "temp", "42000\n")
            _write(sysfs / "class" / "thermal" / "thermal_zone0" / "type", "x86_pkg_temp\n")
            _write(sysfs / "class" / "hwmon" / "hwmon3" / "name", "mlx5\n")
            _write(sysfs / "class" / "hwmon" / "hwmon3" / "temp1_input", "88000\n")
            _write(sysfs / "class" / "hwmon" / "hwmon3" / "temp1_label", "asic\n")

            readings = SYSTEM.thermal_info(sysfs=sysfs)
            by_label = {item["label"]: item["temperature_c"] for item in readings}

            self.assertAlmostEqual(42.0, by_label["x86_pkg_temp"])
            self.assertTrue(any(item["label"].startswith("mlx5 asic") and abs(item["temperature_c"] - 88.0) < 1e-6 for item in readings))

    def test_unreadable_temperature_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sysfs = Path(directory)
            _write(sysfs / "class" / "thermal" / "thermal_zone0" / "temp", "not-a-number\n")

            self.assertEqual([], SYSTEM.thermal_info(sysfs=sysfs))


class TopologyVerificationTests(unittest.TestCase):
    TOPOLOGY = {
        "nodes": [
            {"name": "BC1", "ingress": "p1in", "egress": "p1out"},
            {"name": "BC2", "ingress": "p2in", "egress": "p2out"},
            {"name": "BC3", "ingress": "p3in", "egress": "p3out"},
        ],
        "management_interfaces": ["mgmt0"],
    }

    def _interfaces(self, **overrides):
        base = {
            "p1out": {"name": "p1out", "carrier": True, "speed_mbps": 100000},
            "p2in": {"name": "p2in", "carrier": True, "speed_mbps": 100000},
            "p2out": {"name": "p2out", "carrier": True, "speed_mbps": 100000},
            "p3in": {"name": "p3in", "carrier": True, "speed_mbps": 100000},
            "mgmt0": {"name": "mgmt0", "carrier": True, "speed_mbps": 1000},
        }
        base.update(overrides)
        return list(base.values())

    def test_healthy_cascade_reports_every_link_verified(self) -> None:
        result = SYSTEM.topology_verification(self._interfaces(), self.TOPOLOGY)

        self.assertEqual(2, len(result["links"]), "three nodes form two links")
        self.assertEqual(2, result["verified_links"])
        self.assertEqual(["BC1", "BC2", "BC3"], result["declared_nodes"])
        self.assertEqual(["mgmt0"], result["management_excluded"])
        self.assertFalse(result["discovery"]["available"], "must not claim peer discovery")

    def test_missing_carrier_is_reported_as_a_problem(self) -> None:
        interfaces = self._interfaces(p2in={"name": "p2in", "carrier": False, "speed_mbps": 100000})

        result = SYSTEM.topology_verification(interfaces, self.TOPOLOGY)

        first = result["links"][0]
        self.assertFalse(first["verified"])
        self.assertTrue(any("no carrier" in problem for problem in first["problems"]))
        self.assertEqual(1, result["verified_links"])

    def test_speed_mismatch_is_detected(self) -> None:
        interfaces = self._interfaces(p3in={"name": "p3in", "carrier": True, "speed_mbps": 25000})

        result = SYSTEM.topology_verification(interfaces, self.TOPOLOGY)

        second = result["links"][1]
        self.assertFalse(second["verified"])
        self.assertTrue(any("speed mismatch" in problem for problem in second["problems"]))
        self.assertIsNone(second["speed_mbps"])

    def test_absent_port_is_reported(self) -> None:
        interfaces = [item for item in self._interfaces() if item["name"] != "p2in"]

        result = SYSTEM.topology_verification(interfaces, self.TOPOLOGY)

        self.assertTrue(any("not present" in problem for problem in result["links"][0]["problems"]))

    def test_empty_topology_is_not_an_error(self) -> None:
        result = SYSTEM.topology_verification([], {})

        self.assertEqual("no-topology", result["status"])
        self.assertEqual([], result["links"])


class SnapshotTests(unittest.TestCase):
    def test_snapshot_exposes_every_section_and_states_provenance(self) -> None:
        snapshot = SYSTEM.snapshot(interfaces=[], topology={})

        for key in ("host", "cpu", "memory", "storage", "thermal", "pci", "topology", "provenance"):
            self.assertIn(key, snapshot)
        self.assertIn("read-only", snapshot["provenance"])


if __name__ == "__main__":
    unittest.main()


class NetworkStatusTests(unittest.TestCase):
    """The network view must degrade to a clear state, never a crash.

    The live path shells out to ip, nmcli, and resolvectl, so these tests cover
    the shape and the classification logic rather than the parsing of tool
    output, which is exercised against the appliance directly.
    """

    def test_shape_and_read_only_contract(self) -> None:
        result = SYSTEM.network_status({"nodes": [], "management_interfaces": []})

        for key in ("status", "interfaces", "default_routes", "resolvers", "editable", "observations"):
            self.assertIn(key, result)
        self.assertFalse(result["editable"], "the network view must never advertise itself as editable")
        self.assertIn("rollback", result["interpretation"])

    def test_namespaced_timing_ports_are_declared_but_not_expected_locally(self) -> None:
        topology = {
            "nodes": [
                {"name": "BC1", "ingress": "p1in", "egress": "p1out"},
                {"name": "BC2", "ingress": "p2in", "egress": "p2out"},
            ],
            "management_interfaces": ["mgmt0"],
        }
        result = SYSTEM.network_status(topology)
        observations = result["observations"]

        self.assertEqual(4, observations["declared_timing_ports"])
        # Those ports live in namespaces, so absence here must be explained
        # rather than looking like missing hardware.
        self.assertIn("namespace", observations["note"])

    def test_missing_tools_do_not_raise(self) -> None:
        # network_status is called on hosts without ip/nmcli/resolvectl too.
        result = SYSTEM.network_status({})

        self.assertIn(result["status"], {"ready", "unavailable"})
        self.assertIsInstance(result["interfaces"], list)

    def test_snapshot_includes_the_network_section(self) -> None:
        snapshot = SYSTEM.snapshot(interfaces=[], topology={})

        self.assertIn("network", snapshot)
        self.assertFalse(snapshot["network"]["editable"])
