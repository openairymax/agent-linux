# agentrt-liunx（AirymaxOS）— 极境智能体操作系统

> agentrt-liunx（AirymaxOS / 极境智能体操作系统）智能体操作系统管理仓。
> [airymaxhub](https://atomgit.com/openairymax/airymaxhub) 伞仓下五个管理仓之一。

**语言:** [English](README.md) | 简体中文

[![Version](https://img.shields.io/badge/version-0.1.1-5a6b7e)](https://atomgit.com/openairymax/agentrt-linux)
[![License](https://img.shields.io/badge/license-AGPL--3.0+Apache--2.0-4a90d9)](LICENSE)
[![Linux](https://img.shields.io/badge/Linux-6.6-FCC624?logo=linux&logoColor=black)](https://www.kernel.org)
[![Rust](https://img.shields.io/badge/Rust-experimental-DEA584?logo=rust&logoColor=white)](https://www.rust-lang.org)

---

## 概述

**agentrt-liunx**（正式英文名：AirymaxOS，中文：极境智能体操作系统）是基于 Linux 6.6 构建的 AI 智能体操作系统研究项目。它是 `airymaxhub` 伞仓下与 `agentrt` 同级的管理仓，聚合 **8 个叶子仓**作为 git submodule。

agentrt-liunx 基于三大设计支柱：

1. **微内核设计思想** — 参考 seL4 / Zircon / Minix3、Liedtke 极简原则、capability 安全模型、服务用户态化、消息传递通信
2. **Euler 标准兼容性** — 全面参考 Euler 24.03 LTS / 26.03 模块设计、技术规格和标准；兼容 Euler 标准
3. **Airymax 同源性** — 与 `agentrt` 共享 Airymax 设计理念；架构同源，OS 上运行 `agentrt` 天然更稳健和适配，无适配层

在 Airymax 0.1.1 中，本仓库及其叶子仓包含设计文档、架构草案和工程基线声明。实际的内核与 OS 开发在 **1.0.1** 版本进行。

## 仓库结构

```
agentrt-linux/             # 管理仓（本仓库）
├── kernel/                # agentrt-liunx 内核叶子仓（submodule）
├── services/              # agentrt-liunx 服务态叶子仓（submodule）
├── security/              # agentrt-liunx 安全态叶子仓（submodule）
├── memory/                # agentrt-liunx 内存管理叶子仓（submodule）
├── cognition/             # agentrt-liunx 认知层叶子仓（submodule）
├── cloudnative/           # agentrt-liunx 云原生叶子仓（submodule）
├── system/                # agentrt-liunx 系统层叶子仓（submodule）
├── airymaxos-tests/       # agentrt-liunx 测试叶子仓（submodule）
├── .gitmodules            # Submodule 定义
├── LICENSE                # AGPL-3.0 + Apache-2.0 双许可证全文
├── NOTICE                 # 版权、商标与第三方声明
├── README.md              # 英文版
└── README_zh.md           # 中文版（本文件）
```

## 叶子仓

| 模块 | 目录 | 仓库 URL | 复用 | 描述 |
|------|------|----------|------|------|
| **airymaxos-kernel** | `kernel/` | `git@atomgit.com:openairymax/kernel.git` | atoms/corekern | Linux 6.6 + sched_ext + eBPF + io_uring + Rust（实验性）+ 微内核化改造 |
| **airymaxos-services** | `services/` | `git@atomgit.com:openairymax/services.git` | daemons | VFS + 网络 + 驱动用户态化 + 12 daemons systemd 集成 + io_uring 消息传递 |
| **airymaxos-security** | `security/` | `git@atomgit.com:openairymax/security.git` | cupolas | capability(seL4) + LSM + Landlock + 机密计算 + 国密 |
| **airymaxos-memory** | `memory/` | `git@atomgit.com:openairymax/memory.git` | heapstore + memoryrovol | MemoryRovol 内核态 + CXL + PMEM + MGLRU 2.0 |
| **airymaxos-cognition** | `cognition/` | `git@atomgit.com:openairymax/cognition.git` | coreloopthree + frameworks | CoreLoopThree kthread + Wasm 3.0 + LLM 调度 + Token 能效 + 超节点沙箱 |
| **airymaxos-cloudnative** | `cloudnative/` | `git@atomgit.com:openairymax/cloudnative.git` | gateway + sdk | K8s CRD + containerd shim + OCI + CNI + agentctl + 超节点 OS |
| **airymaxos-system** | `system/` | `git@atomgit.com:openairymax/system.git` | commons | RPM + dnf + 配置 + shell + DevStation |
| **airymaxos-tests** | `airymaxos-tests/` | `git@atomgit.com:openairymax/airymaxos-tests.git` | 全模块测试 | 单元 + 集成 + 形式化验证(seL4 风格) + Soak + 混沌 |

## 架构

```
┌─────────────────────────────────────────────────────────────────────┐
│           agentrt-liunx（AirymaxOS / 极境智能体操作系统）              │
├─────────────────────────────────────────────────────────────────────┤
│  应用层（Agent 租户）                                                  │
│    └── Airymax SDK（Python / Go / Rust / TypeScript）                │
├─────────────────────────────────────────────────────────────────────┤
│  服务态（用户态）                       ← airymaxos-services          │
│    gateway_d · llm_d · tool_d · sched_d · market_d · monit_d · ...   │
├─────────────────────────────────────────────────────────────────────┤
│  认知层（kthread + Wasm）              ← airymaxos-cognition         │
│    CoreLoopThree · TimeSliceInfer · Token 能效                       │
├─────────────────────────────────────────────────────────────────────┤
│  安全层（LSM + capability）            ← airymaxos-security          │
│    Cupolas · seL4 capability · Landlock · 机密计算                   │
├─────────────────────────────────────────────────────────────────────┤
│  内存层（内核态）                       ← airymaxos-memory            │
│    MemoryRovol · CXL · PMEM · MGLRU 2.0                              │
├─────────────────────────────────────────────────────────────────────┤
│  云原生层                              ← airymaxos-cloudnative       │
│    K8s CRD · containerd shim · OCI · CNI                             │
├─────────────────────────────────────────────────────────────────────┤
│  系统层                                ← airymaxos-system            │
│    RPM · dnf · 配置 · shell · DevStation                             │
├─────────────────────────────────────────────────────────────────────┤
│  微内核（基于 Linux 6.6）              ← airymaxos-kernel            │
│    sched_ext · eBPF · io_uring · Rust · 微内核化改造                  │
├─────────────────────────────────────────────────────────────────────┤
│  测试与验证                            ← airymaxos-tests             │
│    单元 · 集成 · 形式化(seL4) · Soak · 混沌                          │
└─────────────────────────────────────────────────────────────────────┘
```

### 与 Airymax agentrt 的关系

agentrt-liunx 与 `agentrt` 共享相同的 Airymax 设计理念（架构同源）。核心微内核原语（`atoms/corekern`）、daemon 服务、安全框架（`cupolas`）、内存引擎（`heapstore` / `memoryrovol`）和认知循环（`coreloopthree`）在用户态运行时（`agentrt`）与 OS 级内核（agentrt-liunx）之间复用。这确保了 `agentrt` 在 agentrt-liunx 上原生运行，无适配层。

### 与 Euler 标准的关系

agentrt-liunx 全面参考 Euler 24.03 LTS / 26.03 的：
- 模块设计和技术规格
- 工程标准和质量规范
- 包管理（RPM / dnf）
- 发行版生命周期和 LTS 支持模型

agentrt-liunx 兼容 Euler 标准，可消费 Euler 标准包和工具链。

### 与 Linux 6.6 的关系

agentrt-liunx 基于 Linux 6.6（openEuler 24.03 LTS 内核基线），集成：
- **sched_ext** 子调度器（Linux 6.15+）实现 AI 感知的 CPU 调度
- **eBPF 签名验证**（Linux 6.15）保障内核可编程安全性
- **io_uring** 提供高性能异步 I/O 与消息传递
- **Rust**（Linux 6.6 实验性支持）用于内存安全的内核模块

微内核化改造策略不从零开发微内核 —— 而是在 Linux 6.6 宏内核之上应用微内核设计原则（服务用户态化、capability 安全、消息传递）。

## 上游依赖

- **Airymax agentrt** — 提供被复用并扩展的运行时平台模块
- **Euler 24.03 LTS / 26.03** — 标准与规范参考发行版
- **Linux 6.6** — 提供 sched_ext、eBPF、io_uring 与 Rust（实验性）支持的基础内核

## 下游消费者

- **Agent 应用** — 通过 Airymax SDK 在 agentrt-liunx 上原生运行
- **云 / 边缘部署** — agentrt-liunx 作为 AI 智能体基础设施的基础 OS
- **研究人员** — 微内核设计与 AI 原生 OS 研究

## 分支策略

- **本管理仓** — 仅 `main` 分支，不创建 feature 分支。
- **叶子仓** — 在 `feature/official-hubs-01` 分支上开发。

克隆本仓库（含 submodule）：

```bash
git clone --recurse-submodules git@atomgit.com:openairymax/agentrt-linux.git
cd agentrt-linux
git submodule update --remote --checkout
```

## 许可证

采用 **AGPL v3 + Apache 2.0** 双许可证（SPDX: `AGPL-3.0-or-later OR Apache-2.0`）。详见 [LICENSE](LICENSE) 双许可证全文和 [NOTICE](NOTICE) 版权、商标与第三方声明。

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.