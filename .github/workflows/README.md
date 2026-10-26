# `.github/` — GitHub Automation & Templates

> GitHub Actions workflows, issue/PR templates and community health files for the
> [agent-linux (AirymaxOS)](https://github.com/openairymax/agent-linux) management repository.

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## Positioning

This directory holds all repository-level GitHub automation for the agent-linux
(AirymaxOS) management repository. It defines the CI/CD pipelines that validate
governance files, build the kernel, run nightly verification, publish releases,
guard the `[SC]` shared-contract layer, and enforce SSoT (Single Source of
Truth) consistency.

Each workflow stays deliberately small: the kernel, management-orchestration,
image-acceptance and Airymax pipelines use **2 jobs** (`kernel-verify.yml`:
`kernel-build` → `kernel-verify`; `mgmt-verify.yml`: `file-integrity` →
`orchestrate-leaf-ci`; `image-build.yml`: `assemble` → `accept`; `airy-verify.yml`:
`airy-build` → `airy-static`), while the remaining gates are single SSoT drift
checks. Language-level CI for C / Rust / Go / Python is delegated to each leaf
repository's own `.github/workflows/`; this repository only performs
management-level orchestration.

Workflow YAML is a thin dispatch layer: every step either checks out, calls
`ci/ci.sh <gate>`, or uploads an artifact. All assertions and host preparation
live in `ci/` (see below), so the same command can be replayed locally against
a leftover build directory instead of waiting for a cloud job.

## Directory Contents

```text
.github/
├── README.md                    # This file
├── workflows/                   # 13 GitHub Actions workflows
│   ├── kernel-verify.yml        # Kernel build matrix + static analysis + tests
│   ├── airy-verify.yml          # Airymax build (4 arches) + checkpatch + SPDX + symbol integrity
│   ├── abi-verify.yml           # syscall ABI four-source + baseline snapshot check
│   ├── dsl-verify.yml           # Policy DSL (.adl → C tables) zero-drift gate
│   ├── farm-verify.yml          # QEMU test-farm unit and end-to-end tests
│   ├── os-chk-verify.yml        # OS-CHK unified gate orchestration (P0/P1 blocking)
│   ├── codegen-verify.yml       # Codegen SSoT drift gate (contract sources → generated outputs)
│   ├── mgmt-verify.yml          # Governance file integrity + leaf CI orchestration
│   ├── image-build.yml          # Desktop ISO assembly + install/cold-boot acceptance
│   ├── nightly.yml              # Nightly test suite + auto-revert/budget check
│   ├── release.yml              # Build + SBOM + sign + publish release
│   ├── sc-verify.yml            # [SC] header validation + agentrt mirror CI
│   └── ssot-verify.yml          # SSoT YAML syntax + cross-reference validation
├── ISSUE_TEMPLATE/              # GitHub issue templates
└── PULL_REQUEST_TEMPLATE.md     # Pull request template
```

## CI Workflows (13 workflows)

| Workflow | Jobs | Trigger |
|----------|------|---------|
| `kernel-verify.yml` | `kernel-build` — Kbuild matrix (4 configs × 4 arches × 2 compilers − 4 excluded cells = 28, `max-parallel: 8`) with `W=2` zero-warning gate; `kernel-verify` — checkpatch `--strict`, sparse `C=2`, Coccinelle, KUnit on UML, kselftest on QEMU | PR / push on `kernel/**` |
| `airy-verify.yml` | `airy-build` — `airy_defconfig` build matrix on 4 arches (x86_64/arm64/riscv/loongarch) + Airymax CONFIG gate + BTF embedded + `.o` artifact check + symbol-clash check between `security/airy/` and `kernel/superv/`; `airy-static` — checkpatch `--strict` plus the Airymax extension rules, SPDX identifier audit (`[SC]` = BSD-3-Clause OR GPL-2.0, else GPL-2.0-only), and SSoT manifest checks (defconfig matrix / BTF / Kbuild version injection) | PR / push on Airymax paths |
| `abi-verify.yml` | `abi-verify` — `abi-check` unit tests plus four-source consistency (SSoT registry / UAPI `unistd.h` / `syscall_64.tbl` / `[SC]` `syscalls.h`) against `abi_baseline.json` (OS-IRON-001) | PR / push on `tools/abi-check/**`, ABI sources |
| `dsl-verify.yml` | `dsl-verify` — `tools/dsl` unit tests plus `dsl_compile.py --check` content compare, proving `.adl` contract sources and generated C tables stay in sync (IRON-9) | PR / push on `tools/dsl/**`, `tools/codegen/**` |
| `farm-verify.yml` | `farm-verify` — QEMU test-farm unit and end-to-end pytest suite; executors are substituted so no host KVM or real QEMU is required | PR / push on `tools/airy-qemu/**` |
| `os-chk-verify.yml` | `os-chk-verify` — resolve `gates.yaml`, dispatch registered OS-CHK executors, aggregate reports and block on P0/P1 findings | PR / push on `tools/**` executors and their audited objects |
| `codegen-verify.yml` | `codegen-diff-check` — run `tools/codegen/run_codegen.py --check` (content compare, works across submodules) as the zero-drift SSoT gate (IRON-9) | PR / push on `tools/codegen/**`, `sdk/**`, `kernel`, `security` |
| `mgmt-verify.yml` | `file-integrity` — verify submodule dirs derived from `.gitmodules` (9 entries), `[SC]` 12 shared headers, 12 governance files, no `airymaxos-` prefix; `orchestrate-leaf-ci` — aggregate leaf CI statuses, markdownlint repository-owned markdown, copyright header check | PR on `**.md`, `.gitmodules`, `ssot-registry.yaml`, `MAINTAINERS`, `CODEOWNERS`, `tools/**`, `.github/**` / push on `main` |
| `image-build.yml` | `assemble` — build the desktop ISO from a pinned openEuler netinst base inside a privileged official container, then verify the assembly manifest and ISO checksum; `accept` — install the ISO in QEMU under BIOS and UEFI, cold-boot to `AIRYMAX-DESKTOP-READY` (or `AIRYMAX-SMOKE-PASS` with `acceptance=true`), publish TAP and serial logs. Both jobs are 12 one-line `ci/ci.sh` calls; all logic lives in `ci/gates/` | `workflow_dispatch` (release gate, not a PR gate) |
| `nightly.yml` | `nightly-test-suite` — seL4-style formal verification, 72h soak test, chaos injection (CPU hotplug / mem hotremove / I/O error / net partition); `nightly-revert-or-budget` — auto-revert on regression via `auto-bisect`, or CI budget check (60 min steady, 90 min kernel cold-cache fallback, OS-STD-TEST-011) | cron `0 2 * * *` / `workflow_dispatch` |
| `release.yml` | `build-and-sign` — SPDX SBOM (syft per submodule + merge), kernel RPM build, GPG + cosign signing, SDK tarball; `publish-release` — publish dnf repo, push OCI image, create GitHub Release with artifacts | tag `v*` / `v*-rc*` / `workflow_dispatch` |
| `sc-verify.yml` | `sc-validate` — verify `[SC]` 12 shared headers exist under `kernel/include/uapi/linux/airymax/`, no physical duplicates (OS-IRON-014); `sc-trigger-and-await` — export header patches, create mirror PR in agentrt repo, poll mirror CI status (30 min timeout) | PR touching any `[SC]` header |
| `ssot-verify.yml` | `ssot-syntax-and-rules` — PyYAML syntax check, rule-ID count + uniqueness (OS-IRON-015), `validate-ssot.py` cross-ref; `doc-links` — repository-owned relative-path markdown link integrity | PR on `**.md`, `ssot-registry.yaml`, `tools/validate-ssot.py` / push on `main` |

## Naming & Structure Conventions

Every workflow obeys one naming scheme so that the Actions list, `gh workflow
run`, and this document can never disagree. The rules are enforced by
`ci/check-workflows.py` (rule 5), which runs from `ci/lint.sh` in
`os-chk-verify.yml` — a convention that is not checked is not a convention.

| Aspect | Rule | Rationale |
|--------|------|-----------|
| File name | kebab-case `^[a-z0-9]+(-[a-z0-9]+)*\.yml$` | Case- and locale-insensitive on every filesystem; sorts predictably |
| `name:` | Identical to the file stem | The UI label and the file path are one single source of truth |
| `-verify` suffix | Reserved for gating workflows (the 10 SSoT/build gates) | A reader immediately knows the workflow fails the build on violation |
| Lifecycle names | `image-build` / `nightly` / `release` | Named after the lifecycle stage they own, not the tool they call |
| Payload | YAML is a thin dispatch layer — checkout, `ci/ci.sh <gate>`, upload | All assertions live in `ci/`, replayable locally |
| Job count | 2 capped per workflow | Keeps the DAG readable and the critical path short |
| Inline `run` budget | Ratcheted in `ci/workflow-budgets.txt` | Non-increasing cap prevents shell logic from creeping back into YAML |

Adding or renaming a workflow therefore requires three coordinated edits: the
file, its `name:`, and `ci/workflow-budgets.txt`; `./ci/lint.sh` rejects the
change if any of them drift apart.

## `[SC]` Shared-Contract Headers

The `sc-verify.yml` and `mgmt-verify.yml` workflows guard the `[SC]` layer —
the single physical source of truth for the kernel↔agentrt ABI, located at
`kernel/include/uapi/linux/airymax/`:

**12 [SC] headers** (per OS-IRON-014) — 10 core plus 2 supplementary:
`error.h`, `log_types.h`, `ipc.h`, `sched.h`, `memory_types.h`, `security_types.h`, `cognition_types.h`, `syscalls.h`, `syscall_gen.h`, `uapi_compat.h`, `lsm_types.h`, `bpf_struct_ops.h`

Any change to a `[SC]` header requires dual CI (agent-linux + agentrt
mirror) per OS-IRON-014.

## Issue & PR Templates

- `ISSUE_TEMPLATE/` — structured issue templates for bugs, features and design proposals.
- `PULL_REQUEST_TEMPLATE.md` — enforces the DCO sign-off reminder, subsystem prefix,
  test/SSoT checklist, and `[SC]` change declaration on every pull request.

## Development Guide

- **Trigger a workflow manually** — workflows exposing `workflow_dispatch` can be
  run from the GitHub Actions UI (`nightly.yml`, `release.yml`, `image-build.yml`).
  For `image-build.yml`, set `acceptance: true` to build the acceptance ISO and
  assert the in-session `AIRYMAX-SMOKE-PASS` marker.
- **Add a new workflow** — keep the 2-job cap, and do not put shell logic in the
  YAML. Add a gate under `ci/gates/` with a `# 用法:` header, dispatch it from
  `ci/ci.sh`, register its inline-block budget in `ci/workflow-budgets.txt`, then
  run `./ci/lint.sh`. The authority document is
  `docs/docs-linux/70-build-system/03-ci-cd-pipeline.md` in the **hub repository**
  (`airymaxhub/docs/`), not in this one.
- **Replay a workflow locally** — every gate is a plain script, so the same
  assertions can be run against a leftover build directory:

  ```bash
  ./ci/ci.sh --list                       # list gates and their usage
  ./ci/ci.sh image-verify --out <dir>     # re-check an existing image-build output
  ACCEPTANCE=true ./ci/ci.sh tap-extract --dir <dir>
  ```

- **Local SSoT validation** before pushing:

  ```bash
  python3 tools/validate-ssot.py . ssot-registry.yaml
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
