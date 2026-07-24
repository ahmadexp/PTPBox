import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ptpbox_kalman_servo.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_kalman_servo", MODULE_PATH)
assert SPEC and SPEC.loader
KALMAN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = KALMAN
SPEC.loader.exec_module(KALMAN)


class KalmanServoTests(unittest.TestCase):
    def test_reacquisition_keeps_phase_samples_with_negative_delay_estimates(self) -> None:
        sample = KALMAN.parse_log_sample(
            "ptp4l[294783.152]: master offset 2386 s0 freq +1408 path delay -224"
        )

        self.assertEqual((2386.0, 294783.152, -224.0), sample)
        self.assertIsNone(
            KALMAN.parse_log_sample(
                "ptp4l[294783.152]: master offset 2386 s0 freq +1408 path delay 1000001"
            )
        )

    def test_tracks_phase_and_oscillator_frequency_under_closed_loop_control(self) -> None:
        servo = KALMAN.KalmanServo(
            measurement_noise_ns=20.0,
            process_noise_ppb=0.5,
            phase_time_constant_s=4.0,
            max_frequency_ppb=200_000.0,
            innovation_gate_sigma=6.0,
        )
        phase_ns = 500.0
        oscillator_ppb = 120.0

        for index in range(1, 301):
            phase_ns += oscillator_ppb - servo.last_correction_ppb
            deterministic_noise = ((index * 17) % 11 - 5) * 1.5
            status = servo.update(phase_ns + deterministic_noise, float(index))

        self.assertEqual("locked", status["state"])
        self.assertLess(abs(phase_ns), 2.0)
        self.assertAlmostEqual(oscillator_ppb, status["frequency_estimate_ppb"], delta=2.0)
        self.assertAlmostEqual(oscillator_ppb, status["correction_ppb"], delta=2.0)
        self.assertEqual(4.0, status["locked_since_source_time"])

    def test_rejects_large_innovation_without_changing_frequency_command(self) -> None:
        servo = KALMAN.KalmanServo(20.0, 0.5, 4.0, 200_000.0, 6.0)
        for index in range(1, 20):
            servo.update(10.0, float(index))
        previous = servo.last_correction_ppb

        status = servo.update(1_000_000.0, 20.0)

        self.assertEqual("innovation-gated", status["state"])
        self.assertFalse(status["measurement_accepted"])
        self.assertEqual(1, status["rejected_count"])
        self.assertEqual(previous, status["correction_ppb"])

    def test_frequency_command_is_bounded(self) -> None:
        servo = KALMAN.KalmanServo(1.0, 1.0, 0.1, 50.0, 100.0)
        status = servo.update(1_000_000.0, 1.0)
        self.assertEqual(50.0, status["correction_ppb"])

    def test_identification_excitation_is_deterministic_bounded_and_targeted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "identification.json"
            path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "target": "BC4",
                        "amplitude_ppb": 30.0,
                        "frequencies_hz": [0.01, 0.04, 0.09],
                        "started_at": 100.0,
                        "expires_at": 400.0,
                        "offset_limit_ns": 5_000.0,
                    }
                ),
                encoding="utf-8",
            )

            first, state = KALMAN.identification_excitation(path, "BC4", 20.0, 135.0)
            repeated, _state = KALMAN.identification_excitation(path, "BC4", 20.0, 135.0)
            wrong_target, _state = KALMAN.identification_excitation(path, "BC3", 20.0, 135.0)

            self.assertEqual(first, repeated)
            self.assertLessEqual(abs(first), 30.0)
            self.assertEqual(0.0, wrong_target)
            self.assertTrue(state["enabled"])

    def test_identification_excitation_aborts_before_applying_an_unsafe_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "identification.json"
            path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "target": "BC4",
                        "amplitude_ppb": 30.0,
                        "frequencies_hz": [0.02],
                        "started_at": 100.0,
                        "expires_at": 400.0,
                        "offset_limit_ns": 1_000.0,
                    }
                ),
                encoding="utf-8",
            )

            correction, state = KALMAN.identification_excitation(path, "BC4", 1_001.0, 140.0)
            persisted = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(0.0, correction)
            self.assertFalse(state["enabled"])
            self.assertFalse(persisted["enabled"])
            self.assertIn("exceeded", persisted["reason"])


if __name__ == "__main__":
    unittest.main()
