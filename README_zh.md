Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

# AirymaxOS 极境智能体操作系统

本仓库是 AirymaxOS 的管理仓库，也是 [airymaxhub](https://atomgit.com/openairymax/airymaxhub) 下的五个管理仓库之一。

**语言：** [English](README.md) | 简体中文

[![AtomGit](https://atomgit.com/openairymax/agent-linux/star/badge.svg)](https://atomgit.com/openairymax/agent-linux)
[![GitHub](https://img.shields.io/github/stars/openairymax/agent-linux?style=flat&logo=github)](https://github.com/openairymax/agent-linux)
[![Version](https://img.shields.io/github/v/release/openairymax/agent-linux)](https://github.com/openairymax/agent-linux/releases)
[![Linux](https://img.shields.io/badge/Linux-6.6-FCC624?logo=linux&logoColor=black)](https://www.kernel.org)
[![GHCR](https://img.shields.io/badge/GHCR-airymaxos--iso-2496ED)](https://github.com/openairymax/agent-linux/pkgs/container/airymaxos-iso)
[![CI](https://github.com/openairymax/agent-linux/actions/workflows/kernel-verify.yml/badge.svg)](https://github.com/openairymax/agent-linux/actions/workflows/kernel-verify.yml)

---

## 概述

AirymaxOS 极境智能体操作系统，**ALK**（Airymax Linux Kernel）是基于Linux 6.6 进行微内化改造的kernel：  

- **微内核化**：Airymax 进行 seL4 形式的改造，主线文件保持跟进上游。
- **原生架构**：x86_64、aarch64、riscv64、loongarch64。
- **对齐欧拉**：基底使用 openEuler 24.03 LTS；使用 UKUI 桌面。

配套的智能体运行时是 [agentrt](https://atomgit.com/openairymax/agentrt)。

## 快速开始

用 SSH 克隆并拉取全部九个子模块（AtomGit 是主托管平台）：

```bash
git clone git@atomgit.com:openairymax/agent-linux.git
cd agent-linux
git submodule update --init --recursive
```

分支：本仓库 `main`；kernel 子模块 `ALK-6.6-dev`；其余八个子模块 `dev/hubs-01`。

## 安装 AirymaxOS（ISO）

桌面 ISO 约 3.4 GB（BIOS / UEFI 均可引导）以 OCI 制品发布在 [GHCR](https://github.com/openairymax/agent-linux/pkgs/container/airymaxos-iso)；
`airy-flash` 按 tag 拉取并校验摘要：

```bash
# 1. 从 Releases 页面下载 airy-flash-<ver>.pyz（单文件，无依赖）

# 2. 按 tag 拉取 ISO（分块、可续传、摘要校验）
python3 airy-flash-<ver>.pyz fetch \
  --ref ghcr.io/openairymax/airymaxos-iso:v0.0.3 \
  --out releases/v0.0.3

# 3. 预检：显示目标设备，拒绝只读/已挂载/系统盘整盘等危险情况
sudo python3 airy-flash-<ver>.pyz plan \
  --image releases/v0.0.3/airymaxos-<ver>-x86_64.iso --device /dev/sdb

# 4. 烧录 + 回读校验（需 root；只接受整盘节点，不接受分区）
sudo python3 airy-flash-<ver>.pyz write \
  --image releases/v0.0.3/airymaxos-<ver>-x86_64.iso --device /dev/sdb --yes
```

也可以直接用 `dd` 或 Rufus 的 DD 模式写入，但 `write` 会在写入后回读校验整段 ISO。
之后：

1. 关机，开机从 U 盘启动（`F12` / `F11` / `Esc`），按安装向导完成安装。
2. 安装后检查：`uname -r` 以 `-alk` 结尾；
   `journalctl -b | grep AIRYMAX-DESKTOP-READY` 能看到桌面就绪。

故障排查与登录策略见 [tools/airy-flash/README.md](tools/airy-flash/README.md)。

## 仓库结构

```text
agent-linux/             # 管理仓（本仓库）
├── kernel/              # ALK 6.6
├── security/            # 安全态
├── memory/              # 内存管理
├── cognition/           # 认知层
├── services/            # 服务态
├── system/              # 系统层
├── cloudnative/         # 云原生
├── distro/              # 发行版组装层
├── tests-linux/         # 测试
└── tools/airy-flash/    # ISO 烧录与校验工具
```

## 叶子仓库

九个叶子仓库聚合在本仓库中，各自独立构建与发布：

| 模块 | 仓库 |
|------|------|
| kernel | [openairymax/kernel](https://github.com/openairymax/kernel) |
| memory | [openairymax/memory](https://github.com/openairymax/memory) |
| security | [openairymax/security](https://github.com/openairymax/security) |
| cognition | [openairymax/cognition](https://github.com/openairymax/cognition) |
| services | [openairymax/services](https://github.com/openairymax/services) |
| system | [openairymax/system](https://github.com/openairymax/system) |
| cloudnative | [openairymax/cloudnative](https://github.com/openairymax/cloudnative) |
| tests-linux | [openairymax/tests-linux](https://github.com/openairymax/tests-linux) |
| distro | [openairymax/distro](https://github.com/openairymax/distro) |

## 架构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                                AirymaxRT                                 │
│                            智能体编排与运行时层                            │
├──────────────────────────────────────────────────────────────────────────┤
│                     POSIX-ABI  兼容层 + airymicro API                     │
├──────────────────────────────────────────────────────────────────────────┤
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐   │
│  │ security  │ │  memory   │ │ services  │ │ cognition │ │  system   │   │
│  │   (LSM)   │ │ (CXL/PM)  │ │ (daemons) │ │   (LLM)   │ │   (pkg)   │   │
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘ └───────────┘   │
├──────────────────────────────────────────────────────────────────────────┤
│         ┌──────────────────────────────────────────────────────┐         │
│         │  ALK（Airymax Linux Kernel，基于 Linux 6.6 ）         │         │
│         │  airy_defconfig: x86_64 / arm64 / riscv / loongarch  │         │
│         └──────────────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────────────────┘
```

Airy 内核模块安装在 ISO 镜像的 `/lib/modules/$(uname -r)/` 下，开机时加载；
用户态工具通过 `airymicro` API 调用微内核服务。

## 持续集成

所有提交均需通过 GitHub Actions 门禁校验（构建、静态分析与测试），
最新状态见 [Actions 页面](https://github.com/openairymax/agent-linux/actions)。

## 参与贡献

- 分支：`main` 为稳定集成分支；功能分支用 `feature/<name>`、`fix/<name>` 或 `refactor/<name>`。
- 提交信息：小写子系统前缀（`kernel:`、`mgmt:`、`ci:`），祈使句，不用 emoji，结尾不加句号。
- 代码风格：遵循各叶子仓库惯例；提交前用 `make -n build` 检查构建完整性。
- 签名（DCO）：所有提交必须附带 `Signed-off-by: Your Name <email@example.com>`。
- SSoT：架构、安全与发布相关的变更必须可追溯至 `ssot-registry.yaml`。

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

本仓库及其子模块（`kernel/` 除外）为双许可，可任选其一：
[AGPL-3.0-or-later](LICENSE) 或 [Apache-2.0](LICENSE)。
`kernel/` 子树为 [GPL-2.0-only](kernel/COPYING)，与上游 Linux 内核保持兼容。
