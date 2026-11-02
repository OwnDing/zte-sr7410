import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from sr7410.boot import HOOK, MONITOR, make_boot_bundle
from sr7410.runtime import package_files, relocate, write_archive


def archive(items):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as output:
        for name, data, mode in items:
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), mode
            output.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


class RuntimeTests(unittest.TestCase):
    def test_unknown_binary_is_never_patched(self):
        with self.assertRaisesRegex(ValueError, "upstream binary"):
            relocate("sbin/dropbear", b"/opt/lib fake ELF")

    def test_ipk_only_reads_allowlist(self):
        payload = archive([("../../outside", b"evil", 0o755),
                           ("./opt/sbin/dropbear", b"ELF", 0o755)])
        self.assertEqual(package_files(archive([("./data.tar.gz", payload, 0o644)])),
                         {"sbin/dropbear": b"ELF"})

    def test_build_reproducibility_and_no_host_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a.gz", Path(directory) / "b.gz"
            for path in (first, second):
                write_archive(path, {"sbin/a": b"data"}, {"sbin/b": "a"})
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first) as output:
                for member in output:
                    self.assertEqual((member.uid, member.gid, member.uname, member.gname, member.mtime),
                                     (0, 0, "", "", 0))

    def test_unknown_stock_bundle_rejected(self):
        with self.assertRaisesRegex(ValueError, "Untested"):
            make_boot_bundle(b"not the tested vendor bundle")

    def test_boot_changes_only_monitor_and_preserves_modes(self):
        old_script = b"#!/bin/sh\nprintf vendor\n"
        stock = archive([(MONITOR, old_script, 0o751),
                         ("dlspeed_rt/data", b"unchanged", 0o640)])
        with patch("sr7410.boot.STOCK_SHA256", hashlib.sha256(stock).hexdigest()):
            changed = make_boot_bundle(stock)
        with tarfile.open(fileobj=io.BytesIO(changed)) as result:
            self.assertEqual(result.extractfile(MONITOR).read(), HOOK + old_script)
            self.assertEqual(result.getmember(MONITOR).mode, 0o751)
            self.assertEqual(result.extractfile("dlspeed_rt/data").read(), b"unchanged")
            self.assertEqual(result.getmember("dlspeed_rt/data").mode, 0o640)
            self.assertEqual(len(result.getmembers()), 2)

    def test_invalid_stock_layout_rejected_even_with_matching_hash(self):
        stock = archive([("../../outside", b"data", 0o644), (MONITOR, b"#!/bin/sh\n", 0o755)])
        with patch("sr7410.boot.STOCK_SHA256", hashlib.sha256(stock).hexdigest()):
            with self.assertRaisesRegex(ValueError, "layout"):
                make_boot_bundle(stock)
