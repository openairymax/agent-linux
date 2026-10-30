<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# airy-flash：AirymaxOS 可复现烧录工具

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

AirymaxOS 的发布物是一个 isohybrid ISO（BIOS 与 UEFI 双固件可引导），
装机只需要把镜像顺序写到整块设备。真正容易出错的并非 `dd` 命令本身，而是其
外围长期缺失的四项保障：**写错盘**（把系统盘或已挂载的盘当 U 盘）、**写坏包**
（拿到残缺或篡改的 ISO 却没发现）、**写完不知道成没成**（没有回读比对）、
**事后无法复盘**（只有一段滚动的终端输出，没有结构化记录）。

本工具把烧录收敛成一条可重放的流水线：**预检 → 校验镜像 → 写入 →
刷盘同步 → 回读校验 → 落报告**，每一步都有固定顺序的机器可读输出，
末尾一行 `AIRY-FLASH-PASS|FAIL` 供 CI 与日志检索直接断言。

工具**不自行实现分区与引导**：ISO 由 `mkksiso` 保留 isohybrid 分区表与
EFI 镜像，顺序写入即可双固件引导；另造一套分区/引导引擎只会引入第二处
事实源。工具的职责边界是**判定这台设备能不能写、这个包是不是发布物、
写完是否逐字节一致、以及把过程留成证据**。

所有判定读取真实 sysfs 与 `/proc/mounts`，所有写入都是真实块设备 I/O，
不做任何模拟。

## 为什么不直接用 dd / Rufus / Etcher

结论：**写入能力用系统自带的块设备路径，判定与留证能力自研**。前者没有第二
种正确实现，后者在既有工具里都不存在。

| 做法 | 缺什么 | 后果 |
|------|--------|------|
| `dd if=… of=/dev/sdX` | 无预检、无摘要、无回读、无记录 | 把 `/dev/sda` 写成 `/dev/sdb` 是一次不可逆操作；写完只能凭「命令没报错」判断成功 |
| Rufus / Etcher / balenaEtcher | 图形界面、Windows/macOS 优先、无机器可读输出 | 无法在流水线里断言；ISO 模式与 DD 模式选错会静默产出不可引导介质 |
| 厂商烧录器 | 绑定自家镜像格式与设备型号 | 与「一份公开发布物、任意块设备」的交付模型冲突 |

四项保障必须由**真正执行写入的那段代码**给出：`dd` 之后另行 `sha256sum -c`
只是把校验放在人的记忆里，而人正是最不可靠的一环。因此本工具把预检、摘要、
写入、刷盘、回读与报告放在同一条流水线，并用 `AIRY-FLASH-PASS` 一行把结论交给
CI——这是 dd 与图形烧录器都无法给出的东西。

边界同样明确：本工具**不做**分区、格式化、引导修复或镜像转换，也不提供图形
界面。它假定发布物已经是可双固件引导的完整 ISO，只负责「这块盘能不能写、这个
包是不是发布物、写完是否逐字节一致」。超出这个边界的诉求应当回到构建侧解决，
而不是在烧录端补一层转换。

权威依据：

| 内容 | 出处 |
|------|------|
| 发布物组成（ISO + `.sha256` + `iso-manifest.txt`） | `.github/workflows/image-build.yml` |
| 发行 ISO 的取件通道与摘要 | `desktop/iso/iso-oci.txt`（由 `ci/ci.sh iso-publish` 写入：整体 sha256、块大小、块数与逐块 digest）。该文件不是唯一入口：清单注解与逐层 digest 已含同样内容，`fetch --ref` 只凭 tag 即可取件 |
| 取件通道的不可变性断言 | `ci/gates/iso-publish.sh`（每块 layer digest 必须等于该块 sha256，拼接后必须等于整体 sha256） |
| BIOS + UEFI 双固件冷启动验收 | `ci/ci.sh accept --firmware both` |
| 装机后桌面就绪标记 `AIRYMAX-DESKTOP-READY` | 同上，产品 ISO 断言 |
| 主机名 `airymaxos`、内核 `-alk` 后缀 | `docs/docs-linux/` 品牌与内核归属规范 |
| Python 类型注解、数据类、错误码 | `docs/docs-linux/50-engineering-standards/10-coding-style/scripting_coding_style.md` |

## 烧录方案

发布物只从流水线取，不从本地构建目录取：本地构建的 ISO 与发行 `.sha256`
不是同一个文件，混用会让「可复现」失效。

发行 ISO 约 3.4 GiB，超过 GitHub Release 单资产 2 GiB 与 AtomGit 网关 3 GiB
上限，因此经 OCI 制品通道交付到独立包 `ghcr.io/openairymax/airymaxos-iso`。
它与 `release.yml` 的基础镜像分属两个包、互不覆盖 tag。

ISO 按 192 MiB 切成定长 layer 推送：镜像站对 registry blob 的 Range 支持不
一致，有的站点忽略 Range，且单连接传输至 314 MB 即断流，整文件单层取件无法续传。
分块后每块 digest 由 registry 按内容计算，逐块可校验、可单块重取；manifest
annotation 记录整体 sha256、块大小与块数，取件侧据此确认块边界未被改动。
Actions 的 `airymaxos-image` artifact 只保留 7 天，仅用于调试，不作为发行取件来源。

| 步骤 | 动作 | 失败即停 |
|------|------|----------|
| 1 | `airy-flash fetch --ref ghcr.io/openairymax/airymaxos-iso:v0.0.3 --out releases/v0.0.3`：按 tag 读清单 → 拉取分块 → 逐块核对 → 拼接 → 整体核对；已有账本文件时改传 `--oci FILE`，两者结论一致 | 是 |
| 2 | 插入 U 盘，用 `lsblk` 找到**整盘**节点（如 `/dev/sdb`，不是 `/dev/sdb1`） | 是 |
| 3 | `plan` 只预检：核对系统盘、挂载、只读、容量、摘要来源 | 是 |
| 4 | `write --yes`：校验镜像 → 写入 → `fsync` → 回读比对 | 是 |
| 5 | 安全弹出，目标机开机按 F12/F11/Esc 选 U 盘 | — |
| 6 | 装机后核对主机名、内核后缀、登录策略 | 是 |

第 1 步的四重核对各有分工：`iso-oci.txt` 的逐块 `sha256=` 证明每块字节就
是发布时那一块，`oras` 自己的 layer digest 证明 registry 端的 blob 与清单一
致，拼接后再算的整体 `sha256` 证明块序与块边界未被改动，清单里的
`sha256=` 证明拿到的就是这个 tag 当初发布的整体字节。任一不符即中止，不进
入烧录。`iso-oci.txt` 里的 `pull=` 与 `join=` 两行保留了原生 `oras` 与 `cat`
的等价写法，供没有本工具的环境照抄执行。

`plan` 与 `write` 共用同一套预检，所以「预检通过」是「可以写」的充分条件；
`plan` 已经算过完整 sha256，`write` 会再算一次，避免两次操作之间镜像被替换。

### 适用平台与介质

工具只依赖 POSIX 用户态与 Linux 的 sysfs / `/proc/mounts`，内不设按机器型号、
目录布局或介质品牌分支的判定逻辑：任何在 Linux 上以块设备出现的介质（U 盘、SD 卡、
USB 硬盘盒、NVMe 转接盘）都走同一套预检与写入路径，节点名不做假设，
整盘与系统盘一律按 sysfs 语义反查。

| 平台 | 是否需要额外步骤 | 写入方式 |
|------|------------------|----------|
| Linux | 否 | 直接用本工具：`plan` → `write` → `verify` |
| WSL2 | 是 | 先做介质直通（见下一节），其余流程与 Linux 相同 |
| macOS | 是 | 本工具无 macOS 构建，改用系统自带件按同一顺序：`shasum -a 256 -c *.iso.sha256` 校验 → `diskutil unmountDisk /dev/diskN` → `dd if=*.iso of=/dev/rdiskN bs=4m` → `dd if=/dev/rdiskN bs=4m \| cmp - *.iso` 回读比对 |
| Windows | 是 | 同上，用平台自带工具：`CertUtil -hashfile *.iso SHA256` 校验，再以 DD/原始镜像模式整盘写入（如 Rufus「DD 镜像」模式），写完回读比对 |

`N` 是整盘号，取自 `diskutil list` 的 `/dev/diskN`，不是 `/dev/diskN sM` 分区号：
误用分区节点只会造成半截写入。非 Linux 平台没有 sysfs 与 `/proc/mounts`，
系统盘反查不成立，写前必须由操作者自行确认整盘身份；写错盘的后果与 Linux 侧相同。

本工具不宣称覆盖这些平台，也不为「表面上的通用性」在非 Linux 路径添加桩分支：
发行取件与写入顺序是跨平台不变的部分，能在目标平台用原生工具等价复现即可。

### 设备判定

- 目标必须是**整块设备**。分区（如 `/dev/sdb1`）会被 `probe_device` 当作
  普通文件路径拒绝，避免只覆盖前 512 MiB 之类的半截写入。
- 系统盘按「承载 `/`、`/boot`、`/boot/efi`、`/usr`、`/var` 的磁盘」反查：
  先 `/proc/mounts` 取挂载源，`/dev/mapper/*` 落到 `/dev/dm-N`，再用 sysfs
  的 `partition` 标记回溯到父盘（`/sys/class/block/sdb1` → `sdb`）。
- 有任何挂载点即拒绝，必须先 `umount`；只读属性（写保护开关）即拒绝。
- 普通文件只有在显式 `--allow-file` 时才允许，用于虚机镜像与单元测试。

### WSL2 上的设备接入

WSL2 的 `/dev/sd*` 只有 WSL 自己的虚拟磁盘，插在主机上的 U 盘不会自动
出现在 Linux 侧；不先做直通，`lsblk` 中不会出现目标设备。

1. 管理员 PowerShell 列出设备并记下 BUSID：`usbipd list`
2. 绑定一次即可长期有效：`usbipd bind --busid <BUSID>`
3. 每次插入 U 盘后接进 WSL：`usbipd attach --wsl --busid <BUSID>`
4. 回到 WSL 用 `lsblk` 确认新出现的整盘节点，再走上面的 `plan` / `write`
5. 写完先 `usbipd detach --busid <BUSID>` 再拔盘，避免页缓存未落盘

直通接入后呈现为块设备，系统盘反查照常生效（WSL 的根盘是它自己的虚拟盘，
不会和新接入的 U 盘混淆）。`write` 需要 root，WSL 里的 `sudo` 必须由操作者本人在交互
终端输入口令，工具只做预检与写入，不代替提权。

### 回读强度

| `--verify` | 读取范围 | 适用 |
|-----------|----------|------|
| `sample`（默认） | 头、中、尾三个 16 MiB 窗口 | 日常烧录；覆盖分区表、EFI 镜像与 ISO 尾部 |
| `full` | 整个镜像长度 | 首次验证新介质、怀疑 U 盘坏块 |
| `none` | 不回读 | 仅调试；报告会标注该阶段 `[SKIP]` |

抽检不是「抽查几个扇区」：三窗各 16 MiB，任何窗口不一致都会报出**首个
不一致的绝对偏移**，便于判断是介质问题还是镜像问题。

## 子命令

| 子命令 | 作用 | 是否写设备 |
|--------|------|------------|
| `fetch` | 按 tag 或 `iso-oci.txt` 取回分块、逐块核对、拼接并核对整体摘要（`--ref` 免账本文件，`--oci` 供离线复盘） | 否 |
| `plan` | 预检 + 校验镜像摘要（默认子命令） | 否 |
| `write` | 预检 → 校验 → 写入 → 刷盘 → 回读 | 是 |
| `verify` | 只回读已写入的设备并与镜像比对 | 否 |
| `check` | 环境自检（python、`/proc/mounts`、sysfs、root 权限） | 否 |

## 使用方法

发行页交付单文件 `airy-flash-<ver>.pyz`（Python 标准库自足，无解压、无依赖），
示例统一用它；仓库内调试可等价改用 `python3 tools/airy-flash/airy_flash.py`。

```bash
# 环境自检
python3 airy-flash-0.0.3.pyz check

# 取回发行 ISO：清单是唯一账本，块错位或字节不符即行中止。
# 发布面的清单本身就是账本（注解 + 逐层 digest），所以只给 tag 即可取件；
# 已拿到 iso-oci.txt 的离线场景改用 --oci FILE，两条路径结论一致。
python3 airy-flash-0.0.3.pyz fetch \
    --ref ghcr.io/openairymax/airymaxos-iso:v0.0.3 --out releases/v0.0.3

# 只预检（不碰目标设备）
sudo python3 airy-flash-0.0.3.pyz plan \
    --image releases/v0.0.3/airymaxos-0.0.3-x86_64.iso --device /dev/sdb

# 正式烧录（块设备必须 --yes）
sudo python3 airy-flash-0.0.3.pyz write \
    --image releases/v0.0.3/airymaxos-0.0.3-x86_64.iso --device /dev/sdb --yes

# 首次使用新介质，全量回读并留证
sudo python3 airy-flash-0.0.3.pyz write \
    --image airymaxos-0.0.3-x86_64.iso --device /dev/sdb \
    --verify full --report flash-report.json --yes

# 事后复核一块已经写好的盘
sudo python3 airy-flash-0.0.3.pyz verify \
    --image airymaxos-0.0.3-x86_64.iso --device /dev/sdb

# CI / 脚本取机器可读结果
sudo python3 airy-flash-0.0.3.pyz write ... --json
```

摘要来源按 `--sha256` → 镜像同目录 `<iso>.sha256` → 同目录 `SHA256SUMS` →
`--sums` 指定文件的顺序解析；同一 `SHA256SUMS` 里出现多条同镜像记录时报
`EXIT_CHECK`，不做任意择一的猜测。

### 预检项

| 检查项 | 判定 |
|--------|------|
| 目标是块设备 | 普通文件需 `--allow-file`，否则标 `[SKIP]` 并在写入阶段拒绝 |
| 非系统盘 | 与关键挂载点反查出的磁盘集合不相交 |
| 未挂载 | 设备自身或其分区没有任何挂载点 |
| 非只读 | sysfs `ro` 属性为 0 |
| 容量充足 | 设备容量 ≥ 镜像字节数 |
| 校验和来源 | 有摘要来源；否则需显式 `--allow-unverified`（标 `[SKIP]`） |

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 成功（`plan` 表示可以写，`write` 表示已写入且回读一致） |
| `1` | 预检未通过 / 镜像摘要不符 / 回读不一致 |
| `2` | 用法或环境错误（缺参数、目标不存在、写块设备未用 root、缺 `--yes`） |
| `3` | 写入或刷盘 I/O 失败 |

### 输出结构

人读输出固定七段（标题 → 身份 → 预检 → 阶段 → 结果 → 下一步 → 标记），
顺序不随结果变化：失败时只少「下一步」，其余段落位置不变，便于人工浏览与
日志切片。

```text
AirymaxOS 烧录工具 0.0.3  (write)
====================================================================
映像   /path/airymaxos-0.0.3-x86_64.iso
       3.40 GiB   sha256 1a2b3c4d…5e6f7a8b
目标   /dev/sdb
       USB 3.0 Flash Disk, usb, 28.7 GiB, 可移动
分区   已有 2 个 sdb1, sdb2
       合计 3.21 GiB，写入后不可恢复
模式   写入 + 回读

预检
  [ OK ] 目标是块设备     /dev/sdb
  [ OK ] 非系统盘         系统盘: sdd
  [ OK ] 未挂载           无挂载点
  [ OK ] 非只读           ro=0
  [ OK ] 容量充足         28.7 GiB ≥ 3.40 GiB
  [ OK ] 校验和来源       airymaxos-0.0.3-x86_64.iso.sha256

阶段
  [ .. ] 1/4 校验镜像      25% 870.24 MiB 1.12 GiB/s ETA 3s
  [ OK ] 1/4 校验镜像     sha256 一致 1a2b3c4d…5e6f7a8b  3s
  [ OK ] 2/4 写入设备     3.40 GiB  92.10 MiB/s  38s
  [ OK ] 3/4 刷盘同步     fsync 完成  1s
  [ OK ] 4/4 回读校验     比对 3 段 / 3.40 GiB  12s

结果
判定   成功
用时   1m12s
报告   flash-report.json

下一步
  1. 安全弹出 U 盘，插入目标机，开机按 F12/F11/Esc 选择该 U 盘
  2. UEFI 固件请选带 UEFI 前缀的条目；BIOS 模式直接选 USB 设备
  3. 装机后核对：hostname=AIRYMAX_PC，uname -r 以 "-alk" 结尾
  4. 核对登录策略：Airymax 本地/SSH 免密，root 口令 00000000

AIRY-FLASH-PASS command=write host=airymax-build image=/path/airymaxos-0.0.3-x86_64.iso sha256=1a2b…7a8b device=/dev/sdb
```

- 版式只有两条规则：**字段块（身份与结果）顶格对齐，列表（预检/阶段/下一步）缩进 2**；
  预检项与阶段共用同一个 `[ OK ]/[SKIP]/[FAIL]` 标签列与 16 列名称列，
  标签按显示宽度补齐（CJK 记 2 列），续行缩进固定，中英混排不错位。
- 非 TTY 时进度按 25% 桶输出整行（`0/25/50/75`），不写 `\r`，日志可直接 `grep`；
  100% 由紧随其后的阶段结束行承担，不另打一行同义进度。
- 每个阶段都有心跳：超过 5 秒没有新进度就补一行 `已等待 Ns`，fsync 与
  回读这类无字节计数的阶段也不会长时间无输出。
- 失败时 `结果` 段追加 `原因` 与 `建议`，报告写失败降级为 `提示` 而不掩盖原始结论；
  标记行的 `sha256=` 用**期望值**兜底，保证凭该行仍可将日志对应到发布物。
- 标记行只写已有事实（`command=`/`host=` 恒有，`image=`/`sha256=`/`device=`
  按已探测到的内容出现），早期失败不会留下空字段。
- `--json` 时 stdout 只有 JSON 对象，人读内容全部抑制；`--report` 支持
  `.json` 与 `key=value` 两种后缀，字段与 `--json` 同源于一个 `Report`，
  失败同样落盘（含 `error=`/`hint=`）。
- `check` 与烧录共用同一套报告、版式与标记，只是没有映像与设备字段。
- `plan` 成功也会给出下一步（加 `--yes` 执行写入），避免把预检通过误当成已写入。
- `fetch` 沿用同一套版式：身份段换成 清单 / 来源 / 目标，预检为三条
  （`oras` 在位、磁盘余量、分块尺寸），阶段为 拉取分块 / 校验分块 /
  拼接镜像 / 校验镜像。目录里已有字节数一致的镜像时只跑「校验镜像」一段，
  `--force` 才重取；拼接边拼边删块，峰值占用是镜像 + 一个块，不是两份镜像。

## 装机后核对

烧录成功只证明「盘上字节正确」，不证明「系统可以正常启动」。装完必须逐项核对：

| 项 | 命令 | 期望 |
|----|------|------|
| 主机名 | `hostnamectl --static` | `airymaxos` |
| 内核归属 | `uname -r` | 以 `-alk` 结尾（Airymax Linux Kernel） |
| 品牌 | `cat /etc/os-release` | `NAME="AirymaxOS"`、`VERSION_ID="0.0.3"` |
| 用户免密 | `su - airymax -c true`、SSH 登录 | 均不提示口令 |
| root 口令 | `ssh root@<host>` | 口令 `00000000` |
| 桌面 | 图形会话 | 进入 UKUI 桌面，无回退到文本控制台 |
| 桌面就绪 | `journalctl -b \| grep AIRYMAX-DESKTOP-READY` | 有该标记 |

任一项不符即回到报告中的 `image.sha256` 与设备型号重新排查，不要先怀疑系统。

## 失败排查

| 现象 | 原因 | 处理 |
|------|------|------|
| `oras pull 失败` | 仓库或网络不可达 | 换镜像站后重试；离线环境用 `--oras` 指定本机可执行文件 |
| `缺少 N 块（首块 …）` | 发布物与清单不是同一份 | 重新取 `iso-oci.txt`，不要手改块名 |
| `第 N 块 sha256 不符` | 该块下载残缺或被替换 | 删掉该块后重跑 `fetch` |
| `声明 N 块却列出 M 块` / `分块合计 …` | 清单自相矛盾 | 重新取清单，勿手工编辑 |
| `目标既不是块设备也不是普通文件` | 传了分区或不存在路径 | `lsblk` 取整盘节点 |
| `非系统盘 [FAIL]` | 目标是当前系统所在盘 | 立即停止，换盘 |
| `未挂载 [FAIL]` | 桌面自动挂载了 U 盘 | `umount /dev/sdb*` 后重跑 |
| `校验和来源 [FAIL]` | 取件目录里缺 `.iso.sha256` | 重跑 `oras pull` 取全；勿用 `--allow-unverified` |
| `摘要不符` | 下载不完整或镜像被替换 | 重新下载；两份摘要不同说明不是同一发布物 |
| `写入失败于偏移 ...` | 介质掉线或坏块 | 换口/换线；仍失败则换盘 |
| `回读校验 [FAIL] 偏移 N` | 介质写入不可靠 | `--verify full` 复现；确认为坏盘 |
| 写块设备报 `EXIT_USAGE` | 未用 root 或缺 `--yes` | `sudo` 并显式确认目标 |

## CI 接入

- **`os-chk-verify.yml`**：`python-verify` 门禁已覆盖本目录，安装
  `requirements-dev.txt` 并运行 pytest。
- **烧录验收**：`write` 的 `--json` 与 `AIRY-FLASH-PASS` 标记用于在发布
  流水线中断言「发布物可写到介质并逐字节回读一致」；真机引导与桌面就绪仍由
  `ci/ci.sh accept --firmware both` 在 QEMU 上完成。

## 单元测试

```bash
pip install -r tools/airy-flash/requirements-dev.txt
python3 -m pytest tools/airy-flash/tests/ -q
```

测试用临时目录合成 sysfs 树、挂载表与镜像侧车文件，以 `S_ISBLK`/`S_ISREG`
替身替掉 `stat` 判定，因此**不需要 root、不需要真实块设备**；覆盖摘要解析
五个来源、系统盘父盘回溯（含 `nvme0n1p2`）、六类预检、抽检窗口划分、
写入与 `ftruncate`、回读篡改定位、清单解析与取回全链路（假 `oras` 拉取、
块篡改、清单不自洽、幂等与 `--force`）、五个子命令的退出码与
`--json`/`--report` 载荷。

## 维护清单

- **新增预检项**：同时更新 `preflight`、README 预检表与测试，保持
  「预检通过即可写」这一不变式。
- **新增摘要来源**：只改 `resolve_expected` 的解析顺序，并在测试里补齐
  优先级用例，避免多来源时静默取错。
- **修改输出结构**：七段顺序与 `AIRY-FLASH-*` 标记是外部契约（CI 解析），
  变更需同步更新 README 与 `--json` 字段。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
