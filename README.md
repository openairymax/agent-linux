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

This repository and its leaf repos hold design documents, architectural drafts, and engineering baseline declarations for the agentrt-linux operating system.

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
├── tests-linux/       # agentrt-linux Tests leaf repo (submodule)
├── .gitmodules            # Submodule definitions
├── LICENSE                # AGPL-3.0 + Apache-2.0 dual license full text
├── NOTICE                 # Copyright, trademark and third-party notices
├── README.md              # This file (English)
└── README_zh.md           # Chinese translation
```

## Leaf Repositories

| Module | Directory | Repository URL | Reuses | Description |
|--------|-----------|----------------|--------|-------------|
| **kernel** | `kernel/` | `git@atomgit.com:openairymax/kernel.git` | atoms/corekern | Linux 6.6 + sched_ext + eBPF + io_uring + Rust + microkernel refactoring |
| **services** | `services/` | `git@atomgit.com:openairymax/services.git` | daemons | VFS + network + driver user-space migration + 12 daemons systemd integration + io_uring message passing |
| **security** | `security/` | `git@atomgit.com:openairymax/security.git` | cupolas | capability (seL4) + LSM + Landlock + confidential computing + national crypto |
| **memory** | `memory/` | `git@atomgit.com:openairymax/memory.git` | heapstore + memoryrovol | MemoryRovol kernel-mode + CXL + PMEM + MGLRU 多代 LRU |
| **cognition** | `cognition/` | `git@atomgit.com:openairymax/cognition.git` | coreloopthree + frameworks | CoreLoopThree kthread + Wasm 3.0 + LLM scheduling + Token energy efficiency + hyper-node sandbox |
| **cloudnative** | `cloudnative/` | `git@atomgit.com:openairymax/cloudnative.git` | gateway + sdk | K8s CRD + containerd shim + OCI + CNI + agentctl + hyper-node OS |
| **system** | `system/` | `git@atomgit.com:openairymax/system.git` | commons | RPM + dnf + configuration + shell + DevStation |
| **tests-linux** | `tests-linux/` | `git@atomgit.com:openairymax/tests-linux.git` | all modules | Unit + integration + formal verification (seL4 style) + Soak + chaos |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│           agentrt-linux（AirymaxOS / 极境智能体操作系统）              │
├─────────────────────────────────────────────────────────────────────┤
│  Applications (Agent tenants)                                        │
│    └── Airymax SDK (Python / Go / Rust / TypeScript)                 │
├─────────────────────────────────────────────────────────────────────┤
│  Daemon Services (user-space)          ← services                     │
│    gateway_d · llm_d · tool_d · sched_d · market_d · monit_d · ...   │
├─────────────────────────────────────────────────────────────────────┤
│  Cognition Layer (kthread + Wasm)      ← cognition                    │
│    CoreLoopThree · TimeSliceInfer · Token energy efficiency          │
├─────────────────────────────────────────────────────────────────────┤
│  Security Layer (LSM + capability)     ← security                     │
│    Cupolas · seL4 capability · Landlock · confidential computing     │
├─────────────────────────────────────────────────────────────────────┤
│  Memory Layer (kernel-mode)            ← memory                       │
│    MemoryRovol · CXL · PMEM · MGLRU 多代 LRU                              │
├─────────────────────────────────────────────────────────────────────┤
│  CloudNative Layer                     ← cloudnative                   │
│    K8s CRD · containerd shim · OCI · CNI                             │
├─────────────────────────────────────────────────────────────────────┤
│  System Layer                          ← system                        │
│    RPM · dnf · configuration · shell · DevStation                    │
├─────────────────────────────────────────────────────────────────────┤
│  Microkernel (Linux 6.6 based)         ← kernel                        │
│    sched_ext · eBPF · io_uring · Rust · microkernel refactoring      │
├─────────────────────────────────────────────────────────────────────┤
│  Tests & Verification                  ← tests-linux                  │
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

agentrt-linux is built on Linux 6.6 (Euler-standard kernel baseline) with:
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

## CI / CD Pipelines

The management repository hosts **6 GitHub Actions workflows** (each capped at 2 jobs). Language-level CI is delegated to the leaf repositories' own `.github/workflows/`.

| Workflow | Trigger | Jobs | Purpose |
|----------|---------|------|---------|
| `ci-kernel.yml` | PR / push on `kernel/**` | `kernel-build` + `kernel-verify` | Kbuild matrix (18 configs) + checkpatch / sparse / Coccinelle / KUnit / kselftest |
| `mgmt-orchestrator.yml` | PR / push on docs, `.gitmodules`, SSoT, governance files | `file-integrity` + `orchestrate-leaf-ci` | Verify 8 submodules, [SC] 6+2 headers, governance files; aggregate leaf CI status |
| `nightly.yml` | cron `0 2 * * *` / manual | `nightly-test-suite` + `nightly-revert-or-budget` | Formal verification (seL4-style) + 72h soak + chaos; auto-revert or CI budget check |
| `release.yml` | tag `v*` / manual | `build-and-sign` + `publish-release` | SPDX SBOM + kernel/SDK build + GPG & cosign signing; publish dnf repo, OCI image, GitHub Release |
| `sc-dual-ci.yml` | PR on [SC] 6+2 headers | `sc-validate` + `sc-trigger-and-await` | Validate shared-contract headers, no duplicates; trigger & await agentrt mirror PR CI |
| `ssot-validate.yml` | PR / push on `ssot-registry.yaml`, `docs/AirymaxOS/**` | `ssot-syntax-and-rules` + `ssot-cross-ref` | YAML syntax + rule-ID uniqueness; cross-document link & format consistency |

### [SC] Shared Contract Layer

The shared-contract (`[SC]`) layer is the single physical source of truth for kernel↔agentrt ABI. It lives under `kernel/include/airymax/` and consists of the **6+2** headers:

| Category | Headers |
|----------|---------|
| Core 6 | `syscalls.h`, `memory_types.h`, `security_types.h`, `cognition_types.h`, `sched.h`, `ipc.h` |
| Extended 2 | `bpf_struct_ops.h`, `error.h` |

Changes to any `[SC]` header require dual CI validation (agentrt-linux `sc-dual-ci.yml` + agentrt mirror PR) per **OS-IRON-014**. The IPC ABI uses magic `0x41524531` (`'ARE1'`); the task descriptor magic is `0x41475453` (`'AGTS'`). Kernel-side functions use the `airy_*` prefix.

## Development Guide

Contributions follow the IRON engineering rules and the SSoT (Single Source of Truth) registry. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full process; key points:

- **DCO** — every commit must be signed off (`git commit -s`, rule OS-IRON-007 / OS-KER-068).
- **Commit prefix** — use the submodule name as prefix (`kernel:`, `services:`, `security:`, `memory:`, `cognition:`, `cloudnative:`, `system:`, `tests-linux:`, `docs:`, `ci:`, `tools:`, `ssot:`).
- **Branches** — management repo is `main` only; leaf repos develop on `feature/official-hubs-01`.
- **Code style** — C: tab-8, 80 cols (`.clang-format`); Rust: `cargo fmt`; Python: PEP 8 (`ruff`); Go: `gofmt`.
- **SSoT compliance** — every `OS-*-NNN` rule ID referenced in docs must be registered in [`ssot-registry.yaml`](ssot-registry.yaml); validate locally with `python3 tools/validate-ssot.py docs/AirymaxOS ssot-registry.yaml`.
- **Function prefix** — kernel-side APIs use the `airy_*` prefix (not legacy `airymaxos_*`).
- **Reviews** — L1 subsystem maintainer + L3 top maintainer approval required for `[SC]` changes; enforced via branch protection and `MAINTAINERS` / `CODEOWNERS`.

See the [Branch Strategy](#branch-strategy) section above for clone-with-submodules instructions.

## License

Dual-licensed under **AGPL v3 + Apache 2.0** (SPDX: `AGPL-3.0-or-later OR Apache-2.0`). You may choose either license at your option. See [LICENSE](LICENSE) for the full text of both licenses and [NOTICE](NOTICE) for copyright, trademark and third-party notices.

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.