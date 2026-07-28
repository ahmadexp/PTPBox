import importlib.util
import math
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_holdover_control.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_holdover_control_test", MODULE_PATH)
assert SPEC and SPEC.loader
HC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HC
SPEC.loader.exec_module(HC)


def series(count=240, step=1.0, temperature=None, correction=None):
    """Build (time, temperature_c, correction_ppb) rows."""
    rows = []
    for index in range(count):
        t = index * step
        temp = temperature(index, t)
        rows.append((t, temp, correction(index, t, temp)))
    return rows


class SmoothingTests(unittest.TestCase):
    def test_filter_is_trailing_so_it_never_sees_the_future(self) -> None:
        # A centred filter would let a later spike change an earlier output, which
        # the live controller cannot do.
        values = [0.0, 0.0, 0.0, 100.0]
        out = HC.smooth(values, window=5)

        self.assertEqual(0.0, out[0])
        self.assertEqual(0.0, out[1])
        self.assertAlmostEqual(25.0, out[3])

    def test_window_of_one_is_a_passthrough(self) -> None:
        self.assertEqual([1.0, 2.0], HC.smooth([1.0, 2.0], window=1))


class SelectionTests(unittest.TestCase):
    def test_genuine_temperature_coefficient_is_armed(self) -> None:
        # Temperature swings non-monotonically so its effect is separable from
        # elapsed time; a pure ramp would be collinear with ageing by construction.
        rows = series(
            temperature=lambda i, t: 80.0 + 4.0 * math.sin(t / 25.0),
            correction=lambda i, t, temp: 100.0 + 8.0 * (temp - 80.0),
        )

        result = HC.evaluate(rows)

        self.assertEqual("ready", result.status, result.reason)
        self.assertIn("temperature", result.armed_kind)
        self.assertGreater(result.benefit_pct, HC.MIN_BENEFIT_PCT)
        self.assertAlmostEqual(8.0, result.model.tempco_ppb_per_c, delta=1.0)

    def test_pure_ageing_ramp_selects_the_drift_model(self) -> None:
        rows = series(
            temperature=lambda i, t: 80.0 + 4.0 * math.sin(t / 25.0),
            correction=lambda i, t, temp: 100.0 + 0.5 * t,
        )

        result = HC.evaluate(rows)

        self.assertEqual("ready", result.status, result.reason)
        self.assertIn("drift", result.armed_kind)
        self.assertAlmostEqual(0.5, result.model.drift_ppb_per_s, delta=0.05)

    def test_no_relationship_is_refused_rather_than_fitted(self) -> None:
        # This is the reference-host case: a stable oscillator plus sensor noise.
        # Fitting it would produce a coefficient that makes holdover worse.
        noise = [((index * 7919) % 101 - 50) / 50.0 for index in range(240)]
        rows = series(
            temperature=lambda i, t: 80.0 + 4.0 * math.sin(t / 25.0),
            correction=lambda i, t, temp: 100.0 + noise[i],
        )

        result = HC.evaluate(rows)

        self.assertEqual("refused", result.status)
        self.assertIsNone(result.armed_kind)
        self.assertIn("frozen", result.reason)

    def test_quantisation_dither_cannot_be_mined_for_a_coefficient(self) -> None:
        # Raw readings alternate across a 2 degC gap, which would pass the span
        # gate unsmoothed. Smoothing collapses the dither, so temperature terms
        # become inadmissible instead of yielding a spurious fit.
        raw = [80.0 if index % 2 == 0 else 82.0 for index in range(240)]
        rows = series(
            temperature=lambda i, t: raw[i],
            correction=lambda i, t, temp: 100.0 + 40.0 * (temp - 81.0),
        )

        self.assertAlmostEqual(2.0, max(raw) - min(raw), msg="raw dither spans the gate")
        smoothed = HC.smooth(raw)
        self.assertLess(max(smoothed) - min(smoothed), HC.MIN_TEMPERATURE_SPAN_C)

        result = HC.evaluate(rows)

        self.assertEqual("refused", result.status)
        self.assertIn("inadmissible", result.reason)

    def test_narrow_temperature_span_excludes_temperature_candidates(self) -> None:
        rows = series(
            temperature=lambda i, t: 80.0 + 0.2 * math.sin(t / 25.0),
            correction=lambda i, t, temp: 100.0 + 8.0 * (temp - 80.0),
        )

        result = HC.evaluate(rows)

        kinds = {item["kind"] for item in result.candidates}
        self.assertNotIn("temperature", kinds)
        self.assertFalse(result.diagnostics["temperature_admissible"])

    def test_short_record_reports_learning(self) -> None:
        rows = series(count=20, temperature=lambda i, t: 80.0, correction=lambda i, t, temp: 100.0)

        result = HC.evaluate(rows)

        self.assertEqual("learning", result.status)

    def test_samples_without_temperature_are_dropped_not_defaulted(self) -> None:
        rows = series(
            temperature=lambda i, t: None if i % 2 else 80.0 + 4.0 * math.sin(t / 25.0),
            correction=lambda i, t, temp: 100.0 + 8.0 * ((temp or 80.0) - 80.0),
        )

        result = HC.evaluate(rows)

        self.assertEqual(120, result.diagnostics["samples"])

    def test_scoring_uses_several_rolling_origins(self) -> None:
        rows = series(
            temperature=lambda i, t: 80.0 + 4.0 * math.sin(t / 25.0),
            correction=lambda i, t, temp: 100.0 + 8.0 * (temp - 80.0),
        )

        result = HC.evaluate(rows)

        self.assertGreaterEqual(result.folds, 3)
        self.assertEqual(result.folds, result.diagnostics["folds"])
        for item in result.candidates:
            self.assertEqual(result.folds, len(item["per_fold_rms_ppb"]))

    def test_every_origin_trains_only_on_its_past(self) -> None:
        splits = HC.rolling_origins(240)

        self.assertTrue(splits)
        for train, test in splits:
            self.assertEqual(0, train.start)
            self.assertEqual(train.stop, test.start, "test block must follow the train block")
            self.assertGreaterEqual(train.stop - train.start, HC.MIN_TRAIN_SAMPLES)
            self.assertGreaterEqual(test.stop - test.start, HC.MIN_HOLDOUT_SAMPLES)

    def test_a_gain_on_one_origin_only_is_not_enough_to_arm(self) -> None:
        # The flaw this replaced: a single split could be flattered by where it
        # fell. A candidate that wins one fold and loses the rest must be refused.
        rows = series(
            temperature=lambda i, t: 80.0 + 4.0 * math.sin(t / 25.0),
            # Constant except for one late excursion, which only the last origin sees.
            correction=lambda i, t, temp: 100.0 + (60.0 if i > 210 else 0.0),
        )

        result = HC.evaluate(rows)

        self.assertEqual("refused", result.status)
        self.assertIn("rolling origins", result.reason)

    def test_the_verdict_is_stable_across_fold_counts(self) -> None:
        # A genuine relationship must survive changing the fold count; the old
        # single-split selector flipped its answer with the split position.
        rows = series(
            temperature=lambda i, t: 80.0 + 4.0 * math.sin(t / 25.0),
            correction=lambda i, t, temp: 100.0 + 8.0 * (temp - 80.0),
        )

        verdicts = {HC.evaluate(rows, folds=count).status for count in (3, 4, 5, 6)}

        self.assertEqual({"ready"}, verdicts)

    def test_reported_benefit_is_measured_against_frozen(self) -> None:
        rows = series(
            temperature=lambda i, t: 80.0 + 4.0 * math.sin(t / 25.0),
            correction=lambda i, t, temp: 100.0 + 8.0 * (temp - 80.0),
        )

        result = HC.evaluate(rows)

        self.assertLess(result.best_rms_ppb, result.frozen_rms_ppb)
        self.assertGreaterEqual(result.benefit_pct, HC.MIN_BENEFIT_PCT)
        winner = next(item for item in result.candidates if item["kind"] == result.armed_kind)
        self.assertGreaterEqual(winner["fold_agreement"], HC.MIN_FOLD_AGREEMENT)


class ModelGuardTests(unittest.TestCase):
    def test_temperature_is_not_extrapolated_far_past_the_fitted_range(self) -> None:
        model = HC.Model("temperature", intercept_ppb=0.0, tempco_ppb_per_c=10.0,
                         reference_temperature_c=80.0, temperature_min_c=78.0,
                         temperature_max_c=82.0)

        limit = model.predict_ppb(0.0, 82.0 + HC.TEMPERATURE_EXTRAPOLATION_MARGIN_C)

        self.assertEqual(limit, model.predict_ppb(0.0, 500.0), "runaway sensor must clamp")

    def test_ageing_ramp_stops_extrapolating_past_the_horizon(self) -> None:
        model = HC.Model("drift", intercept_ppb=0.0, drift_ppb_per_s=1.0, reference_time=0.0)

        capped = model.predict_ppb(HC.MAX_DRIFT_HORIZON_S, None)

        self.assertAlmostEqual(HC.MAX_DRIFT_HORIZON_S, capped)
        self.assertAlmostEqual(capped, model.predict_ppb(HC.MAX_DRIFT_HORIZON_S * 10, None))

    def test_prediction_is_bounded(self) -> None:
        model = HC.Model("drift", intercept_ppb=0.0, drift_ppb_per_s=1e9, reference_time=0.0)

        self.assertLessEqual(abs(model.predict_ppb(10.0, None)), HC.MAX_CORRECTION_PPB)

    def test_frozen_model_averages_the_tail_not_one_sample(self) -> None:
        times = [float(i) for i in range(10)]
        temps = [80.0] * 10
        corrections = [100.0] * 9 + [900.0]

        model = HC._fit("frozen", times, temps, corrections)

        self.assertLess(model.intercept_ppb, 300.0, "a single outlier must not define holdover")


class CompensatorTests(unittest.TestCase):
    def _model(self):
        return HC.Model("temperature", intercept_ppb=100.0, tempco_ppb_per_c=10.0,
                        reference_temperature_c=80.0, temperature_min_c=75.0,
                        temperature_max_c=85.0)

    def test_first_tick_keeps_what_the_servo_applied(self) -> None:
        # Release has to be seamless even when the model disagrees with the
        # servo's last correction, or holdover starts with a frequency step.
        comp = HC.Compensator(self._model(), released_at=0.0, initial_ppb=100.0,
                              max_slew_ppb_per_s=5.0)

        status = comp.tick(0.0, 85.0)

        self.assertAlmostEqual(100.0, status["applied_ppb"])
        self.assertNotAlmostEqual(100.0, status["target_ppb"])
        self.assertTrue(status["slew_limited"])

    def test_slew_limit_bounds_a_sensor_step(self) -> None:
        comp = HC.Compensator(self._model(), released_at=0.0, initial_ppb=100.0,
                              max_slew_ppb_per_s=1.0)
        comp.tick(0.0, 80.0)

        status = comp.tick(1.0, 85.0)

        self.assertLessEqual(abs(status["applied_ppb"] - 100.0), 1.0 + 1e-9)

    def test_repeated_ticks_converge_toward_the_target(self) -> None:
        comp = HC.Compensator(self._model(), released_at=0.0, initial_ppb=100.0,
                              max_slew_ppb_per_s=5.0)
        for index in range(60):
            status = comp.tick(float(index), 83.0)

        self.assertAlmostEqual(status["target_ppb"], status["applied_ppb"], delta=0.5)

    def test_missing_temperature_holds_the_last_reading(self) -> None:
        comp = HC.Compensator(self._model(), released_at=0.0, initial_ppb=100.0)
        comp.tick(0.0, 82.0)

        status = comp.tick(1.0, None)

        self.assertIsNotNone(status["smoothed_temperature_c"])

    def test_controller_uses_the_same_filter_width_as_training(self) -> None:
        comp = HC.Compensator(self._model(), released_at=0.0, initial_ppb=100.0)
        for value in (80.0, 82.0, 80.0, 82.0, 80.0, 82.0, 80.0):
            status = comp.tick(0.0, value)

        self.assertLessEqual(len(comp._temperatures), HC.SMOOTHING_SAMPLES)
        self.assertAlmostEqual(81.0, status["smoothed_temperature_c"], delta=0.5)


if __name__ == "__main__":
    unittest.main()
