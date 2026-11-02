#!/usr/bin/env python3
"""Check publishable files only; do not read ignored private/ or build/ data."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, check=True)
    failures = []
    private_name = re.compile(r"(?i)(?:config.*\.(?:bin|xml)|authorized_keys|known_hosts|"
                              r"ssh_config|.*host_key.*|.*\.(?:ipk|pem|key|tar\.gz))$")
    patterns = [
        ("private key material", re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----")),
        ("literal home directory", re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/")),
        ("literal SSH public key", re.compile(r"ssh-ed25519 [A-Za-z0-9+/]{50,}={0,2}")),
        ("literal MAC address", re.compile(r"(?<![A-Za-z0-9])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![A-Za-z0-9])")),
    ]
    for name in set(result.stdout.decode().strip("\0").split("\0")):
        if not name:
            continue
        path = ROOT / name
        if name.startswith(("private/", "build/", ".cache/", ".venv/")) or private_name.fullmatch(path.name):
            failures.append((name, "private/generated artifact"))
            continue
        if path.is_symlink():
            failures.append((name, "unexpected symlink"))
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            if path.suffix != ".png" or not name.startswith("docs/images/"):
                failures.append((name, "unexpected binary artifact"))
            continue
        for label, pattern in patterns:
            if pattern.search(content):
                failures.append((name, label))
    if failures:
        for name, label in failures:
            print(f"BLOCK: {name}: {label}")
        return 1
    print("通过：待发布文件未发现配置备份、密钥材料、用户主目录或设备 MAC。")
    print("这是静态检查；Issue、截图和新增文件仍需人工检查。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
