import importlib.util
import math
import random
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_thermal.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_thermal_test", MODULE_PATH)
assert SPEC and SPEC.loader
THERMAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = THERMAL
SPEC.loader.exec_module(THERMAL)


def forced_record(tempco, count=600, noise_ppb=5.0, span=20.0, quantise=False, seed=3):
    """A deliberate thermal sweep: temperature is driven, not drifting with time.

    Using a triangular sweep keeps temperature from being collinear with elapsed
    time, which is what a real forced experiment achieves.
    """
    rnd = random.Random(seed)
    out = []
    for index in range(count):
        phase = index / count
        # two full up/down sweeps
        ramp = abs(((phase * 4.0) % 2.0) - 1.0)
        temperature = 40.0 + span * ramp
        if quantise:
            temperature = float(round(temperature))
        frequency = 1000.0 + tempco * (temperature - 50.0) + rnd.gauss(0.0, noise_ppb)
        out.append((float(index), temperature, frequency))
    return out


class RecoveryTests(unittest.TestCase):
    def test_ols_recovers_a_known_tempco_from_a_forced_sweep(self) -> None:
        result = THERMAL.analyse_node(forced_record(-12.0), "BC1")

        self.assertEqual("ready", result["status"])
        self.assertAlmostEqual(-12.0, result["ols"]["tempco_ppb_per_c"], delta=0.5)
        self.assertGreater(result["ols"]["r_squared"], 0.95)

    def test_forced_sweep_satisfies_every_evidence_gate(self) -> None:
        result = THERMAL.analyse_node(forced_record(-8.0), "BC1")

        self.assertEqual("supported", result["evidence"]["verdict"], result["evidence"]["unmet"])

    def test_sign_is_preserved(self) -> None:
        positive = THERMAL.analyse_node(forced_record(+15.0), "BC1")
        self.assertGreater(positive["ols"]["tempco_ppb_per_c"], 0)


class QuantisationTests(unittest.TestCase):
    def test_deming_corrects_the_attenuation_caused_by_quantised_temperature(self) -> None:
        # A narrow span with whole-degree readings is the attenuating case.
        record = forced_record(-20.0, count=800, noise_ppb=30.0, span=4.0, quantise=True)
        result = THERMAL.analyse_node(record, "BC6")

        ols = result["ols"]["tempco_ppb_per_c"]
        deming = result["errors_in_variables"]["deming_tempco_ppb_per_c"]
        self.assertIsNotNone(deming)
        # Errors in the regressor bias OLS toward zero; Deming must not be
        # closer to zero than OLS.
        self.assertGreaterEqual(abs(deming), abs(ols) - 1e-9)
        self.assertTrue(result["temperature"]["quantised_to_whole_degrees"])


class EvidenceGateTests(unittest.TestCase):
    def test_narrow_span_is_refused_even_with_many_samples(self) -> None:
        record = forced_record(-10.0, count=1200, span=2.0, quantise=True)
        result = THERMAL.analyse_node(record, "BC3")

        self.assertFalse(result["evidence"]["gates"]["enough_span"])
        self.assertNotEqual("supported", result["evidence"]["verdict"])
        self.assertIn("enough_span", result["evidence"]["unmet"])

    def test_temperature_that_only_tracks_time_is_flagged_as_confounded(self) -> None:
        # Monotonic warm-up: temperature and elapsed time are the same regressor.
        record = [
            (float(i), 40.0 + 20.0 * i / 600.0, 1000.0 - 10.0 * (20.0 * i / 600.0))
            for i in range(600)
        ]
        result = THERMAL.analyse_node(record, "BC2")

        correlation = result["confounding"]["temperature_time_correlation"]
        self.assertGreater(abs(correlation), 0.99)
        self.assertFalse(result["evidence"]["gates"]["not_time_confounded"])
        self.assertNotEqual("supported", result["evidence"]["verdict"])

    def test_too_few_samples_reports_learning(self) -> None:
        result = THERMAL.analyse_node(forced_record(-10.0, count=10), "BC1")

        self.assertEqual("learning", result["status"])

    def test_constant_temperature_is_unavailable_not_a_crash(self) -> None:
        record = [(float(i), 85.0, 1000.0 + i * 0.1) for i in range(200)]
        result = THERMAL.analyse_node(record, "BC4")

        self.assertEqual("unavailable", result["status"])


class SeparationTests(unittest.TestCase):
    def test_joint_fit_separates_tempco_from_ageing(self) -> None:
        # Forced sweep plus a genuine linear ageing term.
        rnd = random.Random(11)
        ageing = 0.05  # ppb per sample
        record = []
        for index in range(800):
            ramp = abs(((index / 800.0 * 4.0) % 2.0) - 1.0)
            temperature = 40.0 + 20.0 * ramp
            frequency = 1000.0 - 10.0 * (temperature - 50.0) + ageing * index + rnd.gauss(0, 3)
            record.append((float(index), temperature, frequency))
        result = THERMAL.analyse_node(record, "BC5")
        joint = result["confounding"]["joint_fit"]

        self.assertIsNotNone(joint)
        self.assertAlmostEqual(-10.0, joint["tempco_ppb_per_c"], delta=0.6)
        self.assertAlmostEqual(ageing, joint["ageing_ppb_per_s"], delta=0.02)


class RobustnessTests(unittest.TestCase):
    def test_theil_sen_resists_relock_outliers(self) -> None:
        record = forced_record(-10.0, count=400, noise_ppb=2.0)
        # Inject relock spikes of the magnitude a servo restart produces.
        corrupted = list(record)
        for index in range(0, 400, 40):
            t, temp, freq = corrupted[index]
            corrupted[index] = (t, temp, freq + 5000.0)
        result = THERMAL.analyse_node(corrupted, "BC7")

        robust = result["robust"]["theil_sen_tempco_ppb_per_c"]
        self.assertIsNotNone(robust)
        # The robust slope must stay near truth while OLS is dragged away.
        self.assertAlmostEqual(-10.0, robust, delta=2.0)

    def test_serial_correlation_reduces_the_effective_sample_size(self) -> None:
        # Random-walk frequency: heavily autocorrelated residuals.
        rnd = random.Random(5)
        walk, record = 0.0, []
        for index in range(600):
            ramp = abs(((index / 600.0 * 4.0) % 2.0) - 1.0)
            temperature = 40.0 + 20.0 * ramp
            walk += rnd.gauss(0, 8)
            record.append((float(index), temperature, 1000.0 - 10.0 * (temperature - 50.0) + walk))
        result = THERMAL.analyse_node(record, "BC2")

        serial = result["serial_correlation"]
        self.assertGreater(serial["residual_lag_one"], 0.5)
        self.assertLess(serial["effective_samples"], result["samples"])


class FleetTests(unittest.TestCase):
    def test_fleet_summary_counts_and_identifies_the_hottest_node(self) -> None:
        paired = {
            "BC1": [(float(i), 60.0 + 10.0 * abs(((i / 300.0 * 4) % 2) - 1), 1000.0 - 5.0 * i * 0.0) for i in range(300)],
            "BC6": forced_record(-9.0, count=400),
        }
        # Give BC1 a real relationship so it analyses cleanly.
        paired["BC1"] = forced_record(-4.0, count=300, span=10.0)
        paired["BC1"] = [(t, temp + 50.0, f) for t, temp, f in paired["BC1"]]
        result = THERMAL.thermal_analysis(paired)

        self.assertEqual(2, result["summary"]["analysed"])
        self.assertEqual("BC1", result["summary"]["hottest_node"])
        self.assertEqual(0, result["live_changes"])

    def test_empty_input_is_safe(self) -> None:
        result = THERMAL.thermal_analysis({})

        self.assertEqual(0, result["summary"]["analysed"])
        self.assertIsNone(result["summary"]["hottest_node"])


if __name__ == "__main__":
    unittest.main()
