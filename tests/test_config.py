import os
from pathlib import Path
import secrets
import struct
import tempfile
import unittest
from sr7410.config import decode, encode, patch_telnet, prepare, telnet_row


def fixture(wan="0", enabled="0"):
    # Entirely synthetic. No configuration exported from a real device.
    password = secrets.token_hex(12)
    return (f'<?xml version="1.0" encoding="UTF-8"?><DB>'
            f'<Tbl name="Other"><Row No="0"><DM name="TS_Enable" val="0"/></Row></Tbl>'
            f'<Tbl name="TelnetCfg" RowCount="1"><Row No="0">'
            f'<DM name="TS_Enable" val="{enabled}"/><DM name="Lan_Enable" val="{enabled}"/>'
            f'<DM name="Wan_Enable" val="{wan}"/><DM name="TS_Port" val="23"/>'
            f'<DM name="TS_UName" val="root"/><DM name="TS_UPwd" val="{password}"/>'
            f'<DM name="Max_Con_Num" val="5"/></Row></Tbl></DB>').encode()


class ConfigTests(unittest.TestCase):
    def test_only_two_value_bytes_change(self):
        original = fixture()
        changed, fields = patch_telnet(original)
        self.assertEqual(len(fields), 2)
        self.assertEqual(telnet_row(changed)["TS_UPwd"], telnet_row(original)["TS_UPwd"])
        expected = original.replace(b'name="Lan_Enable" val="0"', b'name="Lan_Enable" val="1"')
        start = expected.index(b'<Tbl name="TelnetCfg"')
        expected = expected[:start] + expected[start:].replace(
            b'name="TS_Enable" val="0"', b'name="TS_Enable" val="1"')
        self.assertEqual(changed, expected)

    def test_roundtrip_and_private_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            xml = fixture()
            source = root / "input.bin"
            original = encode(xml)
            source.write_bytes(original)
            report = prepare(source, root / "output")
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(decode((root / "output/config-original.bin").read_bytes())[0], xml)
            self.assertEqual(telnet_row(decode(
                (root / "output/config-telnet-lan-only.bin").read_bytes())[0])["Wan_Enable"], "0")
            self.assertTrue(report["original_roundtrip_identical"])
            self.assertNotIn(telnet_row(xml)["TS_UPwd"], (root / "output/report.json").read_text())
            if os.name == "posix":
                self.assertEqual((root / "output").stat().st_mode & 0o777, 0o700)
                self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in (root / "output").iterdir()))
            with self.assertRaises(FileExistsError):
                prepare(source, root / "output")

    def test_reject_wan_telnet(self):
        with self.assertRaisesRegex(ValueError, "WAN"):
            patch_telnet(fixture(wan="1"))

    def test_already_enabled_is_idempotent(self):
        xml = fixture(enabled="1")
        self.assertEqual(patch_telnet(xml), (xml, []))

    def test_reject_duplicate_fields(self):
        xml = fixture().replace(b'</Row></Tbl></DB>', b'<DM name="TS_Enable" val="0"/></Row></Tbl></DB>')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            patch_telnet(xml)

    def test_corruption_and_foreign_format_rejected(self):
        binary = encode(fixture())
        for damaged in (b"\0" + binary[1:], binary + b"extra", binary[:-1],
                        binary[:72] + bytes([binary[72] ^ 1]) + binary[73:]):
            with self.subTest(size=len(damaged)):
                with self.assertRaises((ValueError, struct.error)):
                    decode(damaged)

    def test_unexpected_outer_header_rejected(self):
        binary = bytearray(encode(fixture()))
        binary[11] = 1
        with self.assertRaisesRegex(ValueError, "outer format"):
            decode(bytes(binary))

    def test_chunk_profile_rejected(self):
        with self.assertRaisesRegex(ValueError, "chunk"):
            decode(encode(fixture(), chunk_size=32768))

    def test_failed_validation_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "bad.bin"
            source.write_bytes(encode(fixture(wan="1")))
            with self.assertRaises(ValueError):
                prepare(source, root / "output")
            self.assertFalse((root / "output").exists())

    def test_entities_rejected(self):
        xml = b'<!DOCTYPE DB [<!ENTITY secret "x">]>' + fixture()
        with self.assertRaisesRegex(ValueError, "entities"):
            decode(encode(xml))


if __name__ == "__main__":
    unittest.main()
