# `.github/` — GitHub Automation & Templates

> GitHub Actions workflows, issue/PR templates and community health files for the
> [agentrt-linux (AirymaxOS)](https://atomgit.com/openairymax/agentrt-linux) management repository.

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## Positioning

This directory holds all repository-level GitHub automation for the agentrt-linux
management repository. It defines the CI/CD pipelines that validate governance
files, build the kernel, run nightly verification, publish releases, guard the
`[SC]` shared-contract layer, and enforce SSoT (Single Source of Truth) consistency.

Each workflow is capped at **2 jobs** (rule: management repo ≤ 2 pipelines per
workflow). Language-level CI for C / Rust / Go / Python is delegated to each leaf
repository's own `.github/workflows/`; this repository only performs
management-level orchestration.

## Directory Contents

```
.github/
├── README.md                    # This file
├── workflows/                   # 6 GitHub Actions workflows (2 jobs each)
│   ├── ci-kernel.yml            # Kernel build matrix + static analysis + tests
│   ├── mgmt-orchestrator.yml    # Governance file integrity + leaf CI orchestration
│   ├── nightly.yml              # Nightly test suite + auto-revert/budget check
│   ├── release.yml              # Build + SBOM + sign + publish release
│   ├── sc-dual-ci.yml           # [SC] header validation + agentrt mirror CI
│   └── ssot-validate.yml        # SSoT YAML syntax + cross-reference validation
├── ISSUE_TEMPLATE/              # GitHub issue templates
└── PULL_REQUEST_TEMPLATE.md     # Pull request template
```

## CI Workflows (6 workflows × 2 jobs)

| Workflow | Job 1 | Job 2 | Trigger |
|----------|-------|-------|---------|
| `ci-kernel.yml` | `kernel-build` — Kbuild matrix (3 configs × 3 arches × 2 compilers = 18) with `W=2` zero-warning gate | `kernel-verify` — checkpatch `--strict`, sparse `C=2`, Coccinelle, KUnit on UML, kselftest on QEMU | PR / push on `kernel/**` |
| `mgmt-orchestrator.yml` | `file-integrity` — verify 8 submodule dirs, `.gitmodules` (8 entries), `[SC]` 6+2 headers, 13 governance files, no `airymaxos-` prefix | `orchestrate-leaf-ci` — aggregate 8 leaf CI statuses, markdownlint docs, copyright header check | PR / push on docs, `.gitmodules`, `ssot-registry.yaml`, `MAINTAINERS`, `CODEOWNERS` |
| `nightly.yml` | `nightly-test-suite` — seL4-style formal verification, 72h soak test, chaos injection (CPU hotplug / mem hotremove / I/O error / net partition) | `nightly-revert-or-budget` — auto-revert on regression via `auto-bisect`, or CI budget check (60 min gate, OS-STD-TEST-011) | cron `0 2 * * *` / `workflow_dispatch` |
| `release.yml` | `build-and-sign` — SPDX SBOM (syft per submodule + merge), kernel RPM build, GPG + cosign signing, SDK tarball | `publish-release` — publish dnf repo, push OCI image, create GitHub Release with artifacts | tag `v*` / `v*-rc*` / `workflow_dispatch` |
| `sc-dual-ci.yml` | `sc-validate` — verify `[SC]` 6+2 headers exist under `kernel/include/airymax/`, no physical duplicates (OS-IRON-014) | `sc-trigger-and-await` — export header patches, create mirror PR in agentrt repo, poll mirror CI status (30 min timeout) | PR touching any `[SC]` header |
| `ssot-validate.yml` | `ssot-syntax-and-rules` — PyYAML syntax check, rule-ID count + uniqueness (OS-IRON-015), `validate-ssot.py` cross-ref | `ssot-cross-ref` — trailing-whitespace warning, broken relative-path markdown link detection under `docs/AirymaxOS/` | PR / push on `ssot-registry.yaml`, `docs/AirymaxOS/**` |

## `[SC]` Shared-Contract Headers

The `sc-dual-ci.yml` and `mgmt-orchestrator.yml` workflows guard the `[SC]` layer —
the single physical source of truth for the kernel↔agentrt ABI, located at
`kernel/include/airymax/`:

- **Core 6**: `syscalls.h`, `memory_types.h`, `security_types.h`, `cognition_types.h`, `sched.h`, `ipc.h`
- **Extended 2**: `bpf_struct_ops.h`, `error.h`

Any change requires dual CI (agentrt-linux + agentrt mirror) per OS-IRON-014.

## Issue & PR Templates

- `ISSUE_TEMPLATE/` — structured issue templates for bugs, features and design proposals.
- `PULL_REQUEST_TEMPLATE.md` — enforces the DCO sign-off reminder, subsystem prefix,
  test/SSoT checklist, and `[SC]` change declaration on every pull request.

## Development Guide

- **Trigger a workflow manually** — workflows exposing `workflow_dispatch` can be
  run from the GitHub Actions UI (`nightly.yml`, `release.yml`).
- **Add a new workflow** — keep the 2-job cap; reference the authority document
  `docs/AirymaxOS/70-build-system/03-ci-cd-pipeline.md` in the header comment.
- **Local SSoT validation** before pushing:
  ```bash
  python3 tools/validate-ssot.py docs/AirymaxOS ssot-registry.yaml
  ```
- **Branch protection** — L1/L3 review is enforced via GitHub branch protection
  rules (`require_code_owner_reviews` + 2 required reviewers), not via a CI job.
- **Secrets** — release signing uses `GPG_PRIVATE_KEY`, `GPG_PASSPHRASE`,
  `COSIGN_KEY`, `COSIGN_PASSWORD`; agentrt mirror uses `AGENTRT_CI_TOKEN`. Never
  log or echo these values.

## License

Dual-licensed under **AGPL v3 + Apache 2.0** (SPDX: `AGPL-3.0-or-later OR Apache-2.0`).
See the repository root [LICENSE](../LICENSE) and [NOTICE](../NOTICE).

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
