import importlib.util
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_research.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_research", MODULE_PATH)
assert SPEC and SPEC.loader
RESEARCH = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RESEARCH
SPEC.loader.exec_module(RESEARCH)


class StabilityTests(unittest.TestCase):
    def test_linear_phase_ramp_has_zero_allan_deviation_and_measured_mtie(self) -> None:
        phase = [12.0 * index for index in range(256)]
        metrics = RESEARCH.stability_metrics(phase, 1.0)

        self.assertGreater(len(metrics["adev"]), 3)
        self.assertTrue(all(point["value"] < 1e-18 for point in metrics["adev"]))
        self.assertAlmostEqual(12.0, metrics["mtie"][0]["value"])
        self.assertAlmostEqual(48.0, next(point["value"] for point in metrics["mtie"] if point["tau_s"] == 4.0))

    def test_time_deviation_retains_nanosecond_units(self) -> None:
        phase = [math.sin(index * 0.23) * 30 for index in range(512)]
        metrics = RESEARCH.stability_metrics(phase, 0.5)

        self.assertTrue(metrics["tdev"])
        self.assertTrue(all(point["value"] >= 0 for point in metrics["tdev"]))
        self.assertTrue(all(point["pairs"] > 0 for point in metrics["tdev"]))

    def test_reference_vector_matches_clock_stability_definitions(self) -> None:
        phase = [
            13.0 * math.sin(index * 0.19)
            + 0.08 * index * index
            - 1.7 * index
            + 3.0 * math.cos(index * 0.071)
            for index in range(128)
        ]
        metrics = RESEARCH.stability_metrics(phase, 0.5)
        expected_at_eight_seconds = {
            "adev": 4.91981234499e-09,
            "mdev": 4.35037157904e-09,
            "hdev": 3.73741242760e-09,
            "pdev": 5.36651060117e-09,
            "totdev": 4.42554501991e-09,
        }
        for metric, expected in expected_at_eight_seconds.items():
            point = next(item for item in metrics[metric] if item["tau_s"] == 8.0)
            self.assertAlmostEqual(expected, point["value"], delta=expected * 1e-10, msg=metric)
            self.assertIsNone(point["confidence"])

        # Theo1's first permitted octave factor is m=16. NIST SP 1065
        # assigns it the effective averaging time 0.75*m*tau0 = 6 s.
        self.assertAlmostEqual(6.0, metrics["theo1"][0]["tau_s"])
        self.assertAlmostEqual(2.692152281409684e-09, metrics["theo1"][0]["value"], delta=3e-19)
        self.assertEqual(896, metrics["theo1"][0]["pairs"])

    def test_time_error_rms_is_the_rms_of_tau_spaced_phase_differences(self) -> None:
        phase = [3.0, -1.0, 8.0, 2.0, 7.0, 12.0]
        metrics = RESEARCH.stability_metrics(phase, 1.0)
        point = next(item for item in metrics["tierms"] if item["tau_s"] == 2.0)
        expected = math.sqrt(sum((phase[index + 2] - phase[index]) ** 2 for index in range(4)) / 4)

        self.assertAlmostEqual(expected, point["value"])
        self.assertEqual(4, point["pairs"])

    def test_stability_summary_reports_detrended_phase_and_never_fakes_confidence(self) -> None:
        phase = [0.04 * index * index + 3.0 * index + 7.0 * math.sin(index * 0.17) for index in range(256)]
        metrics = RESEARCH.stability_metrics(phase, 0.5)
        summary = RESEARCH.clock_stability_summary(phase, 0.5, metrics)

        self.assertEqual("ready", summary["status"])
        self.assertEqual(9, len(summary["metrics_ready"]))
        self.assertAlmostEqual(127.5, summary["record_span_s"])
        self.assertGreater(summary["detrended_rms_ns"], 0)
        self.assertTrue(summary["noise_regions"])
        self.assertIn("candidates", summary["interpretation"])
        self.assertTrue(all(point["confidence"] is None for values in metrics.values() for point in values))


class EstimationTests(unittest.TestCase):
    def test_factor_graph_fuses_direct_and_adjacent_observations(self) -> None:
        observations = [
            RESEARCH.Observation("BC1", "BC2", 10.0, 2.0, "direct"),
            RESEARCH.Observation("BC1", "BC3", 31.0, 3.0, "direct"),
            RESEARCH.Observation("BC2", "BC3", 20.0, 1.0, "hop"),
        ]
        result = RESEARCH.factor_graph_fusion(["BC1", "BC2", "BC3"], observations, "BC1")

        self.assertEqual("solved", result["status"])
        self.assertAlmostEqual(10.4, result["nodes"]["BC2"]["offset_ns"], delta=0.5)
        self.assertAlmostEqual(30.5, result["nodes"]["BC3"]["offset_ns"], delta=0.5)
        self.assertGreater(result["nodes"]["BC3"]["sigma_ns"], 0)

    def test_three_state_kalman_tracks_frequency_and_drift(self) -> None:
        estimator = RESEARCH.AdaptiveKalman3(
            measurement_noise_ns=2.0,
            process_noise_ppb_s=0.3,
            drift_noise_ppb_s2=0.02,
            innovation_gate_sigma=10.0,
        )
        phase = 0.0
        frequency = 25.0
        for index in range(1, 601):
            frequency += 0.01
            phase += frequency
            status = estimator.update(phase + ((index * 13) % 7 - 3) * 0.25, float(index))

        self.assertEqual("locked", status["state"])
        self.assertAlmostEqual(frequency, status["frequency_estimate_ppb"], delta=2.0)
        self.assertAlmostEqual(0.01, status["drift_estimate_ppb_s"], delta=0.02)
        self.assertGreater(status["accepted_count"], 500)

    def test_three_state_kalman_reacquires_after_a_persistent_regime_step(self) -> None:
        estimator = RESEARCH.AdaptiveKalman3(
            measurement_noise_ns=5.0,
            process_noise_ppb_s=0.2,
            drift_noise_ppb_s2=0.01,
            innovation_gate_sigma=4.0,
        )
        for index in range(1, 20):
            estimator.update(0.0, float(index))
        states = [
            estimator.update(50_000.0 + 2_000.0 * index, float(20 + index))["state"]
            for index in range(8)
        ]

        self.assertIn("reacquiring", states)

    def test_imm_probabilities_are_normalized(self) -> None:
        estimator = RESEARCH.InteractingMultipleModel(10.0)
        for index in range(1, 40):
            result = estimator.update(float(index % 3), float(index))

        self.assertAlmostEqual(1.0, sum(result["model_probabilities"].values()), places=9)
        self.assertIn(result["regime"], {"quiet", "dynamic", "holdover"})


class DiagnosticsTests(unittest.TestCase):
    def test_bayesian_change_detector_finds_a_step(self) -> None:
        values = [0.1 * math.sin(index) for index in range(80)] + [120.0 + 0.1 * math.sin(index) for index in range(80)]
        result = RESEARCH.bayesian_change_points(values, hazard=1 / 200)

        self.assertTrue(any(75 <= index <= 85 for index in result["change_points"]))

    def test_recurrence_and_koopman_return_auditable_matrices(self) -> None:
        channels = [
            [math.sin(index * 0.2 + phase) for index in range(160)]
            for phase in (0.0, 0.7, 1.4)
        ]
        recurrence = RESEARCH.recurrence_analysis(channels, max_points=48)
        koopman = RESEARCH.koopman_dmd(channels)

        self.assertEqual("ready", recurrence["status"])
        self.assertEqual(48, len(recurrence["matrix"]))
        self.assertTrue(0 < recurrence["recurrence_rate"] < 1)
        self.assertEqual("ready", koopman["status"])
        self.assertEqual(3, len(koopman["operator"]))
        self.assertTrue(koopman["singular_values"])

    def test_bifurcation_sweep_uses_settled_replay_and_never_changes_live_gains(self) -> None:
        samples = [80.0 * math.sin(index / 7.0) + 0.08 * index for index in range(160)]
        result = RESEARCH.replay_bifurcation_analysis(
            samples,
            1.0,
            0.7,
            0.3,
            active_controller="adaptive-kalman",
        )

        self.assertEqual("ready", result["status"])
        self.assertEqual(0, result["live_changes"])
        self.assertEqual("PI gain scale", result["parameter"])
        self.assertGreaterEqual(len(result["summaries"]), 40)
        self.assertTrue(result["points"])
        self.assertAlmostEqual(1.0, result["current"]["gain_scale"], places=6)
        self.assertEqual("adaptive-kalman", result["active_controller"])
        self.assertFalse(result["baseline_is_live"])
        self.assertTrue(any(item["stable"] for item in result["summaries"]))
        self.assertTrue(all(0.25 <= point["gain_scale"] <= 2.5 for point in result["points"]))
        self.assertIn("true hardware bifurcation", result["interpretation"])

    def test_bifurcation_sweep_waits_for_an_auditable_sample_window(self) -> None:
        result = RESEARCH.replay_bifurcation_analysis([1.0, 2.0, 3.0], 1.0, 0.7, 0.3)

        self.assertEqual("learning", result["status"])
        self.assertEqual([], result["points"])
        self.assertEqual(0, result["live_changes"])

    def test_fractal_analysis_separates_trace_roughness_and_attractor_estimates(self) -> None:
        smooth = [math.sin(index * 0.11) + 0.03 * math.sin(index * 0.017) for index in range(512)]
        generator = random.Random(41)
        noise = [generator.gauss(0.0, 1.0) for _ in range(512)]
        smooth_result = RESEARCH.fractal_analysis(smooth)
        noise_result = RESEARCH.fractal_analysis(noise)

        self.assertEqual("ready", smooth_result["status"])
        self.assertEqual(0, smooth_result["live_changes"])
        self.assertGreater(
            noise_result["higuchi"]["dimension"],
            smooth_result["higuchi"]["dimension"],
        )
        self.assertTrue(smooth_result["correlation"]["converged"])
        self.assertFalse(noise_result["correlation"]["converged"])
        self.assertGreaterEqual(len(smooth_result["correlation"]["embeddings"]), 4)
        self.assertEqual(6, smooth_result["multifractal"]["surrogate_count"])
        self.assertIn("not, by itself", smooth_result["interpretation"])

    def test_fractal_analysis_reports_partial_learning_thresholds(self) -> None:
        result = RESEARCH.fractal_analysis([math.sin(index) for index in range(40)])

        self.assertEqual("partial", result["status"])
        self.assertEqual("ready", result["higuchi"]["status"])
        self.assertEqual("learning", result["correlation"]["status"])
        self.assertEqual("learning", result["multifractal"]["status"])
        self.assertEqual(0, result["live_changes"])

    def test_attractor_reconstruction_selects_delay_embedding_and_recurrent_cores(self) -> None:
        values = [
            75.0 * math.sin(index * 0.17)
            + 24.0 * math.sin(index * 0.071 + 0.4)
            + 4.0 * math.sin(index * 0.013)
            for index in range(512)
        ]
        result = RESEARCH.attractor_reconstruction_analysis(
            values,
            0.5,
            dimension_plateau=True,
        )

        self.assertEqual("ready", result["status"])
        self.assertGreaterEqual(result["delay_samples"], 1)
        self.assertGreaterEqual(result["embedding_dimension"], 2)
        self.assertTrue(result["ami_curve"])
        self.assertTrue(result["fnn_curve"])
        self.assertGreater(len(result["embedding"]), 100)
        self.assertTrue(result["return_map"])
        self.assertTrue(result["evidence"]["stationary_window"])
        self.assertIn(
            result["evidence"]["verdict"],
            {"reconstructed", "recurrent_structure", "candidate_attractor", "inconclusive"},
        )
        self.assertTrue(result["evidence"]["dimension_plateau"])
        self.assertEqual(0, result["live_changes"])
        self.assertIn("not a chaos classifier", result["interpretation"])

    def test_attractor_candidate_is_suppressed_during_a_regime_change(self) -> None:
        values = [
            math.sin(index * 0.17) + 0.2 * math.sin(index * 0.071)
            for index in range(384)
        ]
        result = RESEARCH.attractor_reconstruction_analysis(
            values,
            1.0,
            dimension_plateau=True,
            stationary=False,
        )

        self.assertFalse(result["evidence"]["stationary_window"])
        self.assertNotEqual("candidate_attractor", result["evidence"]["verdict"])

    def test_attractor_reconstruction_waits_for_a_defensible_record(self) -> None:
        result = RESEARCH.attractor_reconstruction_analysis(
            [math.sin(index * 0.2) for index in range(40)],
            1.0,
        )

        self.assertEqual("learning", result["status"])
        self.assertEqual([], result["embedding"])
        self.assertEqual([], result["cores"])
        self.assertEqual(0, result["live_changes"])

    def test_temperature_model_predicts_frequency_sensitivity(self) -> None:
        timestamps = [float(index) for index in range(100)]
        temperatures = [35.0 + index * 0.02 for index in range(100)]
        phase = [0.0]
        for index in range(1, 100):
            frequency = 5.0 + 0.8 * temperatures[index]
            phase.append(phase[-1] + frequency)
        result = RESEARCH.temperature_holdover_model(timestamps, phase, temperatures, 60.0)

        self.assertEqual("ready", result["status"])
        self.assertAlmostEqual(5.0 + 0.8 * temperatures[-1], result["predicted_frequency_ppb"], delta=0.5)

    def test_frequency_domain_analysis_uses_sampled_data_stability_first(self) -> None:
        result = RESEARCH.arx_frequency_domain_diagnostics(
            a1=1.5,
            a2=-0.56,
            b1=0.08,
            b2=0.02,
            sample_period_s=0.5,
            sample_count=256,
        )

        self.assertEqual("ready", result["status"])
        self.assertEqual(72, len(result["frequency_response"]["points"]))
        self.assertAlmostEqual(1.0, result["frequency_response"]["nyquist_frequency_hz"])
        self.assertTrue(result["discrete_stability"]["stable"])
        self.assertTrue(all(condition["pass"] for condition in result["discrete_stability"]["conditions"]))
        self.assertTrue(result["routh_hurwitz"]["stable"])
        self.assertEqual(0, result["routh_hurwitz"]["sign_changes"])
        self.assertTrue(result["nyquist"]["minus_one_reference_only"])
        self.assertEqual("not-evaluated", result["nyquist"]["encirclement_claim"])
        self.assertIn("open-loop transfer", result["nyquist"]["interpretation"])

    def test_routh_equivalent_and_jury_reject_an_unstable_arx_model(self) -> None:
        result = RESEARCH.arx_frequency_domain_diagnostics(
            a1=1.4,
            a2=-0.2,
            b1=0.05,
            b2=0.01,
            sample_period_s=1.0,
            sample_count=128,
        )

        self.assertFalse(result["discrete_stability"]["stable"])
        self.assertFalse(result["routh_hurwitz"]["stable"])
        self.assertGreater(result["routh_hurwitz"]["sign_changes"], 0)
        self.assertTrue(any(not condition["pass"] for condition in result["discrete_stability"]["conditions"]))

    def test_identified_arx_publishes_auditable_frequency_response(self) -> None:
        inputs = [math.sin(index * 0.17) + 0.3 * math.sin(index * 0.051) for index in range(320)]
        outputs = [0.0, 0.0]
        for index in range(2, len(inputs)):
            outputs.append(
                1.45 * outputs[-1]
                - 0.52 * outputs[-2]
                + 0.07 * inputs[index - 1]
                + 0.015 * inputs[index - 2]
            )
        result = RESEARCH.identify_arx(inputs, outputs, 0.5)

        self.assertEqual("stable", result["status"])
        self.assertGreater(result["r_squared"], 0.99)
        self.assertEqual(
            "measured servo frequency correction",
            result["frequency_domain"]["model"]["input"],
        )
        self.assertEqual(
            "identified from measured frequency correction to raw PHC phase offset",
            result["frequency_domain"]["provenance"],
        )

    def test_bayesian_tuner_is_replay_bounded_and_never_changes_live_gains(self) -> None:
        samples = [80.0 * math.sin(index / 7.0) + 0.08 * index for index in range(160)]
        result = RESEARCH.safe_bayesian_tune(samples, 1.0, 0.7, 0.3)

        self.assertEqual("recommended", result["status"])
        self.assertIn("Gaussian-process", result["method"])
        self.assertEqual(0, result["live_changes"])
        self.assertLess(result["evaluated_candidates"], result["candidate_space"])
        self.assertLessEqual(result["evaluated_candidates"], 20)

    def test_error_budget_propagates_cross_hop_covariance(self) -> None:
        first = [float(index) for index in range(20)]
        second = [2.0 * value for value in first]
        result = RESEARCH.error_budget(
            ["BC1", "BC2", "BC3"],
            {"BC2": 1.0, "BC3": 1.0},
            {"BC2": 2.0, "BC3": 2.0},
            {"BC2": 3.0, "BC3": 3.0},
            {},
            [first, second],
        )

        self.assertIsNotNone(result["cascade"])
        self.assertGreater(
            result["cascade"]["correlated_sigma_ns"],
            result["cascade"]["independent_sigma_ns"],
        )


class ExperimentStoreTests(unittest.TestCase):
    def test_records_raw_cycles_and_exports_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RESEARCH.ExperimentStore(Path(temporary) / "experiments.sqlite3")
            run = store.start({"name": "raw baseline", "kind": "metrology"}, {"profile": "G.8275.1"})
            store.record_phc(
                {
                    "sample_id": "phc:1",
                    "clocks": [
                        {
                            "id": "BC1",
                            "observed_at": 1.0,
                            "offset_ns": 0.0,
                            "previous_hop_offset_ns": None,
                            "comparison_uncertainty_ns": 2.0,
                            "valid": True,
                        },
                        {
                            "id": "BC2",
                            "observed_at": 1.0,
                            "offset_ns": 8.0,
                            "previous_hop_offset_ns": 8.0,
                            "comparison_uncertainty_ns": 3.0,
                            "valid": True,
                        },
                    ],
                },
                {"BC2": 43.2},
            )
            completed = store.stop(run["id"])
            exported = store.export_csv(run["id"])

            self.assertEqual("completed", completed["state"])
            self.assertEqual(2, completed["sample_count"])
            self.assertIn("BC2", exported)
            self.assertIn("43.2", exported)
            samples = store.phc_samples(run["id"], since=0.5)
            self.assertEqual(["BC1", "BC2"], [sample["clock_id"] for sample in samples])
            self.assertEqual(8.0, samples[1]["offset_ns"])
            self.assertEqual(3.0, samples[1]["uncertainty_ns"])
            summary = store.phc_holdover_summary(run["id"], 0.5, ["BC2"])
            self.assertEqual(1, summary[0]["samples"])
            self.assertEqual(8.0, summary[0]["latest_offset_ns"])
            series, cycles, stride = store.phc_holdover_series(run["id"], 0.5, ["BC2"], max_cycles=1)
            self.assertEqual(1, cycles)
            self.assertEqual(1, stride)
            self.assertEqual("BC2", series[0]["clock_id"])
            self.assertIsNone(store.active())


class EventMonitorTests(unittest.TestCase):
    def test_timestamp_parser_preserves_nanosecond_precision(self) -> None:
        monitor_path = Path(__file__).parents[1] / "scripts" / "ptpbox_event_monitor.py"
        spec = importlib.util.spec_from_file_location("ptpbox_event_monitor", monitor_path)
        assert spec and spec.loader
        monitor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(monitor)

        self.assertEqual(1_725_000_000_123_456_789, monitor.timestamp_ns("1725000000.123456789"))


if __name__ == "__main__":
    unittest.main()
