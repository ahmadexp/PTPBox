import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_phc_store.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_phc_store_test", MODULE_PATH)
assert SPEC and SPEC.loader
STORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORE)

RESEARCH_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_research.py"
RESEARCH_SPEC = importlib.util.spec_from_file_location("ptpbox_research_store_test", RESEARCH_PATH)
assert RESEARCH_SPEC and RESEARCH_SPEC.loader
RESEARCH = importlib.util.module_from_spec(RESEARCH_SPEC)
sys.modules[RESEARCH_SPEC.name] = RESEARCH
RESEARCH_SPEC.loader.exec_module(RESEARCH)


def _open_descriptors() -> int:
    for candidate in (f"/proc/{os.getpid()}/fd", "/dev/fd"):
        if os.path.isdir(candidate):
            return len(os.listdir(candidate))
    raise unittest.SkipTest("no descriptor listing on this platform")


class PhcStoreTests(unittest.TestCase):
    def test_round_trip_preserves_raw_samples_and_temperatures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.sqlite3"
            now = time.time()
            sample = {
                "observed_at": now,
                "sample_id": "cycle-1",
                "reference": "BC1",
                "clocks": [{"id": "BC2", "offset_ns": 12.5, "valid": True}],
            }
            STORE.append_sample(path, sample, {"BC2": 42.25})

            records = STORE.read_records(path, 30)

            self.assertEqual("cycle-1", records[0]["sample"]["sample_id"])
            self.assertEqual(42.25, records[0]["temperatures"]["BC2"])

    def test_ring_prunes_by_row_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.sqlite3"
            now = time.time()
            for index in range(5):
                STORE.append_sample(
                    path,
                    {"observed_at": now + index * .01, "sample_id": str(index), "clocks": []},
                    max_rows=3,
                )

            records = STORE.read_records(path, 30)

            self.assertEqual(["2", "3", "4"], [record["sample"]["sample_id"] for record in records])

    def test_quality_reports_achieved_rate_and_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.sqlite3"
            now = time.time()
            for index, delta in enumerate([0.0, .25, .5, 1.5, 1.75]):
                STORE.append_sample(
                    path,
                    {"observed_at": now + delta, "sample_id": str(index), "clocks": []},
                )

            quality = STORE.collector_quality(path, 4.0)

            self.assertEqual("external-collector", quality["source"])
            self.assertEqual(1, quality["gap_count"])
            self.assertGreater(quality["achieved_rate_hz"], 2.0)


class DescriptorLifetimeTests(unittest.TestCase):
    """A long-lived writer must not accumulate SQLite descriptors.

    A sqlite3.Connection used directly as a context manager only manages the
    transaction, so the handle stayed open. At the Sync cadence that exhausted
    the collector's descriptor limit within minutes, after which opening
    /dev/ptp* also failed and acquisition stopped silently.
    """

    ITERATIONS = 400

    @staticmethod
    def _sample(index: int) -> dict:
        return {
            "observed_at": time.time() + index,
            "sample_id": f"sample-{index}",
            "clocks": [
                {"id": "BC1", "offset_ns": 0.0, "valid": True, "comparison_uncertainty_ns": 1.0},
                {
                    "id": "BC2",
                    "offset_ns": 4.0,
                    "previous_hop_offset_ns": 4.0,
                    "valid": True,
                    "comparison_uncertainty_ns": 2.0,
                },
            ],
        }

    def test_append_and_read_do_not_leak_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "samples.sqlite3"
            STORE.append_sample(path, self._sample(0), {})
            before = _open_descriptors()
            for index in range(1, self.ITERATIONS):
                STORE.append_sample(path, self._sample(index), {"BC1": 80.0})
                if index % 100 == 0:
                    STORE.read_records(path, 600.0)
            after = _open_descriptors()

            self.assertLessEqual(after - before, 2, "descriptors grew across writes")
            self.assertEqual(self.ITERATIONS, len(STORE.read_records(path, 3600.0)))

    def test_experiment_recorder_does_not_leak_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RESEARCH.ExperimentStore(Path(directory) / "experiments.sqlite3")
            store.record_phc(self._sample(0), {})
            before = _open_descriptors()
            for index in range(1, self.ITERATIONS):
                store.record_phc(self._sample(index), {"BC1": 80.0})
            after = _open_descriptors()

            self.assertLessEqual(after - before, 2, "descriptors grew across recorded samples")


if __name__ == "__main__":
    unittest.main()
