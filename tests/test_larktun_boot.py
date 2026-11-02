import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from sr7410.boot import HOOK

ROOT = Path(__file__).resolve().parents[1]


class LarktunBootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sr7410-larktun-boot-")
        self.root = Path(self.temporary.name)
        self.ssh = self.root / "Plugin/sr7410-ssh"
        self.lark = self.root / "Plugin/sr7410-larktun"
        self.override = self.root / "Plugin/apps/dlspeed_rt.tar.gz"
        for directory in (self.ssh, self.lark, self.override.parent, self.root / "etc"):
            directory.mkdir(parents=True, exist_ok=True)
        (self.lark / "VERSION").write_text("synthetic\n")
        (self.ssh / "autostart.enabled").touch()
        (self.ssh / "boot-created.tar.gz").write_bytes(b"owned SSH component")
        self.override.write_bytes(b"owned SSH component")
        (self.root / "etc/dlspeed_rt.tar.gz").write_bytes(b"stock")
        self.stage = self.root / "var/sr7410-larktun-setup-test"
        self.stage.mkdir(parents=True)
        for name, data in (("stock.tar.gz", b"stock"), ("previous.tar.gz", b"owned SSH component"),
                           ("override.tar.gz", b"new shared component")):
            (self.stage / name).write_bytes(data)
        self.script = self.root / "autostart.sh"
        self.script.write_text(self.redirect((ROOT / "router/larktun/autostart.sh").read_text()))

    def redirect(self, text):
        return re.sub(r"(?<![A-Za-z0-9_/])/(?:Plugin|var|etc|apps)\b",
                      lambda match: str(self.root) + match.group(), text)

    def tearDown(self):
        self.temporary.cleanup()

    def run_action(self, action):
        return subprocess.run(["sh", str(self.script), action, str(self.stage)],
                              capture_output=True)

    def test_shared_upgrade_and_disable_preserve_ssh(self):
        result = self.run_action("enable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.override.read_bytes(), b"new shared component")
        self.assertEqual((self.ssh / "boot-created.tar.gz").read_bytes(), self.override.read_bytes())
        self.assertTrue((self.lark / "autostart.enabled").exists())
        self.assertEqual(self.run_action("disable").returncode, 0)
        self.assertFalse((self.lark / "autostart.enabled").exists())
        self.assertTrue((self.ssh / "autostart.enabled").exists())
        self.assertEqual(self.override.read_bytes(), b"new shared component")

    def test_unowned_override_is_not_replaced(self):
        self.override.write_bytes(b"other software")
        self.assertNotEqual(self.run_action("enable").returncode, 0)
        self.assertEqual(self.override.read_bytes(), b"other software")
        self.assertFalse((self.lark / "autostart.enabled").exists())

    def test_ssh_autostart_is_prerequisite(self):
        (self.ssh / "autostart.enabled").unlink()
        self.assertNotEqual(self.run_action("enable").returncode, 0)
        self.assertEqual(self.override.read_bytes(), b"owned SSH component")

    def test_shared_hook_honors_both_switches(self):
        import time
        hook = self.root / "hook.sh"
        hook.write_text(self.redirect(HOOK.decode()))
        for directory in (self.ssh, self.lark):
            script = directory / "start.sh"
            script.write_text("#!/bin/sh\ntouch " + str(directory / "started") + "\n")
            script.chmod(0o755)
        subprocess.run(["sh", str(hook)], check=True)
        for _ in range(50):
            if (self.ssh / "started").exists():
                break
            time.sleep(0.01)
        self.assertTrue((self.ssh / "started").exists())
        self.assertFalse((self.lark / "started").exists())
        (self.lark / "autostart.enabled").touch()
        subprocess.run(["sh", str(hook)], check=True)
        for _ in range(50):
            if (self.lark / "started").exists():
                break
            time.sleep(0.01)
        self.assertTrue((self.lark / "started").exists())
        (self.ssh / "autostart.enabled").unlink()
        (self.ssh / "started").unlink()
        (self.lark / "started").unlink()
        subprocess.run(["sh", str(hook)], check=True)
        self.assertFalse((self.ssh / "started").exists())
        self.assertFalse((self.lark / "started").exists())
