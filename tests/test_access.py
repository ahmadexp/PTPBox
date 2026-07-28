import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "agent" / "ptpbox_access.py"
SPEC = importlib.util.spec_from_file_location("ptpbox_access_test", MODULE_PATH)
assert SPEC and SPEC.loader
AC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AC
SPEC.loader.exec_module(AC)

OPERATOR_TOKEN = "operator-token-that-is-long-enough"
VIEWER_TOKEN = "viewer-token-that-is-long-enough"
TOKENS = {OPERATOR_TOKEN: AC.OPERATOR, VIEWER_TOKEN: AC.VIEWER}


class Headers(dict):
    """Mimics the mapping BaseHTTPRequestHandler exposes."""

    def get(self, key, default=None):
        for name, value in self.items():
            if name.lower() == str(key).lower():
                return value
        return default


class TokenLoadingTests(unittest.TestCase):
    def test_environment_tokens_are_read_per_role(self) -> None:
        tokens = AC.load_tokens(environment={
            "PTPBOX_OPERATOR_TOKENS": f"{OPERATOR_TOKEN},another-operator-token-long",
            "PTPBOX_VIEWER_TOKENS": VIEWER_TOKEN,
        })

        self.assertEqual(AC.OPERATOR, tokens[OPERATOR_TOKEN])
        self.assertEqual(AC.VIEWER, tokens[VIEWER_TOKEN])
        self.assertEqual(3, len(tokens))

    def test_short_tokens_are_discarded(self) -> None:
        # A four-character token looks like protection and is not.
        tokens = AC.load_tokens(environment={"PTPBOX_OPERATOR_TOKENS": "abcd"})

        self.assertEqual({}, tokens)

    def test_a_token_file_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.json"
            path.write_text(json.dumps({"operator": [OPERATOR_TOKEN], "viewer": VIEWER_TOKEN}))

            tokens = AC.load_tokens(path=path, environment={})

        self.assertEqual(AC.OPERATOR, tokens[OPERATOR_TOKEN])
        self.assertEqual(AC.VIEWER, tokens[VIEWER_TOKEN])

    def test_a_corrupt_token_file_yields_no_tokens_rather_than_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.json"
            path.write_text("{not json")

            self.assertEqual({}, AC.load_tokens(path=path, environment={}))

    def test_generated_tokens_are_long_and_unique(self) -> None:
        first, second = AC.generate_token(), AC.generate_token()

        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), AC.MIN_TOKEN_LENGTH)


class TokenPresentationTests(unittest.TestCase):
    def test_bearer_header_is_accepted(self) -> None:
        headers = Headers({"Authorization": f"Bearer {OPERATOR_TOKEN}"})

        self.assertEqual(OPERATOR_TOKEN, AC.presented_token(headers, None))

    def test_custom_header_is_accepted(self) -> None:
        headers = Headers({"X-PTPBox-Token": VIEWER_TOKEN})

        self.assertEqual(VIEWER_TOKEN, AC.presented_token(headers, None))

    def test_query_parameter_is_accepted_for_a_shared_link(self) -> None:
        self.assertEqual(VIEWER_TOKEN, AC.presented_token(None, {"token": [VIEWER_TOKEN]}))

    def test_absent_token_is_none(self) -> None:
        self.assertIsNone(AC.presented_token(Headers({}), {}))


class LocalAccessTests(unittest.TestCase):
    def test_a_lan_client_still_works_with_no_tokens_configured(self) -> None:
        # Configuring tokens must be a decision, not a prerequisite for the
        # existing local workflow.
        decision = AC.authorize("192.168.1.20", Headers({}), {}, mutating=True, tokens={})

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.may_control())

    def test_loopback_works_with_no_tokens(self) -> None:
        decision = AC.authorize("127.0.0.1", Headers({}), {}, mutating=False, tokens={})

        self.assertTrue(decision.allowed)

    def test_a_public_client_is_refused_when_no_tokens_exist(self) -> None:
        decision = AC.authorize("8.8.8.8", Headers({}), {}, mutating=False, tokens={})

        self.assertFalse(decision.allowed)
        self.assertEqual(401, decision.status)

    def test_only_the_intended_ranges_count_as_local(self) -> None:
        # is_private would have accepted carrier-grade NAT and the documentation
        # ranges. CGNAT is where a VPN such as Tailscale lives, and reaching the
        # appliance over a VPN should still present a token.
        for address in ("192.168.1.20", "10.0.0.5", "172.16.0.9", "127.0.0.1", "169.254.1.1"):
            self.assertTrue(AC._private(address), address)
        for address in ("100.64.1.5", "203.0.113.7", "198.51.100.4", "8.8.8.8", "172.32.0.1"):
            self.assertFalse(AC._private(address), address)

    def test_a_malformed_client_address_is_not_local(self) -> None:
        self.assertFalse(AC._private("not-an-address"))


class TunnelTrapTests(unittest.TestCase):
    """A tunnel daemon runs on the appliance, so tunnelled traffic arrives from
    loopback. Trusting the socket address would publish the control surface."""

    def test_a_forwarded_request_from_loopback_is_refused_without_a_token(self) -> None:
        for header in ("X-Forwarded-For", "CF-Connecting-IP", "Forwarded", "Ngrok-Trace-Id"):
            decision = AC.authorize("127.0.0.1", Headers({header: "203.0.113.9"}),
                                    {}, mutating=False, tokens={})

            self.assertFalse(decision.allowed, f"{header} must not be trusted")
            self.assertEqual(401, decision.status)
            self.assertIn("tunnel", decision.reason)

    def test_a_forwarded_request_with_a_viewer_token_may_observe(self) -> None:
        decision = AC.authorize("127.0.0.1", Headers({"X-Forwarded-For": "203.0.113.9",
                                                      "X-PTPBox-Token": VIEWER_TOKEN}),
                                {}, mutating=False, tokens=TOKENS)

        self.assertTrue(decision.allowed)
        self.assertEqual(AC.VIEWER, decision.role)

    def test_a_forwarded_request_with_a_viewer_token_may_not_control(self) -> None:
        decision = AC.authorize("127.0.0.1", Headers({"X-Forwarded-For": "203.0.113.9",
                                                      "X-PTPBox-Token": VIEWER_TOKEN}),
                                {}, mutating=True, tokens=TOKENS)

        self.assertFalse(decision.allowed)
        self.assertEqual(403, decision.status)
        self.assertIn("read-only", decision.reason)

    def test_forwarding_header_detection_is_case_insensitive(self) -> None:
        self.assertTrue(AC.looks_forwarded(Headers({"x-FoRwArDeD-fOr": "1.2.3.4"})))


class RoleEnforcementTests(unittest.TestCase):
    def test_an_operator_token_may_control(self) -> None:
        decision = AC.authorize("203.0.113.7", Headers({"Authorization": f"Bearer {OPERATOR_TOKEN}"}),
                                {}, mutating=True, tokens=TOKENS)

        self.assertTrue(decision.may_control())

    def test_a_viewer_token_may_read(self) -> None:
        decision = AC.authorize("203.0.113.7", Headers({}), {"token": [VIEWER_TOKEN]},
                                mutating=False, tokens=TOKENS)

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.may_control())

    def test_an_unknown_token_is_refused(self) -> None:
        decision = AC.authorize("127.0.0.1", Headers({"X-PTPBox-Token": "not-a-real-token-but-long"}),
                                {}, mutating=False, tokens=TOKENS)

        self.assertFalse(decision.allowed)
        self.assertEqual(403, decision.status)

    def test_configuring_tokens_ends_anonymous_lan_access(self) -> None:
        # Otherwise tokens would guard the tunnel while the LAN stayed open.
        decision = AC.authorize("192.168.1.20", Headers({}), {}, mutating=False, tokens=TOKENS)

        self.assertFalse(decision.allowed)
        self.assertEqual(401, decision.status)


class SummaryTests(unittest.TestCase):
    def test_summary_counts_roles_and_leaks_no_token(self) -> None:
        result = AC.summary(TOKENS)
        text = json.dumps(result)

        self.assertEqual(1, result["operator_tokens"])
        self.assertEqual(1, result["viewer_tokens"])
        self.assertTrue(result["tokens_configured"])
        self.assertFalse(result["anonymous_local_access"])
        self.assertNotIn(OPERATOR_TOKEN, text)
        self.assertNotIn(VIEWER_TOKEN, text)

    def test_summary_reports_the_open_posture_when_unconfigured(self) -> None:
        result = AC.summary({})

        self.assertFalse(result["tokens_configured"])
        self.assertTrue(result["anonymous_local_access"])


if __name__ == "__main__":
    unittest.main()
