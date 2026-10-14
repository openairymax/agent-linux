#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""ipc_gen — AirymaxOS A-IPC 消息头多语言绑定生成器。

以 R-02 位域契约源 structures_64.bf 的 ipc_msg_hdr / cap_badge 块为布局
SSoT，并以 contracts.md §2（Layout C v4）为语义 SSoT，生成 128 字节定长
IPC 消息头的多语言绑定：
  - C:          kernel/include/uapi/linux/airymax/ipc.h
  - Rust:       sdk/rust/airyx/ipc.rs
  - Python:     sdk/python/airyx/ipc.py
  - Go:         sdk/go/airyx/ipc.go
  - TypeScript: sdk/ts/airyx/ipc.ts

magic 值 0x41524531（'ARE1'）由本生成器自动注入，不在契约源重复声明。
生成前强制校验 .bf 布局与 contracts.md §2 的期望布局（字段偏移/位宽、
Badge 位分配）逐位一致，避免 R-02 漂移静默传播到 [SC] 头与 SDK 绑定。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import sys
from pathlib import Path

import codegen_io

from bitfield_gen import parse_bf_contract
from codegen_types import (
    GENERATOR_VERSION,
    IPC_BADGE_PREFIX,
    IPC_FIELD_COMMENTS,
    IPC_FLAG_SPECS,
    IPC_HEADER_ALIGNMENT,
    IPC_HEADER_SIZE,
    IPC_LAYOUT_ASSERTS,
    IPC_MAGIC,
    IPC_MAX_PAYLOAD,
    IPC_MIN_STRUCT_NAME,
    IPC_OPCODE_SPECS,
    IPC_SIZE_ASSERT_MESSAGE,
    IPC_STRUCT_NAME,
    BitfieldContract,
    BitfieldFieldSpec,
    ContractValidationError,
    bitfield_value_type,
)

GENERATOR = "ipc_gen.py"

# ── 契约源与生成目标（相对 agent-linux/ 仓库根） ──
_UAPI_DIR = Path("kernel") / "include" / "uapi" / "linux" / "airymax"
BF64_REL = _UAPI_DIR / "structures_64.bf"
IPC_H_REL = _UAPI_DIR / "ipc.h"
IPC_RS_REL = Path("sdk") / "rust" / "airyx" / "ipc.rs"
IPC_PY_REL = Path("sdk") / "python" / "airyx" / "ipc.py"
IPC_GO_REL = Path("sdk") / "go" / "airyx" / "ipc.go"
IPC_TS_REL = Path("sdk") / "ts" / "airyx" / "ipc.ts"

IPC_GUARD = "_UAPI_AIRYMAX_IPC_H"
SOURCE_NAME = "contracts.md §2"
IPC_BLOCK_NAME = "ipc_msg_hdr"
BADGE_BLOCK_NAME = "cap_badge"
IPC_MAGIC_MACRO = "AIRY_IPC_MAGIC"
IPC_HDR_SIZE_MACRO = "AIRY_IPC_HDR_SIZE"
IPC_MAX_PAYLOAD_MACRO = "AIRY_IPC_MAX_PAYLOAD"
MAGIC_LITERAL = f"0x{IPC_MAGIC:08X}"

OUTPUTS: tuple[tuple[str, Path], ...] = (
    ("ipc.h.j2", IPC_H_REL),
    ("ipc.rs.j2", IPC_RS_REL),
    ("ipc.py.j2", IPC_PY_REL),
    ("ipc.go.j2", IPC_GO_REL),
    ("ipc.ts.j2", IPC_TS_REL),
)

# contracts.md §2 Layout C v4 期望布局（字段名 → (字节偏移, 位宽)）。
_EXPECTED_IPC_LAYOUT: tuple[tuple[str, int, int], ...] = (
    ("magic", 0, 32),
    ("opcode", 4, 16),
    ("flags", 6, 16),
    ("trace_id", 8, 64),
    ("timestamp_ns", 16, 64),
    ("src_task", 24, 64),
    ("dst_task", 32, 64),
    ("capability_badge", 40, 64),
    ("payload_len", 48, 32),
    ("crc32", 52, 32),
    ("reserved", 56, 576),
)

# Badge 64 位 Native Word 期望位分配（字段名 → (移位, 位宽)）。
_EXPECTED_BADGE_LAYOUT: dict[str, tuple[int, int]] = {
    "epoch": (48, 16),
    "randtag": (16, 32),
    "perms": (0, 16),
}

_BADGE_COMMENTS: dict[str, str] = {
    "epoch": "Epoch (16-bit generation counter)",
    "randtag": "RandomTag (32-bit anti-forgery tag)",
    "perms": "Permissions (16-bit AIRY_CAP_PERM_*)",
}

_CTYPE_TO_RUST: dict[str, str] = {"__u16": "u16", "__u32": "u32", "__u64": "u64"}
_CTYPE_TO_PY: dict[str, str] = {
    "__u16": "ctypes.c_uint16",
    "__u32": "ctypes.c_uint32",
    "__u64": "ctypes.c_uint64",
}
_CTYPE_TO_GO: dict[str, str] = {"__u16": "uint16", "__u32": "uint32", "__u64": "uint64"}


def _expected_offsets() -> dict[str, int]:
    return {name: offset for name, offset, _ in _EXPECTED_IPC_LAYOUT}


def _validate_field_table(ipc_block, offsets: dict[str, int]) -> None:
    by_name = {f.name: f for f in ipc_block.fields}
    for name, offset, width in _EXPECTED_IPC_LAYOUT:
        field = by_name.get(name)
        if field is None:
            raise ContractValidationError(f"{BF64_REL}: {IPC_BLOCK_NAME} missing field {name}")
        if field.low // 8 != offset or field.width != width:
            raise ContractValidationError(
                f"{BF64_REL}: {name} offset/width {field.low // 8}/{field.width} "
                f"!= contracts.md §2 {offset}/{width}"
            )
    if set(by_name) != set(IPC_FIELD_COMMENTS):
        raise ContractValidationError(
            f"{BF64_REL}: {IPC_BLOCK_NAME} fields do not match IPC_FIELD_COMMENTS"
        )
    if set(by_name) != set(offsets):
        raise ContractValidationError(f"{BF64_REL}: {IPC_BLOCK_NAME} offset table drift")


def _validate_badge_table(badge_block) -> None:
    by_name = {f.name: f for f in badge_block.fields}
    if badge_block.width != 64:
        raise ContractValidationError(f"{BF64_REL}: {BADGE_BLOCK_NAME} width != 64")
    if set(by_name) != set(_EXPECTED_BADGE_LAYOUT):
        raise ContractValidationError(f"{BF64_REL}: {BADGE_BLOCK_NAME} field set mismatch")
    for name, (shift, width) in _EXPECTED_BADGE_LAYOUT.items():
        field = by_name[name]
        if field.low != shift or field.width != width:
            raise ContractValidationError(
                f"{BF64_REL}: badge {name} shift/width {field.low}/{field.width} "
                f"!= contracts.md §2 {shift}/{width}"
            )


def _validate_bf_layout(contract: BitfieldContract) -> None:
    """校验 .bf 布局与 contracts.md §2 期望布局逐位一致（§9.1）。"""
    blocks = contract.blocks_by_name
    ipc_block = blocks.get(IPC_BLOCK_NAME)
    badge_block = blocks.get(BADGE_BLOCK_NAME)
    if ipc_block is None:
        raise ContractValidationError(f"{BF64_REL}: block {IPC_BLOCK_NAME} not found")
    if badge_block is None:
        raise ContractValidationError(f"{BF64_REL}: block {BADGE_BLOCK_NAME} not found")
    if ipc_block.width != IPC_HEADER_SIZE * 8:
        raise ContractValidationError(
            f"{BF64_REL}: {IPC_BLOCK_NAME} width {ipc_block.width} "
            f"!= {IPC_HEADER_SIZE * 8} bits"
        )
    if IPC_MAGIC != 0x41524531:
        raise ContractValidationError(f"ipc magic constant drift: {MAGIC_LITERAL}")
    offsets = {name: offset for name, offset, _ in _EXPECTED_IPC_LAYOUT}
    _validate_field_table(ipc_block, offsets)
    _validate_badge_table(badge_block)


def _ctypes(field: BitfieldFieldSpec) -> tuple[str, str]:
    """返回字段的 (C 类型, Rust 类型)。padding 以字节数组承载。"""
    if field.is_padding:
        return "__u8", f"[u8; {field.width // 8}]"
    ctype = bitfield_value_type(field.width)
    return ctype, _CTYPE_TO_RUST[ctype]


def _py_ctype(field: BitfieldFieldSpec) -> str:
    if field.is_padding:
        return f"ctypes.c_uint8 * {field.width // 8}"
    return _CTYPE_TO_PY[bitfield_value_type(field.width)]


def _constant_lines() -> list[str]:
    return [
        "#define " + IPC_MAGIC_MACRO.ljust(23) + " " + f"{MAGIC_LITERAL}u /* 'ARE1' */",
        "#define " + IPC_HDR_SIZE_MACRO.ljust(23) + " " + str(IPC_HEADER_SIZE),
        "#define " + IPC_MAX_PAYLOAD_MACRO.ljust(23) + " "
        + f"0x{IPC_MAX_PAYLOAD:08X}u /* 64 MiB payload ceiling */",
    ]


def _opcode_lines() -> list[str]:
    return [
        "#define " + (op.macro.ljust(23) + " " + op.value_hex).ljust(32) + f"/* {op.comment} */"
        for op in IPC_OPCODE_SPECS
    ]


def _flag_lines() -> list[str]:
    return [
        "#define "
        + (flag.macro.ljust(24) + " " + flag.value_hex).ljust(33)
        + f"/* {flag.comment} */"
        for flag in IPC_FLAG_SPECS
    ]


def _badge_fields(badge_block) -> tuple[BitfieldFieldSpec, ...]:
    """Badge 字段按移位降序排列（epoch → randtag → perms）。"""
    return tuple(sorted(badge_block.fields, key=lambda f: f.low, reverse=True))


def _badge_shift_macro(field: BitfieldFieldSpec) -> str:
    return IPC_BADGE_PREFIX + field.name.upper() + "_SHIFT"


def _badge_mask_macro(field: BitfieldFieldSpec) -> str:
    return IPC_BADGE_PREFIX + field.name.upper() + "_MASK"


def _badge_mask_hex(field: BitfieldFieldSpec) -> str:
    return f"0x{(1 << field.width) - 1:X}"


def _badge_shift_lines(fields: tuple[BitfieldFieldSpec, ...]) -> list[str]:
    return [
        "#define " + _badge_shift_macro(f).ljust(23) + " " + str(f.low) for f in fields
    ]


def _badge_mask_lines(fields: tuple[BitfieldFieldSpec, ...]) -> list[str]:
    return [
        "#define "
        + _badge_mask_macro(f).ljust(23)
        + " ((__u64)"
        + _badge_mask_hex(f)
        + " << "
        + _badge_shift_macro(f)
        + ")"
        for f in fields
    ]


def _badge_extract_lines(fields: tuple[BitfieldFieldSpec, ...]) -> list[str]:
    return [
        "#define "
        + (IPC_BADGE_PREFIX + f.name.upper() + "(b)").ljust(21)
        + " "
        + f"(((b) & {_badge_mask_macro(f)}) >> {_badge_shift_macro(f)})"
        for f in fields
    ]


def _badge_compile_block(fields: tuple[BitfieldFieldSpec, ...]) -> str:
    params = ", ".join(f.name for f in fields)
    parts = []
    for index, field in enumerate(fields):
        indent = "\t(" if index == 0 else "\t "
        expr = (
            f"((__u64)(({field.name}) & {_badge_mask_hex(field)})"
            f" << {_badge_shift_macro(field)})"
        )
        parts.append(indent + expr)
    body = " | \\\n".join(parts) + ")"
    return f"#define AIRY_BADGE_COMPILE({params}) \\\n{body}"


def _c_field_lines(
    fields: tuple[BitfieldFieldSpec, ...], offsets: dict[str, int]
) -> list[str]:
    lines = []
    for field in fields:
        ctype, _ = _ctypes(field)
        decl = f"{field.name}[{field.width // 8}];" if field.is_padding else f"{field.name};"
        comment = IPC_FIELD_COMMENTS[field.name]
        head = "\t" + ctype.ljust(8) + decl.ljust(19)
        lines.append(head + "/* offset " + f"{offsets[field.name]}:".ljust(4) + comment + " */")
    return lines


def _rust_fields(fields: tuple[BitfieldFieldSpec, ...]) -> list[dict[str, str]]:
    return [{"name": f.name, "rust_type": _ctypes(f)[1]} for f in fields]


def _py_field_lines(
    fields: tuple[BitfieldFieldSpec, ...], offsets: dict[str, int]
) -> list[str]:
    lines = []
    for field in fields:
        head = ('("' + field.name + '",').ljust(21)
        line = head + _py_ctype(field) + "),"
        if field.name == "magic":
            line += f"  # offset {offsets[field.name]}, 'ARE1'"
        else:
            line += f"  # offset {offsets[field.name]}"
        lines.append(line)
    return lines


def _camel(name: str) -> str:
    """把蛇形名（capability_badge）转为 camelCase（Go/TS 标识符惯例）。"""
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _pascal(name: str) -> str:
    """把蛇形名（capability_badge）转为 PascalCase（CapabilityBadge）。"""
    return "".join(part.capitalize() for part in name.split("_"))


def _go_ctype(field: BitfieldFieldSpec) -> str:
    """返回 Go 字段类型（padding 以定长字节数组承载）。"""
    if field.is_padding:
        return f"[{field.width // 8}]byte"
    return _CTYPE_TO_GO[bitfield_value_type(field.width)]


def _go_opcode_consts() -> list[dict[str, object]]:
    return [
        {"name": "AiryIpcOp" + _pascal(op.suffix), "value": op.value_hex, "comment": op.comment}
        for op in IPC_OPCODE_SPECS
    ]


def _go_flag_consts() -> list[dict[str, object]]:
    return [
        {
            "name": "AiryIpcFlag" + _pascal(flag.suffix),
            "value": flag.value_hex,
            "comment": flag.comment,
        }
        for flag in IPC_FLAG_SPECS
    ]


def _go_badge_shift_consts(fields: tuple[BitfieldFieldSpec, ...]) -> list[dict[str, object]]:
    return [
        {
            "name": "AiryBadge" + _pascal(f.name) + "Shift",
            "value": f.low,
            "comment": _BADGE_COMMENTS[f.name],
        }
        for f in fields
    ]


def _go_badge_mask_consts(fields: tuple[BitfieldFieldSpec, ...]) -> list[dict[str, str]]:
    return [
        {
            "name": "AiryBadge" + _pascal(f.name) + "Mask",
            "value_hex": f"0x{((1 << f.width) - 1) << f.low:X}",
            "comment": _BADGE_COMMENTS[f.name],
        }
        for f in fields
    ]


def _go_field_lines(fields: tuple[BitfieldFieldSpec, ...]) -> list[str]:
    entries = [(_pascal(f.name), _go_ctype(f)) for f in fields]
    width = max(len(name) for name, _ in entries) + 1
    return ["\t" + name.ljust(width) + go_type for name, go_type in entries]


def _ts_field_type(field: BitfieldFieldSpec) -> str:
    """返回 TS 字段类型（padding→Uint8Array，64 位→bigint，其余→number）。"""
    if field.is_padding:
        return "Uint8Array"
    return "bigint" if field.width == 64 else "number"


def _ts_fields(fields: tuple[BitfieldFieldSpec, ...]) -> list[dict[str, str]]:
    return [{"name": _camel(f.name), "ts_type": _ts_field_type(f)} for f in fields]


def _ts_offsets(
    fields: tuple[BitfieldFieldSpec, ...], offsets: dict[str, int]
) -> list[dict[str, object]]:
    return [{"name": _camel(f.name), "offset": offsets[f.name]} for f in fields]


def _reserved_bytes(fields: tuple[BitfieldFieldSpec, ...]) -> int:
    """返回保留填充区的字节数（.bf padding 字段）。"""
    return next(f.width // 8 for f in fields if f.is_padding)


def _size_assert_lines() -> list[str]:
    return [
        f"_Static_assert(sizeof(struct {IPC_STRUCT_NAME}) == {IPC_HDR_SIZE_MACRO},",
        f'\t       "{IPC_SIZE_ASSERT_MESSAGE}");',
    ]


def _offset_assert_lines(offsets: dict[str, int]) -> list[str]:
    lines = []
    for assert_spec in IPC_LAYOUT_ASSERTS:
        offset = offsets[assert_spec.field]
        lines.append(
            f"_Static_assert(offsetof(struct {IPC_STRUCT_NAME}, {assert_spec.field})"
            f" == {offset},"
        )
        lines.append(f'\t       "{assert_spec.message}");')
    return lines


def _badge_shifts(fields: tuple[BitfieldFieldSpec, ...]) -> list[dict[str, object]]:
    return [
        {"macro": _badge_shift_macro(f), "value": f.low, "comment": _BADGE_COMMENTS[f.name]}
        for f in fields
    ]


def _badge_masks(fields: tuple[BitfieldFieldSpec, ...]) -> list[dict[str, str]]:
    return [
        {
            "macro": _badge_mask_macro(f),
            "value_hex": f"0x{((1 << f.width) - 1) << f.low:X}",
            "comment": _BADGE_COMMENTS[f.name],
        }
        for f in fields
    ]


def build_context(contract: BitfieldContract) -> dict[str, object]:
    """构造三模板统一的渲染上下文。"""
    blocks = contract.blocks_by_name
    fields = blocks[IPC_BLOCK_NAME].fields
    badge_fields = _badge_fields(blocks[BADGE_BLOCK_NAME])
    offsets = _expected_offsets()
    return {
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "source_name": SOURCE_NAME,
        "guard": IPC_GUARD,
        "hdr_size": IPC_HEADER_SIZE,
        "hdr_size_macro": IPC_HDR_SIZE_MACRO,
        "magic_hex": MAGIC_LITERAL,
        "alignment": IPC_HEADER_ALIGNMENT,
        "struct_name": IPC_STRUCT_NAME,
        "min_struct_name": IPC_MIN_STRUCT_NAME,
        "constant_lines": _constant_lines(),
        "opcode_lines": _opcode_lines(),
        "flag_lines": _flag_lines(),
        "badge_shift_lines": _badge_shift_lines(badge_fields),
        "badge_mask_lines": _badge_mask_lines(badge_fields),
        "badge_compile_block": _badge_compile_block(badge_fields),
        "badge_extract_lines": _badge_extract_lines(badge_fields),
        "opcodes": IPC_OPCODE_SPECS,
        "flags": IPC_FLAG_SPECS,
        "badge_shifts": _badge_shifts(badge_fields),
        "badge_masks": _badge_masks(badge_fields),
        "fields": _rust_fields(fields),
        "field_lines": _c_field_lines(fields, offsets),
        "py_field_lines": _py_field_lines(fields, offsets),
        "go_opcodes": _go_opcode_consts(),
        "go_flags": _go_flag_consts(),
        "go_badge_shifts": _go_badge_shift_consts(badge_fields),
        "go_badge_masks": _go_badge_mask_consts(badge_fields),
        "go_field_lines": _go_field_lines(fields),
        "ts_fields": _ts_fields(fields),
        "ts_offsets": _ts_offsets(fields, offsets),
        "badge_offset": offsets["capability_badge"],
        "reserved_offset": offsets["reserved"],
        "reserved_bytes": _reserved_bytes(fields),
        "size_assert_lines": _size_assert_lines(),
        "offset_assert_lines": _offset_assert_lines(offsets),
    }


def render_outputs(repo_root: Path) -> dict[Path, str]:
    """解析契约源、校验布局并渲染全部产物，返回 {目标路径: 内容}。"""
    contract = parse_bf_contract(repo_root / BF64_REL)
    _validate_bf_layout(contract)
    context = build_context(contract)
    env = codegen_io.make_env()
    return {
        repo_root / rel: codegen_io.render(env, template_name, context)
        for template_name, rel in OUTPUTS
    }


def generate(repo_root: Path) -> int:
    """生成全部 IPC 绑定产物（run_codegen 统一入口）。"""
    return codegen_io.generate(render_outputs, repo_root)


def check(repo_root: Path) -> int:
    """校验生成内容与已提交文件一致（CI 零 diff，§9.3）。"""
    return codegen_io.check(render_outputs, repo_root, generator="ipc_gen", make_target="ipc")


def main(argv: list[str] | None = None) -> int:
    return codegen_io.main(
        render_outputs,
        description="structures_64.bf ipc_msg_hdr/cap_badge → C/Rust/Python/Go/TypeScript IPC bindings",
        generator="ipc_gen",
        make_target="ipc",
        argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
