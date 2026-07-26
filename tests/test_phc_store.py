import importlib.util
import tempfile
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_phc_store.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_phc_store_test", MODULE_PATH)
assert SPEC and SPEC.loader
STORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORE)


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


if __name__ == "__main__":
    unittest.main()
