#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""ipc_gen 单元测试 — A-IPC 布局校验、多语言渲染辅助与产物校验。

覆盖 tools/codegen/ipc_gen.py：contracts.md §2 期望布局逐位校验、Badge 位
分配校验、各语言字段/常量行渲染、上下文构造与零漂移校验。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import ipc_gen
from bitfield_gen import parse_bf_contract
from codegen_types import (
    IPC_BADGE_PREFIX,
    IPC_FIELD_COMMENTS,
    IPC_FLAG_SPECS,
    IPC_HEADER_SIZE,
    IPC_MAGIC,
    IPC_MAX_PAYLOAD,
    IPC_OPCODE_SPECS,
    IPC_STRUCT_NAME,
    BitfieldBlockSpec,
    BitfieldContainer,
    BitfieldContract,
    BitfieldFieldSpec,
    ContractValidationError,
    ExitCode,
)

_OUTPUTS = ipc_gen.OUTPUTS


@pytest.fixture()
def ipc_block(repo_root: Path) -> BitfieldBlockSpec:
    return parse_bf_contract(repo_root / ipc_gen.BF64_REL).blocks_by_name[ipc_gen.IPC_BLOCK_NAME]


@pytest.fixture()
def badge_block(repo_root: Path) -> BitfieldBlockSpec:
    return parse_bf_contract(repo_root / ipc_gen.BF64_REL).blocks_by_name[ipc_gen.BADGE_BLOCK_NAME]


def _offsets() -> dict[str, int]:
    return ipc_gen._expected_offsets()


def _struct_block(name: str, width: int, low: int, high: int) -> BitfieldBlockSpec:
    from codegen_types import BitfieldFieldKind

    return BitfieldBlockSpec(
        name=name,
        width=width,
        container=BitfieldContainer.STRUCT,
        ctype="demo_t",
        header="linux/airymax/demo.h",
        fields=(BitfieldFieldSpec("whole", high, low),),
    )


class TestTypeMappings:
    def test_ctypes_regular(self, ipc_block: BitfieldBlockSpec) -> None:
        magic = next(f for f in ipc_block.fields if f.name == "magic")
        assert ipc_gen._ctypes(magic) == ("__u32", "u32")

    def test_ctypes_padding(self, ipc_block: BitfieldBlockSpec) -> None:
        pad = next(f for f in ipc_block.fields if f.is_padding)
        assert ipc_gen._ctypes(pad) == ("__u8", "[u8; 72]")

    def test_py_ctype_padding(self, ipc_block: BitfieldBlockSpec) -> None:
        pad = next(f for f in ipc_block.fields if f.is_padding)
        assert ipc_gen._py_ctype(pad) == "ctypes.c_uint8 * 72"

    def test_go_ctype_padding(self, ipc_block: BitfieldBlockSpec) -> None:
        pad = next(f for f in ipc_block.fields if f.is_padding)
        assert ipc_gen._go_ctype(pad) == "[72]byte"

    def test_go_ctype_regular(self, ipc_block: BitfieldBlockSpec) -> None:
        trace = next(f for f in ipc_block.fields if f.name == "trace_id")
        assert ipc_gen._go_ctype(trace) == "uint64"

    @pytest.mark.parametrize(
        ("width", "expected"),
        [(16, "number"), (32, "number"), (64, "bigint")],
    )
    def test_ts_field_type_regular(self, width: int, expected: str) -> None:
        field = BitfieldFieldSpec("f", high=width - 1, low=0)
        assert ipc_gen._ts_field_type(field) == expected

    def test_ts_field_type_padding(self, ipc_block: BitfieldBlockSpec) -> None:
        pad = next(f for f in ipc_block.fields if f.is_padding)
        assert ipc_gen._ts_field_type(pad) == "Uint8Array"


class TestNameHelpers:
    def test_camel(self) -> None:
        assert ipc_gen._camel("capability_badge") == "capabilityBadge"

    def test_camel_single(self) -> None:
        assert ipc_gen._camel("magic") == "magic"

    def test_pascal(self) -> None:
        assert ipc_gen._pascal("capability_badge") == "CapabilityBadge"


class TestConstantAndMacroLines:
    def test_constant_lines(self) -> None:
        lines = ipc_gen._constant_lines()
        assert len(lines) == 3
        assert "AIRY_IPC_MAGIC" in lines[0]
        assert "0x41524531u" in lines[0]
        assert str(IPC_HEADER_SIZE) in lines[1]
        assert "AIRY_IPC_MAX_PAYLOAD" in lines[2]
        assert f"0x{IPC_MAX_PAYLOAD:08X}u" in lines[2]

    def test_opcode_lines(self) -> None:
        lines = ipc_gen._opcode_lines()
        assert len(lines) == len(IPC_OPCODE_SPECS)
        assert all(line.startswith("#define AIRY_IPC_OP_") for line in lines)

    def test_flag_lines(self) -> None:
        lines = ipc_gen._flag_lines()
        assert len(lines) == len(IPC_FLAG_SPECS)
        assert all(line.startswith("#define AIRY_IPC_FLAG_") for line in lines)

    def test_badge_shift_and_mask_macros(self, badge_block: BitfieldBlockSpec) -> None:
        epoch = next(f for f in badge_block.fields if f.name == "epoch")
        assert ipc_gen._badge_shift_macro(epoch) == IPC_BADGE_PREFIX + "EPOCH_SHIFT"
        assert ipc_gen._badge_mask_macro(epoch) == IPC_BADGE_PREFIX + "EPOCH_MASK"
        assert ipc_gen._badge_mask_hex(epoch) == "0xFFFF"


class TestBadgeRendering:
    def test_badge_fields_descending(self, badge_block: BitfieldBlockSpec) -> None:
        names = [f.name for f in ipc_gen._badge_fields(badge_block)]
        assert names == ["epoch", "randtag", "perms"]

    def test_badge_shift_lines(self, badge_block: BitfieldBlockSpec) -> None:
        fields = ipc_gen._badge_fields(badge_block)
        tokens = [line.split() for line in ipc_gen._badge_shift_lines(fields)]
        assert tokens == [
            ["#define", "AIRY_BADGE_EPOCH_SHIFT", "48"],
            ["#define", "AIRY_BADGE_RANDTAG_SHIFT", "16"],
            ["#define", "AIRY_BADGE_PERMS_SHIFT", "0"],
        ]

    def test_badge_extract_lines(self, badge_block: BitfieldBlockSpec) -> None:
        fields = ipc_gen._badge_fields(badge_block)
        lines = ipc_gen._badge_extract_lines(fields)
        assert lines[0].startswith("#define AIRY_BADGE_EPOCH(b)")
        assert "AIRY_BADGE_EPOCH_MASK" in lines[0]

    def test_badge_compile_block(self, badge_block: BitfieldBlockSpec) -> None:
        fields = ipc_gen._badge_fields(badge_block)
        block = ipc_gen._badge_compile_block(fields)
        assert block.startswith("#define AIRY_BADGE_COMPILE(epoch, randtag, perms)")
        assert block.endswith(")")

    def test_badge_shift_and_mask_specs(self, badge_block: BitfieldBlockSpec) -> None:
        fields = ipc_gen._badge_fields(badge_block)
        assert [spec["value"] for spec in ipc_gen._badge_shifts(fields)] == [48, 16, 0]
        assert ipc_gen._badge_masks(fields)[0]["value_hex"] == "0xFFFF000000000000"


class TestFieldLineRendering:
    def test_c_field_lines(self, ipc_block: BitfieldBlockSpec) -> None:
        lines = ipc_gen._c_field_lines(ipc_block.fields, _offsets())
        assert any("magic" in line and "/* offset 0:" in line for line in lines)
        assert any("reserved[72]" in line for line in lines)

    def test_py_field_lines_magic_comment(self, ipc_block: BitfieldBlockSpec) -> None:
        lines = ipc_gen._py_field_lines(ipc_block.fields, _offsets())
        assert any("# offset 0, 'ARE1'" in line for line in lines)
        assert any("# offset 56" in line for line in lines)

    def test_rust_fields(self, ipc_block: BitfieldBlockSpec) -> None:
        fields = ipc_gen._rust_fields(ipc_block.fields)
        assert {"name": "magic", "rust_type": "u32"} in fields

    def test_go_field_lines_pascal(self, ipc_block: BitfieldBlockSpec) -> None:
        lines = ipc_gen._go_field_lines(ipc_block.fields)
        assert any("CapabilityBadge" in line for line in lines)

    def test_ts_fields(self, ipc_block: BitfieldBlockSpec) -> None:
        fields = ipc_gen._ts_fields(ipc_block.fields)
        assert {"name": "capabilityBadge", "ts_type": "bigint"} in fields

    def test_ts_offsets(self, ipc_block: BitfieldBlockSpec) -> None:
        offsets = ipc_gen._ts_offsets(ipc_block.fields, _offsets())
        assert {"name": "magic", "offset": 0} in offsets

    def test_reserved_bytes(self, ipc_block: BitfieldBlockSpec) -> None:
        assert ipc_gen._reserved_bytes(ipc_block.fields) == 72

    def test_assert_lines(self) -> None:
        size_lines = ipc_gen._size_assert_lines()
        assert size_lines[0].startswith(f"_Static_assert(sizeof(struct {IPC_STRUCT_NAME})")
        offset_lines = ipc_gen._offset_assert_lines(_offsets())
        assert any("offsetof(struct" in line for line in offset_lines)

    def test_go_const_helpers(self) -> None:
        assert ipc_gen._go_opcode_consts()[0]["name"] == "AiryIpcOpSend"
        assert ipc_gen._go_flag_consts()[0]["name"] == "AiryIpcFlagZerocopy"


class TestValidateTables:
    def test_field_table_happy(self, ipc_block: BitfieldBlockSpec) -> None:
        ipc_gen._validate_field_table(ipc_block, _offsets())

    def test_field_table_missing_field(self, ipc_block: BitfieldBlockSpec) -> None:
        trimmed = tuple(f for f in ipc_block.fields if f.name != "crc32")
        with pytest.raises(ContractValidationError, match="missing field crc32"):
            ipc_gen._validate_field_table(SimpleNamespace(fields=trimmed), _offsets())

    def test_field_table_offset_drift(self, ipc_block: BitfieldBlockSpec) -> None:
        drifted = tuple(
            BitfieldFieldSpec("magic", high=39, low=8) if f.name == "magic" else f
            for f in ipc_block.fields
        )
        with pytest.raises(ContractValidationError, match="magic offset/width"):
            ipc_gen._validate_field_table(SimpleNamespace(fields=drifted), _offsets())

    def test_field_table_extra_field(self, ipc_block: BitfieldBlockSpec) -> None:
        extra = ipc_block.fields + (BitfieldFieldSpec("bogus", high=1023, low=1023),)
        with pytest.raises(ContractValidationError, match="do not match IPC_FIELD_COMMENTS"):
            ipc_gen._validate_field_table(SimpleNamespace(fields=extra), _offsets())

    def test_badge_table_happy(self, badge_block: BitfieldBlockSpec) -> None:
        ipc_gen._validate_badge_table(badge_block)

    def test_badge_width_drift(self, badge_block: BitfieldBlockSpec) -> None:
        with pytest.raises(ContractValidationError, match="cap_badge width != 64"):
            ipc_gen._validate_badge_table(SimpleNamespace(width=32, fields=badge_block.fields))

    def test_badge_field_set_mismatch(self, badge_block: BitfieldBlockSpec) -> None:
        subset = tuple(f for f in badge_block.fields if f.name != "epoch")
        with pytest.raises(ContractValidationError, match="field set mismatch"):
            ipc_gen._validate_badge_table(SimpleNamespace(width=64, fields=subset))

    def test_badge_shift_drift(self, badge_block: BitfieldBlockSpec) -> None:
        drifted = tuple(
            BitfieldFieldSpec("epoch", high=47, low=32) if f.name == "epoch" else f
            for f in badge_block.fields
        )
        with pytest.raises(ContractValidationError, match="badge epoch shift/width"):
            ipc_gen._validate_badge_table(SimpleNamespace(width=64, fields=drifted))


class TestValidateBfLayout:
    def test_happy(self, repo_root: Path) -> None:
        contract = parse_bf_contract(repo_root / ipc_gen.BF64_REL)
        ipc_gen._validate_bf_layout(contract)

    def test_missing_ipc_block(self, badge_block: BitfieldBlockSpec) -> None:
        with pytest.raises(ContractValidationError, match="block ipc_msg_hdr not found"):
            ipc_gen._validate_bf_layout(BitfieldContract(blocks=(badge_block,)))

    def test_missing_badge_block(self, ipc_block: BitfieldBlockSpec) -> None:
        with pytest.raises(ContractValidationError, match="block cap_badge not found"):
            ipc_gen._validate_bf_layout(BitfieldContract(blocks=(ipc_block,)))

    def test_width_drift(self, badge_block: BitfieldBlockSpec) -> None:
        small = _struct_block("ipc_msg_hdr", 32, 0, 31)
        with pytest.raises(ContractValidationError, match="!= 1024 bits"):
            ipc_gen._validate_bf_layout(BitfieldContract(blocks=(small, badge_block)))

    def test_magic_constant_drift(
        self, repo_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        contract = parse_bf_contract(repo_root / ipc_gen.BF64_REL)
        monkeypatch.setattr(ipc_gen, "IPC_MAGIC", 0)
        with pytest.raises(ContractValidationError, match="ipc magic constant drift"):
            ipc_gen._validate_bf_layout(contract)


class TestBuildContextAndRender:
    def test_build_context_values(self, repo_root: Path) -> None:
        contract = parse_bf_contract(repo_root / ipc_gen.BF64_REL)
        context = ipc_gen.build_context(contract)
        assert context["hdr_size"] == IPC_HEADER_SIZE
        assert context["magic_hex"] == f"0x{IPC_MAGIC:08X}"
        assert context["struct_name"] == IPC_STRUCT_NAME
        assert context["badge_offset"] == 40
        assert context["reserved_offset"] == 56
        assert context["reserved_bytes"] == 72
        assert len(context["opcode_lines"]) == len(IPC_OPCODE_SPECS)
        assert len(context["flag_lines"]) == len(IPC_FLAG_SPECS)

    def test_render_outputs_paths(self, repo_root: Path) -> None:
        rendered = ipc_gen.render_outputs(repo_root)
        assert set(rendered) == {repo_root / rel for _name, rel in _OUTPUTS}
        assert all(content.endswith("\n") for content in rendered.values())

    def test_render_is_deterministic(self, repo_root: Path) -> None:
        assert ipc_gen.render_outputs(repo_root) == ipc_gen.render_outputs(repo_root)

    def test_check_matches_committed(self, repo_root: Path) -> None:
        assert ipc_gen.check(repo_root) == int(ExitCode.SUCCESS)


def test_field_comments_cover_all_layout_fields(ipc_block: BitfieldBlockSpec) -> None:
    assert {f.name for f in ipc_block.fields} == set(IPC_FIELD_COMMENTS)
