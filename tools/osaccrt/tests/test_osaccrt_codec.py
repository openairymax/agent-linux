# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""osaccrt_codec 单元测试：记录形状、字段集合与文本原语。"""

from __future__ import annotations

import pytest

import osaccrt_codec as oc


def test_read_obj_accepts_text_and_bytes() -> None:
    """文本与字节输入都能解析为对象。"""
    assert oc.read_obj('{"a": 1}', "record") == {"a": 1}
    assert oc.read_obj(b'{"a": 1}', "record") == {"a": 1}


def test_read_obj_rejects_bad_input() -> None:
    """语法、形状、编码与类型错误分门别类拒绝。"""
    with pytest.raises(ValueError, match="record is not valid JSON"):
        oc.read_obj("{", "record")
    with pytest.raises(ValueError, match="record must be a JSON object"):
        oc.read_obj("[]", "record")
    with pytest.raises(ValueError, match="record is not UTF-8"):
        oc.read_obj(b"\xff", "record")
    with pytest.raises(TypeError, match="record must be str or bytes"):
        oc.read_obj(1, "record")  # type: ignore[arg-type]


def test_check_keys_exact_match() -> None:
    """字段集合恰好等于必填加可选时通过。"""
    req = frozenset({"a", "b"})
    oc.check_keys({"a": 1, "b": 2}, req)
    oc.check_keys({"a": 1, "b": 2, "c": 3}, req, frozenset({"c"}), "item")


def test_check_keys_rejects_unknown() -> None:
    """未知字段一律拒绝并指出字段名。"""
    with pytest.raises(ValueError, match="unknown item fields: \\['c'\\]"):
        oc.check_keys({"a": 1, "c": 2}, frozenset({"a"}), what="item")


def test_check_keys_rejects_missing() -> None:
    """缺失必填字段一律拒绝。"""
    with pytest.raises(ValueError, match="missing item fields: \\['b'\\]"):
        oc.check_keys({"a": 1}, frozenset({"a", "b"}), what="item")


def test_as_text() -> None:
    """只接受字符串，其余类型拒绝。"""
    assert oc.as_text("x", "name") == "x"
    with pytest.raises(ValueError, match="name must be a string"):
        oc.as_text(1, "name")


def test_as_texts() -> None:
    """数组逐项校验并归一化为元组。"""
    assert oc.as_texts(["a", "b"], "exps") == ("a", "b")
    with pytest.raises(ValueError, match="exps must be a JSON array"):
        oc.as_texts("a", "exps")
    with pytest.raises(ValueError, match="exps must be a string"):
        oc.as_texts(["a", 1], "exps")
