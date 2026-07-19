import importlib.util
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
        self.original_config = CONTROLLER.CONFIG_FILE
        CONTROLLER.CONFIG_FILE = Path(self.temporary.name) / "missing-config.json"

    def tearDown(self) -> None:
        CONTROLLER.CONFIG_FILE = self.original_config
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
        self.assertIn("tx_timestamp_timeout 100", text)
        self.assertIn("step_threshold 0.000000000", text)

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
