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
└── sc-tri-compile/              # [SC] header tri-compile & layout checker
    ├── sc_tri_compile.py        # Tri-compile + type-size/offset checker (stdlib only)
    ├── requirements-dev.txt     # Test dependency (pytest)
    ├── tests/                   # pytest suite (constants/probe/consistency/CLI)
    └── README.md                # Tool-specific documentation
```

## Tool Catalog

### Implemented

| Tool | Description | Used by |
|------|-------------|---------|
| `validate-ssot.py` | SSoT rule-ID consistency validator — checks that every `OS-*-NNN` (agent-linux) and `*-NNN` (agentrt) rule ID referenced in documentation is registered in `ssot-registry.yaml`. Authority: OS-IRON-015. | `ssot-validate.yml` (`ssot-syntax-and-rules`), local contributor runs |
| `aggregate-subrepo-ci.py` | Aggregate the CI status of all leaf repositories, deriving the list from `.gitmodules` (`--fail-on-red`) | `mgmt-orchestrator.yml` (`orchestrate-leaf-ci`) |
| `check-copyright.py` | Verify that repository-owned `.md` files carry a `Copyright`/`SPDX-License-Identifier` on line 1 (`--root .`) | `mgmt-orchestrator.yml` (`orchestrate-leaf-ci`) |
| `kunit-tap-diff.py` | KUnit KTAP case-count monotonicity gate — parses nested KTAP (`--raw_output=kunit`) and fails when the leaf-case total regresses, a baseline suite disappears, or a new `not ok` appears; the ratchet lives in `kunit-tap-baseline.tap` and is updated explicitly with `--update` (OS-TEST-012) | `ci-kernel.yml` (`kernel-verify`) |
| `parse-tap.py` | Parse kselftest TAP output and fail on `not ok` | `ci-kernel.yml` (`kernel-verify`) |
| `create-agentrt-mirror-pr.py` | Create a mirror PR in the agentrt repository for `[SC]` header changes | `sc-dual-ci.yml` (`sc-trigger-and-await`) |
| `await-agentrt-ci.py` | Poll the agentrt mirror PR CI status to completion | `sc-dual-ci.yml` (`sc-trigger-and-await`) |
| `codegen/run_codegen.py` | Contract code generator; `--check` performs a content compare as the zero-drift SSoT gate (IRON-9) | `codegen-verify.yml`, `tools/codegen/Makefile` |
| `dsl/dsl_compile.py` | Policy DSL (`.adl`) compiler — lexer/parser/semantics/IR → pure C decision table header; `--check` is the zero-drift gate for policy tables (IRON-9, K-1 mechanism/policy split) | `dsl-verify.yml`, `tools/dsl/Makefile` |
| `abi-check/abi_check.py` | Syscall ABI stability checker — three-way consistency + baseline immutability (OS-IRON-001) | `ci-kernel.yml` (`kernel-verify`) |
| `sc-tri-compile/sc_tri_compile.py` | `[SC]` header tri-compile & layout checker — compiles every `kernel/include/uapi/linux/airymax/*.h` under kernel / Linux-user / third-party paths and asserts fixed-width type sizes and struct field offsets are identical across all three; `--check` is the CI gate (OS-IRON-014); pytest suite in `tests/` | `sc-dual-ci.yml` (`sc-validate`) |

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
| `merge-sbom.py` | `release.yml` | Merge per-submodule SPDX SBOM fragments into one (`--output`, inputs) |

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
