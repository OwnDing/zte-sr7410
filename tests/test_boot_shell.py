"""Exercise ownership and rollback guards in a disposable filesystem namespace."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sr7410-shell-tests-")
        self.root = Path(self.temporary.name)
        self.base = self.root / "Plugin/sr7410-ssh"
        self.base.mkdir(parents=True)
        (self.base / "VERSION").write_text("synthetic-test\n")
        (self.root / "etc").mkdir()
        (self.root / "apps").symlink_to(self.root / "Plugin", target_is_directory=True)
        (self.root / "etc/init.norm").write_text(str(self.root / "apps/apps/dlspeed_rt.tar.gz"))
        (self.root / "etc/dlspeed_rt.tar.gz").write_bytes(b"synthetic stock component")
        self.stage = self.root / "var/sr7410-setup-test"
        self.stage.mkdir(parents=True)
        (self.stage / "stock.tar.gz").write_bytes(b"synthetic stock component")
        (self.stage / "override.tar.gz").write_bytes(b"synthetic verified patched component")
        self.override = self.root / "Plugin/apps/dlspeed_rt.tar.gz"
        # Only redirect the fixture copy's absolute filesystem namespace.
        script = (ROOT / "router/boot.sh").read_text()
        script = re.sub(r"(?<![A-Za-z0-9_/])/(?:Plugin|var|etc|apps)\b",
                        lambda match: str(self.root) + match.group(), script)
        self.script = self.base / "boot.sh"
        self.script.write_text(script)
        self.script.chmod(0o755)
        tools = self.root / "tools"
        tools.mkdir()
        (tools / "id").write_text("#!/bin/sh\nprintf '0\\n'\n")
        (tools / "id").chmod(0o755)
        self.environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"])

    def tearDown(self):
        self.temporary.cleanup()

    def run_boot(self, action):
        return subprocess.run(["sh", str(self.script), action, str(self.stage)],
                              capture_output=True, env=self.environment)

    def test_enable_disable_preserves_stock(self):
        result = self.run_boot("enable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.override.read_bytes(), (self.stage / "override.tar.gz").read_bytes())
        self.assertTrue((self.base / "autostart.enabled").exists())
        self.assertEqual(self.run_boot("enable").returncode, 0)
        self.assertEqual(self.run_boot("disable").returncode, 0)
        self.assertFalse(self.override.exists())
        self.assertEqual((self.root / "etc/dlspeed_rt.tar.gz").read_bytes(),
                         b"synthetic stock component")

    def test_existing_other_override_is_never_replaced(self):
        self.override.parent.mkdir()
        self.override.write_bytes(b"other software")
        self.assertNotEqual(self.run_boot("enable").returncode, 0)
        self.assertEqual(self.override.read_bytes(), b"other software")
        self.assertFalse((self.base / "autostart.enabled").exists())

    def test_later_modified_override_is_not_deleted(self):
        self.assertEqual(self.run_boot("enable").returncode, 0)
        self.override.write_bytes(b"modified by another installer")
        self.assertNotEqual(self.run_boot("disable").returncode, 0)
        self.assertEqual(self.override.read_bytes(), b"modified by another installer")
        self.assertFalse((self.base / "autostart.enabled").exists())

    def test_stock_change_between_host_validation_and_install_is_rejected(self):
        (self.root / "etc/dlspeed_rt.tar.gz").write_bytes(b"updated stock")
        self.assertNotEqual(self.run_boot("enable").returncode, 0)
        self.assertFalse(self.override.exists())
