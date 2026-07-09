# agentrt-linux（AirymaxOS）— AI Agent Operating System

> Management repository for the agentrt-linux (AirymaxOS) intelligent agent operating system.
> One of five management repositories under the [airymaxhub](https://atomgit.com/openairymax/airymaxhub) umbrella.

**Language:** English | [简体中文](README_zh.md)

[![Version](https://img.shields.io/badge/version-0.1.1-5a6b7e)](https://atomgit.com/openairymax/agentrt-linux)
[![License](https://img.shields.io/badge/license-AGPL--3.0+Apache--2.0-4a90d9)](LICENSE)
[![Linux](https://img.shields.io/badge/Linux-6.6-FCC624?logo=linux&logoColor=black)](https://www.kernel.org)
[![Rust](https://img.shields.io/badge/Rust-experimental-DEA584?logo=rust&logoColor=white)](https://www.rust-lang.org)

---

## Overview

**agentrt-linux** (formal English name: AirymaxOS, Chinese: 极境智能体操作系统) is an AI Agent Operating System research project built on top of Linux 6.6. It is a management repository at the same level as `agentrt` under the `airymaxhub` umbrella, aggregating **8 leaf repositories** as git submodules.

agentrt-linux is based on three design pillars:

1. **Microkernel Design Principles** — referencing seL4 / Zircon / Minix3, Liedtke minimality principle, capability-based security, user-space service isolation, message-passing communication
2. **Euler Standards Compatibility** — comprehensive reference to Euler 24.03 LTS / 26.03 module design, technical specifications, and standards; Euler-compatible
3. **Airymax Homology** — shares the same Airymax design philosophy with `agentrt`; the OS runs `agentrt` natively with no adaptation layer due to architectural homology

In Airymax 0.1.1, this repository and its leaf repos contain design documents, architectural drafts, and engineering baseline declarations. Actual kernel and OS development takes place in version **1.0.1**.

## Repository Structure

```
agentrt-linux/             # Management repository (this repo)
├── kernel/                # agentrt-linux Kernel leaf repo (submodule)
├── services/              # agentrt-linux Services leaf repo (submodule)
├── security/              # agentrt-linux Security leaf repo (submodule)
├── memory/                # agentrt-linux Memory leaf repo (submodule)
├── cognition/             # agentrt-linux Cognition leaf repo (submodule)
├── cloudnative/           # agentrt-linux CloudNative leaf repo (submodule)
├── system/                # agentrt-linux System leaf repo (submodule)
├── airymaxos-tests/       # agentrt-linux Tests leaf repo (submodule)
├── .gitmodules            # Submodule definitions
├── LICENSE                # AGPL-3.0 + Apache-2.0 dual license full text
├── NOTICE                 # Copyright, trademark and third-party notices
├── README.md              # This file (English)
└── README_zh.md           # Chinese translation
```

## Leaf Repositories

| Module | Directory | Repository URL | Reuses | Description |
|--------|-----------|----------------|--------|-------------|
| **airymaxos-kernel** | `kernel/` | `git@atomgit.com:openairymax/kernel.git` | atoms/corekern | Linux 6.6 + sched_ext + eBPF + io_uring + Rust + microkernel refactoring |
| **airymaxos-services** | `services/` | `git@atomgit.com:openairymax/services.git` | daemons | VFS + network + driver user-space migration + 12 daemons systemd integration + io_uring message passing |
| **airymaxos-security** | `security/` | `git@atomgit.com:openairymax/security.git` | cupolas | capability (seL4) + LSM + Landlock + confidential computing + national crypto |
| **airymaxos-memory** | `memory/` | `git@atomgit.com:openairymax/memory.git` | heapstore + memoryrovol | MemoryRovol kernel-mode + CXL + PMEM + MGLRU 2.0 |
| **airymaxos-cognition** | `cognition/` | `git@atomgit.com:openairymax/cognition.git` | coreloopthree + frameworks | CoreLoopThree kthread + Wasm 3.0 + LLM scheduling + Token energy efficiency + hyper-node sandbox |
| **airymaxos-cloudnative** | `cloudnative/` | `git@atomgit.com:openairymax/cloudnative.git` | gateway + sdk | K8s CRD + containerd shim + OCI + CNI + agentctl + hyper-node OS |
| **airymaxos-system** | `system/` | `git@atomgit.com:openairymax/system.git` | commons | RPM + dnf + configuration + shell + DevStation |
| **airymaxos-tests** | `airymaxos-tests/` | `git@atomgit.com:openairymax/airymaxos-tests.git` | all modules | Unit + integration + formal verification (seL4 style) + Soak + chaos |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│           agentrt-linux（AirymaxOS / 极境智能体操作系统）              │
├─────────────────────────────────────────────────────────────────────┤
│  Applications (Agent tenants)                                        │
│    └── Airymax SDK (Python / Go / Rust / TypeScript)                 │
├─────────────────────────────────────────────────────────────────────┤
│  Daemon Services (user-space)          ← airymaxos-services          │
│    gateway_d · llm_d · tool_d · sched_d · market_d · monit_d · ...   │
├─────────────────────────────────────────────────────────────────────┤
│  Cognition Layer (kthread + Wasm)      ← airymaxos-cognition         │
│    CoreLoopThree · TimeSliceInfer · Token energy efficiency          │
├─────────────────────────────────────────────────────────────────────┤
│  Security Layer (LSM + capability)     ← airymaxos-security          │
│    Cupolas · seL4 capability · Landlock · confidential computing     │
├─────────────────────────────────────────────────────────────────────┤
│  Memory Layer (kernel-mode)            ← airymaxos-memory            │
│    MemoryRovol · CXL · PMEM · MGLRU 2.0                              │
├─────────────────────────────────────────────────────────────────────┤
│  CloudNative Layer                     ← airymaxos-cloudnative       │
│    K8s CRD · containerd shim · OCI · CNI                             │
├─────────────────────────────────────────────────────────────────────┤
│  System Layer                          ← airymaxos-system            │
│    RPM · dnf · configuration · shell · DevStation                    │
├─────────────────────────────────────────────────────────────────────┤
│  Microkernel (Linux 6.6 based)         ← airymaxos-kernel            │
│    sched_ext · eBPF · io_uring · Rust · microkernel refactoring      │
├─────────────────────────────────────────────────────────────────────┤
│  Tests & Verification                  ← airymaxos-tests             │
│    Unit · Integration · Formal (seL4) · Soak · Chaos                 │
└─────────────────────────────────────────────────────────────────────┘
```

### Relationship with Airymax agentrt

agentrt-linux and `agentrt` share the same Airymax design philosophy (architectural homology). The core micro-core primitives (`atoms/corekern`), daemon services, security framework (`cupolas`), memory engine (`heapstore` / `memoryrovol`), and cognition loop (`coreloopthree`) are reused between the user-space runtime (`agentrt`) and the OS-level kernel (agentrt-linux). This ensures `agentrt` runs natively on agentrt-linux with no adaptation layer.

### Relationship with Euler Standards

agentrt-linux comprehensively references Euler 24.03 LTS / 26.03 for:
- Module design and technical specifications
- Engineering standards and quality norms
- Package management (RPM / dnf)
- Distribution lifecycle and LTS support model

agentrt-linux is Euler-compatible and can consume Euler-standard packages and tooling.

### Relationship with Linux 6.6

agentrt-linux is built on Linux 6.6 (openEuler 24.03 LTS kernel baseline) with:
- **sched_ext** sub-scheduler (Linux 6.15+) for AI-aware CPU scheduling
- **eBPF signed verification** (Linux 6.15) for secure in-kernel programmability
- **io_uring** for high-performance async I/O and message passing
- **Rust** (experimental support in Linux 6.6) for memory-safe kernel modules

The microkernel refactoring strategy does NOT develop a microkernel from scratch — it applies microkernel design principles (user-space service isolation, capability-based security, message-passing) on top of the Linux 6.6 monolithic kernel.

## Upstream Dependencies

- **Airymax agentrt** — provides the runtime platform whose modules are reused and extended
- **Euler 24.03 LTS / 26.03** — reference distribution for standards and specifications
- **Linux 6.6** — base kernel with sched_ext, eBPF, io_uring, and Rust（实验性）support

## Downstream Consumers

- **Agent applications** — run natively on agentrt-linux via the Airymax SDK
- **Cloud / edge deployments** — agentrt-linux as the base OS for AI agent infrastructure
- **Researchers** — microkernel design and AI-native OS research

## Branch Strategy

- **This management repo** — `main` only. No feature branches are created here.
- **Leaf repositories** — active development happens on `feature/official-hubs-01`.

When cloning this repo with submodules:

```bash
git clone --recurse-submodules git@atomgit.com:openairymax/agentrt-linux.git
cd agentrt-linux
git submodule update --remote --checkout
```

## License

Dual-licensed under **AGPL v3 + Apache 2.0** (SPDX: `AGPL-3.0-or-later OR Apache-2.0`). You may choose either license at your option. See [LICENSE](LICENSE) for the full text of both licenses and [NOTICE](NOTICE) for copyright, trademark and third-party notices.

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.