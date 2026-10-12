# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_parser — AirymaxOS 策略 DSL（.adl）递归下降语法分析器。

把 dsl_lexer 产出的 Token 序列构造为 dsl_types.Program 语法树。文法：

    program      := version_decl table_decl+ EOF
    version_decl := 'version' STRING ';'
    table_decl   := 'table' IDENT '{' table_member+ '}'
    table_member := hook_decl | input_decl | rule_decl | default_decl
    hook_decl    := 'hook' '=' IDENT ';'
    input_decl   := 'input' IDENT ':' value_type ';'
    rule_decl    := 'rule' INT '{' rule_member+ '}'
    rule_member  := when_clause | verdict_decl | fault_decl
    when_clause  := 'when' IDENT cmp_op INT ';'
    verdict_decl := 'verdict' verdict_name ';'
    fault_decl   := 'fault' INT ';'
    default_decl := 'default' 'verdict' verdict_name ';'

结构级错误（缺 hook/default/verdict、意外 Token、未知裁决名）抛
DslSyntaxError；语义级校验（钩子合法性、重名、越界、裁决-故障一致性）
留给 dsl_semantics。本模块仅构造语法树，不做跨节点语义判断。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

# 1. 标准库导入
from __future__ import annotations

from dsl_lexer import tokenize
from dsl_types import (
    COMPARE_OPS,
    VALUE_TYPES,
    VERDICT_BY_NAME,
    Condition,
    CompareOp,
    DslSyntaxError,
    InputDecl,
    Program,
    Rule,
    SourceSpan,
    Table,
    Token,
    TokenKind,
    ValueType,
    Verdict,
    format_diagnostic,
)

# 2. 公开导出
__all__ = ["Parser", "parse"]


class Parser:
    """.adl 契约源的递归下降语法分析器。"""

    def __init__(self, tokens: tuple[Token, ...], source_name: str) -> None:
        self._tokens = tokens
        self._source_name = source_name
        self._pos = 0

    # ── 游标与错误 ──
    def _current(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        token = self._tokens[self._pos]
        if token.kind is not TokenKind.EOF:
            self._pos += 1
        return token

    def _at_end(self) -> bool:
        return self._current().kind is TokenKind.EOF

    def _error(self, token: Token, message: str) -> DslSyntaxError:
        return DslSyntaxError(format_diagnostic(self._source_name, token.span, message))

    # ── 断言助手 ──
    def _check_keyword(self, text: str) -> bool:
        token = self._current()
        return token.kind is TokenKind.KEYWORD and token.text == text

    def _check_punct(self, text: str) -> bool:
        token = self._current()
        return token.kind is TokenKind.PUNCT and token.text == text

    def _expect_punct(self, text: str) -> Token:
        if not self._check_punct(text):
            raise self._error(self._current(), f"expected {text!r}, got {self._current().text!r}")
        return self._advance()

    def _expect_keyword(self, text: str) -> Token:
        if not self._check_keyword(text):
            raise self._error(
                self._current(),
                f"expected keyword {text!r}, got {self._current().text!r}",
            )
        return self._advance()

    def _expect(self, kind: TokenKind, what: str) -> Token:
        if self._current().kind is not kind:
            raise self._error(self._current(), f"expected {what}, got {self._current().text!r}")
        return self._advance()

    def _expect_int(self, what: str) -> int:
        token = self._expect(TokenKind.INT, what)
        if token.value is None:
            raise self._error(token, f"expected {what}, got {token.text!r}")
        return token.value

    # ── 终结符 ──
    def _parse_value_type(self) -> ValueType:
        token = self._expect(TokenKind.IDENT, "value type (u8|u16|u32|u64)")
        value_type = VALUE_TYPES.get(token.text)
        if value_type is None:
            raise self._error(token, f"unknown value type {token.text!r}")
        return value_type

    def _parse_verdict_name(self) -> Verdict:
        token = self._expect(TokenKind.IDENT, "verdict (ALLOW|DENY|AUDIT|COMPLAIN)")
        verdict = VERDICT_BY_NAME.get(token.text)
        if verdict is None:
            raise self._error(token, f"unknown verdict {token.text!r}")
        return verdict

    def _parse_cmp_op(self) -> CompareOp:
        token = self._current()
        if token.kind is TokenKind.PUNCT and token.text in COMPARE_OPS:
            self._advance()
            return COMPARE_OPS[token.text]
        raise self._error(token, f"expected comparison operator, got {token.text!r}")

    # ── 顶层结构 ──
    def _parse_version(self) -> str:
        self._expect_keyword("version")
        token = self._expect(TokenKind.STRING, "version string literal")
        self._expect_punct(";")
        return token.text

    def _parse_hook(self) -> str:
        self._expect_keyword("hook")
        self._expect_punct("=")
        name = self._expect(TokenKind.IDENT, "hook name").text
        self._expect_punct(";")
        return name

    def _parse_input(self) -> InputDecl:
        span = self._expect_keyword("input").span
        name = self._expect(TokenKind.IDENT, "input field name").text
        self._expect_punct(":")
        value_type = self._parse_value_type()
        self._expect_punct(";")
        return InputDecl(name=name, value_type=value_type, span=span)

    def _parse_when(self) -> Condition:
        span = self._expect_keyword("when").span
        field = self._expect(TokenKind.IDENT, "condition field name").text
        op = self._parse_cmp_op()
        value = self._expect_int("integer literal")
        self._expect_punct(";")
        return Condition(field=field, op=op, value=value, span=span)

    def _parse_fault(self) -> int:
        self._expect_keyword("fault")
        value = self._expect_int("fault code")
        self._expect_punct(";")
        return value

    def _parse_rule(self) -> Rule:
        span = self._expect_keyword("rule").span
        number = self._expect_int("rule number")
        self._expect_punct("{")
        conditions: list[Condition] = []
        verdict: Verdict | None = None
        fault: int | None = None
        while not self._check_punct("}"):
            if self._at_end():
                raise self._error(self._current(), "unexpected end of input inside rule body")
            if self._check_keyword("when"):
                conditions.append(self._parse_when())
            elif self._check_keyword("verdict"):
                verdict = self._parse_rule_verdict(verdict)
            elif self._check_keyword("fault"):
                fault = self._parse_rule_fault(fault)
            else:
                raise self._error(
                    self._current(),
                    f"unexpected token in rule body: {self._current().text!r}",
                )
        self._expect_punct("}")
        return self._make_rule(number, conditions, verdict, fault, span)

    def _parse_rule_verdict(self, existing: Verdict | None) -> Verdict:
        if existing is not None:
            raise self._error(self._current(), "duplicate verdict clause in rule")
        self._expect_keyword("verdict")
        verdict = self._parse_verdict_name()
        self._expect_punct(";")
        return verdict

    def _parse_rule_fault(self, existing: int | None) -> int:
        if existing is not None:
            raise self._error(self._current(), "duplicate fault clause in rule")
        return self._parse_fault()

    def _make_rule(
        self,
        number: int,
        conditions: list[Condition],
        verdict: Verdict | None,
        fault: int | None,
        span: SourceSpan,
    ) -> Rule:
        if not conditions:
            raise self._error(self._current(), "rule requires at least one 'when' clause")
        if verdict is None:
            raise self._error(self._current(), "rule requires a 'verdict' clause")
        return Rule(
            number=number,
            conditions=tuple(conditions),
            verdict=verdict,
            fault=fault,
            span=span,
        )

    def _parse_default_verdict(self, existing: Verdict | None) -> Verdict:
        if existing is not None:
            raise self._error(self._current(), "duplicate default verdict clause in table")
        self._expect_keyword("default")
        self._expect_keyword("verdict")
        verdict = self._parse_verdict_name()
        self._expect_punct(";")
        return verdict

    def _parse_table(self) -> Table:
        span = self._expect_keyword("table").span
        name = self._expect(TokenKind.IDENT, "table name").text
        self._expect_punct("{")
        hook: str | None = None
        inputs: list[InputDecl] = []
        rules: list[Rule] = []
        default: Verdict | None = None
        while not self._check_punct("}"):
            if self._at_end():
                raise self._error(self._current(), "unexpected end of input inside table body")
            if self._check_keyword("hook"):
                if hook is not None:
                    raise self._error(self._current(), "duplicate hook clause in table")
                hook = self._parse_hook()
            elif self._check_keyword("input"):
                inputs.append(self._parse_input())
            elif self._check_keyword("rule"):
                rules.append(self._parse_rule())
            elif self._check_keyword("default"):
                default = self._parse_default_verdict(default)
            else:
                raise self._error(
                    self._current(), f"unexpected token in table body: {self._current().text!r}"
                )
        self._expect_punct("}")
        return self._make_table(name, hook, inputs, rules, default, span)

    def _make_table(
        self,
        name: str,
        hook: str | None,
        inputs: list[InputDecl],
        rules: list[Rule],
        default: Verdict | None,
        span: SourceSpan,
    ) -> Table:
        if hook is None:
            raise self._error(self._current(), f"table {name!r} requires a 'hook' clause")
        if default is None:
            raise self._error(
                self._current(),
                f"table {name!r} requires a 'default verdict' clause",
            )
        return Table(
            name=name,
            hook=hook,
            inputs=tuple(inputs),
            rules=tuple(rules),
            default_verdict=default,
            span=span,
        )

    # ── 主入口 ──
    def parse(self) -> Program:
        """构造语法树根：一个 version 声明 + 至少一个 table 声明。"""
        version = self._parse_version()
        tables: list[Table] = []
        while not self._at_end():
            if not self._check_keyword("table"):
                raise self._error(
                    self._current(), f"expected 'table' declaration, got {self._current().text!r}"
                )
            tables.append(self._parse_table())
        if not tables:
            raise self._error(self._current(), "expected at least one table declaration")
        return Program(version=version, tables=tuple(tables))


def parse(source: str, source_name: str = "<adl>") -> Program:
    """把 .adl 契约源文本解析为语法树。"""
    return Parser(tokenize(source, source_name), source_name).parse()
