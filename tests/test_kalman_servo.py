import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ptpbox_kalman_servo.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_kalman_servo", MODULE_PATH)
assert SPEC and SPEC.loader
KALMAN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = KALMAN
SPEC.loader.exec_module(KALMAN)


class KalmanServoTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
