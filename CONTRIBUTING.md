# Contributing to agentrt-linux (AirymaxOS)

> ** Governance authority**: [`docs/AirymaxOS/50-engineering-standards/07-maintainers-and-governance.md`](https://github.com/openairymax/docs/blob/main/AirymaxOS/50-engineering-standards/07-maintainers-and-governance.md)
> ** Development process**: [`docs/AirymaxOS/50-engineering-standards/05-development-process.md`](https://github.com/openairymax/docs/blob/main/AirymaxOS/50-engineering-standards/05-development-process.md)
> ** Engineering philosophy**: [`docs/AirymaxOS/50-engineering-standards/04-engineering-philosophy.md`](https://github.com/openairymax/docs/blob/main/AirymaxOS/50-engineering-standards/04-engineering-philosophy.md)
> ** SSoT registry**: [`docs/AirymaxOS/50-engineering-standards/09-ssot-registry.md`](https://github.com/openairymax/docs/blob/main/AirymaxOS/50-engineering-standards/09-ssot-registry.md)

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## 1. Prerequisites

Before contributing, ensure you understand:

1. **IRON-1~15 Engineering Iron Rules** — defined in [04-engineering-philosophy.md](https://github.com/openairymax/docs/blob/main/AirymaxOS/50-engineering-standards/04-engineering-philosophy.md). These are non-negotiable.
2. **OS-IRON-013**: 8 submodule architecture — changes to one submodule must not break others.
3. **OS-IRON-014**: [SC] shared contract layer — headers in `kernel/include/airymax/` are the single physical source; changes require dual CI validation.
4. **SSoT Registry** — all rule IDs (OS-IRON, OS-KER, OS-STD-*) are registered in [`09-ssot-registry.md`](https://github.com/openairymax/docs/blob/main/AirymaxOS/50-engineering-standards/09-ssot-registry.md). Never invent new IDs.

## 2. Repository Structure

```
agentrt-linux/          # This management repository (main branch only)
├── kernel/             # submodule → feature/official-hubs-01
├── services/           # submodule → feature/official-hubs-01
├── security/           # submodule → feature/official-hubs-01
├── memory/             # submodule → feature/official-hubs-01
├── cognition/          # submodule → feature/official-hubs-01
├── cloudnative/        # submodule → feature/official-hubs-01
├── system/             # submodule → feature/official-hubs-01
└── tests-linux/        # submodule → feature/official-hubs-01
```

- **Management repo**: `main` branch only. No feature branches here.
- **Leaf repos**: Active development on `feature/official-hubs-01`.

## 3. DCO (Developer's Certificate of Origin)

**OS-IRON-007 / OS-KER-068**: All commits must include a `Signed-off-by:` line.

When you submit a patch, you must sign off by adding:

```bash
git commit -s
```

This adds:

```
Signed-off-by: Your Name <your.email@example.com>
```

By signing off, you certify that:

1. The contribution was created in whole or in part by you, and you have the right to submit it under the open source license (AGPL-3.0 OR Apache-2.0) indicated in the file.
2. The contribution is based upon previous work that, to the best of your knowledge, is covered under an appropriate open source license and you have the right to submit that work with modifications or additions.
3. You understand that submitting the contribution does not grant any license to your contribution beyond those granted in the project's license.

The `Signed-off-by:` chain from author → subsystem maintainer → top maintainer forms a traceable responsibility chain. Each maintainer who forwards a patch adds their own `Signed-off-by:`.

## 4. Commit Message Format

**OS-STD-PROD-031**: All commits must use `git commit -s` to add DCO signature.

### 4.1 Format

```
subsystem: short description (≤72 chars)

Detailed explanation of what and why. Wrap at 72 chars.

Fixes: <commit-sha> ("original commit description")  # if fixing a bug
Closes: #<issue-number>                              # if closing an issue
Link: <url-to-relevant-discussion>                    # if referencing external context

Signed-off-by: Your Name <your.email@example.com>
```

### 4.2 Subsystem Prefixes

| Prefix | Scope |
|--------|-------|
| `kernel:` | kernel/ submodule |
| `services:` | services/ submodule |
| `security:` | security/ submodule |
| `memory:` | memory/ submodule |
| `cognition:` | cognition/ submodule |
| `cloudnative:` | cloudnative/ submodule |
| `system:` | system/ submodule |
| `tests-linux:` | tests-linux/ submodule |
| `docs:` | Documentation changes |
| `ci:` | CI/CD pipeline changes |
| `tools:` | tools/ changes |
| `ssot:` | SSoT registry or validation changes |

### 4.3 [SC] Shared Contract Layer Changes

Changes to `kernel/include/airymax/` ([SC] layer) require:

1. Submit PR to the **kernel** submodule repository.
2. CI validates the kernel submodule builds.
3. A mirror PR is triggered on **agentrt** to verify cross-project compatibility.
4. Both CIs must pass.
5. L1 subsystem maintainer (kernel M:) reviews.
6. L3 top maintainer (SPHARX Engineering) gives final approval.

## 5. Branch Strategy

### 5.1 Management Repository

- **Branch**: `main` only.
- **No feature branches** are created on the management repository.
- Submodule pointer updates are committed directly to `main`.

### 5.2 Leaf Repositories

- **Development branch**: `feature/official-hubs-01`
- Create topic branches from `feature/official-hubs-01` for your work.
- Submit PRs targeting `feature/official-hubs-01`.

```bash
# Clone with submodules
git clone --recurse-submodules git@atomgit.com:openairymax/agentrt-linux.git
cd agentrt-linux

# Work in a submodule
cd kernel
git checkout feature/official-hubs-01
git checkout -b my-feature-branch
# ... make changes ...
git commit -s -m "kernel: add airymax_sched_ext policy"
git push origin my-feature-branch
# Create PR targeting feature/official-hubs-01
```

## 6. Code Style

### 6.1 C Code (kernel / security / memory / cognition kthread)

- **OS-STD-FMT-001**: Tab-8 indentation (enforced by `.clang-format`)
- **OS-STD-FMT-002**: 80-column line width hard limit
- Run `make format-check` before submitting

### 6.2 Rust Code (cognition / cloudnative)

- `rustfmt.toml`: 4-space indentation
- Run `cargo fmt` before submitting

### 6.3 Python / Go / TypeScript

- Python: PEP 8 (enforced by `ruff`)
- Go: `gofmt`
- TypeScript: Prettier with 2-space indentation

## 7. Testing

- **OS-STD-TEST-***: All changes must include or update tests.
- Unit tests: alongside the code in each submodule.
- Integration tests: in `tests-linux/` submodule.
- Formal verification: seL4-style proofs where applicable.

## 8. Review Process

1. Submit PR to the appropriate leaf repository on `feature/official-hubs-01`.
2. CI pipeline runs (SSoT validation, build, checkpatch, sparse, tests).
3. Reviewers are automatically assigned via `MAINTAINERS` / `CODEOWNERS`.
4. At least one maintainer approval is required.
5. For [SC] changes, L3 top maintainer approval is also required.
6. Squash-merge to `feature/official-hubs-01`.

## 9. SSoT Compliance

All PRs are validated by the SSoT validation CI:

```bash
python3 tools/validate-ssot.py docs/AirymaxOS ssot-registry.yaml
```

This checks that every `OS-*-NNN` rule ID used in documentation is registered in the SSoT YAML registry (`ssot-registry.yaml` at the management repo root). Unregistered IDs cause CI failure.

## 10. Reporting Issues

- **Bugs**: File on the relevant submodule's GitHub Issues.
- **Security vulnerabilities**: See [SECURITY.md](SECURITY.md).
- **Design discussions**: Use GitHub Discussions on the management repository.
