import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_agent.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_agent", MODULE_PATH)
assert SPEC and SPEC.loader
AGENT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AGENT
SPEC.loader.exec_module(AGENT)


class TelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.log_dir = root / "logs"
        self.log_dir.mkdir()
        self.topology = root / "topology.json"
        self.topology.write_text(
            json.dumps(
                {
                    "nodes": [
                        {"name": "BC1", "ingress": "p1", "egress": "p2"},
                        {"name": "BC7", "ingress": "p3", "egress": "p4"},
                        {"name": "BC4", "ingress": "p5", "egress": "p6"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.originals = (AGENT.ROOT, AGENT.LOG_DIR, AGENT.TOPOLOGY_FILE)
        AGENT.ROOT = root
        AGENT.LOG_DIR = self.log_dir
        AGENT.TOPOLOGY_FILE = self.topology

    def tearDown(self) -> None:
        AGENT.ROOT, AGENT.LOG_DIR, AGENT.TOPOLOGY_FILE = self.originals
        self.temporary.cleanup()

    def write_log(self, name: str) -> Path:
        path = self.log_dir / f"{name}-OC.log"
        path.write_text(
            "ptp4l[100.000]: master offset        -12 s1 freq   +4 path delay       210\n"
            "ptp4l[100.063]: master offset          7 s2 freq   -3 path delay       214\n",
            encoding="utf-8",
        )
        now = time.time()
        os.utime(path, (now, now))
        return path

    def test_parses_raw_linuxptp_samples_without_smoothing(self) -> None:
        samples = AGENT.parse_log_measurements(self.write_log("BC7"))

        self.assertEqual([-12.0, 7.0], [sample["offset_ns"] for sample in samples])
        self.assertEqual(["s1", "s2"], [sample["servo_state"] for sample in samples])
        self.assertTrue(all(sample["raw"] for sample in samples))
        self.assertTrue(all(sample["valid"] for sample in samples))
        self.assertAlmostEqual(0.063, samples[1]["observed_at"] - samples[0]["observed_at"], places=3)

    def test_telemetry_uses_physical_topology_order_and_incremental_cutoff(self) -> None:
        self.write_log("BC7")
        self.write_log("BC4")

        payload = AGENT.telemetry(history_seconds=120)
        self.assertEqual(["BC1", "BC7", "BC4"], [clock["id"] for clock in payload["clocks"]])
        self.assertEqual(["grandmaster", "boundary", "ordinary"], [clock["role"] for clock in payload["clocks"]])
        self.assertEqual("live", payload["mode"])
        self.assertEqual("none", payload["smoothing"])
        self.assertEqual(4, payload["sample_count"])

        latest = max(sample["observed_at"] for clock in payload["clocks"] for sample in clock["samples"])
        incremental = AGENT.telemetry(history_seconds=120, since=latest)
        self.assertEqual(0, incremental["sample_count"])
        self.assertEqual(2, incremental["measured_clocks"])

    def test_discards_samples_before_a_monotonic_clock_reset(self) -> None:
        path = self.log_dir / "BC7-OC.log"
        path.write_text(
            "ptp4l[900.000]: master offset 999 s2 freq 0 path delay 100\n"
            "ptp4l[10.000]: master offset 8 s1 freq 2 path delay 101\n"
            "ptp4l[10.063]: master offset 3 s2 freq 1 path delay 102\n",
            encoding="utf-8",
        )

        samples = AGENT.parse_log_measurements(path)
        self.assertEqual([8.0, 3.0], [sample["offset_ns"] for sample in samples])

    def test_flags_impossible_hardware_path_delay_without_changing_raw_value(self) -> None:
        path = self.log_dir / "BC7-OC.log"
        path.write_text(
            "ptp4l[10.000]: master offset 17 s2 freq 3 path delay 781275143\n",
            encoding="utf-8",
        )

        sample = AGENT.parse_log_measurements(path)[0]
        self.assertEqual(17.0, sample["offset_ns"])
        self.assertEqual(781275143.0, sample["mean_path_delay_ns"])
        self.assertFalse(sample["valid"])


if __name__ == "__main__":
    unittest.main()
