from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import secrets
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from sr7410.larktun import (SSH, authenticate, configure_remote_ssh, expose_router,
                            status, validate_login_server)
from sr7410.larktun_build import download, verify_binary


def synthetic_elf(interpreter=False):
    data = bytearray(120)
    data[:6] = b"\x7fELF\x02\x01"
    data[18:20] = b"\xb7\x00"
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 1)
    struct.pack_into("<I", data, 64, 3 if interpreter else 1)
    return bytes(data)


def item_for(data):
    return {"filename": "synthetic-client", "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


class Recorder:
    def __init__(self, running=True, fail_login=False):
        self.calls = []
        self.running = running
        self.fail_login = fail_login

    def run(self, command, data=None, timeout=30):
        self.calls.append((command, data))
        if self.fail_login and " up " in command:
            raise RuntimeError("synthetic authentication failure")
        if "status --json" in command:
            return json.dumps({
                "BackendState": "Running" if self.running else "NeedsLogin",
                "Self": {"TailscaleIPs": ["100.64.0.99"], "DNSName": "private-test-name"},
                "Peer": {"test": {"User": "private-test-account"}},
            }).encode()
        return b"autostart disabled"


class LarktunTests(unittest.TestCase):
    def test_static_arm64_and_digest_checks(self):
        data = synthetic_elf()
        verify_binary(data, item_for(data))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            verify_binary(data + b"corrupt", item_for(data))
        dynamic = synthetic_elf(True)
        with self.assertRaisesRegex(ValueError, "static"):
            verify_binary(dynamic, item_for(dynamic))

    def test_untrusted_cache_is_rejected_without_downloading(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            data = synthetic_elf()
            (path / "synthetic-client").write_bytes(b"bad")
            with patch("sr7410.larktun_build.subprocess.run") as network:
                with self.assertRaises(ValueError):
                    download(item_for(data), ["https://example.invalid/"], path)
                network.assert_not_called()

    def test_auth_key_only_on_encrypted_stdin_and_ram_file_is_cleaned(self):
        ssh = Recorder()
        key = secrets.token_hex(24)
        output = io.StringIO()
        with redirect_stdout(output):
            authenticate(ssh, key, "https://hs.larktun.com", "zte-sr7410")
        self.assertEqual(sum(data == key.encode() for _, data in ssh.calls), 1)
        self.assertTrue(all(key not in command for command, _ in ssh.calls))
        self.assertNotIn(key, output.getvalue())
        auth_command = next(command for command, data in ssh.calls if data is not None)
        self.assertIn("--auth-key=file:/var/", auth_command)
        self.assertIn("trap 'rm -f ", auth_command)
        self.assertIn("--accept-dns=false --accept-routes=false", auth_command)
        self.assertTrue(ssh.calls[-1][0].startswith("rm -rf /var/sr7410-larktun-setup-"))

    def test_auth_cleanup_runs_on_failure(self):
        ssh = Recorder(fail_login=True)
        with self.assertRaises(RuntimeError):
            authenticate(ssh, secrets.token_hex(24), "https://hs.larktun.com", "zte-sr7410")
        self.assertTrue(ssh.calls[-1][0].startswith("rm -rf /var/"))

    def test_invalid_login_inputs_never_contact_router(self):
        for server in ("http://example.invalid", "https://user:secret@example.invalid",
                       "https://example.invalid/?token=sensitive", "https://example.invalid/#fragment"):
            with self.assertRaises(ValueError):
                validate_login_server(server)
        ssh = Recorder()
        with self.assertRaises(ValueError):
            authenticate(ssh, "short", "https://hs.larktun.com", "zte-sr7410")
        with self.assertRaises(ValueError):
            authenticate(ssh, secrets.token_hex(24), "https://hs.larktun.com", "host; reboot")
        self.assertEqual(ssh.calls, [])

    def test_status_does_not_dump_accounts_or_peer_names(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertTrue(status(Recorder()))
        self.assertIn("Running", output.getvalue())
        self.assertNotIn("private-test-account", output.getvalue())
        self.assertNotIn("private-test-name", output.getvalue())

    def test_tcp_mapping_is_private_loopback_and_requires_running(self):
        ssh = Recorder(running=False)
        with self.assertRaises(RuntimeError):
            expose_router(ssh)
        self.assertFalse(any(" serve " in command for command, _ in ssh.calls))
        ssh = Recorder()
        with redirect_stdout(io.StringIO()):
            expose_router(ssh, ssh_port=2222)
        commands = [command for command, _ in ssh.calls if " serve " in command]
        self.assertEqual(len(commands), 2)
        self.assertTrue(all("tcp://127.0.0.1:" in command for command in commands))
        self.assertTrue(all("funnel" not in command for command in commands))
        self.assertIn("--tcp 2222", commands[1])

    def test_ssh_errors_never_expose_raw_output_or_input(self):
        secret = secrets.token_hex(24)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "ssh_config").write_text("# synthetic\n")
            ssh = SSH(directory)
            with patch("sr7410.larktun.subprocess.run",
                       return_value=SimpleNamespace(returncode=1, stdout=secret.encode(),
                                                    stderr=secret.encode())) as child:
                with self.assertRaises(RuntimeError) as error:
                    ssh.run("generic-auth-operation", data=secret.encode())
                self.assertNotIn(secret, str(error.exception))
                self.assertNotIn(secret, str(child.call_args.args))
                self.assertEqual(child.call_args.kwargs["input"], secret.encode())

    def test_upload_length_mismatch_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "ssh_config").write_text("# synthetic\n")
            ssh = SSH(directory)
            with patch.object(ssh, "run", side_effect=[b"", b"1\n"]):
                with self.assertRaisesRegex(RuntimeError, "length"):
                    ssh.upload("/var/synthetic-payload", b"longer")

    def test_remote_config_reuses_trusted_key_and_keeps_lan_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = ("Host sr7410\n    HostName 192.168.1.1\n    Port 2222\n"
                      "    User root\n    UserKnownHostsFile original\n"
                      "    StrictHostKeyChecking yes\n")
            (directory / "ssh_config").write_text(config)
            trusted_key = "c3ludGhldGljLXRlc3Qta2V5"
            (directory / "known_hosts").write_text(f"[192.168.1.1]:2222 ssh-ed25519 {trusted_key}\n")
            with redirect_stdout(io.StringIO()):
                configure_remote_ssh(Recorder(), directory)
            self.assertEqual((directory / "ssh_config").read_text(), config)
            result = (directory / "ssh_config.larktun").read_text()
            self.assertIn("Host sr7410-vpn\n", result)
            self.assertIn("HostName 100.64.0.99", result)
            self.assertIn("StrictHostKeyChecking yes", result)
            self.assertEqual((directory / "known_hosts.larktun").read_text(),
                             f"[100.64.0.99]:2222 ssh-ed25519 {trusted_key}\n")
            with self.assertRaisesRegex(ValueError, "already exist"):
                configure_remote_ssh(Recorder(), directory)
