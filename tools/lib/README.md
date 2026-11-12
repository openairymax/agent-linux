<!-- SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0 -->

# tools/lib — Cross-tool shared layer

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

This directory is **not** a CLI tool. It holds the code that several tool
directories must share for their outputs to stay comparable; `tools/airy`
does not dispatch anything here.

| File | Purpose |
|---|---|
| `check_report.py` | `CheckResult` / `ManifestError` / `lead` / `print_report`: the result type and the report contract used by `btf-integration`, `kbuild-version`, `ci-matrix`, `defconfig-matrix`, `nightly`, and `repo-scaffold`. |
| `manifest_io.py` | `find_repo_root` / `load_manifest` / `Gate` / `open_gate` / `run_gate`: the shared CLI preamble (repo-root probe, PyYAML/kernel-tree checks, manifest path resolution and load errors) and the exit-code policy used by the same six tools. |

## Why it exists

Each of those six tools grew its own copy of the same three-field result
type and the same "count → print → summarise" routine. The copies had
already drifted in punctuation, which means a reader of CI logs has to
know *which* tool produced a line before trusting its shape. Report format
is an external contract, so it lives in exactly one place.

Business logic stays in the tool directories; only the contract is here.

## Import convention

Tools are executed by path (`tools/airy <cmd>`), so the script directory is
`sys.path[0]`. A tool that needs this layer prepends it explicitly:

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from check_report import CheckResult, print_report
```

Tests import the tool module by name and inherit the same path, so both
entry points see the same module instance.
