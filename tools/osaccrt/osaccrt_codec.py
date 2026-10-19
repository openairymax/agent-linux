# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_codec — 证据记录 JSON 编解码原语。

六步规程中所有落盘的 JSON 记录都受同一组形状约束：可解码为 UTF-8
对象、字段集合与规范精确匹配、标量字段为字符串。把这些校验收敛到
一处，各模块只声明自己的字段集合，避免错误文案与容错行为在不同
记录之间漂移。

权威依据：
    docs/docs-closed/agent-linux-dev/agentrt-linux验收实验任务卡草案.md
        §3.4 证据包规范

Example:
    >>> check_keys({"a": 1}, frozenset({"a"}))
    >>> as_text("x", "name")
    'x'

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import json
from typing import Any, Final

__all__ = ["as_text", "as_texts", "check_keys", "read_obj"]

_EMPTY: Final[frozenset[str]] = frozenset()


def read_obj(raw: str | bytes, what: str) -> dict[str, Any]:
    """解析 JSON 对象；编码、语法或形状错误一律转为 ValueError。"""
    text = _text(raw, what)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{what} must be a JSON object")
    return data


def check_keys(data: dict[str, Any], req: frozenset[str],
               opt: frozenset[str] = _EMPTY,
               what: str = "record") -> None:
    """拒绝未知字段与缺失的必填字段。"""
    keys = set(data)
    unknown = keys - (req | opt)
    if unknown:
        raise ValueError(f"unknown {what} fields: {sorted(unknown)}")
    missing = req - keys
    if missing:
        raise ValueError(f"missing {what} fields: {sorted(missing)}")


def as_text(value: Any, name: str) -> str:
    """要求字段为字符串。"""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def as_texts(value: Any, name: str) -> tuple[str, ...]:
    """还原字符串数组，拒绝把单个字符串当作数组。"""
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a JSON array")
    return tuple(as_text(item, name) for item in value)


def _text(raw: str | bytes, what: str) -> str:
    """把字节输入解码为文本，非法编码转为 ValueError。"""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{what} is not UTF-8: {exc}") from exc
    if not isinstance(raw, str):
        raise TypeError(f"{what} must be str or bytes")
    return raw
