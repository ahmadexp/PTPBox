import importlib.util
import base64
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_agent.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_agent", MODULE_PATH)
assert SPEC and SPEC.loader
AGENT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AGENT
SPEC.loader.exec_module(AGENT)


class TelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.log_dir = root / "logs"
        self.log_dir.mkdir()
        self.topology = root / "topology.json"
        self.topology.write_text(
            json.dumps(
                {
                    "nodes": [
                        {"name": "BC1", "ingress": "p1", "egress": "p2"},
                        {"name": "BC7", "ingress": "p3", "egress": "p4"},
                        {"name": "BC4", "ingress": "p5", "egress": "p6"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.phc_map = root / "phcs.json"
        self.originals = (
            AGENT.ROOT,
            AGENT.LOG_DIR,
            AGENT.TOPOLOGY_FILE,
            AGENT.PHC_MAP_FILE,
            AGENT.read_phc_ns,
            AGENT.read_phc_cross_timestamp,
        )
        AGENT.ROOT = root
        AGENT.LOG_DIR = self.log_dir
        AGENT.TOPOLOGY_FILE = self.topology
        AGENT.PHC_MAP_FILE = self.phc_map
        with AGENT.PHC_HISTORY_LOCK:
            AGENT.PHC_HISTORY.clear()

    def tearDown(self) -> None:
        (
            AGENT.ROOT,
            AGENT.LOG_DIR,
            AGENT.TOPOLOGY_FILE,
            AGENT.PHC_MAP_FILE,
            AGENT.read_phc_ns,
            AGENT.read_phc_cross_timestamp,
        ) = self.originals
        with AGENT.PHC_HISTORY_LOCK:
            AGENT.PHC_HISTORY.clear()
        self.temporary.cleanup()

    def write_log(self, name: str) -> Path:
        path = self.log_dir / f"{name}-OC.log"
        path.write_text(
            "ptp4l[100.000]: master offset        -12 s1 freq   +4 path delay       210\n"
            "ptp4l[100.063]: master offset          7 s2 freq   -3 path delay       214\n",
            encoding="utf-8",
        )
        now = time.time()
        os.utime(path, (now, now))
        return path

    def test_parses_raw_linuxptp_samples_without_smoothing(self) -> None:
        samples = AGENT.parse_log_measurements(self.write_log("BC7"))

        self.assertEqual([-12.0, 7.0], [sample["offset_ns"] for sample in samples])
        self.assertEqual(["s1", "s2"], [sample["servo_state"] for sample in samples])
        self.assertTrue(all(sample["raw"] for sample in samples))
        self.assertTrue(all(sample["valid"] for sample in samples))
        self.assertAlmostEqual(0.063, samples[1]["observed_at"] - samples[0]["observed_at"], places=3)

    def test_reads_every_raw_kalman_update_from_the_current_worker_session(self) -> None:
        path = self.log_dir / "BC7-ADAPTIVE-KALMAN.log"
        records = [
            {
                "node": "BC7",
                "servo": "adaptive-kalman",
                "sample_count": index,
                "source_time": 100.0 + index * 0.125,
                "observed_at": 1_000.0 + index * 0.125,
                "excitation_ppb": float(index),
            }
            for index in range(6)
        ]
        path.write_text(
            '{"node":"BC7","servo":"adaptive-kalman","sample_count":999,"observed_at":1}\n'
            "PTPBox session start [99.000]\n"
            + "\n".join(json.dumps(item) for item in records)
            + "\n",
            encoding="utf-8",
        )

        history = AGENT.load_kalman_history("BC7", limit=4)

        self.assertEqual([2, 3, 4, 5], [item["sample_count"] for item in history])
        self.assertEqual([2.0, 3.0, 4.0, 5.0], [item["excitation_ppb"] for item in history])

    def test_config_accepts_only_protocol_representable_sync_rates(self) -> None:
        value = json.loads(json.dumps(AGENT.DEFAULT_CONFIG))
        value["log_sync_interval"] = -3
        self.assertEqual([], AGENT.validate_config(value))

        value["log_sync_interval"] = -3.5
        self.assertIn("log_sync_interval must be an integer from -3 through 1 (8 Hz through 0.5 Hz)", AGENT.validate_config(value))

        value["log_sync_interval"] = -4
        self.assertIn("log_sync_interval must be an integer from -3 through 1 (8 Hz through 0.5 Hz)", AGENT.validate_config(value))

    def test_profile_guardrails_use_profile_specific_domain_ranges(self) -> None:
        value = json.loads(json.dumps(AGENT.DEFAULT_CONFIG))
        value.update({"profile": "G.8275.2 Telecom", "domain": 44, "transport": "UDPv6", "delay_mechanism": "E2E"})
        self.assertEqual([], AGENT.validate_config(value))
        self.assertTrue(AGENT.profile_compliance(value)["compliant"])
        self.assertFalse(AGENT.profile_compliance(value)["certification"])

        value["domain"] = 24
        self.assertIn("G.8275.2 Telecom requires domain 44 through 63", AGENT.validate_config(value))

        value.update({"profile": "IEEE C37.238 Power", "domain": 254, "transport": "L2", "delay_mechanism": "P2P"})
        self.assertEqual([], AGENT.validate_config(value))

    def test_authentication_requires_two_step_operation(self) -> None:
        value = json.loads(json.dumps(AGENT.DEFAULT_CONFIG))
        value["security"]["authentication"]["enabled"] = True
        value["two_step"] = False
        self.assertIn("LinuxPTP Authentication TLVs require two_step=true", AGENT.validate_config(value))

    def test_pps_config_requires_distinct_hardware_source_and_sinks(self) -> None:
        value = json.loads(json.dumps(AGENT.DEFAULT_CONFIG))
        value["pps"].update({"enabled": True, "source": "BC1", "sinks": ["BC7"]})
        self.assertEqual([], AGENT.validate_config(value))

        value["pps"]["sinks"] = ["BC1"]
        self.assertIn("pps.source cannot also be a sink", AGENT.validate_config(value))

        value["pps"]["source"] = "unknown"
        self.assertIn("pps.source must be external or a topology clock", AGENT.validate_config(value))

        value["pps"].update({"source": "BC1", "sinks": ["BC7"], "polarity": "both"})
        value["pps"]["ts2phc"]["holdover_seconds"] = 30
        self.assertIn("pps.ts2phc holdover is not supported when pps.polarity is both", AGENT.validate_config(value))

    def test_pps_status_reports_live_pin_functions_per_topology_node(self) -> None:
        value = json.loads(json.dumps(AGENT.DEFAULT_CONFIG))
        value["pps"].update({"enabled": True, "source": "BC1", "sinks": ["BC7"]})
        inventory = [
            {"id": "BC1", "measurement_phc": "ptp0"},
            {"id": "BC7", "measurement_phc": "ptp1"},
            {"id": "BC4", "measurement_phc": "ptp2"},
        ]
        managed = [{"label": "PPS-ts2phc", "kind": "ts2phc", "pid": os.getpid()}]

        def fake_load_json(path, fallback=None):
            if path == AGENT.PHC_MAP_FILE:
                return inventory
            if path == AGENT.PPS_PROCESS_FILE:
                return managed
            if path == AGENT.TOPOLOGY_FILE:
                return {
                    "nodes": [
                        {"name": "BC1", "ingress": "p1", "egress": "p2"},
                        {"name": "BC7", "ingress": "p3", "egress": "p4"},
                        {"name": "BC4", "ingress": "p5", "egress": "p6"},
                    ]
                }
            return fallback

        def capabilities(phc):
            function = "periodic-output" if phc == "ptp0" else "external-timestamp" if phc == "ptp1" else "none"
            return {
                "available": True,
                "external_timestamp_channels": 1,
                "periodic_output_channels": 1,
                "programmable_pins": 2,
                "pins": [{"index": 0, "name": "mlx5_pps0", "function": function, "channel": 0}],
            }

        with (
            mock.patch.object(AGENT, "load_config", return_value=value),
            mock.patch.object(AGENT, "load_json", side_effect=fake_load_json),
            mock.patch.object(AGENT, "phc_pps_capabilities", side_effect=capabilities),
        ):
            status = AGENT.pps_status()

        self.assertTrue(status["running"])
        self.assertEqual(("source", "active"), (status["nodes"]["BC1"]["role"], status["nodes"]["BC1"]["state"]))
        self.assertEqual(("sink", "active"), (status["nodes"]["BC7"]["role"], status["nodes"]["BC7"]["state"]))
        self.assertEqual(("disabled", "ready"), (status["nodes"]["BC4"]["role"], status["nodes"]["BC4"]["state"]))

    def test_phc_sampler_matches_the_applied_sync_cadence(self) -> None:
        class StopAfterOneSample:
            def __init__(self) -> None:
                self.delays: list[float] = []

            def is_set(self) -> bool:
                return bool(self.delays)

            def wait(self, delay: float) -> bool:
                self.delays.append(delay)
                return True

        stop = StopAfterOneSample()
        with (
            mock.patch.object(AGENT, "load_config", return_value={"log_sync_interval": -3}),
            mock.patch.object(AGENT, "record_phc_sample"),
            mock.patch.object(AGENT.time, "monotonic", side_effect=[100.0, 100.01]),
        ):
            self.assertEqual(8.0, AGENT.configured_phc_sample_rate_hz())
            AGENT.phc_sampler_loop(stop)  # type: ignore[arg-type]

        self.assertEqual(1, len(stop.delays))
        self.assertAlmostEqual(0.115, stop.delays[0], places=6)

    def test_telemetry_uses_physical_topology_order_and_incremental_cutoff(self) -> None:
        self.write_log("BC7")
        self.write_log("BC4")

        payload = AGENT.telemetry(history_seconds=120)
        self.assertEqual(["BC1", "BC7", "BC4"], [clock["id"] for clock in payload["clocks"]])
        self.assertEqual(["grandmaster", "boundary", "ordinary"], [clock["role"] for clock in payload["clocks"]])
        self.assertEqual("live", payload["mode"])
        self.assertEqual("none", payload["smoothing"])
        self.assertEqual(4, payload["sample_count"])
        bc7 = next(clock for clock in payload["clocks"] if clock["id"] == "BC7")
        self.assertEqual(1, bc7["window_locked_sample_count"])
        self.assertEqual(7.0, bc7["rms_ns"])

        latest = max(sample["observed_at"] for clock in payload["clocks"] for sample in clock["samples"])
        incremental = AGENT.telemetry(history_seconds=120, since=latest)
        self.assertEqual(0, incremental["sample_count"])
        self.assertEqual(2, incremental["measured_clocks"])

    def test_kalman_status_overlays_applied_correction_and_lock_state(self) -> None:
        self.write_log("BC7")
        state_dir = Path(self.temporary.name) / "kalman"
        state_dir.mkdir()
        (state_dir / "kalman-bc7.json").write_text(
            json.dumps(
                {
                    "node": "BC7",
                    "servo": "kalman",
                    "state": "locked",
                    "observed_at": time.time(),
                    "correction_ppb": 123.5,
                    "phase_estimate_ns": 4.0,
                    "frequency_estimate_ppb": 122.0,
                    "locked_since_source_time": 100.063,
                }
            ),
            encoding="utf-8",
        )
        control = {
            "nodes": {
                "BC7": {"type": "kalman", "enabled": True, "mode": "active"},
                "BC4": {"type": "pi", "enabled": True, "mode": "active"},
            }
        }

        with (
            mock.patch.object(AGENT, "KALMAN_STATE_DIR", state_dir),
            mock.patch.object(AGENT, "load_servo_state", return_value=control),
        ):
            payload = AGENT.telemetry(history_seconds=120)

        bc7 = next(clock for clock in payload["clocks"] if clock["id"] == "BC7")
        self.assertEqual("s2", bc7["measurement"]["servo_state"])
        self.assertEqual(123.5, bc7["measurement"]["frequency_ppb"])
        self.assertEqual(-3.0, bc7["measurement"]["linuxptp_frequency_ppb"])
        self.assertEqual("ptpbox-kalman", bc7["measurement"]["control_source"])
        self.assertTrue(bc7["kalman"]["fresh"])
        self.assertEqual(1, bc7["window_locked_sample_count"])

    def test_discards_samples_before_a_monotonic_clock_reset(self) -> None:
        path = self.log_dir / "BC7-OC.log"
        path.write_text(
            "ptp4l[900.000]: master offset 999 s2 freq 0 path delay 100\n"
            "ptp4l[10.000]: master offset 8 s1 freq 2 path delay 101\n"
            "ptp4l[10.063]: master offset 3 s2 freq 1 path delay 102\n",
            encoding="utf-8",
        )

        samples = AGENT.parse_log_measurements(path)
        self.assertEqual([8.0, 3.0], [sample["offset_ns"] for sample in samples])

    def test_restart_marker_invalidates_previous_session_without_new_sample(self) -> None:
        path = self.log_dir / "BC7-BC.log"
        path.write_text(
            "ptp4l[100.000]: master offset 9 s2 freq 1 path delay 250\n"
            "ptp4l[200.000]: selected /dev/ptp13 as PTP clock\n"
            "ptp4l[200.010]: port 1: INITIALIZING to SLAVE on INIT_COMPLETE\n",
            encoding="utf-8",
        )

        self.assertEqual([], AGENT.parse_log_measurements(path))

    def test_restart_marker_keeps_only_new_session_samples(self) -> None:
        path = self.log_dir / "BC7-BC.log"
        path.write_text(
            "ptp4l[100.000]: master offset 9 s2 freq 1 path delay 250\n"
            "ptp4l[200.000]: selected /dev/ptp13 as PTP clock\n"
            "ptp4l[201.000]: master offset -4 s1 freq 2 path delay 252\n",
            encoding="utf-8",
        )

        samples = AGENT.parse_log_measurements(path)
        self.assertEqual([-4.0], [sample["offset_ns"] for sample in samples])

    def test_ptpbox_session_marker_resets_free_running_log_window(self) -> None:
        path = self.log_dir / "BC7-OC.log"
        path.write_text(
            "ptp4l[100.000]: master offset 99 s2 freq 1 path delay 250\n"
            "PTPBox session start [200.000]\n"
            "ptp4l[201.000]: master offset 7 s0 freq 2 path delay 252\n",
            encoding="utf-8",
        )

        samples = AGENT.parse_log_measurements(path)
        self.assertEqual([7.0], [sample["offset_ns"] for sample in samples])

    def test_current_boundary_log_does_not_fall_back_to_stale_oc_log(self) -> None:
        stale = self.write_log("BC7")
        current = self.log_dir / "BC7-BC.log"
        current.write_text(
            "ptp4l[200.000]: selected /dev/ptp13 as PTP clock\n",
            encoding="utf-8",
        )
        now = time.time()
        os.utime(stale, (now - 60, now - 60))
        os.utime(current, (now, now))

        self.assertEqual([current], AGENT.clock_log_candidates("BC7"))
        payload = AGENT.telemetry(history_seconds=120)
        bc7 = next(clock for clock in payload["clocks"] if clock["id"] == "BC7")
        self.assertIsNone(bc7["measurement"])

    def test_flags_impossible_hardware_path_delay_without_changing_raw_value(self) -> None:
        path = self.log_dir / "BC7-OC.log"
        path.write_text(
            "ptp4l[10.000]: master offset 17 s2 freq 3 path delay 781275143\n",
            encoding="utf-8",
        )

        sample = AGENT.parse_log_measurements(path)[0]
        self.assertEqual(17.0, sample["offset_ns"])
        self.assertEqual(781275143.0, sample["mean_path_delay_ns"])
        self.assertFalse(sample["valid"])

    def test_compares_phcs_without_disciplining_them(self) -> None:
        self.phc_map.write_text(
            json.dumps(
                [
                    {"id": "BC1", "measurement_phc": "ptp2"},
                    {"id": "BC7", "measurement_phc": "ptp13"},
                    {"id": "BC4", "measurement_phc": "ptp5"},
                ]
            ),
            encoding="utf-8",
        )
        reads = iter(
            [
                AGENT.PhcCrossTimestamp(1_000, 500, 40, "extended"),
                AGENT.PhcCrossTimestamp(1_100, 600, 30, "extended"),
                AGENT.PhcCrossTimestamp(1_200, 720, 20, "extended"),
                AGENT.PhcCrossTimestamp(1_300, 500, 40, "extended"),
            ]
        )
        AGENT.read_phc_cross_timestamp = lambda _device: next(reads)

        sample = AGENT.record_phc_sample()
        self.assertIsNotNone(sample)
        payload = AGENT.phc_telemetry(history_seconds=120)

        self.assertEqual("common-system cross timestamps with interpolated BC1 reference", payload["method"])
        self.assertEqual([0.0, 100.0, 220.0], [clock["measurement"]["offset_ns"] for clock in payload["clocks"]])
        self.assertEqual(120.0, payload["clocks"][2]["measurement"]["previous_hop_offset_ns"])
        self.assertEqual(35.0, payload["clocks"][1]["measurement"]["comparison_uncertainty_ns"])
        self.assertEqual("extended", payload["clocks"][1]["measurement"]["cross_timestamp_method"])
        self.assertEqual(1.0, payload["sample_rate_hz"])
        self.assertTrue(all(clock["measurement"]["raw"] for clock in payload["clocks"]))

    def test_extended_cross_timestamp_selects_shortest_kernel_bracket(self) -> None:
        def fill_ioctl(_fd: int, request: int, buffer: bytearray, _mutate: bool) -> int:
            self.assertEqual(AGENT.PTP_SYS_OFFSET_EXTENDED, request)
            value = AGENT.PtpSysOffsetExtended.from_buffer(buffer)
            for index in range(AGENT.PHC_CROSS_TIMESTAMP_SAMPLES):
                before = 10_000 + index * 1_000
                delay = 900 - index * 50
                midpoint = before + delay // 2
                value.ts[index][0].nsec = before
                value.ts[index][1].nsec = midpoint + 37
                value.ts[index][2].nsec = before + delay
            return 0

        with mock.patch.object(AGENT.fcntl, "ioctl", side_effect=fill_ioctl):
            result = AGENT.extended_cross_timestamp(9, AGENT.CLOCK_MONOTONIC_RAW)

        self.assertEqual(500, result.delay_ns)
        self.assertEqual(37, result.phc_minus_system_ns)
        self.assertIn("best of 9", result.method)

    def test_merges_controller_inventory_for_namespaced_timing_ports(self) -> None:
        self.phc_map.write_text(
            json.dumps(
                [
                    {
                        "id": "BC1",
                        "namespace": "BC1",
                        "ingress": "timing0",
                        "egress": "timing1",
                        "ingress_phc": "ptp20",
                        "egress_phc": "ptp21",
                        "measurement_phc": "ptp21",
                        "ingress_interface": {
                            "state": "UP",
                            "carrier": True,
                            "speed_mbps": 100000,
                            "mac": "00:11:22:33:44:55",
                            "driver": "mlx5_core",
                            "bus": "0000:19:00.0",
                            "hardware_timestamping": True,
                        },
                        "egress_interface": {
                            "state": "UP",
                            "carrier": True,
                            "speed_mbps": 100000,
                            "mac": "00:11:22:33:44:56",
                            "driver": "mlx5_core",
                            "bus": "0000:19:00.1",
                            "hardware_timestamping": True,
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )

        ports = AGENT.interfaces()
        timing0 = next(port for port in ports if port.name == "timing0")
        timing1 = next(port for port in ports if port.name == "timing1")

        self.assertEqual("BC1", timing0.namespace)
        self.assertEqual("BC1 / INACTIVE IN", timing0.assignment)
        self.assertEqual("BC1 / GM OUT", timing1.assignment)
        self.assertEqual(100000, timing1.speed_mbps)
        self.assertEqual("ptp21", timing1.phc)
        self.assertTrue(timing1.carrier)


class HoldoverSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.originals = (
            AGENT.STATE_DIR,
            AGENT.CONFIG_FILE,
            AGENT.SERVO_REQUEST_FILE,
            AGENT.SERVO_STATE_FILE,
            AGENT.HOLDOVER_SESSION_FILE,
        )
        AGENT.STATE_DIR = root
        AGENT.CONFIG_FILE = root / "config.json"
        AGENT.SERVO_REQUEST_FILE = root / "servo-request.json"
        AGENT.SERVO_STATE_FILE = root / "servo-state.json"
        AGENT.HOLDOVER_SESSION_FILE = root / "holdover-session.json"
        with AGENT._EXPERIMENT_STORES_LOCK:
            AGENT._EXPERIMENT_STORES.clear()

    def tearDown(self) -> None:
        (
            AGENT.STATE_DIR,
            AGENT.CONFIG_FILE,
            AGENT.SERVO_REQUEST_FILE,
            AGENT.SERVO_STATE_FILE,
            AGENT.HOLDOVER_SESSION_FILE,
        ) = self.originals
        with AGENT._EXPERIMENT_STORES_LOCK:
            AGENT._EXPERIMENT_STORES.clear()
        self.temporary.cleanup()

    @staticmethod
    def topology() -> list[dict[str, str]]:
        return [
            {"name": "BC1", "ingress": "p1", "egress": "p2"},
            {"name": "BC2", "ingress": "p3", "egress": "p4"},
            {"name": "BC3", "ingress": "p5", "egress": "p6"},
        ]

    @staticmethod
    def lock_observation(stable: bool = True) -> dict[str, object]:
        return {
            "all_stable": stable,
            "nodes": {
                "BC2": {
                    "stable": stable,
                    "reason": "locked" if stable else "servo state is s1",
                    "ptp_offset_ns": 12.0,
                    "phc_offset_ns": 20.0,
                    "servo_state": "s2" if stable else "s1",
                    "observed_at": time.time(),
                }
            },
            "observed_at": time.time(),
            "phc_mode": "live",
            "measurement_method": "test",
        }

    def test_holdover_run_qualifies_releases_measures_and_recovers(self) -> None:
        servo_state = {"nodes": {"BC2": {"type": "pi", "enabled": True}}}
        with (
            mock.patch.object(AGENT, "topology_nodes", side_effect=self.topology),
            mock.patch.object(AGENT, "load_servo_state", return_value=servo_state),
            mock.patch.object(AGENT, "_set_servo_controls") as controls,
            mock.patch.object(AGENT, "_holdover_lock_observation", return_value=self.lock_observation()),
        ):
            armed = AGENT.start_holdover_session(
                {
                    "nodes": ["BC2"],
                    "stable_dwell_s": 5,
                    "stable_threshold_ns": 100,
                    "duration_s": 60,
                    "auto_release": False,
                }
            )
            self.assertEqual("synchronizing", armed["session"]["phase"])
            self.assertEqual(["BC2"], armed["session"]["nodes"])

            store = AGENT.experiment_store()
            before_release = time.time()
            store.record_phc(
                {
                    "sample_id": "pre:1",
                    "clocks": [
                        {
                            "id": "BC2",
                            "observed_at": before_release,
                            "offset_ns": 20.0,
                            "previous_hop_offset_ns": 20.0,
                            "comparison_uncertainty_ns": 3.0,
                            "valid": True,
                        }
                    ],
                }
            )
            session = AGENT.load_holdover_session()
            self.assertIsNotNone(session)
            session["stable_since"] = time.time() - 6
            AGENT.save_holdover_session(session)

            released = AGENT.release_holdover_session()
            self.assertEqual("holdover", released["session"]["phase"])
            self.assertEqual(20.0, released["session"]["baseline"]["BC2"]["offset_ns"])

            release_at = released["session"]["release_at"]
            for index, offset in enumerate((70.0, 120.0), start=1):
                store.record_phc(
                    {
                        "sample_id": f"hold:{index}",
                        "clocks": [
                            {
                                "id": "BC2",
                                "observed_at": release_at + index,
                                "offset_ns": offset,
                                "previous_hop_offset_ns": offset,
                                "comparison_uncertainty_ns": 4.0,
                                "valid": True,
                            }
                        ],
                    }
                )
            measured = AGENT.holdover_session_snapshot()
            self.assertEqual(100.0, measured["metrics"]["BC2"]["current_wander_ns"])
            self.assertAlmostEqual(50.0, measured["metrics"]["BC2"]["drift_ppb"])
            self.assertEqual("none", measured["smoothing"])

            completed = AGENT.resume_holdover_session()
            self.assertEqual("completed", completed["session"]["phase"])
            self.assertIsNone(store.active())
            self.assertEqual(
                [(["BC2"], True), (["BC2"], False), (["BC2"], True)],
                [(call.args[0], call.args[1]) for call in controls.call_args_list],
            )

    def test_unstable_sample_resets_the_release_dwell(self) -> None:
        session = {
            "id": "holdover-test",
            "phase": "synchronizing",
            "nodes": ["BC2"],
            "stable_dwell_s": 30.0,
            "stable_threshold_ns": 100.0,
            "stable_since": time.time() - 20,
            "auto_release": False,
        }
        AGENT.save_holdover_session(session)
        with mock.patch.object(AGENT, "_holdover_lock_observation", return_value=self.lock_observation(False)):
            refreshed = AGENT.refresh_holdover_session(session)

        self.assertIsNone(refreshed["stable_since"])
        self.assertFalse(refreshed["ready_to_release"])

    def test_automatic_transition_returns_session_state_not_api_envelope(self) -> None:
        session = {
            "id": "holdover-auto",
            "phase": "synchronizing",
            "nodes": ["BC2"],
            "stable_dwell_s": 5.0,
            "stable_threshold_ns": 100.0,
            "stable_since": time.time() - 6,
            "auto_release": True,
        }
        AGENT.save_holdover_session(session)

        def release() -> dict[str, object]:
            released = AGENT.load_holdover_session()
            released["phase"] = "holdover"
            released["release_at"] = time.time()
            AGENT.save_holdover_session(released)
            return {"active": True, "session": released}

        with (
            mock.patch.object(AGENT, "_holdover_lock_observation", return_value=self.lock_observation()),
            mock.patch.object(AGENT, "release_holdover_session", side_effect=release),
        ):
            refreshed = AGENT.refresh_holdover_session(session)

        self.assertEqual("holdover", refreshed["phase"])
        self.assertNotIn("session", refreshed)


class ResearchSnapshotCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        with AGENT._RESEARCH_SNAPSHOT_CONDITION:
            AGENT._RESEARCH_SNAPSHOT_CACHE.clear()
            AGENT._RESEARCH_SNAPSHOT_REFRESHING.clear()

    def tearDown(self) -> None:
        with AGENT._RESEARCH_SNAPSHOT_CONDITION:
            AGENT._RESEARCH_SNAPSHOT_CACHE.clear()
            AGENT._RESEARCH_SNAPSHOT_REFRESHING.clear()

    def test_identical_pollers_share_one_heavy_analysis_snapshot(self) -> None:
        payload = {"sample_count": 72, "mode": "live", "dynamics": {}}
        with mock.patch.object(AGENT, "_build_research_snapshot", return_value=payload) as build:
            first = AGENT.research_snapshot(900.0)
            second = AGENT.research_snapshot(900.0)

        build.assert_called_once_with(900.0)
        self.assertEqual(72, first["sample_count"])
        self.assertEqual(72, second["sample_count"])
        self.assertTrue(second["analysis_cache"]["request_coalescing"])
        self.assertFalse(second["analysis_cache"]["refreshing"])
        self.assertLess(second["analysis_cache"]["age_s"], AGENT.RESEARCH_CACHE_SECONDS)


class GraphAlbumTests(unittest.TestCase):
    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    def test_saves_lists_and_deletes_a_png_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(AGENT, "ALBUM_DIR", Path(temporary)):
            item = AGENT.save_album_capture(
                {
                    "title": "Cascade amplification",
                    "section": "Cascade dynamics",
                    "graph_id": "cascade-amplification",
                    "captured_at": 1_784_867_000.0,
                    "width": 1280,
                    "height": 720,
                    "metadata": {"data_mode": "LIVE", "nested": {"ignored": True}},
                    "data_url": f"data:image/png;base64,{base64.b64encode(self.PNG).decode()}",
                }
            )

            snapshot = AGENT.album_snapshot()
            self.assertEqual(1, snapshot["count"])
            self.assertEqual("Cascade amplification", snapshot["items"][0]["title"])
            self.assertEqual("LIVE", snapshot["items"][0]["metadata"]["data_mode"])
            self.assertNotIn("nested", snapshot["items"][0]["metadata"])
            self.assertEqual(self.PNG, AGENT.album_image_path(item["id"]).read_bytes())
            self.assertTrue(AGENT.delete_album_capture(item["id"]))
            self.assertEqual(0, AGENT.album_snapshot()["count"])

    def test_rejects_non_png_capture_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(AGENT, "ALBUM_DIR", Path(temporary)):
            with self.assertRaisesRegex(ValueError, "not a PNG"):
                AGENT.save_album_capture(
                    {
                        "width": 100,
                        "height": 100,
                        "data_url": f"data:image/png;base64,{base64.b64encode(b'not a png' * 10).decode()}",
                    }
                )


if __name__ == "__main__":
    unittest.main()
