<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# airy-flash — AirymaxOS 可复现烧录工具

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 用途

AirymaxOS 的发布物是一个 isohybrid ISO（BIOS 与 UEFI 双固件可引导），
装机只需要把镜像顺序写到整块设备。真正出问题的不是 `dd` 本身，而是它周围
没人做的四件事：**写错盘**（把系统盘或已挂载的盘当 U 盘）、**写坏包**
（拿到残缺或篡改的 ISO 却没发现）、**写完不知道成没成**（没有回读比对）、
**事后无法复盘**（只有一段滚动的终端输出，没有结构化记录）。

本工具把烧录收敛成一条可重放的流水线：**预检 → 校验镜像 → 写入 →
刷盘同步 → 回读校验 → 落报告**，每一步都有固定顺序的机器可读输出，
末尾一行 `AIRY-FLASH-PASS|FAIL` 供 CI 与日志检索直接断言。

工具**不自己实现分区与引导**：ISO 由 `mkksiso` 保留 isohybrid 分区表与
EFI 镜像，顺序写入即可双固件引导；另造一套分区/引导引擎只会引入第二处
事实源。工具的职责边界是**判定这台设备能不能写、这个包是不是发布物、
写完是否逐字节一致、以及把过程留成证据**。

所有判定读取真实 sysfs 与 `/proc/mounts`，所有写入都是真实块设备 I/O，
不做任何模拟。

权威依据：

| 内容 | 出处 |
|------|------|
| 发布物组成（ISO + `.sha256` + `iso-manifest.txt`） | `.github/workflows/image-ci.yml` 的 `airymaxos-image` artifact |
| BIOS + UEFI 双固件冷启动验收 | `ci/ci.sh accept --firmware both` |
| 装机后桌面就绪标记 `AIRYMAX-DESKTOP-READY` | 同上，产品 ISO 断言 |
| 主机名 `airymaxos`、内核 `-alk` 后缀 | `docs/docs-linux/` 品牌与内核归属规范 |
| Python 类型注解、数据类、错误码 | `docs/docs-linux/50-engineering-standards/10-coding-style/scripting_coding_style.md` |

## 烧录方案

发布物只从流水线取，不从本地构建目录取——本地构建的 ISO 与发行 `.sha256`
不是同一个文件，混用会让「可复现」失效。

| 步骤 | 动作 | 失败即停 |
|------|------|----------|
| 1 | 从 `image-ci` 下载 `airymaxos-image` artifact，解出 `*.iso` 与同名 `*.sha256` | 否 |
| 2 | 插入 U 盘，用 `lsblk` 找到**整盘**节点（如 `/dev/sdb`，不是 `/dev/sdb1`） | 是 |
| 3 | `plan` 只预检：核对系统盘、挂载、只读、容量、摘要来源 | 是 |
| 4 | `write --yes`：校验镜像 → 写入 → `fsync` → 回读比对 | 是 |
| 5 | 安全弹出，目标机开机按 F12/F11/Esc 选 U 盘 | — |
| 6 | 装机后核对主机名、内核后缀、登录策略 | 是 |

`plan` 与 `write` 共用同一套预检，所以「预检通过」是「可以写」的充分条件；
`plan` 已经算过完整 sha256，`write` 会再算一次，避免两次操作之间镜像被替换。

### 设备判定

- 目标必须是**整块设备**。分区（如 `/dev/sdb1`）会被 `probe_device` 当作
  普通文件路径拒绝，避免只覆盖前 512 MiB 之类的半截写入。
- 系统盘按「承载 `/`、`/boot`、`/boot/efi`、`/usr`、`/var` 的磁盘」反查：
  先 `/proc/mounts` 取挂载源，`/dev/mapper/*` 落到 `/dev/dm-N`，再用 sysfs
  的 `partition` 标记回溯到父盘（`/sys/class/block/sdb1` → `sdb`）。
- 有任何挂载点即拒绝，必须先 `umount`；只读属性（写保护开关）即拒绝。
- 普通文件只有在显式 `--allow-file` 时才允许，用于虚机镜像与单元测试。

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
| `plan` | 预检 + 校验镜像摘要（默认子命令） | 否 |
| `write` | 预检 → 校验 → 写入 → 刷盘 → 回读 | 是 |
| `verify` | 只回读已写入的设备并与镜像比对 | 否 |
| `check` | 环境自检（python、`/proc/mounts`、sysfs、root 权限） | 否 |

## 使用方法

```bash
# 环境自检
python3 tools/airy-flash/airy_flash.py check

# 只预检（不碰目标设备）
sudo python3 tools/airy-flash/airy_flash.py plan \
    --image releases/v0.0.3/AirymaxOS-0.0.3-x86_64.iso --device /dev/sdb

# 正式烧录（块设备必须 --yes）
sudo python3 tools/airy-flash/airy_flash.py write \
    --image releases/v0.0.3/AirymaxOS-0.0.3-x86_64.iso --device /dev/sdb --yes

# 首次使用新介质，全量回读并留证
sudo python3 tools/airy-flash/airy_flash.py write \
    --image AirymaxOS-0.0.3-x86_64.iso --device /dev/sdb \
    --verify full --report flash-report.json --yes

# 事后复核一块已经写好的盘
sudo python3 tools/airy-flash/airy_flash.py verify \
    --image AirymaxOS-0.0.3-x86_64.iso --device /dev/sdb

# CI / 脚本取机器可读结果
sudo python3 tools/airy-flash/airy_flash.py write ... --json
```

摘要来源按 `--sha256` → 镜像同目录 `<iso>.sha256` → 同目录 `SHA256SUMS` →
`--sums` 指定文件的顺序解析；同一 `SHA256SUMS` 里出现多条同镜像记录时报
`EXIT_CHECK`，不做「随便挑一条」的猜测。

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

人读输出固定六段，顺序不随结果变化，便于肉眼扫描与日志切片：

```
AirymaxOS 烧录工具 0.0.3  (write)
============================================================
映像    /path/AirymaxOS-0.0.3-x86_64.iso
        3.4 GiB   sha256 1a2b3c4d5e6f
目标    /dev/sdb
        USB 3.0 Flash Disk, usb, 28.7 GiB, 可移动

预检
  [ OK ] 目标是块设备     /dev/sdb
  [ OK ] 非系统盘         系统盘: sdd
  ...

阶段
  [ OK ] 1/4 校验镜像     sha256 一致 1a2b3c4d5e6f
  [ OK ] 2/4 写入设备     3.4 GiB, 92.1 MiB/s, 用时 38s
  [ OK ] 3/4 刷盘同步     fsync 完成
  [ OK ] 4/4 回读校验     sample 3 窗一致

结果
  结果    成功    用时 1m12s
  报告    flash-report.json
AIRY-FLASH-PASS image=/path/AirymaxOS-0.0.3-x86_64.iso device=/dev/sdb sha256=1a2b...e6f

下一步
  1. 安全弹出 U 盘，插入目标机，开机按 F12/F11/Esc 选择该 U 盘
  2. UEFI 固件请选带 UEFI 前缀的条目；BIOS 模式直接选 USB 设备
  3. 装机后核对：hostname=airymaxos，uname -r 以 "-alk" 结尾
  4. 核对登录策略：airymax 本地/SSH 免密，root 口令 00000000
```

- 标签按显示宽度补齐（CJK 记 2 列），续行缩进固定，中英混排不错位。
- 非 TTY 时进度按 10% 桶输出整行，不写 `\r`，日志可直接 `grep`。
- 失败时 `sha256=` 用**期望值**兜底，保证这一行仍能把日志对回发布物。
- `--json` 时 stdout 只有 JSON 对象，人读内容全部抑制；`--report` 支持
  `.json` 与 `key=value` 两种后缀，字段与 `--json` 同源于一个 `Report`。
- `check` 是环境自检，只输出检查项与一条用法提示，不打 `AIRY-FLASH-*` 标记。

## 装机后核对

烧录成功只证明「盘上的字节是对的」，不证明「系统起来了」。装完必须逐项核对：

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
| `目标既不是块设备也不是普通文件` | 传了分区或不存在路径 | `lsblk` 取整盘节点 |
| `非系统盘 [FAIL]` | 目标是当前系统所在盘 | 立即停止，换盘 |
| `未挂载 [FAIL]` | 桌面自动挂载了 U 盘 | `umount /dev/sdb*` 后重跑 |
| `校验和来源 [FAIL]` | artifact 里没带 `.sha256` | 从 `image-ci` 重新下载，勿用 `--allow-unverified` |
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
写入与 `ftruncate`、回读篡改定位、四个子命令的退出码与 `--json`/`--report`
载荷。

## 维护清单

- **新增预检项**：同时更新 `preflight`、README 预检表与测试，保持
  「预检通过即可写」这一不变式。
- **新增摘要来源**：只改 `resolve_expected` 的解析顺序，并在测试里补齐
  优先级用例，避免多来源时静默取错。
- **修改输出结构**：六段顺序与 `AIRY-FLASH-*` 标记是外部契约（CI 解析），
  变更需同步更新 README 与 `--json` 字段。

## 许可证

SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
