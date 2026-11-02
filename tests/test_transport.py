from pathlib import Path
import re
import secrets
import shlex
import socketserver
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from sr7410.router import client_files, lan_address, payload_server, transfer
from sr7410.telnet import RemoteError, Shell


class FakeRouter(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"Login: ")
        self.wfile.flush()
        self.rfile.readline()
        self.wfile.write(b"Password: ")
        self.wfile.flush()
        if self.rfile.readline().strip() != self.server.password:
            self.wfile.write(b"Login incorrect\r\n")
            self.wfile.flush()
            return
        self.wfile.write(b"\r\nrouter # ")
        self.wfile.flush()
        while True:
            try:
                line = self.rfile.readline()
            except ConnectionResetError:
                return
            if not line:
                return
            # The fake device is UID 0 even when tests run as an ordinary user.
            command = line.decode().rstrip("\r\n").replace("; id -u;", "; printf '0\\n';")
            # Emulate the target's wget without requiring wget on the developer's Mac.
            download = re.search(r"; (wget -q -O .*?); _sr7410_rc=", command)
            if download:
                parts = shlex.split(download.group(1))
                payload = urllib.request.urlopen(parts[-1], timeout=5).read()
                Path(parts[3]).write_bytes(payload)
                command = command.replace(download.group(1), "true")
            result = subprocess.run(["/bin/sh", "-c", command], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            self.wfile.write(line + result.stdout.replace(b"\n", b"\r\n") + b"router # ")
            self.wfile.flush()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.server = socketserver.TCPServer(("127.0.0.1", 0), FakeRouter)
        self.server.password = secrets.token_hex(12).encode()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def shell(self, password=None):
        return Shell("127.0.0.1", self.server.server_address[1], "root",
                     (self.server.password if password is None else password).decode())

    def test_shell_and_complete_binary_readback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "file with spaces"
            data = bytes(range(256)) * 1000  # Multiple read-back chunks.
            path.write_bytes(data)
            with self.shell() as shell:
                self.assertEqual(shell.run("printf hello"), "hello")
                self.assertEqual(shell.read_file(str(path)), data)
                with self.assertRaisesRegex(RemoteError, "failed"):
                    shell.run("false")
                self.assertEqual(shell.run("printf alive"), "alive")

    def test_bad_password_fails_once(self):
        with self.assertRaises((RemoteError, EOFError)):
            self.shell(b"wrong-synthetic-test-value")

    def test_http_transfer_and_byte_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "payload"
            data = secrets.token_bytes(12000)
            with self.shell() as shell:
                transfer(shell, "127.0.0.1", "127.0.0.1", data, str(destination))
            self.assertEqual(destination.read_bytes(), data)

    def test_corrupt_readback_stops_installation(self):
        class Corrupt:
            def run(self, command, timeout=30):
                pass
            def read_file(self, path):
                return b"corrupt"
        with self.assertRaisesRegex(RemoteError, "verification"):
            transfer(Corrupt(), "127.0.0.1", "127.0.0.1", b"expected", "/unused")

    def test_server_has_no_directory_or_other_file_access(self):
        with payload_server(b"payload", "127.0.0.1", "127.0.0.1") as url:
            self.assertEqual(urllib.request.urlopen(url).read(), b"payload")
            origin = "/".join(url.split("/")[:3])
            with self.assertRaises(urllib.error.HTTPError) as result:
                urllib.request.urlopen(origin + "/private/config.xml")
            self.assertEqual(result.exception.code, 404)

    def test_public_targets_rejected(self):
        for value in ("8.8.8.8", "example.com", "127.0.0.1", "192.168.1.1; reboot"):
            with self.assertRaises(ValueError):
                lan_address(value)
        self.assertEqual(lan_address("192.168.1.1"), "192.168.1.1")

    def test_client_uses_pinned_host_key_and_separate_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private"
            client_files(directory, "192.168.1.1", 2222, "root",
                         Path(temporary) / "local key", "synthetic-host-key-for-test")
            config = (directory / "ssh_config").read_text()
            self.assertIn("StrictHostKeyChecking yes", config)
            self.assertIn("PasswordAuthentication no", config)
            self.assertEqual((directory / "known_hosts").read_text(),
                             "[192.168.1.1]:2222 synthetic-host-key-for-test\n")


if __name__ == "__main__":
    unittest.main()
