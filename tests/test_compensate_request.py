"""The compensation request crosses the privilege boundary, so root must not
trust it. These cover the validators that stand between the HTTP surface and a
process running as root."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
os.environ.setdefault("PTPBOX_CONTROL_STATE", tempfile.mkdtemp())
SPEC = importlib.util.spec_from_file_location("ptpboxctl_test", ROOT / "scripts" / "ptpboxctl.py")
assert SPEC and SPEC.loader
CTL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CTL
SPEC.loader.exec_module(CTL)


class TemperatureFileValidationTests(unittest.TestCase):
    def test_a_path_outside_hwmon_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            CTL._validated_temperature_file("/etc/shadow")

        self.assertIn("temperature input", str(caught.exception))

    def test_a_traversal_dressed_as_a_sensor_is_refused(self) -> None:
        # The basename passes the pattern, but resolution must move it out of
        # /sys/class/hwmon and so it has to be rejected.
        with self.assertRaises(ValueError):
            CTL._validated_temperature_file("/sys/class/hwmon/../../../tmp/temp1_input")

    def test_a_non_temperature_file_inside_hwmon_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            CTL._validated_temperature_file("/sys/class/hwmon/hwmon0/name")

    def test_empty_and_non_string_are_refused(self) -> None:
        for value in ("", None, 42, ["/sys/class/hwmon/hwmon0/temp1_input"]):
            with self.assertRaises(ValueError):
                CTL._validated_temperature_file(value)

    def test_a_real_hwmon_input_is_accepted(self) -> None:
        # Skip where the host exposes no hwmon temperature at all.
        candidates = sorted(Path("/sys/class/hwmon").glob("hwmon*/temp*_input")) \
            if Path("/sys/class/hwmon").exists() else []
        if not candidates:
            self.skipTest("no hwmon temperature inputs on this host")

        accepted = CTL._validated_temperature_file(str(candidates[0]))

        self.assertEqual(candidates[0].resolve(), accepted)

    def test_the_pci_discovery_form_is_accepted(self) -> None:
        # The agent finds sensors via /sys/bus/pci/devices/<bus>/hwmon, which
        # resolves into /sys/devices, never under /sys/class/hwmon. Rejecting
        # that form would refuse every real request.
        candidates = sorted(Path("/sys/bus/pci/devices").glob("*/hwmon/hwmon*/temp*_input")) \
            if Path("/sys/bus/pci/devices").exists() else []
        if not candidates:
            self.skipTest("no PCI-attached hwmon temperature inputs on this host")

        accepted = CTL._validated_temperature_file(str(candidates[0]))

        self.assertEqual(candidates[0].resolve(), accepted)

    def test_a_forged_hwmon_directory_is_refused(self) -> None:
        # Correct basename and a hwmonN parent, but not the device the kernel
        # registered under that name.
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "hwmon0"
            fake.mkdir()
            sensor = fake / "temp1_input"
            sensor.write_text("101000\n")

            with self.assertRaises(ValueError) as caught:
                CTL._validated_temperature_file(str(sensor))

        self.assertIn("registered", str(caught.exception))


class ModelValidationTests(unittest.TestCase):
    def base(self, **overrides):
        model = {
            "kind": "temperature-drift",
            "intercept_ppb": 120.0,
            "tempco_ppb_per_c": 4.0,
            "drift_ppb_per_s": 0.05,
            "reference_temperature_c": 86.0,
            "temperature_range_c": [84.0, 90.0],
        }
        model.update(overrides)
        return model

    def test_a_sound_model_is_accepted(self) -> None:
        model = CTL._validated_model(self.base())

        self.assertEqual("temperature-drift", model["kind"])
        self.assertAlmostEqual(4.0, model["tempco_ppb_per_c"])
        self.assertEqual([84.0, 90.0], model["temperature_range_c"])

    def test_an_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            CTL._validated_model(self.base(kind="neural-hunch"))

    def test_an_absurd_coefficient_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            CTL._validated_model(self.base(tempco_ppb_per_c=1e9))

    def test_a_runaway_drift_rate_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            CTL._validated_model(self.base(drift_ppb_per_s=1000.0))

    def test_non_finite_values_are_refused(self) -> None:
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                CTL._validated_model(self.base(intercept_ppb=bad))

    def test_an_inverted_temperature_range_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            CTL._validated_model(self.base(temperature_range_c=[95.0, 80.0]))

    def test_unknown_extra_keys_are_dropped_not_forwarded(self) -> None:
        model = CTL._validated_model(self.base(command="rm -rf /"))

        self.assertNotIn("command", model)

    def test_missing_coefficients_default_to_zero(self) -> None:
        model = CTL._validated_model({"kind": "frozen"})

        self.assertEqual(0.0, model["tempco_ppb_per_c"])
        self.assertEqual(0.0, model["drift_ppb_per_s"])


class VerbRegistrationTests(unittest.TestCase):
    def test_compensate_is_a_recognised_action(self) -> None:
        source = (ROOT / "scripts" / "ptpboxctl.py").read_text(encoding="utf-8")

        self.assertIn('"compensate"', source)
        self.assertIn("result = compensate_apply()", source)

    def test_the_sudoers_line_grants_the_verb(self) -> None:
        installer = (ROOT / "scripts" / "install-host.sh").read_text(encoding="utf-8")

        self.assertIn("/usr/local/sbin/ptpboxctl compensate", installer)

    def test_the_helper_and_module_are_installed(self) -> None:
        installer = (ROOT / "scripts" / "install-host.sh").read_text(encoding="utf-8")

        self.assertIn("ptpbox-holdover-compensator", installer)
        self.assertIn("ptpbox_holdover_control.py", installer)


if __name__ == "__main__":
    unittest.main()
