#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_compile 单元测试 — 端到端编译编排、零漂移校验与生成头可用性。

覆盖 tools/dsl/dsl_compile.py：契约登记、render_outputs 目标映射与确定性、
--check 零漂移校验、缺失契约源的错误处理、真实示例 IR 装载，以及生成头
经 gcc（-std=c11 -Wall -Wextra -Werror -pedantic）编译与 first-match 行为
验证。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from codegen_types import ExitCode
from dsl_compile import CONTRACTS, check, compile_text, load_program_ir, main, render_outputs
from dsl_types import DslSourceError

_GCC = shutil.which("gcc")

_SRC = """version "1.0.1";
table uring_guard {
\thook = uring_cmd;
\tinput op : u8;
\tinput cmd_flags : u32;
\trule 10 { when op == 31; verdict DENY; fault 0x100A; }
\trule 20 { when cmd_flags & 0x40000000; verdict AUDIT; }
\tdefault verdict ALLOW;
}
"""

_EXAMPLE_SOURCE = Path("tools") / "dsl" / "examples" / "security_policy.adl"
_EXAMPLE_OUTPUT = Path("tools") / "dsl" / "examples" / "security_policy_tables.h"


class TestCompileText:
    def test_inline_source_renders_header(self) -> None:
        header = compile_text(_SRC, "policy.adl")
        assert "airy_policy_uring_guard_eval" in header
        assert "#ifndef AIRY_GENERATED_POLICY_POLICY_H" in header


class TestContracts:
    def test_single_contract_registered(self) -> None:
        assert CONTRACTS == ((_EXAMPLE_SOURCE, _EXAMPLE_OUTPUT),)

    def test_render_outputs_target_paths(self, repo_root: Path) -> None:
        rendered = render_outputs(repo_root)
        assert set(rendered) == {repo_root / rel for _src, rel in CONTRACTS}

    def test_render_outputs_is_deterministic(self, repo_root: Path) -> None:
        assert render_outputs(repo_root) == render_outputs(repo_root)

    def test_check_matches_committed(self, repo_root: Path) -> None:
        assert check(repo_root) == int(ExitCode.SUCCESS)

    def test_missing_source_raises_source_error(self, tmp_path: Path) -> None:
        with pytest.raises(DslSourceError, match="not found"):
            render_outputs(tmp_path)


class TestRealExample:
    def test_example_ir_tables(self, repo_root: Path) -> None:
        ir = load_program_ir(repo_root / _EXAMPLE_SOURCE, _EXAMPLE_SOURCE.as_posix())
        assert [table.name for table in ir.tables] == [
            "uring_guard",
            "task_guard",
            "file_open_guard",
        ]
        assert ir.source_name.endswith("security_policy.adl")


class TestMainCli:
    def test_main_check_success(self, repo_root: Path) -> None:
        assert main(["--repo-root", str(repo_root), "--check"]) == int(ExitCode.SUCCESS)

    def test_main_check_failure(self, tmp_path: Path) -> None:
        assert main(["--repo-root", str(tmp_path), "--check"]) == int(ExitCode.FAILURE)


@pytest.mark.skipif(_GCC is None, reason="gcc not available")
class TestGeneratedHeader:
    def _write_header(self, repo_root: Path, tmp_path: Path) -> Path:
        rendered = render_outputs(repo_root)
        header = rendered[repo_root / _EXAMPLE_OUTPUT]
        header_path = tmp_path / "security_policy_tables.h"
        header_path.write_text(header, encoding="utf-8")
        return header_path

    def test_compiles_clean(self, repo_root: Path, tmp_path: Path) -> None:
        assert _GCC is not None
        self._write_header(repo_root, tmp_path)
        source = tmp_path / "syntax.c"
        source.write_text(
            '#include "security_policy_tables.h"\n'
            "int main(void)\n"
            "{\n"
            "\t__u64 fields[2] = {0, 0};\n"
            "\treturn (int)airy_policy_uring_guard_eval(fields);\n"
            "}\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [
                _GCC,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-pedantic",
                "-fsyntax-only",
                "-I",
                str(tmp_path),
                str(source),
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr

    def test_first_match_behaviour(self, repo_root: Path, tmp_path: Path) -> None:
        assert _GCC is not None
        self._write_header(repo_root, tmp_path)
        source = tmp_path / "run.c"
        source.write_text(
            """
#include <stdio.h>
#include "security_policy_tables.h"

int main(void)
{
\t__u64 deny[2] = {31, 0};
\t__u64 audit[2] = {0, 0x40000000};
\t__u64 allow[2] = {0, 0};

\tprintf("%u %u %u\\n",
\t       airy_policy_uring_guard_eval(deny),
\t       airy_policy_uring_guard_eval(audit),
\t       airy_policy_uring_guard_eval(allow));
\treturn 0;
}
""",
            encoding="utf-8",
        )
        exe = tmp_path / "run"
        compiled = subprocess.run(
            [
                _GCC,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-pedantic",
                "-I",
                str(tmp_path),
                str(source),
                "-o",
                str(exe),
            ],
            capture_output=True,
            text=True,
        )
        assert compiled.returncode == 0, compiled.stderr
        result = subprocess.run([str(exe)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "1 2 0"
