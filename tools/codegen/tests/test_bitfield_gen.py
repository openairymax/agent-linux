#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""bitfield_gen 单元测试 — .bf 契约解析、状态机校验与布局一致性。

覆盖 tools/codegen/bitfield_gen.py：DSL 行解析、逐块状态机错误路径、
32/64 位布局一致性强制、上下文构造与零漂移校验。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

from pathlib import Path

import pytest

import bitfield_gen
from bitfield_gen import (
    BF32_REL,
    BF64_REL,
    HDR32_REL,
    HDR64_REL,
    _build_context,
    _guard_for,
    _make_field,
    _meaningful_lines,
    _parse_block_header,
    _strip_comment,
    check,
    load_contracts,
    parse_bf_contract,
    render_outputs,
)
from codegen_types import (
    BitfieldContainer,
    BitfieldFieldKind,
    ContractSourceError,
    ContractValidationError,
    ExitCode,
)

_VALID_BLOCK = """\
block demo(32) {
bind struct demo_s from linux/airymax/demo.h
field lo 15..0
field hi 31..16
}
"""

_OTHER_BLOCK = """\
block other(32) {
bind word __u32
field whole 31..0
}
"""


def _write_bf(tmp_path: Path, text: str, name: str = "x.bf") -> Path:
    target = tmp_path / name
    target.write_text(text, encoding="utf-8")
    return target


def _write_arch_contract(tmp_path: Path, name: str, text: str) -> None:
    target = tmp_path / BF64_REL.parent / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class TestLineHelpers:
    def test_strip_comment(self) -> None:
        assert _strip_comment("  field a 3..0 -- note  ") == "field a 3..0"

    def test_meaningful_lines_keeps_linenos(self) -> None:
        text = "block a(8) {\n\n-- comment\nbind word __u8\n}"
        assert _meaningful_lines(text) == [
            (1, "block a(8) {"),
            (4, "bind word __u8"),
            (5, "}"),
        ]

    def test_parse_block_header(self) -> None:
        assert _parse_block_header("block cap_badge( 64 ){") == ("cap_badge", 64)


class TestMakeField:
    def test_valid_field(self) -> None:
        spec = _make_field("a", "7", "0", BitfieldFieldKind.FIELD, Path("f.bf"), 1)
        assert spec.width == 8
        assert spec.kind is BitfieldFieldKind.FIELD

    def test_bad_range_prefixed(self) -> None:
        with pytest.raises(ContractValidationError, match=r"f\.bf:5: invalid bitfield range"):
            _make_field("a", "0", "3", BitfieldFieldKind.FIELD, Path("f.bf"), 5)


class TestParseBfContract:
    def test_real_64_source(self, repo_root: Path) -> None:
        contract = parse_bf_contract(repo_root / BF64_REL)
        names = [block.name for block in contract.blocks]
        assert names == ["ipc_msg_hdr", "cap_badge", "task_desc"]
        by_name = contract.blocks_by_name
        assert by_name["ipc_msg_hdr"].width == 1024
        assert by_name["ipc_msg_hdr"].container is BitfieldContainer.STRUCT
        assert by_name["cap_badge"].ctype == "__u64"
        assert by_name["cap_badge"].container is BitfieldContainer.WORD

    def test_real_32_and_64_signatures_identical(self, repo_root: Path) -> None:
        contract32, contract64 = load_contracts(repo_root)
        assert contract32.layout_signature() == contract64.layout_signature()

    def test_synthetic_minimal(self, tmp_path: Path) -> None:
        contract = parse_bf_contract(_write_bf(tmp_path, _VALID_BLOCK))
        assert contract.blocks[0].name == "demo"

    def test_empty_source_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ContractSourceError, match="no blocks parsed"):
            parse_bf_contract(_write_bf(tmp_path, "-- only a comment\n"))

    def test_missing_source_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ContractSourceError, match="not found"):
            parse_bf_contract(tmp_path / "absent.bf")

    def test_nested_block_rejected(self, tmp_path: Path) -> None:
        text = "block a(8) {\nblock b(8) {\n"
        with pytest.raises(ContractValidationError, match="nested block not allowed"):
            parse_bf_contract(_write_bf(tmp_path, text))

    def test_directive_outside_block_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ContractValidationError, match="directive outside a block"):
            parse_bf_contract(_write_bf(tmp_path, "bind word __u8\n"))

    def test_close_without_open_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ContractValidationError, match="directive outside a block"):
            parse_bf_contract(_write_bf(tmp_path, "}\n"))

    def test_unrecognized_directive_rejected(self, tmp_path: Path) -> None:
        text = "block a(8) {\nbind word __u8\nfrobnicate\n}\n"
        with pytest.raises(ContractValidationError, match="unrecognized directive: frobnicate"):
            parse_bf_contract(_write_bf(tmp_path, text))

    def test_eof_inside_block_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ContractValidationError, match="unexpected EOF inside block a"):
            parse_bf_contract(_write_bf(tmp_path, "block a(8) {\nbind word __u8\n"))

    def test_block_without_bind_rejected(self, tmp_path: Path) -> None:
        text = "block a(8) {\nfield whole 7..0\n}\n"
        with pytest.raises(ContractValidationError, match="block a has no bind"):
            parse_bf_contract(_write_bf(tmp_path, text))


class TestLoadContracts:
    def test_layout_mismatch_rejected(self, tmp_path: Path) -> None:
        _write_arch_contract(tmp_path, "structures_64.bf", _VALID_BLOCK)
        _write_arch_contract(tmp_path, "structures_32.bf", _OTHER_BLOCK)
        with pytest.raises(ContractValidationError, match="not bit-for-bit identical"):
            load_contracts(tmp_path)

    def test_consistent_synthetic(self, tmp_path: Path) -> None:
        _write_arch_contract(tmp_path, "structures_64.bf", _VALID_BLOCK)
        _write_arch_contract(tmp_path, "structures_32.bf", _VALID_BLOCK)
        contract32, contract64 = load_contracts(tmp_path)
        assert contract32.layout_signature() == contract64.layout_signature()


class TestGuardAndContext:
    def test_guard_name(self) -> None:
        assert _guard_for(HDR64_REL) == "_UAPI_AIRYMAX_STRUCTURES_64_GEN_H"
        assert _guard_for(HDR32_REL) == "_UAPI_AIRYMAX_STRUCTURES_32_GEN_H"

    def test_build_context_shape(self, repo_root: Path) -> None:
        contract = parse_bf_contract(repo_root / BF64_REL)
        context = _build_context(contract, BF64_REL, HDR64_REL)
        assert set(context) == {
            "generator",
            "generator_version",
            "source_name",
            "source_path",
            "guard",
            "includes",
            "blocks",
        }


class TestRenderAndCheck:
    def test_render_outputs_paths(self, repo_root: Path) -> None:
        rendered = render_outputs(repo_root)
        assert set(rendered) == {repo_root / HDR64_REL, repo_root / HDR32_REL}
        assert all(content.endswith("\n") for content in rendered.values())

    def test_render_is_deterministic(self, repo_root: Path) -> None:
        assert render_outputs(repo_root) == render_outputs(repo_root)

    def test_check_matches_committed(self, repo_root: Path) -> None:
        assert check(repo_root) == int(ExitCode.SUCCESS)
