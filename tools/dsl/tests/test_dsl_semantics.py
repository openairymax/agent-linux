#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_semantics 单元测试 — .adl 语义校验与 IR 构造。

覆盖 tools/dsl/dsl_semantics.py：版本一致性、表名/字段/规则编号唯一性、
钩子成员校验、字段引用与字面量范围校验、裁决-故障一致性校验，以及
行按规则编号升序（first-match）的降级结果。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import pytest

from dsl_parser import parse
from dsl_semantics import to_ir
from dsl_types import CompareOp, DslSemanticError, ProgramIR, Verdict

_MULTI_RULE = """version "1.0.1";
table t {
\thook = uring_cmd;
\tinput a : u8;
\trule 20 { when a == 2; verdict AUDIT; }
\trule 5 { when a == 1; verdict DENY; fault 0x1001; }
\tdefault verdict ALLOW;
}
"""


def _ir(source: str, name: str = "t.adl") -> ProgramIR:
    return to_ir(parse(source, name), name)


class TestToIr:
    def test_meta_and_fields(self) -> None:
        ir = _ir(_MULTI_RULE)
        assert ir.version == "1.0.1"
        assert ir.source_name == "t.adl"
        assert len(ir.tables) == 1
        table = ir.tables[0]
        assert table.name == "t"
        assert table.hook == "uring_cmd"
        assert table.prefix == "airy_policy_t"
        assert table.inputs[0].name == "a"
        assert table.inputs[0].index == 0
        assert table.default_verdict is Verdict.ALLOW

    def test_rows_sorted_by_number(self) -> None:
        table = _ir(_MULTI_RULE).tables[0]
        assert [row.number for row in table.rows] == [5, 20]

    def test_row_and_condition_ir(self) -> None:
        row = _ir(_MULTI_RULE).tables[0].rows[0]
        assert row.verdict is Verdict.DENY
        assert row.fault == 0x1001
        condition = row.conditions[0]
        assert condition.field_index == 0
        assert condition.field_name == "a"
        assert condition.op is CompareOp.EQ
        assert condition.value == 1

    def test_rule_without_fault_stays_none(self) -> None:
        row = _ir(_MULTI_RULE).tables[0].rows[1]
        assert row.verdict is Verdict.AUDIT
        assert row.fault is None


class TestSemanticErrors:
    def test_unsupported_version(self) -> None:
        source = (
            'version "9.9.9"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="unsupported DSL version"):
            _ir(source)

    def test_unknown_hook(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = nope; input a : u8; '
            'rule 1 { when a == 1; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="unknown LSM hook"):
            _ir(source)

    def test_table_requires_input(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; '
            'rule 1 { when a == 1; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="requires at least one input field"):
            _ir(source)

    def test_duplicate_input_field(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; input a : u32; '
            'rule 1 { when a == 1; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="duplicate input field"):
            _ir(source)

    def test_table_requires_rule(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="requires at least one rule"):
            _ir(source)

    def test_undeclared_field_reference(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when b == 1; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="references undeclared field"):
            _ir(source)

    def test_literal_out_of_range(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 300; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="out of range for 'a'"):
            _ir(source)

    def test_fault_only_on_deny(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; verdict AUDIT; fault 0x1001; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="only allowed on DENY rules"):
            _ir(source)

    def test_unknown_fault_code(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; verdict DENY; fault 0x9999; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="unknown fault code"):
            _ir(source)

    def test_duplicate_table_name(self) -> None:
        table = (
            'table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="duplicate table"):
            _ir('version "1.0.1";' + table + table)

    def test_non_positive_rule_number(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 0 { when a == 1; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="rule number must be positive"):
            _ir(source)

    def test_duplicate_rule_number(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; verdict DENY; } '
            'rule 1 { when a == 2; verdict AUDIT; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSemanticError, match="duplicate rule number"):
            _ir(source)
