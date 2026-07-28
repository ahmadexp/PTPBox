import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
# The worker imports the model from the agent directory at run time.
os.environ["PTPBOX_AGENT_DIR"] = str(ROOT / "agent")
SPEC = importlib.util.spec_from_file_location(
    "ptpbox_holdover_compensator_test", ROOT / "scripts" / "ptpbox_holdover_compensator.py")
assert SPEC and SPEC.loader
WORKER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = WORKER
SPEC.loader.exec_module(WORKER)
CONTROL = WORKER.control


class FakeAdjuster:
    """Records what the worker would have written to the clock."""

    def __init__(self, kernel_ppb=-100.0, fail=False):
        self._kernel_ppb = kernel_ppb
        self.applied: list[float] = []
        self.fail = fail

    def kernel_frequency_ppb(self) -> float:
        return self._kernel_ppb

    def set_servo_frequency_ppb(self, correction_ppb: float) -> None:
        if self.fail:
            raise OSError(1, "denied", "/dev/ptp9")
        self.applied.append(correction_ppb)

    def close(self) -> None:
        pass


class Clock:
    def __init__(self, step=1.0):
        self.t = 1000.0
        self.step = step

    def now(self) -> float:
        return self.t

    def sleep(self, _seconds: float) -> None:
        self.t += self.step


def drive(model, temperature=lambda: 85.0, ticks=5, adjuster=None, **kwargs):
    clock = Clock()
    stop = [False]
    counter = {"n": 0}

    def counted():
        counter["n"] += 1
        if counter["n"] >= ticks:
            stop[0] = True
        return temperature()

    adjuster = adjuster or FakeAdjuster()
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory) / "state.json"
        status = WORKER.run(
            model, adjuster, counted, state, "BC6",
            interval_s=kwargs.pop("interval_s", 1.0),
            max_seconds=kwargs.pop("max_seconds", 0.0),
            max_slew_ppb_per_s=kwargs.pop("max_slew_ppb_per_s", 5.0),
            stop=stop, now=clock.now, sleep=clock.sleep, **kwargs)
        payload = json.loads(state.read_text()) if state.exists() else {}
    return adjuster, status, payload


class ModelLoadingTests(unittest.TestCase):
    def test_round_trips_an_evaluator_model(self) -> None:
        model = CONTROL.Model("temperature-drift", intercept_ppb=120.0, tempco_ppb_per_c=3.5,
                              drift_ppb_per_s=0.02, reference_temperature_c=86.0,
                              temperature_min_c=84.0, temperature_max_c=90.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            path.write_text(json.dumps({"model": model.as_dict()}))

            loaded = WORKER.load_model(path)

        self.assertEqual("temperature-drift", loaded.kind)
        self.assertAlmostEqual(3.5, loaded.tempco_ppb_per_c)
        self.assertAlmostEqual(0.02, loaded.drift_ppb_per_s)
        self.assertAlmostEqual(84.0, loaded.temperature_min_c)

    def test_unknown_kind_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            path.write_text(json.dumps({"kind": "quadratic-hunch"}))

            with self.assertRaises(SystemExit):
                WORKER.load_model(path)


class TemperatureReadTests(unittest.TestCase):
    def test_millidegrees_are_converted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "temp1_input"
            path.write_text("101000\n")

            self.assertAlmostEqual(101.0, WORKER.read_temperature(path))

    def test_unreadable_sensor_is_none_not_zero(self) -> None:
        # Zero would look like a very cold card and drag the correction with it.
        self.assertIsNone(WORKER.read_temperature(Path("/nonexistent/temp1_input")))

    def test_garbage_sensor_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "temp1_input"
            path.write_text("n/a\n")

            self.assertIsNone(WORKER.read_temperature(path))


class RunLoopTests(unittest.TestCase):
    def _model(self):
        return CONTROL.Model("temperature", intercept_ppb=200.0, tempco_ppb_per_c=10.0,
                             reference_temperature_c=85.0, temperature_min_c=82.0,
                             temperature_max_c=90.0)

    def test_first_correction_continues_from_the_servo(self) -> None:
        # The kernel had -100 ppb, so the servo correction was +100. Starting
        # anywhere else would step the oscillator at release.
        adjuster, _status, _payload = drive(self._model(), ticks=1,
                                            adjuster=FakeAdjuster(kernel_ppb=-100.0))

        self.assertAlmostEqual(100.0, adjuster.applied[0], delta=1e-9)

    def test_it_slews_toward_the_model_rather_than_jumping(self) -> None:
        adjuster, _status, _payload = drive(self._model(), ticks=4,
                                            adjuster=FakeAdjuster(kernel_ppb=-100.0),
                                            max_slew_ppb_per_s=5.0)

        steps = [abs(b - a) for a, b in zip(adjuster.applied, adjuster.applied[1:])]
        self.assertTrue(steps, "expected several ticks")
        for step in steps:
            self.assertLessEqual(step, 5.0 + 1e-9)

    def test_model_time_is_rebased_to_release(self) -> None:
        # A drift model fitted hours ago must ramp from release, not from its
        # original fit epoch, or the first tick would be wildly extrapolated.
        model = CONTROL.Model("drift", intercept_ppb=100.0, drift_ppb_per_s=1.0,
                              reference_time=0.0)
        _adjuster, _status, payload = drive(model, ticks=1)

        self.assertAlmostEqual(1000.0, payload["released_at"])
        self.assertLess(abs(payload["target_ppb"] - 100.0), 1.0)

    def test_max_seconds_stops_the_worker(self) -> None:
        clock = Clock(step=10.0)
        adjuster = FakeAdjuster()
        with tempfile.TemporaryDirectory() as directory:
            WORKER.run(self._model(), adjuster, lambda: 85.0, Path(directory) / "s.json",
                       "BC6", interval_s=1.0, max_seconds=25.0, max_slew_ppb_per_s=5.0,
                       stop=[False], now=clock.now, sleep=clock.sleep)

        self.assertEqual(3, len(adjuster.applied), "must stop once past the horizon")

    def test_a_failed_write_is_recorded_and_does_not_kill_the_loop(self) -> None:
        adjuster, status, payload = drive(self._model(), ticks=3,
                                          adjuster=FakeAdjuster(fail=True))

        self.assertEqual([], adjuster.applied)
        self.assertFalse(payload["applied"])
        self.assertIn("denied", payload["error"])

    def test_missing_sensor_does_not_stall_the_controller(self) -> None:
        adjuster, _status, payload = drive(self._model(), temperature=lambda: None, ticks=3)

        self.assertTrue(adjuster.applied)
        self.assertIsNone(payload["temperature_c"])

    def test_state_file_reports_the_armed_model(self) -> None:
        _adjuster, _status, payload = drive(self._model(), ticks=2)

        self.assertEqual("holdover-compensator", payload["servo"])
        self.assertEqual("temperature", payload["model"]["kind"])
        self.assertEqual("BC6", payload["node"])
        self.assertGreaterEqual(payload["ticks"], 1)


if __name__ == "__main__":
    unittest.main()
