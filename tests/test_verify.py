from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import select
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from sr7410.router import verify
from sr7410.telnet import RemoteError


class VerifyTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX select can inspect pipe EOF")
    def test_forced_pty_keeps_stdin_open_until_remote_command_exits(self):
        def ssh(arguments, **kwargs):
            output = "0\n"
            if "-tt" in arguments:
                # If no writer remains, select reports EOF as readable. That
                # reproduces the early HUP observed on the actual router.
                self.assertIn("stdin", kwargs)
                readable, _, _ = select.select([kwargs["stdin"]], [], [], 0)
                self.assertEqual(readable, [])
                output = "/var/pts/3\n0\n"
            return SimpleNamespace(returncode=0, stdout=output, stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "ssh_config").write_text("# synthetic\n")
            with patch("sr7410.router.subprocess.run", side_effect=ssh), redirect_stdout(io.StringIO()):
                verify(SimpleNamespace(client_dir=directory))

    def test_command_success_without_pty_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "ssh_config").write_text("# synthetic\n")
            with patch("sr7410.router.subprocess.run",
                       return_value=SimpleNamespace(returncode=0, stdout="0\n", stderr="")):
                with self.assertRaisesRegex(RemoteError, "PTY"):
                    verify(SimpleNamespace(client_dir=directory))
