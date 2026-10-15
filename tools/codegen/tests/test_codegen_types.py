# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""codegen_types 数据模型与常量单元测试。

覆盖契约版本常量、命名空间前缀、槽位/布局常量、生成器入口契约、异常层级，
以及 syscall / capability / bitfield / IPC 四类 frozen dataclass 的构造时
校验与派生属性（__post_init__ 语义边界）。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import pytest

from codegen_types import (
    BITFIELD_WIDTH_TYPES,
    BITFIELD_WORD_TYPES,
    CAP_EXTENSION_BASE,
    CAP_ID_COUNT,
    CORE_SYSCALL_COUNT,
    FUNCTION_PREFIX,
    GENERATOR_VERSION,
    INDEX_PREFIX,
    IPC_FIELD_COMMENTS,
    IPC_FLAG_SPECS,
    IPC_HEADER_ALIGNMENT,
    IPC_HEADER_SIZE,
    IPC_LAYOUT_ASSERTS,
    IPC_MAGIC,
    IPC_OPCODE_SPECS,
    IPC_RING_DEF_ENTRIES,
    IPC_RING_MAX_ENTRIES,
    LINUX_BASE,
    PIPELINE_ORDER,
    RESERVED_SLOT_COUNT,
    SCHEMA_VERSION,
    SYMBOL_PREFIX,
    TOTAL_SLOT_COUNT,
    BitfieldBlockSpec,
    BitfieldContainer,
    BitfieldContract,
    BitfieldFieldKind,
    BitfieldFieldSpec,
    CapContract,
    CapIdSpec,
    CodegenError,
    ContractSourceError,
    ContractValidationError,
    ExitCode,
    GeneratedDriftError,
    GeneratorName,
    IpcFlagSpec,
    IpcOpcodeSpec,
    ReservedRange,
    SyscallContract,
    SyscallSpec,
    TemplateRenderError,
    bitfield_mask_literal,
    bitfield_value_type,
)

CAP_SYMBOL = "AIRY_CAP"

# ── 契约常量 ──


class TestContractConstants:
    def test_version_constants(self) -> None:
        assert GENERATOR_VERSION == "1.0.1"
        assert SCHEMA_VERSION == "1.0"

    def test_namespace_prefixes(self) -> None:
        assert SYMBOL_PREFIX == "AIRY_SYS_"
        assert INDEX_PREFIX == "AIRY_SYS_IDX_"
        assert FUNCTION_PREFIX == "airy_sys_"

    def test_syscall_slot_constants(self) -> None:
        assert LINUX_BASE == 548
        assert CORE_SYSCALL_COUNT == 4
        assert RESERVED_SLOT_COUNT == 20
        assert TOTAL_SLOT_COUNT == CORE_SYSCALL_COUNT + RESERVED_SLOT_COUNT

    def test_ipc_layout_constants(self) -> None:
        assert IPC_HEADER_SIZE == 128
        assert IPC_HEADER_ALIGNMENT == 64
        assert IPC_MAGIC == 0x41524531

    def test_ipc_ring_capacity_constants(self) -> None:
        assert IPC_RING_DEF_ENTRIES == 256
        assert IPC_RING_MAX_ENTRIES == 32768
        assert IPC_RING_DEF_ENTRIES <= IPC_RING_MAX_ENTRIES

    def test_capability_constants(self) -> None:
        assert CAP_ID_COUNT == 44
        assert CAP_EXTENSION_BASE == 41

    def test_bitfield_width_types_are_inverses(self) -> None:
        assert BITFIELD_WIDTH_TYPES == {8: "__u8", 16: "__u16", 32: "__u32", 64: "__u64"}
        assert BITFIELD_WORD_TYPES == {"__u8": 8, "__u16": 16, "__u32": 32, "__u64": 64}


class TestEnums:
    def test_generator_name_values(self) -> None:
        assert GeneratorName.CAP.value == "cap_gen"
        assert GeneratorName.BITFIELD.value == "bitfield_gen"
        assert GeneratorName.IPC.value == "ipc_gen"
        assert GeneratorName.SYSCALL.value == "syscall_gen"

    def test_exit_codes(self) -> None:
        assert ExitCode.SUCCESS == 0
        assert ExitCode.FAILURE == 1
        assert int(ExitCode.FAILURE) == 1

    def test_pipeline_order_is_dependency_sequence(self) -> None:
        assert PIPELINE_ORDER == (
            GeneratorName.CAP,
            GeneratorName.BITFIELD,
            GeneratorName.IPC,
            GeneratorName.SYSCALL,
        )


class TestExceptionHierarchy:
    def test_codegen_error_carries_message_and_code(self) -> None:
        exc = CodegenError("boom")
        assert str(exc) == "boom"
        assert exc.message == "boom"
        assert exc.code == "AIRY_CODEGEN_ERROR"

    def test_codegen_error_custom_code(self) -> None:
        exc = CodegenError("boom", code="AIRY_X")
        assert exc.code == "AIRY_X"

    @pytest.mark.parametrize(
        "exc_type",
        [ContractSourceError, ContractValidationError, TemplateRenderError, GeneratedDriftError],
    )
    def test_subclasses_are_codegen_errors(self, exc_type: type[CodegenError]) -> None:
        assert issubclass(exc_type, CodegenError)
        assert exc_type("m").code == "AIRY_CODEGEN_ERROR"


# ── ReservedRange ──


class TestReservedRange:
    def test_count_inclusive(self) -> None:
        assert ReservedRange(4, 23).count == 20

    def test_default_reason(self) -> None:
        assert ReservedRange(4, 23).reason == "future extension"

    def test_negative_start_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="invalid reserved range"):
            ReservedRange(-1, 3)

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="invalid reserved range"):
            ReservedRange(5, 4)


# ── SyscallSpec / SyscallContract ──


def _syscall(**overrides: object) -> SyscallSpec:
    fields: dict[str, object] = {
        "name": "airy_sys_call",
        "number": 0,
        "signature": "airy_cap_t cap",
        "flags": "blocking",
        "category": "ipc_primitive",
        "since": "1.0.1",
        "stability": "stable",
        "doc": "doc",
    }
    fields.update(overrides)
    return SyscallSpec(**fields)  # type: ignore[arg-type]


class TestSyscallSpec:
    def test_derived_macros_and_numbers(self) -> None:
        spec = _syscall(name="airy_sys_rovol_ctl", number=1)
        assert spec.suffix == "ROVOL_CTL"
        assert spec.symbol_macro == "AIRY_SYS_ROVOL_CTL"
        assert spec.index_macro == "AIRY_SYS_IDX_ROVOL_CTL"
        assert spec.linux_nr() == 549
        assert spec.linux_nr(1000) == 1001
        assert spec.linux_nr_macro() == "__NR_airy_sys_rovol_ctl"

    def test_name_without_prefix_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="lacks airy_sys_ prefix"):
            _syscall(name="bad_name")

    def test_negative_number_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="negative syscall number"):
            _syscall(number=-1)


class TestSyscallContract:
    def test_valid_contract_indexes_by_number(self) -> None:
        contract = _syscall_contract()
        assert contract.core_count == 1
        assert contract.syscalls_by_number[0].name == "airy_sys_call"

    def test_core_count_mismatch_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="core_count"):
            _syscall_contract(core_count=2)

    def test_reserved_count_mismatch_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="reserved_count"):
            _syscall_contract(reserved_count=99)

    def test_total_slots_mismatch_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="total_slots"):
            _syscall_contract(total_slots=99)

    def test_non_contiguous_numbers_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="contiguous"):
            _syscall_contract(syscalls=(_syscall(number=1),))


def _syscall_contract(**overrides: object) -> SyscallContract:
    fields: dict[str, object] = {
        "version": "0x0100",
        "linux_base": LINUX_BASE,
        "core_count": 1,
        "reserved_count": 4,
        "total_slots": 5,
        "syscalls": (_syscall(),),
        "reserved": (ReservedRange(1, 4),),
    }
    fields.update(overrides)
    return SyscallContract(**fields)  # type: ignore[arg-type]


# ── CapIdSpec / CapContract ──


class TestCapIdSpec:
    def test_extension_flag(self) -> None:
        assert CapIdSpec("AIRY_CAP_AGENT_SPAWN", 41, "d").is_extension is True
        assert CapIdSpec("AIRY_CAP_CHOWN", 0, "d").is_extension is False

    def test_variant_is_pascal_case(self) -> None:
        assert CapIdSpec("AIRY_CAP_AGENT_SPAWN", 41, "d").variant == "AgentSpawn"

    def test_bad_prefix_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="lacks AIRY_CAP_ prefix"):
            CapIdSpec("CAP_CHOWN", 0, "d")

    def test_value_out_of_range_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="out of range"):
            CapIdSpec("AIRY_CAP_CHOWN", CAP_ID_COUNT, "d")

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="empty description"):
            CapIdSpec("AIRY_CAP_CHOWN", 0, "")


class TestCapContract:
    def test_partitions_posix_and_extension(self) -> None:
        contract = _cap_contract()
        assert len(contract.posix_caps) == CAP_EXTENSION_BASE
        assert len(contract.extension_caps) == CAP_ID_COUNT - CAP_EXTENSION_BASE

    def test_symbol_lookup_helpers(self) -> None:
        contract = _cap_contract()
        assert contract.cap_ids_by_value[0].value == 0
        assert len(contract.symbol_names) == CAP_ID_COUNT
        assert contract.symbol_width == max(len(c.symbol) for c in contract.cap_ids) + 1

    def test_count_mismatch_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="capability id count"):
            CapContract(cap_ids=tuple(_cap(i) for i in range(CAP_ID_COUNT - 1)))

    def test_non_contiguous_values_rejected(self) -> None:
        caps = tuple(_cap(i) for i in range(CAP_ID_COUNT))
        with pytest.raises(ContractValidationError, match="contiguous"):
            CapContract(cap_ids=(_cap(1), *caps[:-1]))


def _cap(value: int) -> CapIdSpec:
    return CapIdSpec(f"AIRY_CAP_ID_{value:02d}", value, f"cap {value}")


def _cap_contract() -> CapContract:
    return CapContract(cap_ids=tuple(_cap(i) for i in range(CAP_ID_COUNT)))


# ── bitfield 辅助函数 ──


class TestBitfieldHelpers:
    @pytest.mark.parametrize(
        ("width", "expected"),
        [(1, "__u8"), (8, "__u8"), (16, "__u16"), (17, "__u32"), (32, "__u32"), (64, "__u64")],
    )
    def test_value_type_rounds_up(self, width: int, expected: str) -> None:
        assert bitfield_value_type(width) == expected

    def test_value_type_exceeds_64_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="exceeds 64 bits"):
            bitfield_value_type(65)

    @pytest.mark.parametrize(
        ("width", "expected"),
        [(16, "0xffffu"), (32, "0xffffffffu"), (33, "0x1ffffffffULL"), (64, "0xffffffffffffffffULL")],
    )
    def test_mask_literal_suffix(self, width: int, expected: str) -> None:
        assert bitfield_mask_literal(width) == expected


# ── BitfieldFieldSpec ──


class TestBitfieldFieldSpec:
    def test_geometry_properties(self) -> None:
        field = BitfieldFieldSpec("magic", high=31, low=0)
        assert field.width == 32
        assert field.offset == 0
        assert field.upper == "MAGIC"
        assert field.value_type == "__u32"
        assert field.mask_literal == "0xffffffffu"

    def test_kind_derived_predicates(self) -> None:
        padding = BitfieldFieldSpec("", high=7, low=0, kind=BitfieldFieldKind.PADDING)
        mask = BitfieldFieldSpec("m", high=7, low=0, kind=BitfieldFieldKind.MASK)
        assert padding.is_padding is True
        assert padding.is_readable is False
        assert padding.is_writable is False
        assert mask.is_readable is True
        assert mask.is_writable is False

    def test_byte_alignment(self) -> None:
        assert BitfieldFieldSpec("a", high=15, low=8).byte_aligned is True
        assert BitfieldFieldSpec("a", high=11, low=4).byte_aligned is False

    def test_invalid_range_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="invalid bitfield range"):
            BitfieldFieldSpec("a", high=0, low=1)


# ── BitfieldBlockSpec / BitfieldContract ──


def _struct_block(**overrides: object) -> BitfieldBlockSpec:
    fields: dict[str, object] = {
        "name": "blk",
        "width": 32,
        "container": BitfieldContainer.STRUCT,
        "ctype": "airy_demo",
        "fields": (
            BitfieldFieldSpec("lo", high=15, low=0),
            BitfieldFieldSpec("hi", high=31, low=16),
        ),
        "header": "linux/airymax/demo.h",
    }
    fields.update(overrides)
    return BitfieldBlockSpec(**fields)  # type: ignore[arg-type]


def _word_block(**overrides: object) -> BitfieldBlockSpec:
    fields: dict[str, object] = {
        "name": "cap_badge",
        "width": 64,
        "container": BitfieldContainer.WORD,
        "ctype": "__u64",
        "fields": (
            BitfieldFieldSpec("perms", high=15, low=0),
            BitfieldFieldSpec("randtag", high=47, low=16),
            BitfieldFieldSpec("epoch", high=63, low=48),
        ),
    }
    fields.update(overrides)
    return BitfieldBlockSpec(**fields)  # type: ignore[arg-type]


class TestBitfieldBlockSpec:
    def test_struct_block_properties(self) -> None:
        block = _struct_block(pivot="lo")
        assert block.macro_prefix == "AIRY_BLK"
        assert block.function_prefix == "airy_blk"
        assert block.include_header == "<linux/airymax/demo.h>"
        assert [f.name for f in block.accessors] == ["lo", "hi"]
        assert [f.name for f in block.writable_fields] == ["lo", "hi"]

    def test_word_block_includes_no_header(self) -> None:
        assert _word_block().include_header == ""

    def test_padding_fields_are_named_only(self) -> None:
        block = _struct_block(
            fields=(
                BitfieldFieldSpec("lo", high=7, low=0),
                BitfieldFieldSpec("pad", high=31, low=8, kind=BitfieldFieldKind.PADDING),
            )
        )
        assert [f.name for f in block.padding_fields] == ["pad"]
        assert [f.name for f in block.accessors] == ["lo"]

    def test_non_positive_width_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="non-positive width"):
            _struct_block(width=0, fields=(BitfieldFieldSpec("a", high=0, low=0),))

    def test_missing_ctype_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="missing bind target type"):
            _struct_block(ctype="")

    def test_struct_without_header_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="struct bind requires a header"):
            _struct_block(header="")

    def test_word_width_mismatch_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="word width"):
            _word_block(width=32)

    def test_no_fields_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="no fields declared"):
            _struct_block(fields=())

    def test_fields_must_start_at_zero(self) -> None:
        with pytest.raises(ContractValidationError, match="must start at bit 0"):
            _struct_block(fields=(BitfieldFieldSpec("hi", high=31, low=16),))

    def test_fields_must_cover_full_width(self) -> None:
        with pytest.raises(ContractValidationError, match="must cover up to bit"):
            _struct_block(fields=(BitfieldFieldSpec("lo", high=15, low=0),))

    def test_gap_between_fields_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="gap or overlap"):
            _struct_block(
                fields=(
                    BitfieldFieldSpec("lo", high=7, low=0),
                    BitfieldFieldSpec("hi", high=31, low=16),
                )
            )

    def test_duplicate_field_name_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="duplicate field"):
            _struct_block(
                fields=(
                    BitfieldFieldSpec("a", high=15, low=0),
                    BitfieldFieldSpec("a", high=31, low=16),
                )
            )

    def test_non_byte_aligned_struct_field_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="must be 8/16/32/64-bit byte aligned"):
            _struct_block(width=24, fields=(BitfieldFieldSpec("a", high=23, low=0),))

    def test_unaligned_padding_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="must be byte aligned"):
            _struct_block(
                fields=(
                    BitfieldFieldSpec("p", high=4, low=0, kind=BitfieldFieldKind.PADDING),
                    BitfieldFieldSpec("a", high=31, low=5),
                )
            )

    def test_pivot_must_reference_declared_field(self) -> None:
        with pytest.raises(ContractValidationError, match="pivot ghost is not a declared field"):
            _struct_block(pivot="ghost")


class TestBitfieldContract:
    def test_blocks_by_name_and_includes(self) -> None:
        contract = BitfieldContract(blocks=(_struct_block(), _word_block()))
        assert set(contract.blocks_by_name) == {"blk", "cap_badge"}
        assert contract.includes == ("<linux/airymax/demo.h>",)

    def test_layout_signature_shape(self) -> None:
        signature = BitfieldContract(blocks=(_word_block(),)).layout_signature()
        name, width, container, ctype, header, pivot, fields = signature[0]
        assert (name, width, container, ctype, header, pivot) == (
            "cap_badge",
            64,
            "word",
            "__u64",
            "",
            "",
        )
        assert tuple((f[0], f[1], f[2]) for f in fields) == (
            ("perms", 15, 0),
            ("randtag", 47, 16),
            ("epoch", 63, 48),
        )

    def test_empty_contract_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="no blocks"):
            BitfieldContract(blocks=())

    def test_duplicate_block_names_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="duplicate block names"):
            BitfieldContract(blocks=(_word_block(), _word_block()))


# ── IPC 契约模型 ──


class TestIpcSpecs:
    def test_opcode_macro_and_hex(self) -> None:
        op = IpcOpcodeSpec("SEND", 0x0001, "Unicast send")
        assert op.macro == "AIRY_IPC_OP_SEND"
        assert op.value_hex == "0x0001"

    def test_flag_macro_and_hex(self) -> None:
        flag = IpcFlagSpec("RESERVED", 0xFFE0, "bits")
        assert flag.macro == "AIRY_IPC_FLAG_RESERVED"
        assert flag.value_hex == "0xFFE0"

    @pytest.mark.parametrize("value", [0, 0x10000])
    def test_opcode_value_out_of_range_rejected(self, value: int) -> None:
        with pytest.raises(ContractValidationError, match="out of range"):
            IpcOpcodeSpec("SEND", value, "c")

    def test_opcode_empty_suffix_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="suffix must not be empty"):
            IpcOpcodeSpec("", 1, "c")

    def test_opcode_empty_comment_rejected(self) -> None:
        with pytest.raises(ContractValidationError, match="empty comment"):
            IpcOpcodeSpec("SEND", 1, "")


class TestIpcSpecTables:
    def test_opcode_table_contents(self) -> None:
        names = {op.suffix for op in IPC_OPCODE_SPECS}
        assert names == {
            "SEND",
            "RECV",
            "SEND_BATCH",
            "CANCEL",
            "FREEZE",
            "CAP_REQUEST",
            "CAP_RESPONSE",
        }

    def test_flag_table_contents(self) -> None:
        names = {flag.suffix for flag in IPC_FLAG_SPECS}
        assert names == {
            "ZEROCOPY",
            "CAP_CARRY",
            "ENCRYPT",
            "COMPRESS",
            "BATCH_TAIL",
            "RESERVED",
        }

    def test_field_comments_cover_layout(self) -> None:
        assert set(IPC_FIELD_COMMENTS) == {
            "magic",
            "opcode",
            "flags",
            "trace_id",
            "timestamp_ns",
            "src_task",
            "dst_task",
            "capability_badge",
            "payload_len",
            "crc32",
            "reserved",
        }

    def test_layout_asserts_reference_expected_fields(self) -> None:
        assert {a.field for a in IPC_LAYOUT_ASSERTS} == {
            "magic",
            "opcode",
            "capability_badge",
            "payload_len",
            "crc32",
            "reserved",
        }
        assert all(a.message for a in IPC_LAYOUT_ASSERTS)
