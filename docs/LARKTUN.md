# SR7410 原厂系统安装 Larktun

按本仓库完成 Telnet、SSH 和 SSH 自启后，可继续安装 Larktun，让同一工作空间中的电脑或手机远程访问路由器后台和原生 SSH。

这个指南对应 SR7410 / V1.0.0.7B4.8000，采用之前已经实机运行的用户态方案。整理版安装器的组件验证与完整重装边界见 [验证记录](VALIDATION.md)。

## 为什么使用专用安装器

普通 Linux 的[官方安装脚本](https://docs.larktun.com/docs/getting-started/linux-install-larktun)会向系统目录写入程序并注册系统服务。此设备的原厂根文件系统只读，可写 /Plugin 分区空间有限，因此这里使用：

| 项目 | 本方案 |
| --- | --- |
| 程序 | 固定版本 1.86.2-dev20260503，Linux ARM64 静态二进制 |
| 下载 | 官方国内与国际 HTTPS 通道，逐文件检查预置 SHA256 |
| 闪存 | /Plugin/sr7410-larktun 保存 gzip 程序、CA 证书与私有状态 |
| RAM | /var/sr7410-larktun 保存解压程序、socket 与日志 |
| 网络模式 | userspace-networking，不依赖 /dev/net/tun |
| DNS / 接受路由 | 首次授权显式关闭 |
| 远程服务 | TCP Serve 转发原厂 HTTP 与原生 Dropbear SSH |
| 认证 | 用户现场输入一次性 Auth Key，状态由每台设备独立生成 |
| 开机启动 | 复用本仓库拥有的 SSH 组件覆盖入口 |

仓库不提供任何已登录的状态包。不要复制别人的 larktund.state，也不要把自己的状态文件、私钥、Auth Key 或完整 status JSON 公开。

## 1. 安装前检查

在电脑仓库目录激活虚拟环境，更新依赖：

~~~bash
source .venv/bin/activate
python -m pip install -r requirements.txt
~~~

Windows 使用 .venv\Scripts\Activate.ps1 或直接运行虚拟环境中的 python.exe。

确认电脑能通过仓库生成的配置登录：

~~~bash
ssh -F private/ssh/ssh_config sr7410
~~~

在路由器 Shell 中检查空间：

~~~sh
df -k /Plugin
grep MemAvailable /proc/meminfo
~~~

压缩程序大约占 29 MiB，安装器另保留 4 MiB 闪存余量，安装期间要求至少 128 MiB 可用 RAM。RAM 中两个解压程序合计约 56 MiB，守护进程还会额外占用运行内存。GOMEMLIMIT 是 Go 的软目标，不是硬内存限额。

电脑上如使用带口令的私钥，先运行 ssh-add 解锁；安装器使用 BatchMode，不在后台重复索取密码。后续所有命令默认使用 private/ssh；自定义目录时统一加 --client-dir。

安装、停用与卸载使用 LAN 连接，不要依赖正在操作的 Larktun 链路。

## 2. 下载、校验并安装

~~~bash
python scripts/larktun_tool.py install
~~~

程序会读取 larktun.lock.json，下载官方 ARM64 文件，核对固定大小、SHA256、ELF 架构与静态链接特征，构建不含身份信息的安装包。

通过已有的严格主机校验 SSH 连接上传到 RAM，再安装压缩副本到 /Plugin。SSH 协议保护传输完整性，另检查落地长度；不在可信主机校验失败时自动接受新密钥。

安装只启动守护进程，不自动登录账号、不发布端口、不更改自启开关。已存在 /Plugin/larktun 或 /Plugin/sr7410-larktun 时停止，不自动迁移或覆盖旧身份。

想先在电脑构建、暂不安装：

~~~bash
python scripts/build_larktun.py
~~~

缓存位于 .cache/larktun，构建包位于 build，均被 Git 忽略。软件源有新版本时，固定 SHA256 不匹配会停止；请勿关闭校验。升级需先独立验证新版本、内存与 Serve 行为，再更新锁文件。

## 3. 为设备授权入网

根据[官方 Linux 接入说明](https://docs.larktun.com/docs/getting-started/linux-install-larktun)，在 Larktun 控制台相应租户 / 工作空间的 Nodes（设备）页生成一枚专用于此设备的一次性 Auth Key。

在自己的交互终端运行：

~~~bash
python scripts/larktun_tool.py login
~~~

按提示粘贴 Key，输入不回显。它只通过加密 SSH stdin 发送到路由器 RAM 内 0600 临时文件，客户端使用 file: 参数读取，退出或收到常见中断信号时清理。电脑不保存 Key，不将其放到 SSH 命令参数、仓库或 Shell 历史中；路由器断电重启也会清空这份 RAM 临时文件。

默认参数：

~~~text
login-server: https://hs.larktun.com
hostname: zte-sr7410
accept-dns: false
accept-routes: false
tun: userspace-networking
~~~

如使用自己的控制服务器或设备名：

~~~bash
python scripts/larktun_tool.py login --login-server https://你的控制服务器 --hostname zte-sr7410
~~~

这里的服务器地址是占位示意，需要换成真实 HTTPS 地址；不要把真实 Auth Key 写入这条命令。

授权后：

~~~bash
python scripts/larktun_tool.py status
~~~

确认 Running，记录工具显示的本机私网 IP。工具只输出本节点的状态与 IP，不输出其他节点、账号名或登录链接。自己的私网 IP 不固定，不能照抄别人的地址。

登录状态保存于 /Plugin/sr7410-larktun/larktund.state，目录仅 root 可访问；重启时使用该状态恢复连接，不需要再次保存或输入 Auth Key。

## 4. 发布路由器本身的网页与 SSH

用户态网络运行在 Larktun 进程内，不会自动将原厂管理网页和 Dropbear 接到虚拟网卡。即使 ping 通，TCP 服务也可能无法访问。

显式配置：

~~~bash
python scripts/larktun_tool.py expose-router
~~~

它创建后台持久 TCP Serve：

~~~text
本节点私网 TCP 80 → 127.0.0.1:80 → 原厂管理网页
本节点私网 TCP 22 → 127.0.0.1:22 → 原生 Dropbear SSH
~~~

如果本仓库 SSH 安装时用了端口 2222：

~~~bash
python scripts/larktun_tool.py expose-router --ssh-port 2222
~~~

此时发布的是私网 TCP 2222 到回环 2222，后续 SSH 客户端也要使用对应端口。--web-port 同时控制网页发布端口与回环目标端口，默认 80。

这些服务仅向当前 Larktun 网络提供，并继续受该网络 ACL 约束。它们不是公网 Funnel，也不需要在原厂后台开放 WAN 80/22 或新增公网端口映射。不要为了测试去关闭管理密码或 SSH 密钥认证。

### 网页验证

手机连接同一账号 / 工作空间，临时关闭 Wi-Fi，使用移动网络。在浏览器中打开 **http://本节点私网IP/**。

使用 HTTP，不要自动替换成 HTTPS；本步骤没有配置 HTTPS 监听。路由器仍要求自己的管理密码。

### SSH 验证与主机公钥

电脑仍处于 LAN 连接时，生成一份独立私网配置：

~~~bash
python scripts/larktun_tool.py configure-remote-ssh
~~~

工具读取本节点实际 IP，并沿用可信 LAN 会话已经固定的 Ed25519 主机公钥，生成：

~~~text
private/ssh/ssh_config.larktun
private/ssh/known_hosts.larktun
~~~

原有 LAN 配置不修改。新文件若已存在，会停止；节点 IP 变化后应先保留旧文件，再重新生成。不要用 StrictHostKeyChecking=no 绕过 IP 更换后的提示。

当前电脑连接 Larktun 后：

~~~bash
ssh -F private/ssh/ssh_config.larktun sr7410-vpn
~~~

先在 LAN 验证，再切换到外部网络验证。原生 SSH 仍要求已授权私钥；Larktun 入网权限不会自动替代 SSH 认证。

手机或另一台电脑如使用不同密钥，需另行把该设备公钥加入路由器的授权列表。系统 UID 0 用户名见自己电脑的 private/ssh/ssh_config，不要照抄其他设备的用户名，也不要在 Issue 中贴出个人私钥。

以上 TCP Serve 步骤只处理路由器本身。访问 NAS、摄像头等其他 LAN 设备，可继续按第 6 节显式配置子网路由；这里没有启用出口节点。

## 5. 开机自启

先完成主 README 第 9 节的 SSH 自启并验收：

~~~bash
python scripts/router.py enable-autostart --host 192.168.5.1 --config private/telnet/config-original.xml
~~~

然后：

~~~bash
python scripts/larktun_tool.py enable-autostart
~~~

工具确认当前组件包与本仓库 SSH 保存副本一致，从已校验的原厂组件生成共享钩子，保持其他成员不变。更新前再次核对当前包与原厂组件，拒绝其他软件或并发更新后的包；不会自动重启。

共享开关关系：

| SSH 自启 | Larktun 自启 | 重启后 |
| --- | --- | --- |
| 开 | 关 | 只启动 SSH |
| 开 | 开 | 启动 SSH 与 Larktun |
| 关 | 任意 | 此共享入口不启动服务 |

所以单独停用 Larktun 自启不影响 SSH；关闭 SSH 自启则同时关闭这个入口下的 Larktun 开机启动。较早版本仓库生成的 SSH 覆盖包，也通过这个命令更新共享钩子，无需替换已生成的设备主机密钥。

自行在后台重启后，依次检查：

~~~bash
python scripts/router.py verify-ssh
python scripts/larktun_tool.py status
~~~

再用手机移动网络验证网页，并实际验证私网 SSH。状态 Running 或 ping 通都不等于端口访问验收完成。

## 6. 可选：宣告家庭 LAN 子网

**用户态不等于只能访问节点本身。** [上游 netstack 说明](https://tailscale.com/docs/reference/kernel-vs-userspace-routers)明确支持用户态子网路由：TCP、UDP 由进程重新发起到目标设备的连接，ping 通过代理方式处理；其他 ICMP 和 SCTP 等协议不等同于内核三层转发。重度吞吐或完整 IP 协议需求需要另评估内核模式。

本设备已使用原有用户态程序成功写入子网宣告，并核对其余偏好和 Serve 设置不变。宣告成功不代表已经从外部访问成功，控制台批准、ACL、客户端接收与目标设备服务仍需分别验收。

以下统一以 **192.168.1.0/24 作为示例**，请先确认自己的 br0 实际 LAN 网段，再替换：

~~~bash
python scripts/larktun_tool.py advertise-subnet --subnet 192.168.1.0/24
python scripts/larktun_tool.py subnet-status
~~~

脚本只允许当前 br0 直接连接范围内的规范 RFC1918 IPv4 子网；拒绝默认路由、公网地址和不匹配的网段。它合并已有宣告，并使用 set 只修改 AdvertiseRoutes，不重新登录、不重置 DNS、不打开接受其他路由的开关，也不改动 Serve。

然后：

1. 登录与该节点相同的 Larktun 账号 / 工作空间，在“路由”页面批准目标网段。
2. 确认 ACL 允许需要的用户访问该网段及目标端口；宣告命令不会自动扩大 ACL。
3. 外面的客户端需接受子网路由。Linux 客户端可按其 CLI 使用 set --accept-routes=true；手机与桌面客户端请核对自己的子网路由设置。
4. 手机关闭 Wi-Fi 使用移动网络，访问 LAN 设备的原 IP 和实际服务端口，避免本机就在 LAN 中造成误判。

例如，目标 NAS 实际地址如果是示例网段中的 192.168.1.10，应访问它原有的网页或 SSH 服务，而不是给每台设备另外安装 Larktun。目标设备的认证和本机防火墙仍生效。广播、mDNS 发现、任意 IP 协议或保留原客户端源 IP的场景不应按普通三层网桥假设。

网关自身的 accept-routes=false 可以保留；它控制是否接收**其他节点**路由，与宣告自己的 LAN 是两个设置。不要为了宣告 LAN 开启出口节点或清空其他偏好。

subnet-status 优先从 debug netmap 的 SelfNode 读取 AllowedIPs / PrimaryRoutes，失败时回退 status，并标明 route_data_source。实测 status 的本机字段未显示已批准子网，但网络映射能够正确显示；不能单凭回退字段为空就判定未批准。工具只输出本机路由，不公开网络映射中的节点身份或其他设备信息。

本次原始设备流程已经完成宣告、控制台批准、网络映射中的允许与主用路由确认，并由手机在移动网络下通过路由器原始 LAN 地址打开后台。该结果验证子网路径；NAS 等其他设备的端口和自身访问控制仍需分别确认。

撤销单个宣告：

~~~bash
python scripts/larktun_tool.py withdraw-subnet --subnet 192.168.1.0/24
~~~

它只移除指定网段，不清空其他已宣告路由，也不删除原来的网页 / SSH Serve。偏好由守护进程保存至设备状态，重启后仍应按实际客户端连接进行验收。

## 7. 运维与卸载

~~~bash
python scripts/larktun_tool.py disable-autostart
python scripts/larktun_tool.py stop
python scripts/larktun_tool.py start
~~~

stop 保留身份，start 使用原状态恢复。disable-autostart 只清除 Larktun 自启标记，保留 SSH 钩子。

清除此节点**全部** Serve 映射：

~~~bash
python scripts/larktun_tool.py reset-serve
~~~

这不会关闭局域网 SSH。若此节点已另行配置其他 Serve，运行前要知道它们也会被清除。

完全卸载：

~~~bash
python scripts/larktun_tool.py uninstall
~~~

卸载停止本工具守护进程、删除程序与节点身份，保留 SSH 及其共享自启入口；留下的钩子因标记不存在，不会启动 Larktun。控制台中的旧节点可另行删除。

完整回退顺序是 **Larktun → SSH → 恢复自己的原始配置**。不要直接删除系统 libc、原厂组件、/dev 或仍被其他软件使用的路径。

路由器内可用于排查的文件与命令：

~~~sh
/Plugin/sr7410-larktun/cli.sh status
/Plugin/sr7410-larktun/cli.sh ip -4
/Plugin/sr7410-larktun/cli.sh serve status
/Plugin/sr7410-larktun/start.sh
~~~

日志在 /var/sr7410-larktun/daemon.log，自启日志在 /var/sr7410-larktun-boot.log。日志可能含账号或节点信息，发布前人工脱敏。不要上传 larktund.state 或完整状态 JSON。

## 常见问题

- **只 ping 通，网页打不开**：先确认 Running，再创建 TCP Serve，检查工作空间与 ACL，使用正确的 HTTP 地址。
- **SSH 私网 IP 改了后主机校验失败**：在可信 LAN 中确认设备，重新生成独立私网配置；不关闭主机校验。
- **安装器只提示 SSH 操作失败**：先手动验证 LAN SSH 和 ssh-agent，再检查空间、内存、是否已有安装。授权相关的原始输出默认不显示，以免泄漏 Key 或登录链接。
- **提示已有旧安装**：先保留旧状态并了解旧目录与自启归属。本工具不自动覆盖旧 /Plugin/larktun；原始会话中的旧方案与新公开工程使用不同目录。
- **停机后仍显示在线一会儿**：控制面状态更新可能有延迟，需结合进程、端口和其他客户端验证。
- **证书错误**：确认设备时间、控制服务器和 CA 信任链；本包使用独立 Mozilla CA 证书集，不关闭证书验证。自签 CA 需要单独审查配置。
- **为什么没有自动下载最新版本**：固定哈希保证复现这次实测程序；升级必须重新验证路由器兼容性。
