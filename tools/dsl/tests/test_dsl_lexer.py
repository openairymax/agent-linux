#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_lexer 单元测试 — .adl 契约源词法切分。

覆盖 tools/dsl/dsl_lexer.py：关键字/标识符、十进制与十六进制整数、
字符串字面量、单/双字符标点、注释与空白跳过、1 基行列位置，以及
非法字符、未闭合字符串、畸形数字等词法错误的诊断。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import pytest

from dsl_lexer import Lexer, tokenize
from dsl_types import AIRY_DSL_SYNTAX_ERROR, DslSyntaxError, SourceSpan, TokenKind


class TestTokenizeBasics:
    def test_version_decl_sequence(self) -> None:
        tokens = tokenize('version "1.0.1";')
        assert [t.kind for t in tokens] == [
            TokenKind.KEYWORD,
            TokenKind.STRING,
            TokenKind.PUNCT,
            TokenKind.EOF,
        ]
        assert tokens[0].text == "version"
        assert tokens[1].text == "1.0.1"
        assert tokens[2].text == ";"

    def test_eof_span_marks_end(self) -> None:
        eof = tokenize("version")[-1]
        assert eof.kind is TokenKind.EOF
        assert eof.text == ""
        assert eof.span == SourceSpan(1, 8)

    def test_comments_and_blank_lines_skipped(self) -> None:
        tokens = tokenize("# leading comment\n  version # trailing\n\n\t")
        assert [t.text for t in tokens] == ["version", ""]

    def test_lexer_class_is_reusable(self) -> None:
        assert Lexer("table", "t.adl").tokenize()[0].text == "table"


class TestNumberLiterals:
    def test_decimal_integer(self) -> None:
        token = tokenize("42")[0]
        assert token.kind is TokenKind.INT
        assert token.text == "42"
        assert token.value == 42

    def test_hex_integer_lowercase(self) -> None:
        assert tokenize("0x100a")[0].value == 0x100A

    def test_hex_integer_uppercase_marker(self) -> None:
        assert tokenize("0X10")[0].value == 16

    def test_zero_is_decimal(self) -> None:
        token = tokenize("0")[0]
        assert token.kind is TokenKind.INT
        assert token.value == 0

    def test_malformed_hex_rejected(self) -> None:
        with pytest.raises(DslSyntaxError, match="malformed hexadecimal literal"):
            tokenize("0xZZ")

    def test_ident_suffix_after_decimal_rejected(self) -> None:
        with pytest.raises(DslSyntaxError, match="malformed numeric literal"):
            tokenize("12abc")

    def test_ident_suffix_after_hex_rejected(self) -> None:
        with pytest.raises(DslSyntaxError, match="malformed numeric literal"):
            tokenize("0x1g")


class TestIdentifiersAndKeywords:
    def test_keyword_recognized(self) -> None:
        assert tokenize("verdict")[0].kind is TokenKind.KEYWORD

    def test_identifier_recognized(self) -> None:
        token = tokenize("uring_cmd")[0]
        assert token.kind is TokenKind.IDENT
        assert token.text == "uring_cmd"

    def test_identifier_may_lead_with_underscore(self) -> None:
        assert tokenize("_x1")[0].kind is TokenKind.IDENT


class TestPunctuation:
    @pytest.mark.parametrize("op", ["==", "!=", "<=", ">="])
    def test_two_char_operators(self, op: str) -> None:
        token = tokenize(op)[0]
        assert token.kind is TokenKind.PUNCT
        assert token.text == op

    @pytest.mark.parametrize("ch", ["&", "<", ">", "=", "{", "}", ";", ":"])
    def test_single_char_tokens(self, ch: str) -> None:
        assert tokenize(ch)[0].text == ch

    def test_unexpected_character_rejected(self) -> None:
        with pytest.raises(DslSyntaxError, match="unexpected character"):
            tokenize("@")


class TestDiagnostics:
    def test_unterminated_string_rejected(self) -> None:
        with pytest.raises(DslSyntaxError, match="unterminated string literal"):
            tokenize('"open')

    def test_newline_inside_string_rejected(self) -> None:
        with pytest.raises(DslSyntaxError, match="unterminated string literal"):
            tokenize('"open\nclose"')

    def test_error_code_and_source_name(self) -> None:
        with pytest.raises(DslSyntaxError) as err:
            tokenize("@", "policy.adl")
        assert err.value.code == AIRY_DSL_SYNTAX_ERROR
        assert err.value.message.startswith("policy.adl:1:1:")

    def test_multiline_positions(self) -> None:
        tokens = tokenize("version\n  table")
        assert tokens[0].span == SourceSpan(1, 1)
        assert tokens[1].span == SourceSpan(2, 3)


def test_source_span_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match="invalid source span"):
        SourceSpan(0, 1)
