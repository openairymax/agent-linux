#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_parser 单元测试 — .adl 契约源递归下降语法分析。

覆盖 tools/dsl/dsl_parser.py：顶层 version/table 结构、hook/input/rule/
when/verdict/fault/default 成员的语法树构造，以及结构级错误（缺 hook/
default/verdict、意外 Token、未知裁决/类型、重复子句）的诊断。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import pytest

from dsl_lexer import tokenize
from dsl_parser import Parser, parse
from dsl_types import CompareOp, DslSyntaxError, Verdict

_VALID = """version "1.0.1";
table t {
\thook = uring_cmd;
\tinput a : u8;
\tinput b : u32;
\trule 10 {
\t\twhen a == 1;
\t\twhen b & 0x10;
\t\tverdict DENY;
\t\tfault 0x1001;
\t}
\tdefault verdict ALLOW;
}
"""


def _table(name: str = "t") -> str:
    return (
        'table %s { hook = uring_cmd; input a : u8; '
        'rule 1 { when a == 1; verdict DENY; } default verdict ALLOW; }' % name
    )


class TestParseStructure:
    def test_program_shape(self) -> None:
        program = parse(_VALID, "t.adl")
        assert program.version == "1.0.1"
        assert len(program.tables) == 1
        table = program.tables[0]
        assert table.name == "t"
        assert table.hook == "uring_cmd"
        assert [item.name for item in table.inputs] == ["a", "b"]
        assert [item.value_type.value for item in table.inputs] == ["u8", "u32"]
        assert table.default_verdict is Verdict.ALLOW

    def test_rule_members(self) -> None:
        rule = parse(_VALID, "t.adl").tables[0].rules[0]
        assert rule.number == 10
        assert rule.verdict is Verdict.DENY
        assert rule.fault == 0x1001
        assert [cond.field for cond in rule.conditions] == ["a", "b"]
        assert [cond.op for cond in rule.conditions] == [CompareOp.EQ, CompareOp.MASK]
        assert [cond.value for cond in rule.conditions] == [1, 0x10]

    def test_multiple_tables(self) -> None:
        program = parse('version "1.0.1";' + _table("t") + _table("u"), "t.adl")
        assert [table.name for table in program.tables] == ["t", "u"]

    def test_rule_may_omit_fault(self) -> None:
        rule = parse('version "1.0.1";' + _table(), "t.adl").tables[0].rules[0]
        assert rule.fault is None

    def test_parser_class_direct(self) -> None:
        program = Parser(tokenize(_VALID, "t.adl"), "t.adl").parse()
        assert program.tables[0].name == "t"


class TestParseErrors:
    def test_missing_version(self) -> None:
        with pytest.raises(DslSyntaxError, match="expected keyword 'version'"):
            parse('table t { hook = uring_cmd; default verdict ALLOW; }')

    def test_no_tables(self) -> None:
        with pytest.raises(DslSyntaxError, match="expected at least one table declaration"):
            parse('version "1.0.1";')

    def test_unexpected_top_level_token(self) -> None:
        with pytest.raises(DslSyntaxError, match="expected 'table' declaration"):
            parse('version "1.0.1"; input a : u8;')

    def test_rule_requires_when(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSyntaxError, match="at least one 'when' clause"):
            parse(source)

    def test_rule_requires_verdict(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSyntaxError, match="requires a 'verdict' clause"):
            parse(source)

    def test_table_requires_hook(self) -> None:
        source = (
            'version "1.0.1"; table t { input a : u8; '
            'rule 1 { when a == 1; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSyntaxError, match="requires a 'hook' clause"):
            parse(source)

    def test_table_requires_default(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; verdict DENY; } }'
        )
        with pytest.raises(DslSyntaxError, match="requires a 'default verdict' clause"):
            parse(source)

    def test_unknown_value_type(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u99; '
            'default verdict ALLOW; }'
        )
        with pytest.raises(DslSyntaxError, match="unknown value type 'u99'"):
            parse(source)

    def test_unknown_verdict_name(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; verdict NOPE; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSyntaxError, match="unknown verdict 'NOPE'"):
            parse(source)

    def test_duplicate_verdict_clause(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; verdict DENY; verdict AUDIT; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSyntaxError, match="duplicate verdict clause"):
            parse(source)

    def test_duplicate_hook_clause(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; hook = file_open; input a : u8; '
            'rule 1 { when a == 1; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSyntaxError, match="duplicate hook clause"):
            parse(source)

    def test_missing_comparison_operator(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a 1; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSyntaxError, match="expected comparison operator"):
            parse(source)

    def test_unexpected_token_in_table(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'bogus; default verdict ALLOW; }'
        )
        with pytest.raises(DslSyntaxError, match="unexpected token in table body"):
            parse(source)

    def test_unexpected_token_in_rule(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; bogus; verdict DENY; } default verdict ALLOW; }'
        )
        with pytest.raises(DslSyntaxError, match="unexpected token in rule body"):
            parse(source)

    def test_unclosed_rule_body(self) -> None:
        source = (
            'version "1.0.1"; table t { hook = uring_cmd; input a : u8; '
            'rule 1 { when a == 1; verdict DENY;'
        )
        with pytest.raises(DslSyntaxError, match="unexpected end of input inside rule body"):
            parse(source)
