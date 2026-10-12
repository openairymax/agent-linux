# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_lexer — AirymaxOS 策略 DSL（.adl）词法分析器。

把 .adl 契约源文本切分为 dsl_types.Token 序列，供 dsl_parser 递归下降解析。
支持：行内注释（`#` 到行尾）、单行字符串字面量、十进制与十六进制整数、
标识符/关键字，以及单/双字符比较算符。词法错误（非法字符、未闭合字符串、
畸形数字）一律抛 DslSyntaxError 并携带 1 基行列位置。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

# 1. 标准库导入
from __future__ import annotations

from typing import Final

from dsl_types import (
    KEYWORDS,
    PUNCT_TOKENS,
    DslSyntaxError,
    SourceSpan,
    Token,
    TokenKind,
    format_diagnostic,
)

# 2. 公开导出
__all__ = ["Lexer", "tokenize"]

# 3. 词法常量
_WHITESPACE: Final[str] = " \t\r\n"
_COMMENT_MARKER: Final[str] = "#"
_HEX_DIGITS: Final[str] = "0123456789abcdefABCDEF"
_TWO_CHAR_PUNCT: Final[tuple[str, ...]] = ("==", "!=", "<=", ">=")


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _is_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


class Lexer:
    """.adl 契约源的词法分析器（保留 1 基行/列位置）。"""

    def __init__(self, source: str, source_name: str) -> None:
        self._source = source
        self._source_name = source_name
        self._index = 0
        self._line = 1
        self._column = 1

    # ── 位置游标 ──
    def _at_end(self) -> bool:
        return self._index >= len(self._source)

    def _peek(self, offset: int = 0) -> str:
        pos = self._index + offset
        if pos >= len(self._source):
            return ""
        return self._source[pos]

    def _advance(self) -> str:
        ch = self._source[self._index]
        self._index += 1
        if ch == "\n":
            self._line += 1
            self._column = 1
        else:
            self._column += 1
        return ch

    def _span(self) -> SourceSpan:
        return SourceSpan(self._line, self._column)

    def _consume_while(self, predicate) -> str:
        start = self._index
        while not self._at_end() and predicate(self._peek()):
            self._advance()
        return self._source[start:self._index]

    def _error(self, span: SourceSpan, message: str) -> DslSyntaxError:
        return DslSyntaxError(format_diagnostic(self._source_name, span, message))

    # ── 空白与注释 ──
    def _skip_ignorable(self) -> None:
        while not self._at_end():
            ch = self._peek()
            if ch in _WHITESPACE:
                self._advance()
            elif ch == _COMMENT_MARKER:
                self._consume_while(lambda c: c != "\n")
            else:
                return

    # ── 词法单元读取 ──
    def _read_string(self, span: SourceSpan) -> Token:
        self._advance()
        chunks: list[str] = []
        while not self._at_end() and self._peek() != '"':
            if self._peek() == "\n":
                raise self._error(span, "unterminated string literal")
            chunks.append(self._advance())
        if self._at_end():
            raise self._error(span, "unterminated string literal")
        self._advance()
        return Token(TokenKind.STRING, "".join(chunks), span)

    def _read_hex_number(self, span: SourceSpan) -> Token:
        self._advance()
        self._advance()
        digits = self._consume_while(lambda c: c in _HEX_DIGITS)
        if not digits:
            raise self._error(span, "malformed hexadecimal literal")
        self._reject_trailing_ident_char(span)
        return Token(TokenKind.INT, f"0x{digits}", span, int(digits, 16))

    def _reject_trailing_ident_char(self, span: SourceSpan) -> None:
        if _is_ident_start(self._peek()):
            raise self._error(span, "malformed numeric literal")

    def _read_decimal_number(self, span: SourceSpan) -> Token:
        digits = self._consume_while(str.isdigit)
        self._reject_trailing_ident_char(span)
        return Token(TokenKind.INT, digits, span, int(digits, 10))

    def _read_number(self, span: SourceSpan) -> Token:
        if self._peek() == "0" and self._peek(1) in ("x", "X"):
            return self._read_hex_number(span)
        return self._read_decimal_number(span)

    def _read_ident(self, span: SourceSpan) -> Token:
        text = self._consume_while(_is_ident_char)
        kind = TokenKind.KEYWORD if text in KEYWORDS else TokenKind.IDENT
        return Token(kind, text, span)

    def _read_punct(self, span: SourceSpan) -> Token:
        for two in _TWO_CHAR_PUNCT:
            if self._peek() == two[0] and self._peek(1) == two[1]:
                self._advance()
                self._advance()
                return Token(TokenKind.PUNCT, two, span)
        ch = self._peek()
        if ch in PUNCT_TOKENS:
            self._advance()
            return Token(TokenKind.PUNCT, ch, span)
        raise self._error(span, f"unexpected character {ch!r}")

    def _next_token(self) -> Token:
        span = self._span()
        ch = self._peek()
        if ch == '"':
            return self._read_string(span)
        if ch.isdigit():
            return self._read_number(span)
        if _is_ident_start(ch):
            return self._read_ident(span)
        return self._read_punct(span)

    # ── 主入口 ──
    def tokenize(self) -> tuple[Token, ...]:
        """扫描全部词法单元，末尾追加 EOF（携带文件结束位置）。"""
        tokens: list[Token] = []
        while True:
            self._skip_ignorable()
            if self._at_end():
                break
            tokens.append(self._next_token())
        tokens.append(Token(TokenKind.EOF, "", self._span()))
        return tuple(tokens)


def tokenize(source: str, source_name: str = "<adl>") -> tuple[Token, ...]:
    """把 .adl 契约源文本切分为 Token 序列。"""
    return Lexer(source, source_name).tokenize()
