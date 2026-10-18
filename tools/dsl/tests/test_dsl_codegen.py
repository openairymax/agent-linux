#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_codegen 单元测试 — IR 到纯 C 决策表的渲染上下文与产物。

覆盖 tools/dsl/dsl_codegen.py：模板上下文构造（生成器标识、按源文件名
派生的 include guard、算子表、逐表策略数据）、无符号字面量渲染策略、
渲染确定性与模板缺失时的错误包装。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

from pathlib import Path

import pytest

import codegen_io
from dsl_codegen import build_context, render_program
from dsl_parser import parse
from dsl_semantics import to_ir
from dsl_types import CompareOp, DslRenderError, ProgramIR

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


def _program_ir(name: str = "policy.adl") -> ProgramIR:
    return to_ir(parse(_SRC, name), name)


class TestBuildContext:
    def test_context_shape(self) -> None:
        context = build_context(_program_ir())
        assert set(context) == {
            "generator",
            "generator_version",
            "dsl_version",
            "source_name",
            "source_path",
            "header_guard",
            "compare_ops",
            "tables",
        }
        assert context["generator"] == "dsl_compile"
        assert context["dsl_version"] == "1.0.1"

    def test_header_guard_derives_from_source_name(self) -> None:
        context = build_context(_program_ir("security_policy.adl"))
        assert context["header_guard"] == "AIRY_GENERATED_POLICY_SECURITY_POLICY_H"

    def test_compare_ops_cover_every_code(self) -> None:
        ops = build_context(_program_ir())["compare_ops"]
        assert [op["code"] for op in ops] == [0, 1, 2, 3, 4, 5, 6]
        assert [op["macro"] for op in ops] == [item.c_macro for item in CompareOp]

    def test_table_subcontext(self) -> None:
        table = build_context(_program_ir())["tables"][0]
        assert table["prefix"] == "airy_policy_uring_guard"
        assert table["upper_name"] == "URING_GUARD"
        assert table["hook"] == "uring_cmd"
        assert table["field_names"] == ["op", "cmd_flags"]
        assert table["row_count"] == 2
        assert table["default_verdict_value"] == 0

    def test_row_subcontexts(self) -> None:
        rows = build_context(_program_ir())["tables"][0]["rows"]
        assert rows[0]["number"] == 10
        assert rows[0]["cond_count"] == 1
        assert rows[0]["verdict_value"] == 1
        assert rows[0]["fault_literal"] == "0x100A"
        assert rows[0]["conds"][0]["op_macro"] == "AIRY_CMP_EQ"
        assert rows[0]["conds"][0]["value_literal"] == "0x1F"
        assert rows[1]["fault_literal"] == "0"
        assert rows[1]["conds"][0]["op_macro"] == "AIRY_CMP_MASK"
        assert rows[1]["conds"][0]["value_literal"] == "0x40000000"

    def test_small_values_rendered_as_decimal(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = file_open; input a : u8; '
            'rule 1 { when a == 9; verdict DENY; } '
            'rule 2 { when a == 10; verdict AUDIT; } default verdict ALLOW; }'
        )
        rows = build_context(to_ir(parse(source, "t.adl"), "t.adl"))["tables"][0]["rows"]
        assert rows[0]["conds"][0]["value_literal"] == "9"
        assert rows[1]["conds"][0]["value_literal"] == "0xA"


class TestRenderProgram:
    def test_header_carries_mechanism_and_policy(self) -> None:
        header = render_program(_program_ir())
        assert header.startswith(
            "/* SPDX-License-Identifier: GPL-2.0-only WITH Linux-syscall-note */"
        )
        assert "#ifndef AIRY_GENERATED_POLICY_POLICY_H" in header
        assert "#ifndef AIRY_POLICY_MECHANISM_DEFINED" in header
        assert "#include <airymax/airy_policy.h>" in header
        assert "#define AIRY_POLICY_URING_GUARD_ROW_COUNT 2" in header
        assert "static inline __u8 airy_policy_uring_guard_eval(const __u64 *fields)" in header
        assert header.rstrip().endswith("#endif /* AIRY_GENERATED_POLICY_POLICY_H */")

    def test_unused_macro_outside_mechanism_guard(self) -> None:
        header = render_program(_program_ir())
        assert header.index("#define AIRY_POLICY_UNUSED") < header.index(
            "#ifndef AIRY_POLICY_MECHANISM_DEFINED"
        )

    def test_render_is_deterministic(self) -> None:
        assert render_program(_program_ir()) == render_program(_program_ir())

    def test_missing_template_wrapped_as_render_error(self, tmp_path: Path) -> None:
        env = codegen_io.make_env(tmp_path)
        with pytest.raises(DslRenderError):
            render_program(_program_ir(), env)
