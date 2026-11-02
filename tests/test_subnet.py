from contextlib import redirect_stdout
import io
import json
import secrets
import shlex
import unittest
from sr7410.larktun import change_subnet, subnet_status


class SubnetRouter:
    def __init__(self, routes=None, running=True, lan="192.168.1.1/24"):
        self.calls = []
        self.lan = lan
        self.running = running
        self.preferences = {
            "AdvertiseRoutes": routes or [], "RouteAll": False, "CorpDNS": False,
            "WantRunning": True,
            "Persist": {"SyntheticPrivateTestValue": secrets.token_hex(32)},
        }

    def run(self, command, **kwargs):
        self.calls.append(command)
        if " status --json" in command and " serve " not in command:
            return json.dumps({"BackendState": "Running" if self.running else "NeedsLogin",
                               "Self": {"AllowedIPs": ["100.64.0.99/32"],
                                        "PrimaryRoutes": []}}).encode()
        if "ip -4 addr show br0" in command:
            return self.lan.encode()
        if "debug prefs" in command:
            return json.dumps(self.preferences).encode()
        if "debug netmap" in command:
            return json.dumps({
                "SelfNode": {
                    "AllowedIPs": ["100.64.0.99/32"] + self.preferences["AdvertiseRoutes"],
                    "PrimaryRoutes": self.preferences["AdvertiseRoutes"],
                },
                "PrivateKey": self.preferences["Persist"]["SyntheticPrivateTestValue"],
            }).encode()
        if "serve status" in command:
            return b'{"synthetic":"serve unchanged"}'
        if "set --advertise-routes=" in command:
            value = shlex.split(command)[-1].split("=", 1)[1]
            self.preferences["AdvertiseRoutes"] = value.split(",") if value else []
            return b""
        raise AssertionError("Unexpected command")


class SubnetTests(unittest.TestCase):
    def test_advertise_preserves_other_routes_and_credentials(self):
        device = SubnetRouter(["192.168.2.0/24"])
        before = dict(device.preferences)
        output = io.StringIO()
        with redirect_stdout(output):
            change_subnet(device, "192.168.1.0/24")
        self.assertEqual(device.preferences["AdvertiseRoutes"],
                         ["192.168.2.0/24", "192.168.1.0/24"])
        for field in before.keys() - {"AdvertiseRoutes"}:
            self.assertEqual(device.preferences[field], before[field])
        self.assertNotIn(before["Persist"]["SyntheticPrivateTestValue"], output.getvalue())
        mutations = [command for command in device.calls if " set " in command]
        self.assertEqual(len(mutations), 1)
        self.assertNotIn("accept-dns", mutations[0])
        self.assertNotIn("accept-routes", mutations[0])
        self.assertNotIn(" up ", mutations[0])

    def test_unreachable_subnet_rejected_before_mutation(self):
        device = SubnetRouter()
        with self.assertRaisesRegex(ValueError, "br0"):
            change_subnet(device, "192.168.2.0/24")
        self.assertFalse(any(" set " in command for command in device.calls))

    def test_public_default_ipv6_and_noncanonical_prefixes_rejected(self):
        for prefix in ("0.0.0.0/0", "8.8.8.0/24", "fd00::/64", "192.168.1.1/24"):
            device = SubnetRouter()
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    change_subnet(device, prefix)
                self.assertEqual(device.calls, [])

    def test_withdraw_removes_only_requested_subnet(self):
        device = SubnetRouter(["192.168.1.0/24", "192.168.2.0/24"])
        with redirect_stdout(io.StringIO()):
            change_subnet(device, "192.168.1.0/24", withdraw=True)
        self.assertEqual(device.preferences["AdvertiseRoutes"], ["192.168.2.0/24"])

    def test_withdraw_last_route_and_idempotent_advertise(self):
        device = SubnetRouter(["192.168.1.0/24"])
        with redirect_stdout(io.StringIO()):
            change_subnet(device, "192.168.1.0/24")
        self.assertFalse(any(" set " in command for command in device.calls))
        with redirect_stdout(io.StringIO()):
            change_subnet(device, "192.168.1.0/24", withdraw=True)
        self.assertEqual(device.preferences["AdvertiseRoutes"], [])

    def test_status_does_not_dump_persisted_identity(self):
        device = SubnetRouter(["192.168.1.0/24"])
        output = io.StringIO()
        with redirect_stdout(output):
            subnet_status(device)
        self.assertIn("192.168.1.0/24", output.getvalue())
        self.assertNotIn(device.preferences["Persist"]["SyntheticPrivateTestValue"], output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["route_data_source"], "network_map")
        self.assertEqual(json.loads(output.getvalue())["allowed_advertised_routes"],
                         ["192.168.1.0/24"])

    def test_status_falls_back_when_network_map_debug_is_unavailable(self):
        device = SubnetRouter(["192.168.1.0/24"])
        original = device.run
        def without_netmap(command, **kwargs):
            if "debug netmap" in command:
                raise RuntimeError("Unavailable")
            return original(command, **kwargs)
        device.run = without_netmap
        output = io.StringIO()
        with redirect_stdout(output):
            subnet_status(device)
        report = json.loads(output.getvalue())
        self.assertEqual(report["route_data_source"], "status")
        self.assertEqual(report["advertised_routes"], ["192.168.1.0/24"])
        self.assertEqual(report["allowed_advertised_routes"], [])

    def test_offline_node_is_not_changed(self):
        device = SubnetRouter(running=False)
        with self.assertRaisesRegex(RuntimeError, "Running"):
            change_subnet(device, "192.168.1.0/24")
        self.assertFalse(any(" set " in command for command in device.calls))
