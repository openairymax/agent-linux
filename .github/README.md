# `.github/` — GitHub Automation & Templates

> Repository-level GitHub automation for the
> [agent-linux (AirymaxOS)](https://github.com/openairymax/agent-linux) management repository.

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## Directory Contents

```text
.github/
├── README.md                    # This file: layout and conventions
├── workflows/                   # 13 GitHub Actions workflows
│   └── README.md                # Trigger / job matrix — the workflow SSoT
├── ISSUE_TEMPLATE/              # Bug / feature / design issue templates
└── PULL_REQUEST_TEMPLATE.md     # DCO, subsystem prefix, [SC] declaration
```

## Authority

The per-workflow trigger and job matrix is documented **once**, in
[`workflows/README.md`](workflows/README.md). This file deliberately does not
repeat it: the previous duplicate table drifted from the YAML and reported a
31-cell matrix long after the manifest settled on 28.

| Question | Authority |
|----------|-----------|
| What does a workflow trigger on, and which jobs does it run? | [`workflows/README.md`](workflows/README.md) |
| Which matrix cells, exclusions and timeouts are legal? | `tools/ci-matrix/manifest.yaml` (checked by `tools/ci-matrix/ci_matrix.py --check`) |
| How many inline `run:` blocks may a workflow keep? | `ci/workflow-budgets.txt` (ratchet, checked by `ci/check-workflows.py`) |
| What must a gate assert, and how is it replayed locally? | `ci/gates/*.sh` + `ci/ci.sh --list` |
| Why the pipeline is shaped this way | `docs/docs-linux/70-build-system/03-ci-cd-pipeline.md` in the hub repository |

## Conventions

- **File name** — kebab-case `^[a-z0-9]+(-[a-z0-9]+)*\.yml$`; `name:` must equal the file stem.
- **Suffix** — `-verify` marks a blocking gate; `image-build` / `nightly` / `release` are named after the lifecycle stage they own.
- **Payload** — YAML stays a thin dispatch layer: checkout → `ci/ci.sh <gate>` → upload.
- **Job count** — 2 per workflow.
- **Branch protection** — L1/L3 review via GitHub rules (`require_code_owner_reviews`, 2 reviewers), not a CI job.
- **Secrets** — `GPG_PRIVATE_KEY`, `GPG_PASSPHRASE`, `COSIGN_KEY`, `COSIGN_PASSWORD`, `AGENTRT_CI_TOKEN`. Never echo them.

Adding or renaming a workflow needs three coordinated edits: the file, its
`name:`, and `ci/workflow-budgets.txt`; `./ci/lint.sh` rejects drift.

## License

Dual-licensed under **AGPL v3 + Apache 2.0** (SPDX: `AGPL-3.0-or-later OR Apache-2.0`).
See the repository root [LICENSE](../LICENSE) and [NOTICE](../NOTICE).
