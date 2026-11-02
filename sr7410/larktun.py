"""Install and manage Larktun through the repository's pinned SSH connection."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import getpass
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from .boot import make_boot_bundle
from .config import private_write
from .larktun_build import build

BASE = "/Plugin/sr7410-larktun"
CLI = BASE + "/cli.sh"
SSH_BASE = "/Plugin/sr7410-ssh"
OVERRIDE = "/Plugin/apps/dlspeed_rt.tar.gz"


class SSH:
    def __init__(self, client_dir: Path):
        config = client_dir / "ssh_config"
        if not config.is_file():
            raise ValueError("SSH client configuration missing; complete README SSH setup first")
        self.arguments = ["ssh", "-F", str(config), "-T", "-o", "BatchMode=yes", "sr7410"]

    def run(self, command: str, data: bytes | None = None, timeout=30) -> bytes:
        result = subprocess.run(self.arguments + [command], input=data,
                                capture_output=True, timeout=timeout)
        if result.returncode:
            # CLI stderr can contain login URLs or credentials. Do not expose it
            # by default, especially for the authentication operation.
            raise RuntimeError(f"SSH operation failed (exit {result.returncode}); "
                               "check SSH access, prerequisites and the router's local log")
        return result.stdout

    def upload(self, destination: str, data: bytes):
        quoted = shlex.quote(destination)
        self.run("umask 077; cat > " + quoted, data=data, timeout=300)
        # SSH transport authenticates and checks integrity; wc catches truncation.
        if self.run("wc -c < " + quoted).strip() != str(len(data)).encode():
            raise RuntimeError("Remote upload length mismatch")

    def read(self, source: str, limit=8 * 1024 * 1024):
        quoted = shlex.quote(source)
        size = self.run("wc -c < " + quoted).strip()
        if not size.isdigit() or int(size) > limit:
            raise ValueError("Unexpected remote file size")
        data = self.run("cat " + quoted, timeout=60)
        if len(data) != int(size):
            raise RuntimeError("Remote read length mismatch")
        return data


@contextmanager
def stage(ssh):
    path = "/var/sr7410-larktun-setup-" + secrets.token_hex(8)
    ssh.run("umask 077; mkdir " + path)
    try:
        yield path
    finally:
        try:
            ssh.run("rm -rf " + path)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass


def install(ssh, cache_dir: Path):
    ssh.run("test \"$(id -u)\" = 0 && test \"$(uname -m)\" = aarch64 && "
            "test -f " + SSH_BASE + "/VERSION && "
            "test ! -e " + BASE + " && test ! -L " + BASE + " && test ! -e /Plugin/larktun")
    with tempfile.TemporaryDirectory(prefix="sr7410-larktun-build-") as temporary:
        archive = build(Path(temporary) / "runtime.tar.gz", cache_dir)
        with stage(ssh) as remote:
            print("通过 SSH 上传运行包（仅公开程序与证书，不含登录状态）...", flush=True)
            ssh.upload(remote + "/payload.tar.gz", archive.read_bytes())
            ssh.run("mkdir " + remote + "/payload && tar -xzf " + remote +
                    "/payload.tar.gz -C " + remote + "/payload", timeout=90)
            ssh.run("cd " + remote + "/payload && sh install.sh", timeout=120)
    print("已安装并启动；请运行 login 授权。尚未发布端口或启用 Larktun 自启。")


def validate_login_server(value: str):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Use an HTTPS control-server origin without credentials or query")
    return value.rstrip("/")


def authenticate(ssh, key: str, login_server: str, hostname: str):
    if not 16 <= len(key) <= 1024 or any(c.isspace() or ord(c) < 32 for c in key):
        raise ValueError("Auth Key format is incomplete")
    server = validate_login_server(login_server)
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", hostname):
        raise ValueError("Use a simple device hostname (letters, digits and hyphens)")
    with stage(ssh) as remote:
        filename = remote + "/auth.key"
        # Key bytes travel only on encrypted stdin; command line contains a RAM
        # filename. EXIT and signal traps delete it even when authentication fails.
        command = (
            "umask 077; trap 'rm -f " + filename + "' EXIT HUP INT TERM; "
            "cat > " + filename + "; " + CLI + " up "
            "--login-server=" + shlex.quote(server) + " --hostname=" + shlex.quote(hostname) +
            " --accept-dns=false --accept-routes=false --timeout=30s "
            "--auth-key=file:" + filename
        )
        ssh.run(command, data=key.encode(), timeout=45)
    print("授权命令完成；请用 status 确认 Running 和本机私网 IP。")


def status(ssh):
    value = json.loads(ssh.run(CLI + " status --json").decode())
    own = value.get("Self") or {}
    ips = own.get("TailscaleIPs") or value.get("TailscaleIPs") or []
    print("状态：" + str(value.get("BackendState", "unknown")))
    print("本机私网 IP：" + (", ".join(ips) if ips else "尚未分配"))
    print(ssh.run(BASE + "/autostart.sh status").decode().strip())
    # Never dump peer lists, account names, login URLs or node keys.
    return value.get("BackendState") == "Running"


def expose_router(ssh, web_port=80, ssh_port=22):
    for port in (web_port, ssh_port):
        if not 1 <= port <= 65535:
            raise ValueError("Port must be in 1..65535")
    if web_port == ssh_port:
        raise ValueError("Web and SSH must use different TCP ports")
    value = json.loads(ssh.run(CLI + " status --json").decode())
    if value.get("BackendState") != "Running":
        raise RuntimeError("Larktun is not Running; authenticate and verify status first")
    for port in (web_port, ssh_port):
        ssh.run(f"{CLI} serve --bg --yes --tcp {port} tcp://127.0.0.1:{port}", timeout=30)
    print("已创建两条 TCP Serve 映射，仅供你的 Larktun 网络按 ACL 访问。")
    print("请在另一台设备连接同一工作空间，关闭 Wi-Fi 后验证网页和原生 SSH。")


def enable_autostart(ssh):
    ssh.run("test -f " + BASE + "/VERSION && test -f " + SSH_BASE + "/autostart.enabled && "
            "test -f " + OVERRIDE + " && test ! -L " + OVERRIDE + " && "
            "cmp -s " + OVERRIDE + " " + SSH_BASE + "/boot-created.tar.gz")
    stock = ssh.read("/etc/dlspeed_rt.tar.gz")
    previous = ssh.read(OVERRIDE)
    override = make_boot_bundle(stock)
    with stage(ssh) as remote:
        for name, data in (("stock.tar.gz", stock), ("previous.tar.gz", previous),
                           ("override.tar.gz", override)):
            ssh.upload(remote + "/" + name, data)
        ssh.run(BASE + "/autostart.sh enable " + remote)
    print("已接入本仓库的共享 SSH 自启入口；请自行重启，随后验证 SSH 与 Larktun。")
    print("关闭共享 SSH 自启也会停止 Larktun 的开机启动。")


def configure_remote_ssh(ssh, client_dir):
    value = json.loads(ssh.run(CLI + " status --json").decode())
    if value.get("BackendState") != "Running":
        raise RuntimeError("Larktun is not Running")
    own = value.get("Self") or {}
    addresses = own.get("TailscaleIPs") or value.get("TailscaleIPs") or []
    ips = [str(ipaddress.ip_address(ip)) for ip in addresses
           if isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address)]
    if not ips:
        raise ValueError("No assigned IPv4 address")
    source = (client_dir / "ssh_config").read_text()
    key = (client_dir / "known_hosts").read_text().split()
    port = re.search(r"(?m)^\s+Port ([0-9]+)$", source)
    if len(key) != 3 or key[1] != "ssh-ed25519" or port is None:
        raise ValueError("Expected this repository's generated SSH configuration and host key")
    target_config, target_hosts = client_dir / "ssh_config.larktun", client_dir / "known_hosts.larktun"
    if target_config.exists() or target_hosts.exists():
        raise ValueError("Remote SSH files already exist; preserve/remove them yourself before regenerating")
    text, count = re.subn(r"(?m)^Host sr7410$", "Host sr7410-vpn", source)
    if count != 1:
        raise ValueError("Unexpected SSH config layout")
    text, count = re.subn(r"(?m)^    HostName .+$", "    HostName " + ips[0], text)
    if count != 1:
        raise ValueError("Unexpected SSH hostname layout")
    path = str(target_hosts.resolve())
    if any(c in path for c in '\n\r"'):
        raise ValueError("Unsupported SSH config path")
    text, count = re.subn(r"(?m)^    UserKnownHostsFile .+$",
                         lambda _: '    UserKnownHostsFile "' + path + '"', text)
    if count != 1:
        raise ValueError("Unexpected known_hosts layout")
    host = ips[0] if port.group(1) == "22" else f"[{ips[0]}]:{port.group(1)}"
    private_write(target_hosts, f"{host} {key[1]} {key[2]}\n".encode())
    private_write(target_config, text.encode())
    print("已为同一台设备生成私网 SSH 配置，沿用可信 LAN 会话已固定的主机公钥。")
    print("连接：" + "ssh -F " + shlex.quote(str(target_config)) + " sr7410-vpn")


def main():
    parser = argparse.ArgumentParser(description="通过已有 SSH 安装、登录和管理 SR7410 Larktun")
    parser.add_argument("action", choices=("install", "login", "status", "expose-router",
                                         "enable-autostart", "disable-autostart",
                                         "start", "stop", "reset-serve", "uninstall",
                                         "configure-remote-ssh"))
    parser.add_argument("--client-dir", type=Path, default=Path("private/ssh"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/larktun"))
    parser.add_argument("--login-server", default="https://hs.larktun.com")
    parser.add_argument("--hostname", default="zte-sr7410")
    parser.add_argument("--web-port", type=int, default=80)
    parser.add_argument("--ssh-port", type=int, default=22)
    args = parser.parse_args()
    try:
        ssh = SSH(args.client_dir.expanduser())
        if args.action == "install":
            install(ssh, args.cache_dir.expanduser())
        elif args.action == "login":
            if not sys.stdin.isatty():
                raise ValueError("请在交互终端运行 login，以隐藏 Auth Key 输入")
            key = getpass.getpass("粘贴为此设备生成的一次性 Auth Key（不回显）：").strip()
            authenticate(ssh, key, args.login_server, args.hostname)
            del key
        elif args.action == "status":
            if not status(ssh):
                parser.exit(1, "节点尚未 Running，请先完成授权。\n")
        elif args.action == "expose-router":
            expose_router(ssh, args.web_port, args.ssh_port)
        elif args.action == "enable-autostart":
            enable_autostart(ssh)
        elif args.action == "configure-remote-ssh":
            configure_remote_ssh(ssh, args.client_dir.expanduser())
        elif args.action == "disable-autostart":
            print(ssh.run(BASE + "/autostart.sh disable").decode().strip())
        elif args.action in ("start", "stop"):
            ssh.run(BASE + "/" + args.action + ".sh", timeout=90)
            print("操作完成；用 status 核对状态。")
        elif args.action == "reset-serve":
            ssh.run(CLI + " serve reset")
            print("已清除此节点的全部 Serve 映射；SSH 局域网访问不变。")
        elif args.action == "uninstall":
            ssh.run(BASE + "/uninstall.sh", timeout=30)
            print("已卸载 Larktun 并删除本机登录状态；请到控制台清理对应节点。SSH 保留。")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"停止：{exc}\n")


if __name__ == "__main__":
    main()
