Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

# tools/ — Cross-Submodule CI & Validation Tools

> Cross-submodule tool aggregation for the
> [agent-linux (AirymaxOS)](https://atomgit.com/openairymax/agent-linux) management repository.

---

## Positioning

This directory is the home for cross-submodule tools used by the management
repository's GitHub Actions workflows and by contributors locally. The management
CI pipelines (each ≤ 2 jobs) invoke these scripts to validate the SSoT registry,
aggregate leaf CI status, check governance files, parse TAP output, run nightly
verification, build SBOMs, and orchestrate the `[SC]` dual-CI mirror flow.

## Directory Contents

```text
tools/
├── README.md                    # This file
├── validate-ssot.py             # SSoT rule-ID consistency validator
├── aggregate-subrepo-ci.py      # Leaf-repo CI status aggregator
├── check-copyright.py           # Copyright/SPDX header gate
├── kunit-tap-diff.py            # KUnit KTAP case-count monotonicity gate
├── kunit-tap-baseline.tap       # Committed KUnit KTAP ratchet baseline (OS-TEST-012)
├── parse-tap.py                 # kselftest TAP parser
├── create-agentrt-mirror-pr.py  # agentrt mirror PR creator ([SC] dual CI)
├── await-agentrt-ci.py          # agentrt mirror CI poller
├── codegen/                     # Contract code generator (contracts → outputs)
│   ├── run_codegen.py           # Entry point (`--check` zero-drift gate)
│   ├── cap_gen.py               # Capability-ID generator
│   ├── bitfield_gen.py          # Bitfield generator
│   ├── ipc_gen.py               # IPC contract generator
│   ├── syscall_gen.py           # Syscall generator
│   ├── codegen_io.py            # Shared I/O helpers
│   ├── codegen_types.py         # Shared type definitions
│   ├── templates/               # Jinja2 output templates
│   ├── Makefile                 # Local codegen driver
│   └── requirements.txt         # Python dependencies
├── dsl/                         # Policy DSL (.adl) compiler (IRON-9 decision tables)
│   ├── dsl_compile.py           # Entry point (`--check` zero-drift gate)
│   ├── dsl_types.py             # Tokens/AST/IR/verdicts/compare-ops
│   ├── dsl_lexer.py             # Lexer (comments, strings, dec/hex, two-char ops)
│   ├── dsl_parser.py            # Recursive-descent parser → AST
│   ├── dsl_semantics.py         # AST → IR semantic checks (hooks/types/ranges)
│   ├── dsl_codegen.py           # IR → pure C decision table header
│   ├── templates/               # Jinja2 decision-table template
│   ├── examples/                # .adl contract source + committed generated header
│   ├── tests/                   # pytest suite (lexer/parser/semantics/codegen/compile)
│   └── Makefile                 # Local DSL compiler driver
├── abi-check/                   # Syscall ABI stability checker
│   ├── abi_check.py             # ABI checker (stdlib only)
│   ├── abi_baseline.json        # ABI baseline snapshot
│   └── README.md                # Tool-specific documentation
├── sc-tri-compile/              # [SC] header tri-compile & layout checker
│   ├── sc_tri_compile.py        # Tri-compile + type-size/offset checker (stdlib only)
│   ├── requirements-dev.txt     # Test dependency (pytest)
│   ├── tests/                   # pytest suite (constants/probe/consistency/CLI)
│   └── README.md                # Tool-specific documentation
├── airy-checkpatch/             # AirymaxOS custom checkpatch extension rules
│   ├── requirements-dev.txt     # Test dependency (pytest)
│   ├── tests/                   # pytest suite driving kernel/scripts/airy_checkpatch_ext.pl
│   └── README.md                # Tool-specific documentation
├── defconfig-matrix/            # defconfig build-matrix SSoT + static gate
│   ├── defconfig_matrix.py      # Manifest ↔ defconfig ↔ CI cross-checker (`--check` gate)
│   ├── manifest.yaml            # Single source of truth for arches/configs/LSM/CI matrix
│   ├── requirements-dev.txt     # Runtime + test dependency (pyyaml, pytest)
│   ├── tests/                   # pytest suite (schema/fs/format/configs/ci/kconfig/CLI)
│   └── README.md                # Tool-specific documentation
├── ci-matrix/                   # CI build-matrix SSoT + static gate
│   ├── ci_matrix.py             # Manifest ↔ kernel-verify.yml cross-checker (`--check` gate)
│   ├── manifest.yaml            # Single source of truth for configs/compilers/exclusions/budget/gates
│   ├── requirements-dev.txt     # Runtime + test dependency (pyyaml, pytest)
│   ├── tests/                   # pytest suite (schema/arches/job/cells/schedule/gates/summary/CLI)
│   └── README.md                # Tool-specific documentation
├── btf-integration/             # BTF / eBPF baseline SSoT + static gate
│   ├── btf_integration.py       # Manifest ↔ airy_defconfig ↔ CI cross-checker (`--check` gate)
│   ├── manifest.yaml            # Single source of truth for BPF/BTF/tracing baselines & CI supply
│   ├── requirements-dev.txt     # Runtime + test dependency (pyyaml, pytest)
│   ├── tests/                   # pytest suite (schema/fragments/bpf/btf/tracing/ci/CLI)
│   └── README.md                # Tool-specific documentation
├── kbuild-version/              # Kbuild version-injection SSoT + static gate
│   ├── kbuild_version.py        # Manifest ↔ Makefile.airymaxos ↔ Makefile ↔ contract cross-checker (`--check` gate)
│   ├── manifest.yaml            # Single source of truth for the AIRYMAXOS_* version tuple & injection wiring
│   ├── contract/
│   │   └── version_h.airy.txt   # Committed snapshot of the injected `#define AIRYMAXOS_*` block
│   ├── requirements-dev.txt     # Runtime + test dependency (pyyaml, pytest)
│   ├── tests/                   # pytest suite (schema/guard/tuple/injection/contract/CLI)
│   └── README.md                # Tool-specific documentation
├── nightly/                     # Nightly pipeline contract SSoT + static gate
│   ├── nightly.py               # Manifest ↔ nightly.yml cross-checker (`--check` gate)
│   ├── manifest.yaml            # Single source of truth for triggers/concurrency/jobs/helpers/budget
│   ├── requirements-dev.txt     # Runtime + test dependency (pyyaml, pytest)
│   ├── tests/                   # pytest suite (schema/trigger/flight/perms/jobs/runner/timeout/vars/surface/helpers/budget/CLI)
│   └── README.md                # Tool-specific documentation
├── repo-scaffold/               # Eight-subrepo skeleton SSoT + static gate
│   ├── repo_scaffold.py         # Manifest ↔ .gitmodules ↔ filesystem cross-checker (`--check` gate)
│   ├── manifest.yaml            # Single source of truth for skeletons/licenses/build entries
│   ├── requirements-dev.txt     # Runtime + test dependency (pyyaml, pytest)
│   ├── tests/                   # pytest suite (schema/registry/checkout/skeleton/license/build/entry/report/CLI)
│   └── README.md                # Tool-specific documentation
├── airy-rollback/               # Three-level rollback SSoT + orchestrator
│   ├── airy_rollback.py         # Manifest-driven L1/L2/L3 rollback orchestrator (`check` gate)
│   ├── rollback_types.py        # Frozen data model and exit-code contract
│   ├── manifest.yaml            # Single source of truth for paths/12 daemons/command templates
│   ├── requirements-dev.txt     # Runtime + test dependency (pyyaml, pytest)
│   ├── tests/                   # pytest suite (manifest/snapshot/layer/steps/preflight/apply/prune/CLI)
│   └── README.md                # Tool-specific documentation
├── airy-flash/                  # Reproducible ISO flashing tool
│   ├── airy_flash.py            # Preflight → digest → write → fsync → readback (`plan`/`write`/`verify`/`check`)
│   ├── flash_types.py           # Frozen data model and exit-code contract
│   ├── requirements-dev.txt     # Test dependency (pytest)
│   ├── tests/                   # pytest suite (sysfs/mounts/digest/preflight/write/readback/CLI)
│   └── README.md                # Tool-specific documentation
├── osaccrt/                     # OS-ACC-RT runtime acceptance executor (module V / V-01)
│   ├── osaccrt_model.py         # Verdict/Step/Threshold/Evidence/ExpResult/Judge protocol
│   ├── osaccrt_spec.py          # NFR/OS-ACC threshold table loader (specs/nfr.toml)
│   ├── osaccrt_evidence.py      # Manifest schema and sha256 chain
│   ├── osaccrt_store.py         # Append-only evidence store (create/write/seal/verify)
│   ├── osaccrt_judge.py         # PackJudge four-state decision machine
│   ├── osaccrt_waiver.py        # FAIL → WAIVED dual-sign workflow
│   ├── osaccrt_blocked.py       # BLOCKED pre-registration ledger
│   ├── osaccrt_repro.py         # Offline replay script generator
│   ├── osaccrt_runner.py        # Execution loop with resume and review callback
│   ├── requirements-dev.txt     # Test dependency (pytest)
│   ├── tests/                   # pytest suite
│   └── README.md                # Tool-specific documentation
└── release-engineering/         # Release-engineering tool set (SPDX SBOM + release material gate)
    ├── merge_sbom.py            # Merge per-submodule SPDX 2.3 JSON fragments into one document
    ├── release_manifest.py      # Manifest ↔ release.yml bidirectional gate + checksums + notes (`--check` gate)
    ├── manifest.yaml            # Single source of truth for the 12 release materials + pipeline supply
    ├── requirements-dev.txt     # Runtime + test dependency (pyyaml, pytest)
    ├── tests/                   # pytest suite (sbom merge/dedupe/conflict; manifest schema/pipeline/checksums/notes/CLI)
    └── README.md                # Tool-specific documentation
```

## Tool Catalog

### Implemented

| Tool | Description | Used by |
|------|-------------|---------|
| `validate-ssot.py` | SSoT rule-ID consistency validator — checks that every `OS-*-NNN` (agent-linux) and `*-NNN` (agentrt) rule ID referenced in documentation is registered in `ssot-registry.yaml`. Authority: OS-IRON-015. | `ssot-verify.yml` (`ssot-syntax-and-rules`), local contributor runs |
| `aggregate-subrepo-ci.py` | Aggregate the CI status of all leaf repositories, deriving the list from `.gitmodules` (`--fail-on-red`) | `mgmt-verify.yml` (`orchestrate-leaf-ci`) |
| `check-copyright.py` | Verify that repository-owned `.md` files carry a `Copyright`/`SPDX-License-Identifier` on line 1 (`--root .`) | `mgmt-verify.yml` (`orchestrate-leaf-ci`) |
| `kunit-tap-diff.py` | KUnit KTAP case-count monotonicity gate — parses nested KTAP (`--raw_output=kunit`) and fails when the leaf-case total regresses, a baseline suite disappears, or a new `not ok` appears; the ratchet lives in `kunit-tap-baseline.tap` and is updated explicitly with `--update` (OS-TEST-012) | `kernel-verify.yml` (`kernel-verify`) |
| `parse-tap.py` | Parse kselftest TAP output and fail on `not ok` | `kernel-verify.yml` (`kernel-verify`) |
| `create-agentrt-mirror-pr.py` | Create a mirror PR in the agentrt repository for `[SC]` header changes | `sc-verify.yml` (`sc-trigger-and-await`) |
| `await-agentrt-ci.py` | Poll the agentrt mirror PR CI status to completion | `sc-verify.yml` (`sc-trigger-and-await`) |
| `codegen/run_codegen.py` | Contract code generator; `--check` performs a content compare as the zero-drift SSoT gate (IRON-9) | `os-chk-verify.yml`, `tools/codegen/Makefile` |
| `dsl/dsl_compile.py` | Policy DSL (`.adl`) compiler — lexer/parser/semantics/IR → pure C decision table header; `--check` is the zero-drift gate for policy tables (IRON-9, K-1 mechanism/policy split) | `os-chk-verify.yml`, `tools/dsl/Makefile` |
| `abi-check/abi_check.py` | Syscall ABI stability checker — three-way consistency + baseline immutability (OS-IRON-001) | `kernel-verify.yml` (`kernel-verify`) |
| `sc-tri-compile/sc_tri_compile.py` | `[SC]` header tri-compile & layout checker — compiles every `kernel/include/uapi/linux/airymax/*.h` under kernel / Linux-user / third-party paths and asserts fixed-width type sizes and struct field offsets are identical across all three; `--check` is the CI gate (OS-IRON-014); pytest suite in `tests/` | `sc-verify.yml` (`sc-validate`) |
| `airy-checkpatch/` (integration tests) | AirymaxOS custom checkpatch extension rules (`AIRY-CHECKPATCH-001`~`011`: log interface, naming prefixes, concurrency memory order, IPC entry, LSM registration, DMA usage, config black/white lists, file-header convention). Rule logic lives solely in `kernel/scripts/airy_checkpatch_ext.pl`; this directory holds only the pytest integration suite and docs, so the rules never drift across two copies (OS-TEST-066 / OS-KER-120 / OS-STD-STY-007) | `airy-verify.yml` (`airy-static`), `os-chk-verify.yml`, `tools/os-chk/gates.yaml` (OS-TEST-066) |
| `defconfig-matrix/defconfig_matrix.py` | defconfig build-matrix SSoT checker — reads `manifest.yaml` (the only source of truth for architectures, mandatory configs, LSM entry, and the CI matrix) and verifies manifest↔filesystem consistency, `airy_defconfig` header format, mandatory `CONFIG_*` presence, `CONFIG_LSM` entry, CI build-matrix arch equality, CI `required=()` equality, and Kconfig declaration closure; `--check` is the CI gate (OS-BUILD-026 / OS-BUILD-028) | `airy-verify.yml` (`airy-static`), `os-chk-verify.yml`, `tools/os-chk/gates.yaml` (OS-BUILD-MATRIX) |
| `ci-matrix/ci_matrix.py` | CI build-matrix SSoT checker — reads `manifest.yaml` (the only source of truth for configs, compilers, the loongarch×clang exclusion, the 28-cell expectation, `fail-fast`/`max-parallel`, the 60-minute job budget, the compile-gate tokens, and failure attribution) and verifies manifest schema, architecture source (`defconfig-matrix/manifest.yaml` build entries), workflow/job presence, dimension equality, cell count, exclusion validity, scheduler constraints, timeout budget, job count, and gate-token supply; `--list` expands the expected cells and `--summary` attributes per-cell results to a `ci-fail-*` label; `--check` is the CI gate (OS-STD-TOOL-161 / OS-STD-TEST-011) | `kernel-verify.yml` (`kernel-verify`), `os-chk-verify.yml`, `tools/os-chk/gates.yaml` (OS-BUILD-CI-MATRIX) |
| `btf-integration/btf_integration.py` | BTF / eBPF baseline SSoT checker — reads `manifest.yaml` (the only source of truth for the eBPF baseline, the BTF baseline plus its mutex/DWARF prerequisites, the `BPF_EVENTS` tracing chain, the pahole package, the BTF artifact token, and the workflows that must supply them) and verifies manifest schema, build-entry fragment presence, eBPF baseline, BTF baseline, tracing chain, and CI pahole/artifact supply; `--check` is the CI gate (OS-KER-106 / OS-OBS-012) | `airy-verify.yml` (`airy-static`), `os-chk-verify.yml`, `tools/os-chk/gates.yaml` (OS-BUILD-BTF) |
| `kbuild-version/kbuild_version.py` | Kbuild version-injection SSoT checker — reads `manifest.yaml` (the only source of truth for the `AIRYMAXOS_LTS/MAJOR/MINOR/RELEASE` version tuple, the `kernel/Makefile.airymaxos` file, the top-level `kernel/Makefile` wiring, and the committed `contract/version_h.airy.txt` snapshot) and verifies schema, version-file presence, tuple equality, the "version variables only" rule (no feature switches in `Makefile.airymaxos`), the `include $(srctree)/Makefile.airymaxos` directive, the `filechk_version.h` `#define AIRYMAXOS_*` injection, and the contract snapshot; `--check` is the CI gate (OS-STD-049 / OS-BUILD-010) | `airy-verify.yml` (`airy-static`), `os-chk-verify.yml`, `tools/os-chk/gates.yaml` (OS-BUILD-KBUILD-VERSION) |
| `nightly/nightly.py` | Nightly pipeline contract SSoT checker — reads `manifest.yaml` (the only source of truth for triggers, concurrency, permissions, job topology, timeouts, helper calls, and the 60-minute CI budget) and verifies manifest schema, cron/`workflow_dispatch`-only triggers, single-flight concurrency, read-only permissions, job/`needs` topology, runner label and hosted timeout ceiling, shell variable definitions, helper-surface registration, deferred-helper guards plus `SKIP:` markers (and their removal once a script lands), and the `--max-minutes 60` budget call; `--check` is the CI gate (OS-STD-TEST-011 / §6.1 / §9.2) | `nightly.yml` (`nightly-test-suite`), `os-chk-verify.yml`, `tools/os-chk/gates.yaml` (OS-BUILD-NIGHTLY) |
| `repo-scaffold/repo_scaffold.py` | Eight-subrepo skeleton SSoT checker — reads `manifest.yaml` (the only source of truth for the eight subrepositories, their common skeleton profiles, license markers, and build entries) and verifies manifest schema, `.gitmodules` registration, checkout presence, non-empty skeleton files, license markers, non-empty build entries, and that CMake/Kbuild entries reference real non-empty sources (decision F1); the kernel fork is modelled with its native upstream layout plus an entry probe; `--check` is the CI gate (07-directory-structure.md §3.6 / Appendix A) | `os-chk-verify.yml`, `tools/os-chk/gates.yaml` (OS-BUILD-REPO-SCAFFOLD) |
| `airy-rollback/airy_rollback.py` | Three-level rollback orchestrator — reads `manifest.yaml` (the only source of truth for paths, the 12 daemon units, and every external command template) and implements `check`/`plan`/`apply`/`verify`/`prune`: snapshot selection, L1/L2/L3 layer decision, preflight checks (snapshot, pending-upgrade flag, memory-volume independence, btrfs source), first-failure-short-circuit step execution, post-rollback 12-daemon verification, 7-day snapshot pruning, flock mutual exclusion, and append-only JSONL audit; `check` is the CI gate (01-deployment.md §11, OS-OPS-021/022/023/036) | `os-chk-verify.yml`, `tools/os-chk/gates.yaml` (OS-OPS-ROLLBACK) |
| `airy-flash/airy_flash.py` | Reproducible ISO flasher — reads `/proc/mounts` plus sysfs to prove the target is a whole block device and not the disk backing `/`, `/boot`, `/boot/efi`, `/usr`, or `/var` (partition nodes resolve to their parent disk, `/dev/mapper/*` to `/dev/dm-N`), then verifies the image digest from `--sha256`/sidecar/`SHA256SUMS`/`--sums`, writes in 4 MiB chunks, syncs in a standalone stage, and reads back either three 16 MiB windows or the full image while reporting the first mismatching offset; emits a seven-section report, `--json`/`--report` records, and a trailing `AIRY-FLASH-PASS` or `AIRY-FLASH-FAIL` marker; the flashing plan and post-install checks live in `README.md` (01-deployment.md has no flashing chapter, so the tool README is the authority) | `os-chk-verify.yml` (pytest suite), release media preparation |
| `osaccrt/` (module V / V-01) | OS-ACC-RT runtime acceptance executor — the single entry point for the six-step acceptance procedure: threshold table loading with source-backed limits (`specs/nfr.toml`), append-only evidence store (`<date>/<exp-id>/`) with a rolling sha256 chain, four-state judgement (`PASS`/`FAIL`/`WAIVED`/`BLOCKED`) from the evidence pack alone, dual-sign waivers, pre-registered BLOCKED degradation ledger, offline replay-script generation, and a resumable execution loop with a review callback; the four verdict paths are pinned by `tests/test_v01_judge.py` (docs-closed acceptance task card §3) | `os-chk-verify.yml` (`os-chk-unit-tests`) |
| `release-engineering/merge_sbom.py` | Merge per-submodule SPDX 2.3 JSON SBOM fragments into one `agent-linux` document — unions `packages`/`files`/`relationships`/`externalDocumentRefs`/`hasExtractedLicensingInfos` by their identity key, fails on conflicting duplicates, pins the document-level fields (`SPDX-2.3` / `CC0-1.0` / `SPDXRef-DOCUMENT`), and derives `created` from `SOURCE_DATE_EPOCH` for byte-deterministic output; `--output` + fragments (`0`/`1`/`2` exit codes) | `release.yml` (`build-and-sign`) |
| `release-engineering/release_manifest.py` | Release-material SSoT checker & generator — reads `manifest.yaml` (the only source of truth for the 12 release materials, their formats/signatures/authorities/delivery state) and verifies manifest schema plus **bidirectional** manifest↔`release.yml` consistency (every `active` material's `evidence` must be declared by the pipeline, and every pipeline artifact glob must be registered); also emits `sha256sum`-compatible checksums (`--checksums`) and the three-section release notes (`--notes`); `--check` is the CI gate (OS-BUILD-RELEASE) | `os-chk-verify.yml`, `tools/os-chk/gates.yaml` (OS-BUILD-RELEASE); `release.yml` (`build-and-sign`) via checksums/notes |

### CI-Referenced Helper Surface

The management workflows reference the following helper scripts that are not yet
implemented. They form the remaining CI helper surface, invoked behind an
existence guard (`if [ -f ... ]`) so the pipelines stay green while the
corresponding subsystem matures:

| Script | Workflow | Intended role |
|--------|----------|---------------|
| `formal-verify.py` | `nightly.yml` | seL4-style formal verification of a target source file (`--target`) |
| `soak-runner.py` | `nightly.yml` | Long-running soak test runner (`--duration 72h`, `--workload`) |
| `chaos-runner.py` | `nightly.yml` | Chaos fault-injection runner (`--profile full`) |
| `auto-bisect.py` | `nightly.yml` | Auto-bisect to locate a regression commit (`--baseline last-green`) |
| `create-revert-pr.py` | `nightly.yml` | Create a revert PR for a regression commit (`--regression-commit`) |
| `ci-budget-check.py` | `nightly.yml` | Check total CI time against the budget gate (`--max-minutes 60`, OS-STD-TEST-011) |

## Usage

### SSoT Validation (local)

```bash
# Install dependency
pip install pyyaml

# Run validation (from agent-linux management repo root)
python3 tools/validate-ssot.py . ssot-registry.yaml
```

`validate-ssot.py` exit codes:

- `0` — all rule IDs registered (PASS, or only deprecated warnings)
- `1` — one or more unregistered rule IDs found (FAIL)
- `2` — YAML parsing or file error

The validator recognises both `OS-*-NNN` (agent-linux) and `*-NNN` (agentrt)
rule-ID patterns, including range forms (`OS-TEST-001~012`) and suffix variants
(`-SP1`, `OS1`). Deprecated IDs are retained but flagged.

## Development Guide

- New cross-submodule tools belong in this directory. Each tool must:
  1. Include a `--help` flag and a module docstring.
  2. Return proper exit codes (`0` success, `1` content failure, `2` input/parse error).
  3. Be referenced in this README and in the relevant workflow.
- Python tools follow PEP 8 (enforced by `ruff`); shell helpers pass `shellcheck`.
- Tools that consume CI secrets (`AGENTRT_CI_TOKEN`, `COSIGN_KEY`, `GPG_*`,
  `DNF_REPO_HOST`) must read them from the environment and never log or echo them.
- Keep the management-repo 2-job-per-workflow cap when wiring a tool into CI.

## License

Dual-licensed under **AGPL v3 + Apache 2.0** (SPDX: `AGPL-3.0-or-later OR Apache-2.0`).
See the repository root [LICENSE](../LICENSE) and [NOTICE](../NOTICE).
