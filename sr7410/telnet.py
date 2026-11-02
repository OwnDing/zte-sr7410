"""Prompted or private-file credentials; never pass a password on the command line."""
from __future__ import annotations
import re
import secrets
from telnetlib3.telnetlib import Telnet


class RemoteError(RuntimeError):
    pass


class Shell:
    def __init__(self, host: str, port: int, user: str, password: str):
        if any(c in user + password for c in "\r\n\x00"):
            raise ValueError("Credentials must not contain control characters")
        self.password = password
        self.connection = Telnet(host, port, timeout=8)
        try:
            index, _, _ = self.connection.expect([rb"(?i)(?:login|username):\s*"], 8)
            if index < 0:
                raise RemoteError("Telnet login prompt was not found")
            self.connection.write(user.encode() + b"\r\n")
            index, _, _ = self.connection.expect([rb"(?i)password:\s*"], 8)
            if index < 0:
                raise RemoteError("Telnet password prompt was not found")
            self.connection.write(password.encode() + b"\r\n")
            index, _, _ = self.connection.expect([
                rb"(?m)(?:^|\n)[^\r\n]*[#$] ?$",
                rb"(?i)login incorrect", rb"(?i)login:",
            ], 8)
            if index != 0:
                raise RemoteError("Telnet authentication failed; no automatic retry")
            if self.run("id -u").strip() != "0":
                raise RemoteError("A root Telnet shell is required")
        except BaseException:
            self.close()
            raise

    def run(self, command: str, timeout=30) -> str:
        token = secrets.token_hex(12)
        start, end = "__START_" + token, "__END_" + token
        script = ("printf '\\n" + start + "\\n'; " + command +
                  "; _sr7410_rc=$?; printf '\\n" + end + ":%s\\n' \"$_sr7410_rc\"")
        self.connection.write(script.encode() + b"\r\n")
        pattern = rb"\r?\n" + end.encode() + rb":([0-9]+)\r?\n"
        index, match, data = self.connection.expect([pattern], timeout)
        if index < 0:
            raise RemoteError("Remote command timed out; inspect the router before retrying")
        starts = list(re.finditer(rb"\r?\n" + start.encode() + rb"\r?\n", data))
        if not starts:
            raise RemoteError("Remote command start marker missing")
        body = data[starts[-1].end():match.start()]
        result = body.decode("utf-8", "replace").replace("\r\n", "\n")
        if int(match.group(1)):
            safe = result.replace(self.password, "<redacted>") if self.password else result
            raise RemoteError("Remote command failed: " + safe[-1500:])
        return result

    def read_file(self, path: str, max_size=8 * 1024 * 1024) -> bytes:
        import shlex
        quoted = shlex.quote(path)
        size = self.run("wc -c < " + quoted).strip()
        if not size.isdigit() or not 0 <= int(size) <= max_size:
            raise RemoteError("Remote file size is outside the permitted range")
        result = bytearray()
        # The stdlib-compatible Telnet expect() repeatedly scans its accumulated
        # buffer. A multi-megabyte single line becomes quadratic; bound each read.
        for offset in range(0, int(size), 32768):
            count = min(32768, int(size) - offset)
            command = (f"hexdump -v -s {offset} -n {count} "
                       "-e '1/1 \"%02x\"' " + quoted)
            encoded = self.run(command, timeout=30).strip()
            if len(encoded) != count * 2 or re.fullmatch(r"[0-9a-f]*", encoded) is None:
                raise RemoteError("Invalid hexadecimal file read-back")
            result.extend(bytes.fromhex(encoded))
        return bytes(result)

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
