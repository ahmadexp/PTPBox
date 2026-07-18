import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


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

    def test_boundary_config_has_no_forced_port_role(self) -> None:
        path = Path(self.temporary.name) / "ptpbox-bc.conf"
        CONTROLLER.render_ptp_config("boundary", path, boundary_jbod=True)
        text = path.read_text(encoding="utf-8")

        self.assertIn("boundary_clock_jbod 1", text)
        self.assertIn("summary_interval 0", text)
        self.assertIn("tx_timestamp_timeout 100", text)
        self.assertNotIn("serverOnly", text)
        self.assertNotIn("clientOnly", text)

    def test_endpoint_configs_force_their_roles(self) -> None:
        server = Path(self.temporary.name) / "server.conf"
        client = Path(self.temporary.name) / "client.conf"
        CONTROLLER.render_ptp_config("server", server)
        CONTROLLER.render_ptp_config("client", client)

        self.assertIn("serverOnly 1", server.read_text(encoding="utf-8"))
        self.assertIn("clientOnly 1", client.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
