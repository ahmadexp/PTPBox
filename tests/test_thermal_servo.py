import importlib.util
import math
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_thermal_servo.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_thermal_servo_test", MODULE_PATH)
assert SPEC and SPEC.loader
TS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TS
SPEC.loader.exec_module(TS)


def supported(tempco=200.0, sigma=20.0):
    return TS.ThermalDriftModel(tempco, sigma, evidence_supported=True)


class ModelGateTests(unittest.TestCase):
    def test_an_unsupported_coefficient_is_never_usable(self) -> None:
        # This is the whole safety argument: replay shows an unsupported
        # coefficient degrading phase prediction by 161% at a 64 s interval,
        # because drift extrapolates as half d t squared.
        model = TS.ThermalDriftModel(200.0, 20.0, evidence_supported=False)

        self.assertFalse(model.usable())

    def test_a_coefficient_whose_error_bar_covers_zero_is_refused(self) -> None:
        self.assertFalse(supported(tempco=10.0, sigma=50.0).usable())

    def test_a_supported_significant_coefficient_is_usable(self) -> None:
        self.assertTrue(supported().usable())

    def test_non_finite_coefficients_are_refused(self) -> None:
        for bad in (float("nan"), float("inf")):
            self.assertFalse(supported(tempco=bad).usable())

    def test_analysis_verdict_drives_the_gate(self) -> None:
        node = {
            "ols": {"tempco_ppb_per_c": 200.0, "standard_error_ppb_per_c": 10.0},
            "samples": 400, "serial_correlation": {"effective_samples": 40.0},
            "evidence": {"verdict": "candidate"},
        }

        candidate = TS.model_from_analysis(node)
        node["evidence"]["verdict"] = "supported"
        promoted = TS.model_from_analysis(node)

        self.assertFalse(candidate.usable())
        self.assertTrue(promoted.usable())

    def test_serial_correlation_inflates_the_error_bar(self) -> None:
        # n/n_eff = 10, so the standard error must grow by sqrt(10).
        node = {
            "ols": {"tempco_ppb_per_c": 200.0, "standard_error_ppb_per_c": 10.0},
            "samples": 400, "serial_correlation": {"effective_samples": 40.0},
            "evidence": {"verdict": "supported"},
        }

        model = TS.model_from_analysis(node)

        self.assertAlmostEqual(10.0 * math.sqrt(10.0), model.tempco_sigma_ppb_per_c, places=6)


class SlopeTests(unittest.TestCase):
    def _feed(self, feedforward, rate_c_per_s, count=120, step=1.0, quantise=False):
        for index in range(count):
            t = index * step
            value = 80.0 + rate_c_per_s * t
            feedforward.observe(t, round(value) if quantise else value)

    def test_slope_recovers_a_known_ramp(self) -> None:
        ff = TS.ThermalFeedforward(supported(), window_s=1e9)
        self._feed(ff, 0.01)

        slope, sigma = ff.temperature_slope()

        self.assertAlmostEqual(0.01, slope, delta=0.001)
        self.assertGreater(sigma, 0.0)

    def test_a_short_record_has_no_slope(self) -> None:
        ff = TS.ThermalFeedforward(supported())
        ff.observe(0.0, 80.0)

        self.assertIsNone(ff.temperature_slope())

    def test_a_brief_baseline_is_refused_however_many_samples(self) -> None:
        # Many samples crammed into a couple of seconds cannot resolve a slope
        # through a whole-degree quantiser.
        ff = TS.ThermalFeedforward(supported())
        for index in range(200):
            ff.observe(index * 0.01, 80.0)

        self.assertIsNone(ff.temperature_slope())

    def test_missing_readings_are_skipped_not_zeroed(self) -> None:
        ff = TS.ThermalFeedforward(supported(), window_s=1e9)
        self._feed(ff, 0.01, count=60)
        ff.observe(60.0, None)
        self._feed(ff, 0.01, count=60)

        self.assertIsNotNone(ff.temperature_slope())

    def test_the_window_bounds_memory(self) -> None:
        ff = TS.ThermalFeedforward(supported(), window_s=60.0)
        self._feed(ff, 0.01, count=500)

        self.assertLessEqual(len(ff._times), 70)

    def test_drift_prediction_is_tempco_times_slope(self) -> None:
        ff = TS.ThermalFeedforward(supported(tempco=200.0), window_s=1e9)
        self._feed(ff, 0.01)

        drift, sigma = ff.drift_prediction()

        self.assertAlmostEqual(200.0 * 0.01, drift, delta=0.05)
        self.assertGreater(sigma, 0.0)

    def test_an_unusable_model_yields_no_prediction(self) -> None:
        ff = TS.ThermalFeedforward(TS.ThermalDriftModel(200.0, 20.0, evidence_supported=False),
                                   window_s=1e9)
        self._feed(ff, 0.01)

        self.assertIsNone(ff.drift_prediction())

    def test_an_absurd_drift_is_rejected(self) -> None:
        ff = TS.ThermalFeedforward(supported(tempco=100_000.0, sigma=1.0), window_s=1e9)
        self._feed(ff, 1.0)

        self.assertIsNone(ff.drift_prediction())


class FusionTests(unittest.TestCase):
    def test_no_thermal_prediction_leaves_the_filter_untouched(self) -> None:
        result = TS.fuse_drift(0.5, 0.1, None)

        self.assertEqual(0.5, result["drift_ppb_s"])
        self.assertEqual(0.0, result["thermal_weight"])

    def test_a_sharp_filter_estimate_keeps_the_thermal_weight_negligible(self) -> None:
        # The safety property that lets this be enabled at a high Sync rate: a
        # well-observed drift state gives the thermal term almost no say.
        result = TS.fuse_drift(0.5, 0.001, (5.0, 1.0))

        self.assertLess(result["thermal_weight"], 1e-4)
        self.assertAlmostEqual(0.5, result["drift_ppb_s"], places=4)

    def test_a_loose_filter_estimate_gives_the_thermal_term_weight(self) -> None:
        result = TS.fuse_drift(0.5, 10.0, (5.0, 1.0))

        self.assertGreater(result["thermal_weight"], 0.9 - 1e-9)
        self.assertGreater(result["drift_ppb_s"], 0.5)

    def test_weight_rises_monotonically_as_the_filter_loosens(self) -> None:
        weights = [
            TS.fuse_drift(0.5, sigma, (5.0, 1.0))["thermal_weight"]
            for sigma in (0.01, 0.1, 1.0, 5.0)
        ]

        self.assertEqual(weights, sorted(weights))

    def test_one_sensor_can_never_take_the_loop(self) -> None:
        result = TS.fuse_drift(0.5, 1e6, (5.0, 1e-9))

        self.assertLessEqual(result["thermal_weight"], TS.MAX_THERMAL_WEIGHT)
        self.assertNotAlmostEqual(5.0, result["drift_ppb_s"], places=3)

    def test_fusion_lands_between_the_two_estimates(self) -> None:
        result = TS.fuse_drift(0.0, 1.0, (10.0, 1.0))

        self.assertGreater(result["drift_ppb_s"], 0.0)
        self.assertLess(result["drift_ppb_s"], 10.0)

    def test_an_unusable_filter_variance_falls_back_to_the_filter(self) -> None:
        for bad in (0.0, -1.0, float("nan")):
            result = TS.fuse_drift(0.5, bad, (5.0, 1.0))
            self.assertEqual(0.5, result["drift_ppb_s"])
            self.assertEqual(0.0, result["thermal_weight"])

    def test_the_weight_is_derived_not_tuned(self) -> None:
        # Equal information must split the difference exactly, with no constant.
        result = TS.fuse_drift(0.0, 1.0, (1.0, 1.0))

        self.assertAlmostEqual(0.5, result["thermal_weight"])
        self.assertAlmostEqual(0.5, result["drift_ppb_s"])


if __name__ == "__main__":
    unittest.main()
