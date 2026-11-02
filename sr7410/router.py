"""Host-side SSH installer and maintenance commands. Does not reboot the router."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import getpass
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import subprocess
import tarfile
import tempfile
import threading
import xml.etree.ElementTree as ET

from .boot import make_boot_bundle
from .config import private_write, telnet_row
from .runtime import build, write_archive
from .telnet import RemoteError, Shell

BASE = "/Plugin/sr7410-ssh"
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in
                         ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def lan_address(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError("Use a numeric LAN IPv4 address") from exc
    if not any(address in net for net in PRIVATE_NETWORKS):
        raise ValueError("Only RFC1918 LAN addresses are supported by this installer")
    return str(address)


def port_number(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise ValueError("Port must be in 1..65535")
    return number


def credentials(args):
    if args.config:
        values = telnet_row(args.config.read_bytes())
        return values["TS_UName"], values["TS_UPwd"], port_number(values["TS_Port"])
    user = args.user or input("Telnet 用户名（原配置 TS_UName）：").strip()
    return user, getpass.getpass("Telnet 密码（输入不回显）："), args.telnet_port


def public_key(path: Path):
    # Strip comments so hostnames/email addresses never get copied unnecessarily.
    parts = path.read_text().strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519" or not re.fullmatch(r"[A-Za-z0-9+/]+=*", parts[1]):
        raise ValueError("Provide an OpenSSH Ed25519 .pub file, not a private key")
    result = subprocess.run(["ssh-keygen", "-l", "-f", str(path)],
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError("ssh-keygen rejected the public key")
    return (parts[0] + " " + parts[1] + "\n").encode()


def local_address(host):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((host, 23))
        return sock.getsockname()[0]


@contextmanager
def payload_server(payload: bytes, bind_address: str, router_address: str):
    token = "/" + secrets.token_urlsafe(24) + "/payload"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != token or self.client_address[0] != router_address:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer((bind_address, 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://{bind_address}:{server.server_port}{token}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def transfer(shell, host: str, bind_address: str, payload: bytes, target: str):
    with payload_server(payload, bind_address, host) as url:
        shell.run("wget -q -O " + shlex.quote(target) + " " + shlex.quote(url), timeout=60)
    # The stock BusyBox has hexdump but lacks sha256sum/base64. Compare every byte
    # on the host before any downloaded archive is unpacked or executed.
    if shell.read_file(target) != payload:
        raise RemoteError("Downloaded payload failed complete byte read-back verification")


@contextmanager
def remote_stage(shell):
    path = "/var/sr7410-setup-" + secrets.token_hex(8)
    shell.run("umask 077; mkdir " + shlex.quote(path))
    try:
        yield path
    finally:
        try:
            shell.run("rm -rf " + shlex.quote(path))
        except (OSError, RemoteError, EOFError):
            pass


def probe(shell):
    arch = shell.run("uname -m").strip()
    if arch != "aarch64":
        raise ValueError("Only the tested aarch64 platform is supported")
    lan = shell.run("ip -4 addr show br0 | awk '/inet / {print $2; exit}' | cut -d/ -f1").strip()
    lan_address(lan)
    user = shell.run("awk -F: '$3 == 0 {print $1; exit}' /etc/passwd").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", user):
        raise ValueError("Cannot identify the system's native UID 0 account")
    shell.run("test -d /Plugin && test -w /Plugin")
    shell.run("(for c in wget hexdump wc cmp tar ip mount awk cut readlink grep tr; "
              "do command -v \"$c\" >/dev/null || exit 1; done)")
    return lan, user


def client_files(directory: Path, host: str, port: int, user: str, identity: Path, host_key: str):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    directory = directory.resolve()
    host_field = host if port == 22 else f"[{host}]:{port}"
    private_write(directory / "known_hosts", f"{host_field} {host_key}\n".encode())
    def quote(value):
        value = str(value)
        if any(c in value for c in '\n\r"'):
            raise ValueError("Unsupported SSH config path")
        return '"' + value + '"'
    config = (
        f"Host sr7410\n"
        f"    HostName {host}\n    Port {port}\n    User {user}\n"
        f"    IdentityFile {quote(identity.resolve())}\n"
        f"    IdentitiesOnly yes\n    PasswordAuthentication no\n"
        f"    UserKnownHostsFile {quote(directory / 'known_hosts')}\n"
        f"    StrictHostKeyChecking yes\n    ConnectTimeout 8\n"
    )
    private_write(directory / "ssh_config", config.encode())


def install(args, shell, lan, user):
    if not args.public_key:
        raise ValueError("install-ssh requires --public-key")
    if args.client_dir.exists():
        raise ValueError("Client output directory already exists; use --client-dir with a new directory")
    identity = args.identity or Path(str(args.public_key).removesuffix(".pub"))
    if not identity.is_file():
        raise ValueError("Matching private key not found; specify --identity (it stays on the computer)")
    key = public_key(args.public_key)
    shell.run("test ! -e " + BASE + " && test ! -L " + BASE)
    try:
        connection = socket.create_connection((args.host, args.ssh_port), timeout=2)
    except (ConnectionRefusedError, TimeoutError, socket.timeout):
        pass
    else:
        connection.close()
        raise ValueError("The selected SSH port is already in use; select --ssh-port or use the existing server")
    with tempfile.TemporaryDirectory(prefix="sr7410-build-") as temp:
        archive = build(Path(temp) / "runtime.tar.gz", args.cache_dir)
        files, links = {}, {}
        with tarfile.open(archive) as source:
            for member in source:
                if member.isfile():
                    files[member.name] = source.extractfile(member).read()
                elif member.issym():
                    links[member.name] = member.linkname
                else:
                    raise ValueError("Unexpected runtime member")
        files["authorized_keys"] = key
        files["settings.conf"] = f"SSH_LAN_IP='{lan}'\nSSH_PORT='{args.ssh_port}'\n".encode()
        write_archive(archive, files, links)
        with remote_stage(shell) as stage:
            transfer(shell, args.host, args.bind_address or local_address(args.host),
                     archive.read_bytes(), stage + "/payload.tar.gz")
            shell.run("mkdir " + stage + "/payload && tar -xzf " + stage +
                      "/payload.tar.gz -C " + stage + "/payload")
            shell.run("cd " + stage + "/payload && sh install.sh", timeout=150)
    key_output = shell.run(BASE + "/sbin/dropbearkey -y -f " + BASE + "/dropbear_ed25519_host_key")
    matches = re.findall(r"(?m)^ssh-ed25519 ([A-Za-z0-9+/]+=*)(?:\s|$)", key_output)
    if len(matches) != 1:
        raise RemoteError("Cannot read host public key from the trusted Telnet session")
    client_files(args.client_dir, args.host, args.ssh_port, user, identity,
                 "ssh-ed25519 " + matches[0])
    print("已安装 SSH；仅监听 LAN 与回环地址，密码登录已关闭，尚未启用开机自启。")
    print(f"主机公钥通过当前 Telnet 会话固定到 {args.client_dir / 'known_hosts'}。")
    print(f"登录：ssh -F {shlex.quote(str(args.client_dir / 'ssh_config'))} sr7410")
    print("下一步：运行 verify-ssh，分别验证命令执行和交互 PTY。")


def enable_boot(args, shell):
    shell.run("test -f " + BASE + "/VERSION")
    # If already owned, avoid another copy and let the router verify its snapshot.
    owned = shell.run("if test -f " + BASE + "/boot-created.tar.gz; then echo yes; else echo no; fi").strip()
    if owned == "yes":
        print(shell.run(BASE + "/boot.sh enable").strip())
        return
    shell.run("test ! -e /Plugin/apps/dlspeed_rt.tar.gz && test ! -L /Plugin/apps/dlspeed_rt.tar.gz")
    stock = shell.read_file("/etc/dlspeed_rt.tar.gz")
    override = make_boot_bundle(stock)
    with remote_stage(shell) as stage:
        for name, data in (("stock.tar.gz", stock), ("override.tar.gz", override)):
            transfer(shell, args.host, args.bind_address or local_address(args.host),
                     data, stage + "/" + name)
        print(shell.run(BASE + "/boot.sh enable " + stage).strip())
    print("请自行在管理后台重启，再运行 verify-ssh 验证重启后的命令与交互登录。")


def verify(args):
    config = args.client_dir / "ssh_config"
    if not config.is_file():
        raise ValueError("Generated SSH client configuration was not found")
    common = ["ssh", "-F", str(config), "-o", "BatchMode=yes"]
    command = subprocess.run(common + ["sr7410", "id -u"], capture_output=True, text=True, timeout=20)
    if command.returncode or command.stdout.strip() != "0":
        raise RemoteError("SSH command verification failed. Unlock the private key in ssh-agent, "
                          "or try interactive ssh -F private/ssh/ssh_config sr7410")
    # Keep stdin open while the remote command runs. An immediate EOF on a
    # forced PTY can hang up the router shell before it produces any output.
    read_end, write_end = os.pipe()
    try:
        pty = subprocess.run(
            common + ["-tt", "sr7410", "test -t 0 && readlink /proc/$$/fd/0 && id -u"],
            stdin=read_end, capture_output=True, text=True, timeout=20)
    finally:
        os.close(read_end)
        os.close(write_end)
    lines = pty.stdout.replace("\r", "").splitlines()
    if pty.returncode or not any(line.startswith("/var/pts/") for line in lines) or "0" not in lines:
        raise RemoteError("SSH PTY verification failed; inspect /var/sr7410-ssh/ssh.log")
    print("通过：SSH 公钥认证、uid=0 命令执行、/var/pts 交互终端。")


def main():
    parser = argparse.ArgumentParser(description="SR7410 SSH 安装、校验与维护（不自动重启）")
    parser.add_argument("action", choices=("doctor", "install-ssh", "verify-ssh",
                                         "enable-autostart", "disable-autostart", "status", "uninstall-ssh"))
    parser.add_argument("--host", default="192.168.5.1")
    parser.add_argument("--config", type=Path, help="本地私有 XML，自动读取 Telnet 账号与密码")
    parser.add_argument("--user", help="不用 --config 时使用的 Telnet 用户名")
    parser.add_argument("--telnet-port", type=int, default=23)
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--public-key", type=Path, help="电脑上的 Ed25519 公钥 .pub 文件")
    parser.add_argument("--identity", type=Path, help="电脑上的匹配私钥路径；私钥永不上传")
    parser.add_argument("--client-dir", type=Path, default=Path("private/ssh"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/entware"))
    parser.add_argument("--bind-address", help="电脑用于临时 HTTP 传输的 LAN 地址，通常自动检测")
    args = parser.parse_args()
    for name in ("config", "public_key", "identity", "client_dir", "cache_dir"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.expanduser())
    try:
        args.host = lan_address(args.host)
        args.ssh_port, args.telnet_port = port_number(args.ssh_port), port_number(args.telnet_port)
        if args.bind_address:
            args.bind_address = lan_address(args.bind_address)
        if args.action == "verify-ssh":
            verify(args)
            return
        user, password, telnet_port = credentials(args)
        with Shell(args.host, telnet_port, user, password) as shell:
            lan, native_user = probe(shell)
            if args.action == "doctor":
                print(f"只读检查通过：aarch64，br0 地址 {lan}，UID 0 账号已识别。")
                print(shell.run("df -k /Plugin").strip())
            elif args.action == "install-ssh":
                install(args, shell, lan, native_user)
            elif args.action == "enable-autostart":
                enable_boot(args, shell)
            elif args.action == "disable-autostart":
                print(shell.run(BASE + "/boot.sh disable").strip())
            elif args.action == "status":
                print(shell.run(BASE + "/boot.sh status").strip())
                print(shell.run("if test -f /var/sr7410-ssh/dropbear.pid; then "
                                "kill -0 \"$(cat /var/sr7410-ssh/dropbear.pid)\" && echo 'SSH running'; "
                                "else echo 'SSH stopped'; fi").strip())
            elif args.action == "uninstall-ssh":
                print(shell.run(BASE + "/uninstall.sh", timeout=30).strip())
    except (OSError, ValueError, RemoteError, EOFError, ET.ParseError,
            tarfile.TarError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"停止：{exc}\n")


if __name__ == "__main__":
    main()
