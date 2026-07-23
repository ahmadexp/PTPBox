import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "ptpboxctl.py"
SPEC = importlib.util.spec_from_file_location("ptpboxctl", MODULE_PATH)
assert SPEC and SPEC.loader
CONTROLLER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONTROLLER
SPEC.loader.exec_module(CONTROLLER)


class ControllerConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.originals = (CONTROLLER.CONFIG_FILE, CONTROLLER.SERVO_STATE_FILE, CONTROLLER.SERVO_REQUEST_FILE)
        temporary = Path(self.temporary.name)
        CONTROLLER.CONFIG_FILE = temporary / "missing-config.json"
        CONTROLLER.SERVO_STATE_FILE = temporary / "servo-state.json"
        CONTROLLER.SERVO_REQUEST_FILE = temporary / "servo-request.json"

    def tearDown(self) -> None:
        CONTROLLER.CONFIG_FILE, CONTROLLER.SERVO_STATE_FILE, CONTROLLER.SERVO_REQUEST_FILE = self.originals
        self.temporary.cleanup()

    def test_boundary_config_has_directional_static_roles(self) -> None:
        path = Path(self.temporary.name) / "ptpbox-bc.conf"
        CONTROLLER.render_ptp_config(
            "boundary",
            path,
            boundary_jbod=True,
            ingress="p1",
            egress="p2",
            uds_label="bc2",
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn("boundary_clock_jbod 1", text)
        self.assertIn("BMCA noop", text)
        self.assertIn("clientOnly 1", text)
        self.assertIn("[p1]", text)
        self.assertIn("[p2]\nserverOnly 1", text)
        self.assertIn("uds_address /run/ptpbox/ptp4l-bc2", text)
        self.assertIn("uds_ro_address /run/ptpbox/ptp4l-bc2-ro", text)
        self.assertIn("summary_interval 0", text)
        self.assertIn("freq_est_interval 0", text)
        self.assertIn("tx_timestamp_timeout 100", text)
        self.assertIn("step_threshold 0.000000000", text)
        self.assertIn("free_running 0", text)

    def test_configured_sync_frequency_reaches_linuxptp(self) -> None:
        value = json.loads(json.dumps(CONTROLLER.DEFAULT_CONFIG))
        value["log_sync_interval"] = -3
        CONTROLLER.CONFIG_FILE.write_text(json.dumps(value), encoding="utf-8")
        path = Path(self.temporary.name) / "ptpbox-fast-sync.conf"

        CONTROLLER.render_ptp_config("client", path)
        text = path.read_text(encoding="utf-8")

        self.assertIn("logSyncInterval -3", text)
        self.assertIn("summary_interval -3", text)
        self.assertIn("freq_est_interval -3", text)

    def test_holdover_config_keeps_measurement_running_without_adjustment(self) -> None:
        path = Path(self.temporary.name) / "ptpbox-holdover.conf"
        CONTROLLER.render_ptp_config("client", path, servo_override={"type": "linreg"}, free_running=True)
        text = path.read_text(encoding="utf-8")

        self.assertIn("clock_servo linreg", text)
        self.assertIn("free_running 1", text)

    def test_kalman_config_keeps_linuxptp_observing_without_competing_control(self) -> None:
        path = Path(self.temporary.name) / "ptpbox-kalman.conf"
        CONTROLLER.render_ptp_config("client", path, servo_override={"type": "kalman"})
        text = path.read_text(encoding="utf-8")

        self.assertIn("clock_servo nullf", text)
        self.assertIn("free_running 1", text)

    def test_kalman_worker_receives_phc_log_and_filter_settings(self) -> None:
        temporary = Path(self.temporary.name)
        helper = temporary / "ptpbox-kalman-servo"
        helper.touch()
        processes = []
        values = json.loads(json.dumps(CONTROLLER.DEFAULT_CONFIG))

        def fake_spawn(label, args, spawned):
            spawned.append({"label": label, "pid": 42, "command": args, "log": "/tmp/worker.log"})

        with (
            patch.object(CONTROLLER, "KALMAN_HELPER", helper),
            patch.object(CONTROLLER, "STATE_DIR", temporary),
            patch.object(CONTROLLER, "spawn", side_effect=fake_spawn),
        ):
            CONTROLLER.spawn_kalman("BC7", "ptp8", "/tmp/BC7-OC.log", values, processes)

        self.assertEqual("BC7-KALMAN", processes[0]["label"])
        self.assertEqual("kalman", processes[0]["kind"])
        self.assertEqual("BC7", processes[0]["kalman_for"])
        self.assertIn("/dev/ptp8", processes[0]["command"])
        self.assertIn("/tmp/BC7-OC.log", processes[0]["command"])

    def test_adaptive_and_imm_workers_receive_the_requested_estimator_mode(self) -> None:
        temporary = Path(self.temporary.name)
        helper = temporary / "ptpbox-kalman-servo"
        helper.touch()
        values = json.loads(json.dumps(CONTROLLER.DEFAULT_CONFIG))

        for mode in ("adaptive-kalman", "imm"):
            processes = []

            def fake_spawn(label, args, spawned):
                spawned.append({"label": label, "pid": 42, "command": args, "log": "/tmp/worker.log"})

            with (
                patch.object(CONTROLLER, "KALMAN_HELPER", helper),
                patch.object(CONTROLLER, "STATE_DIR", temporary),
                patch.object(CONTROLLER, "spawn", side_effect=fake_spawn),
            ):
                CONTROLLER.spawn_kalman("BC4", "ptp5", "/tmp/BC4.log", values, processes, mode=mode)

            command = processes[0]["command"]
            self.assertEqual(mode, command[command.index("--mode") + 1])
            self.assertEqual(mode, processes[0]["servo_type"])

    def test_ts2phc_config_maps_periodic_output_and_external_timestamp_pins(self) -> None:
        path = Path(self.temporary.name) / "ptpbox-ts2phc.conf"
        values = json.loads(json.dumps(CONTROLLER.DEFAULT_CONFIG))
        values["pps"].update(
            {
                "enabled": True,
                "source": "BC1",
                "sinks": ["BC2"],
                "output_pin": 1,
                "input_pin": 0,
                "polarity": "both",
                "pulse_width_ns": 100_000_000,
                "perout_phase_ns": 250,
                "extts_correction_ns": -17,
            }
        )
        values["pps"]["ts2phc"].update({"servo": "linreg", "holdover_seconds": 30})

        with patch.object(
            CONTROLLER,
            "validate_pps_hardware",
            return_value=("/dev/ptp1", ["/dev/ptp2"]),
        ):
            source, sinks = CONTROLLER.render_ts2phc_config(path, values, {})

        text = path.read_text(encoding="utf-8")
        self.assertEqual("/dev/ptp1", source)
        self.assertEqual(["/dev/ptp2"], sinks)
        self.assertIn("clock_servo linreg", text)
        self.assertIn("ts2phc.holdover 30", text)
        self.assertIn("[/dev/ptp1]\nts2phc.master 1", text)
        self.assertIn("ts2phc.pin_index 1", text)
        self.assertIn("[/dev/ptp2]", text)
        self.assertIn("ts2phc.extts_polarity both", text)
        self.assertIn("ts2phc.extts_correction -17", text)

        values["pps"]["source"] = "external"
        external_path = Path(self.temporary.name) / "ptpbox-ts2phc-external.conf"
        with patch.object(
            CONTROLLER,
            "validate_pps_hardware",
            return_value=(None, ["/dev/ptp2"]),
        ):
            CONTROLLER.render_ts2phc_config(external_path, values, {})
        self.assertNotIn("ts2phc.perout_phase", external_path.read_text(encoding="utf-8"))

    def test_pps_start_is_safe_when_disabled_and_managed_when_enabled(self) -> None:
        values = json.loads(json.dumps(CONTROLLER.DEFAULT_CONFIG))
        processes = [{"label": "BC1-GM", "pid": 1}]
        with patch.object(CONTROLLER, "spawn") as spawn:
            CONTROLLER.start_pps(values, {}, processes)
        spawn.assert_not_called()

        values["pps"].update({"enabled": True, "source": "BC1", "sinks": ["BC2"]})

        def fake_spawn(label, args, spawned):
            spawned.append({"label": label, "pid": 99, "command": args})

        with (
            patch.object(
                CONTROLLER,
                "render_ts2phc_config",
                return_value=("/dev/ptp1", ["/dev/ptp2"]),
            ),
            patch.object(CONTROLLER, "spawn", side_effect=fake_spawn),
        ):
            CONTROLLER.start_pps(values, {}, processes)

        self.assertEqual("PPS-ts2phc", processes[-1]["label"])
        self.assertEqual("ts2phc", processes[-1]["kind"])
        self.assertEqual(["BC2"], processes[-1]["pps_sinks"])
        self.assertIn("ptpbox-ts2phc.conf", " ".join(processes[-1]["command"]))

    def test_endpoint_configs_force_their_roles(self) -> None:
        server = Path(self.temporary.name) / "server.conf"
        client = Path(self.temporary.name) / "client.conf"
        CONTROLLER.render_ptp_config("server", server)
        CONTROLLER.render_ptp_config("client", client)

        self.assertIn("serverOnly 1", server.read_text(encoding="utf-8"))
        self.assertIn("priority1 1", server.read_text(encoding="utf-8"))
        self.assertIn("clientOnly 1", client.read_text(encoding="utf-8"))

    def test_real_time_cascade_uses_one_ptp4l_per_nic(self) -> None:
        topology = {
            "nodes": [
                {"name": "BC1", "ingress": "p1", "egress": "p2"},
                {"name": "BC2", "ingress": "p3", "egress": "p4"},
                {"name": "BC4", "ingress": "p5", "egress": "p6"},
            ]
        }
        processes = []

        def fake_spawn(label, args, spawned):
            spawned.append({"label": label, "pid": len(spawned) + 1, "command": args})
            processes.append((label, args))

        pids_file = Path(self.temporary.name) / "processes.json"
        with (
            patch.object(CONTROLLER, "STATE_DIR", Path(self.temporary.name)),
            patch.object(CONTROLLER, "PIDS_FILE", pids_file),
            patch.object(CONTROLLER, "require_root"),
            patch.object(CONTROLLER, "status", return_value={"running": False}),
            patch.object(
                CONTROLLER,
                "setup",
                return_value={
                    "ok": True,
                    "phcs": [
                        {"id": "BC1", "shared_phc": False},
                        {"id": "BC2", "shared_phc": True},
                        {"id": "BC4", "shared_phc": False},
                    ],
                },
            ),
            patch.object(CONTROLLER, "prioritize_timestamp_workers", return_value=[]),
            patch.object(CONTROLLER, "topology", return_value=topology),
            patch.object(CONTROLLER, "render_ptp_config"),
            patch.object(CONTROLLER, "spawn", side_effect=fake_spawn),
        ):
            result = CONTROLLER.start()

        self.assertTrue(result["running"])
        self.assertEqual(["BC1-GM", "BC2-BC", "BC4-OC"], [label for label, _args in processes])
        self.assertTrue(all("ptp4l" in args for _label, args in processes))
        self.assertTrue(all("phc2sys" not in args for _label, args in processes))
        boundary_args = processes[1][1]
        self.assertEqual(0, boundary_args.count("-i"))

    def test_servo_request_enters_holdover_and_restarts_only_target(self) -> None:
        topology = {
            "nodes": [
                {"name": "BC1", "ingress": "p1", "egress": "p2"},
                {"name": "BC2", "ingress": "p3", "egress": "p4"},
                {"name": "BC3", "ingress": "p5", "egress": "p6"},
            ]
        }
        temporary = Path(self.temporary.name)
        pids_file = temporary / "processes.json"
        phc_map_file = temporary / "phcs.json"
        CONTROLLER.SERVO_REQUEST_FILE.write_text(json.dumps({"target": "BC3", "enabled": False, "type": "linreg"}), encoding="utf-8")
        pids_file.write_text(
            json.dumps(
                [
                    {"label": "BC1-GM", "node": "BC1", "pid": 11, "command": ["ptp4l", "bc1"]},
                    {"label": "BC2-BC", "node": "BC2", "pid": 12, "command": ["ptp4l", "bc2"]},
                    {"label": "BC3-OC", "node": "BC3", "pid": 13, "command": ["ptp4l", "bc3"]},
                ]
            ),
            encoding="utf-8",
        )
        phc_map_file.write_text(json.dumps([{"id": "BC3", "shared_phc": True}]), encoding="utf-8")

        def fake_spawn(label, args, spawned):
            spawned.append({"label": label, "pid": 99, "command": args})

        with (
            patch.object(CONTROLLER, "STATE_DIR", temporary),
            patch.object(CONTROLLER, "PIDS_FILE", pids_file),
            patch.object(CONTROLLER, "PHC_MAP_FILE", phc_map_file),
            patch.object(CONTROLLER, "require_root"),
            patch.object(CONTROLLER, "topology", return_value=topology),
            patch.object(CONTROLLER, "stop_process") as stop_process,
            patch.object(CONTROLLER, "render_ptp_config") as render,
            patch.object(CONTROLLER, "spawn", side_effect=fake_spawn),
        ):
            result = CONTROLLER.servo_apply()

        self.assertEqual(["BC3"], result["changed"])
        self.assertFalse(result["servo"]["nodes"]["BC3"]["enabled"])
        self.assertEqual("linreg", result["servo"]["nodes"]["BC3"]["type"])
        stop_process.assert_called_once()
        self.assertTrue(render.call_args.kwargs["free_running"])
        self.assertEqual({"type": "linreg"}, render.call_args.kwargs["servo_override"])

    def test_captures_interface_metadata_inside_namespace(self) -> None:
        def fake_command(args, check=True):
            class Result:
                returncode = 0
                stderr = ""

                def __init__(self, stdout):
                    self.stdout = stdout

            if args[-4:-1] == ["ip", "-j", "link"]:
                return Result("[]")
            if args[-3:] == ["link", "show", "dev"]:
                return Result("[]")
            if "-j" in args:
                return Result('[{"ifname":"p1","flags":["UP","LOWER_UP"],"operstate":"UP","address":"00:11:22:33:44:55"}]')
            if "-i" in args:
                return Result("driver: mlx5_core\nbus-info: 0000:19:00.0\n")
            return Result("Speed: 100000Mb/s\nLink detected: yes\n")

        with patch.object(CONTROLLER, "command", side_effect=fake_command):
            details = CONTROLLER.namespace_interface_details("BC1", "p1", "ptp0")

        self.assertEqual("BC1", details["namespace"])
        self.assertEqual("mlx5_core", details["driver"])
        self.assertEqual("0000:19:00.0", details["bus"])
        self.assertEqual(100000, details["speed_mbps"])
        self.assertTrue(details["carrier"])


if __name__ == "__main__":
    unittest.main()
