Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

# agent-linux (AirymaxOS) — AI Agent Operating System

This repository is the management repo for AirymaxOS and one of five under
the [airymaxhub](https://atomgit.com/openairymax/airymaxhub) umbrella.

**Language:** English | [简体中文](README_zh.md)

[![AtomGitStars](https://atomgit.com/openairymax/agent-linux/star/badge.svg)](https://atomgit.com/openairymax/agent-linux)
[![GitHub](https://img.shields.io/github/stars/openairymax/agent-linux?style=flat&logo=github)](https://github.com/openairymax/agent-linux)
[![Version](https://img.shields.io/github/v/release/openairymax/agent-linux)](https://github.com/openairymax/agent-linux/releases)
[![Linux](https://img.shields.io/badge/Linux-6.6-FCC624?logo=linux&logoColor=black)](https://www.kernel.org)
[![GHCR](https://img.shields.io/badge/GHCR-airymaxos--iso-2496ED)](https://github.com/openairymax/agent-linux/pkgs/container/airymaxos-iso)
[![CI](https://github.com/openairymax/agent-linux/actions/workflows/kernel-verify.yml/badge.svg)](https://github.com/openairymax/agent-linux/actions/workflows/kernel-verify.yml)

---

## Overview

The AirymaxOS kernel is **ALK** (Airymax Linux Kernel), a fork of Linux 6.6:

- **Mainline untouched**: Airymax-specific code lives only in newly added
  directories (e.g. `security/airy`, `kernel/superv`); mainline files stay
  as-is, which keeps tracking upstream easy.
- **Verified on four architectures**: `airy_defconfig` builds on x86_64,
  aarch64, riscv64, and loongarch64 in CI; the output consists of six
  Airymax-only directories, and after install `uname -r` ends with `-alk`.
- **Euler-compatible**: kernel config, patch policy, and ABI follow the Euler
  standard; userspace is openEuler 24.03 LTS, reusing its verified packages
  as-is, with the UKUI desktop consumed as RPMs rather than vendored source.

The companion agent runtime is [agentrt](https://github.com/openairymax/agentrt).
Architecture, security, and release rules are registered in
`ssot-registry.yaml` and checked against the documentation by CI.

## Quick Start

Clone over SSH (AtomGit is the primary host) and fetch all nine submodules:

```bash
git clone git@atomgit.com:openairymax/agent-linux.git
cd agent-linux
git submodule update --init --recursive
```

Branches: `main` here, `ALK-6.6-dev` for the kernel submodule, `dev/hubs-01`
for the other eight.

## Install AirymaxOS (ISO)

The desktop ISO is ≈3.4 GiB (BIOS + UEFI bootable), larger than the release
asset size limit, so it is published as an OCI artifact on
[GHCR](https://github.com/openairymax/agent-linux/pkgs/container/airymaxos-iso);
`airy-flash` pulls it by tag with digest verification:

```bash
# 1. Download airy-flash-<ver>.pyz from the Releases page (single file, no dependencies)

# 2. Pull the ISO by tag (chunked, resumable, digest-verified)
python3 airy-flash-<ver>.pyz fetch \
  --ref ghcr.io/openairymax/airymaxos-iso:v0.0.3 \
  --out releases/v0.0.3

# 3. Preflight: show the target device, reject read-only/mounted/whole-disk-of-system
sudo python3 airy-flash-<ver>.pyz plan \
  --image releases/v0.0.3/airymaxos-<ver>-x86_64.iso --device /dev/sdb

# 4. Write + read-back verify (requires root; whole-disk nodes only, no partitions)
sudo python3 airy-flash-<ver>.pyz write \
  --image releases/v0.0.3/airymaxos-<ver>-x86_64.iso --device /dev/sdb --yes
```

`dd` or Rufus in DD mode also works, but `write` reads the image back and
verifies it after writing. Then:

1. Power off, boot from the USB stick (`F12` / `F11` / `Esc`), and finish
   the install wizard.
2. After install, check that `uname -r` ends with `-alk` and that
   `journalctl -b | grep AIRYMAX-DESKTOP-READY` reports the desktop is ready.

Troubleshooting and login policy: [tools/airy-flash/README.md](tools/airy-flash/README.md).

## Repository Layout

```text
agent-linux/             # management repo (this repository)
├── kernel/              # Linux 6.6 fork (submodule)
├── security/            # security
├── memory/              # memory management
├── cognition/           # cognition
├── services/            # services
├── system/              # system
├── cloudnative/         # cloud-native
├── distro/              # distro assembly
├── tests-linux/         # tests
├── .github/workflows/   # CI pipelines
└── tools/airy-flash/    # ISO flash and verification tool
```

## Leaf Repositories

Nine leaf repositories are aggregated in this repository; each builds and
releases independently:

| Module | Repository |
|--------|------------|
| kernel | [openairymax/kernel](https://github.com/openairymax/kernel) |
| memory | [openairymax/memory](https://github.com/openairymax/memory) |
| security | [openairymax/security](https://github.com/openairymax/security) |
| cognition | [openairymax/cognition](https://github.com/openairymax/cognition) |
| services | [openairymax/services](https://github.com/openairymax/services) |
| system | [openairymax/system](https://github.com/openairymax/system) |
| cloudnative | [openairymax/cloudnative](https://github.com/openairymax/cloudnative) |
| tests-linux | [openairymax/tests-linux](https://github.com/openairymax/tests-linux) |
| distro | [openairymax/distro](https://github.com/openairymax/distro) |

## Architecture

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                                AirymaxRT                                 │
│                      Agent orchestration & runtime                       │
├──────────────────────────────────────────────────────────────────────────┤
│                     POSIX-ABI layer + airymicro API                      │
├──────────────────────────────────────────────────────────────────────────┤
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐   │
│  │ security  │ │  memory   │ │ services  │ │ cognition │ │  system   │   │
│  │   (LSM)   │ │ (CXL/PM)  │ │ (daemons) │ │   (LLM)   │ │   (pkg)   │   │
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘ └───────────┘   │
├──────────────────────────────────────────────────────────────────────────┤
│         ┌──────────────────────────────────────────────────────┐         │
│         │  ALK (Airymax Linux Kernel, Linux 6.6 fork)          │         │
│         │  airy_defconfig: x86_64 / arm64 / riscv / loongarch  │         │
│         └──────────────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────────────────┘
```

The Airy kernel modules are installed under `/lib/modules/$(uname -r)/` in
the ISO and loaded at boot; userspace tools call the microkernel services
through the `airymicro` API.

## Continuous Integration

All commits must pass GitHub Actions gates (build, static analysis, and
tests); see the [Actions page](https://github.com/openairymax/agent-linux/actions)
for the latest status.

## Contributing

- Branch strategy: `main` for stable integration; feature branches use
  `feature/<name>`, `fix/<name>`, or `refactor/<name>`.
- Commit messages: lowercase subsystem prefix (`kernel:`, `mgmt:`, `ci:`),
  imperative mood, no emoji, no trailing period.
- Code style: follow the conventions of each leaf repository; run `make -n build`
  locally to verify build completeness.
- Sign-off (DCO): all commits must include `Signed-off-by: Your Name <email@example.com>`.
- SSoT: architecture, security, and release changes must be traceable to
  `ssot-registry.yaml`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

This repository and its submodules (except `kernel/`) are dual-licensed;
choose either [AGPL-3.0-or-later](LICENSE) or [Apache-2.0](LICENSE).
The `kernel/` subtree is [GPL-2.0-only](kernel/COPYING), keeping it
compatible with upstream Linux.
