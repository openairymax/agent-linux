# tools/ — Cross-Submodule CI & Validation Tools

> Cross-submodule tool aggregation for the
> [agentrt-linux (AirymaxOS)](https://atomgit.com/openairymax/agentrt-linux) management repository.

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## Positioning

This directory is the home for cross-submodule tools used by the management
repository's GitHub Actions workflows and by contributors locally. The management
CI pipelines (each ≤ 2 jobs) invoke these scripts to validate the SSoT registry,
aggregate leaf CI status, check governance files, parse TAP output, run nightly
verification, build SBOMs, and orchestrate the `[SC]` dual-CI mirror flow.

## Directory Contents

```
tools/
├── README.md            # This file
└── validate-ssot.py     # SSoT rule-ID consistency validator (implemented)
```

## Tool Catalog

### Implemented

| Tool | Description | Used by |
|------|-------------|---------|
| `validate-ssot.py` | SSoT rule-ID consistency validator — checks that every `OS-*-NNN` (agentrt-linux) and `*-NNN` (agentrt) rule ID referenced in documentation is registered in `ssot-registry.yaml`. Authority: OS-IRON-015. | `ssot-validate.yml` (`ssot-syntax-and-rules`), local contributor runs |

### CI-Referenced Helper Surface

The management workflows reference the following helper scripts (invoked as
`agentrt-linux/tools/<name>.py` or `tools/<name>.py`). They form the CI helper
surface that contributors implement as the corresponding subsystem matures:

| Script | Workflow | Role |
|--------|----------|------|
| `aggregate-subrepo-ci.py` | `mgmt-orchestrator.yml` | Aggregate the CI status of all 8 leaf repositories (`--fail-on-red`) |
| `check-copyright.py` | `mgmt-orchestrator.yml` | Verify the copyright header on line 1 of docs (`--root docs`) |
| `kunit-tap-diff.py` | `ci-kernel.yml` | Diff KUnit TAP output against a baseline (`--baseline origin/develop`) |
| `parse-tap.py` | `ci-kernel.yml` | Parse kselftest TAP output and fail on `not ok` (`--input`, `--fail-on-not-ok`) |
| `formal-verify.py` | `nightly.yml` | seL4-style formal verification of a target source file (`--target`) |
| `soak-runner.py` | `nightly.yml` | Long-running soak test runner (`--duration 72h`, `--workload`) |
| `chaos-runner.py` | `nightly.yml` | Chaos fault-injection runner (`--profile full`) |
| `auto-bisect.py` | `nightly.yml` | Auto-bisect to locate a regression commit (`--baseline last-green`) |
| `create-revert-pr.py` | `nightly.yml` | Create a revert PR for a regression commit (`--regression-commit`) |
| `ci-budget-check.py` | `nightly.yml` | Check total CI time against the budget gate (`--max-minutes 60`, OS-STD-TEST-011) |
| `merge-sbom.py` | `release.yml` | Merge per-submodule SPDX SBOM fragments into one (`--output`, inputs) |
| `create-agentrt-mirror-pr.py` | `sc-dual-ci.yml` | Create a mirror PR in the agentrt repo for `[SC]` header changes (`--patches`, `--source-pr`, `--token`) |
| `await-agentrt-ci.py` | `sc-dual-ci.yml` | Poll the agentrt mirror PR CI status (`--source-pr`, `--timeout`, `--token`) |

## Usage

### SSoT Validation (local)

```bash
# Install dependency
pip install pyyaml

# Run validation (from agentrt-linux management repo root)
python3 tools/validate-ssot.py docs/AirymaxOS ssot-registry.yaml
```

`validate-ssot.py` exit codes:

- `0` — all rule IDs registered (PASS, or only deprecated warnings)
- `1` — one or more unregistered rule IDs found (FAIL)
- `2` — YAML parsing or file error

The validator recognises both `OS-*-NNN` (agentrt-linux) and `*-NNN` (agentrt)
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

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
