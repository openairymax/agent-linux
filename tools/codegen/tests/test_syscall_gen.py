# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""syscall_gen 单元测试：契约解析、类型词汇表、多语言绑定辅助、渲染与校验。

设计依据: docs-linux/30-interfaces/06-codegen-pipeline.md §2 / §8 / §9.3

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

from pathlib import Path

import pytest

import syscall_gen
from codegen_types import (
    ContractSourceError,
    ContractValidationError,
    ExitCode,
    SyscallSpec,
)


def _spec(
    name: str = "airy_sys_call",
    number: int = 0,
    signature: str = "airy_cap_t cap, const struct airy_ipc_msg_hdr *msg",
    *,
    doc: str = "Unified capability invocation.",
    capability_required: str = "",
) -> SyscallSpec:
    return SyscallSpec(
        name=name,
        number=number,
        signature=signature,
        flags="blocking",
        category="ipc_primitive",
        since="1.0.1",
        stability="stable",
        doc=doc,
        capability_required=capability_required,
    )


class TestParseContract:
    def test_real_contract_meta(self, repo_root: Path) -> None:
        contract = syscall_gen.parse_contract(repo_root / syscall_gen.SYSCALL_XML_REL)
        assert contract.version == "0x0100"
        assert contract.linux_base == 548
        assert contract.core_count == 4
        assert contract.reserved_count == 20
        assert contract.total_slots == 24
        assert contract.generator == "syscall_gen.py"
        assert contract.generator_version == "1.0.1"
        assert contract.schema_version == "1.0"

    def test_real_contract_syscalls(self, repo_root: Path) -> None:
        contract = syscall_gen.parse_contract(repo_root / syscall_gen.SYSCALL_XML_REL)
        names = [sc.name for sc in contract.syscalls]
        assert names == [
            "airy_sys_call",
            "airy_sys_rovol_ctl",
            "airy_sys_sched_ctl",
            "airy_sys_clt_notify",
        ]
        assert contract.syscalls_by_number[0].name == "airy_sys_call"
        assert contract.syscalls[1].signature == "__u32 op, __u32 pid, __u64 arg"

    def test_real_contract_reserved(self, repo_root: Path) -> None:
        contract = syscall_gen.parse_contract(repo_root / syscall_gen.SYSCALL_XML_REL)
        assert len(contract.reserved) == 1
        assert contract.reserved[0].start == 4
        assert contract.reserved[0].end == 23
        assert contract.reserved[0].count == 20

    def test_spec_derived_names(self, repo_root: Path) -> None:
        contract = syscall_gen.parse_contract(repo_root / syscall_gen.SYSCALL_XML_REL)
        spec = contract.syscalls[1]
        assert spec.suffix == "ROVOL_CTL"
        assert spec.symbol_macro == "AIRY_SYS_ROVOL_CTL"
        assert spec.index_macro == "AIRY_SYS_IDX_ROVOL_CTL"
        assert spec.linux_nr(548) == 549
        assert spec.linux_nr_macro() == "__NR_airy_sys_rovol_ctl"

    def test_missing_source_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ContractSourceError, match="not found"):
            syscall_gen.parse_contract(tmp_path / "nope.xml")

    def test_bad_xml_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "syscall.xml"
        bad.write_text("<syscalls><broken>", encoding="utf-8")
        with pytest.raises(ContractSourceError, match="XML parse error"):
            syscall_gen.parse_contract(bad)

    def test_malformed_attribute_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "syscall.xml"
        bad.write_text('<syscalls core_count="abc"></syscalls>', encoding="utf-8")
        with pytest.raises(ContractSourceError, match="malformed syscall contract"):
            syscall_gen.parse_contract(bad)

    def test_child_helpers(self) -> None:
        import xml.etree.ElementTree as ET

        elem = ET.fromstring("<syscall><name>airy_sys_call</name><number>7</number></syscall>")
        assert syscall_gen._child_text(elem, "name") == "airy_sys_call"
        assert syscall_gen._child_text(elem, "missing") == ""
        assert syscall_gen._child_int(elem, "number") == 7
        assert syscall_gen._child_int(elem, "missing", default=3) == 3


class TestParamHelpers:
    def test_parse_params(self) -> None:
        assert syscall_gen._parse_params("void") == []
        assert syscall_gen._parse_params("") == []
        assert syscall_gen._parse_params("__u32 op, __u32 pid, __u64 arg") == [
            "__u32 op",
            "__u32 pid",
            "__u64 arg",
        ]

    def test_normalize_type(self) -> None:
        assert syscall_gen._normalize_type("const char", True) == "const char *"
        assert syscall_gen._normalize_type("airy_cap_t", False) == "airy_cap_t"

    def test_split_param(self) -> None:
        c_type, name = syscall_gen._split_param("const char *cgroup_path", "airy_sys_sched_ctl")
        assert c_type == "const char *"
        assert name == "cgroup_path"

    def test_split_param_malformed(self) -> None:
        with pytest.raises(ContractValidationError, match="malformed parameter"):
            syscall_gen._split_param("voidx", "airy_sys_call")

    def test_lookup_types_known(self) -> None:
        assert syscall_gen._lookup_types("__u64", "airy_sys_call") == (
            "u64",
            "int",
            "ctypes.c_uint64",
            "uint64",
        )

    def test_lookup_types_unknown_raises(self) -> None:
        with pytest.raises(ContractValidationError, match="unsupported parameter type"):
            syscall_gen._lookup_types("frobnicate", "airy_sys_call")

    def test_camel_and_pascal(self) -> None:
        assert syscall_gen._camel("cgroup_path") == "cgroupPath"
        assert syscall_gen._camel("pid") == "pid"
        assert syscall_gen._pascal("ROVOL_CTL") == "RovolCtl"
        assert syscall_gen._pascal("CALL") == "Call"

    def test_param_binding_value_and_pointer(self) -> None:
        value = syscall_gen._param_binding("__u32 op", "airy_sys_rovol_ctl")
        assert value["name"] == "op"
        assert value["c_type"] == "__u32"
        assert value["rust_type"] == "u32"
        assert value["py_type"] == "int"
        assert value["py_call"] == "ctypes.c_uint32(op)"
        assert value["go_name"] == "op"
        assert value["go_type"] == "uint32"
        assert value["go_arg"] == "uintptr(op)"

        pointer = syscall_gen._param_binding("const char *cgroup_path", "airy_sys_sched_ctl")
        assert pointer["py_call"] == "cgroup_path"
        assert pointer["go_type"] == "unsafe.Pointer"

    def test_go_arg_list_pads_and_rejects(self) -> None:
        one = [syscall_gen._param_binding("__u32 op", "airy_sys_rovol_ctl")]
        assert syscall_gen._go_arg_list(one, "airy_sys_rovol_ctl") == "uintptr(op), 0, 0"

        four = [syscall_gen._param_binding("__u32 f0", "airy_sys_call")]
        with pytest.raises(ContractValidationError, match="exceed the 3-register"):
            syscall_gen._go_arg_list(four * 4, "airy_sys_call")

    def test_any_needs_unsafe(self) -> None:
        pointer = [syscall_gen._param_binding("const char *p", "airy_sys_sched_ctl")]
        assert syscall_gen._any_needs_unsafe(pointer) is True
        scalar = [syscall_gen._param_binding("__u32 op", "airy_sys_rovol_ctl")]
        assert syscall_gen._any_needs_unsafe(scalar) is False


class TestLineBuilders:
    def test_doc_lines_short(self) -> None:
        lines = syscall_gen._doc_lines(_spec(doc="Short doc."))
        assert lines == [" * airy_sys_call - Short doc."]

    def test_doc_lines_wraps(self) -> None:
        long_doc = "word " * 40
        lines = syscall_gen._doc_lines(_spec(doc=long_doc.strip()))
        assert len(lines) == 2
        assert lines[0].startswith(" * airy_sys_call - ")
        assert lines[1].startswith(" * ")

    def test_proto_lines_short(self) -> None:
        lines = syscall_gen._proto_lines(_spec(signature="__u32 op"))
        assert lines == ["AIRY_API int airy_sys_call(__u32 op);"]

    def test_proto_lines_wraps(self) -> None:
        spec = _spec(
            name="airy_sys_sched_ctl",
            signature="__u32 op, const char *cgroup_path, const char *policy",
        )
        lines = syscall_gen._proto_lines(spec)
        assert len(lines) == 2
        assert lines[0].endswith(",")
        assert lines[1].startswith(syscall_gen.PROTO_CONT_INDENT)
        assert lines[1].endswith(");")

    def test_proto_lines_no_comma(self) -> None:
        spec = _spec(signature="x" * 90)
        lines = syscall_gen._proto_lines(spec)
        assert lines == [f"AIRY_API int airy_sys_call({'x' * 90});"]

    def test_prototype_block_with_capability(self) -> None:
        spec = _spec(capability_required="AIRY_CAP_CHOWN")
        block = syscall_gen._prototype_block(spec)
        assert block[0] == "/**"
        assert block[-1] == ""
        assert " * @since 1.0.1" in block
        assert " * @stability stable" in block
        assert " * @capability AIRY_CAP_CHOWN" in block

    def test_prototype_block_without_capability(self) -> None:
        block = syscall_gen._prototype_block(_spec())
        assert not any(line.startswith(" * @capability") for line in block)


class TestSlotAndTableLines:
    def test_slot_macro_lines(self, repo_root: Path) -> None:
        contract = syscall_gen.parse_contract(repo_root / syscall_gen.SYSCALL_XML_REL)
        lines = syscall_gen._slot_macro_lines(contract)
        assert len(lines) == 4
        assert lines[0].startswith("#define AIRY_SYS_BASE")
        assert "548" in lines[0]
        assert lines[3].startswith("#define AIRY_SYS_NR_TOTAL")
        assert "548-571" in lines[3]

    def test_index_and_nr_lines(self, repo_root: Path) -> None:
        contract = syscall_gen.parse_contract(repo_root / syscall_gen.SYSCALL_XML_REL)
        index = syscall_gen._index_lines(contract)
        assert index[0] == "#define AIRY_SYS_IDX_CALL\t0  /* airy_sys_call */"

        nr = syscall_gen._nr_lines(contract)
        assert nr[0] == "#define __NR_airy_sys_call\t548"
        assert nr[3] == "#define __NR_airy_sys_clt_notify\t551"

    def test_reserved_slot_lines(self, repo_root: Path) -> None:
        contract = syscall_gen.parse_contract(repo_root / syscall_gen.SYSCALL_XML_REL)
        lines = syscall_gen._reserved_slot_lines(contract)
        assert len(lines) == 20
        assert lines[0] == "#define AIRY_SYS_RESERVED_4\t4  /* Linux 552 */"
        assert lines[-1] == "#define AIRY_SYS_RESERVED_23\t23  /* Linux 571 */"
        assert list(syscall_gen._reserved_slots(contract)) == list(range(4, 24))

    def test_tbl_lines(self, repo_root: Path) -> None:
        contract = syscall_gen.parse_contract(repo_root / syscall_gen.SYSCALL_XML_REL)
        syscall_lines = syscall_gen._tbl_syscall_lines(contract)
        assert syscall_lines[0] == "548\tcommon\tairy_sys_call\t\tsys_airy_sys_call"
        assert syscall_lines[3] == "551\tcommon\tairy_sys_clt_notify\t\tsys_airy_sys_clt_notify"

        reserved_lines = syscall_gen._tbl_reserved_lines(contract)
        assert reserved_lines[0] == "552\tcommon\tairy_reserved_4\t\tsys_ni_syscall"
        assert reserved_lines[-1] == "571\tcommon\tairy_reserved_23\t\tsys_ni_syscall"

    def test_reserved_slots_default_when_empty(self) -> None:
        from codegen_types import SyscallContract

        contract = SyscallContract(
            version="0x0100",
            linux_base=548,
            core_count=1,
            reserved_count=0,
            total_slots=1,
            syscalls=(_spec(),),
            reserved=(),
        )
        assert list(syscall_gen._reserved_slots(contract)) == []


class TestBuildContextAndRender:
    def test_build_context_values(self, repo_root: Path) -> None:
        contract = syscall_gen.parse_contract(repo_root / syscall_gen.SYSCALL_XML_REL)
        ctx = syscall_gen.build_context(contract)
        assert ctx["guard"] == "AIRY_SYSCALL_GEN_H"
        assert ctx["source_name"] == "syscall.xml"
        assert ctx["source_path"] == "include/uapi/linux/airymax/syscall.xml"
        assert ctx["symbol_prefix"] == "AIRY_SYS_"
        assert ctx["index_prefix"] == "AIRY_SYS_IDX_"
        assert ctx["linux_base"] == 548
        assert ctx["linux_end"] == 571
        assert ctx["x32_end"] == 547
        assert ctx["core_end"] == 551
        assert ctx["control_count"] == 3
        assert ctx["reserved_linux_start"] == 552
        assert ctx["reserved_internal_start"] == 4
        assert ctx["reserved_internal_end"] == 23
        assert ctx["index_max"] == 3
        assert ctx["reserved_stub"] == "sys_ni_syscall"
        assert ctx["reserved_name_prefix"] == "airy_reserved_"
        assert ctx["go_needs_unsafe"] is True
        assert len(ctx["bindings"]) == 4

    def test_binding_go_helpers(self, repo_root: Path) -> None:
        contract = syscall_gen.parse_contract(repo_root / syscall_gen.SYSCALL_XML_REL)
        ctx = syscall_gen.build_context(contract)
        binding = ctx["bindings"][1]
        assert binding["pascal"] == "RovolCtl"
        assert binding["nr"] == 549
        assert binding["index_macro"] == "AIRY_SYS_IDX_ROVOL_CTL"
        assert binding["nr_macro"] == "__NR_AIRY_SYS_ROVOL_CTL"
        assert binding["go_index_const"] == "AirySysIdxRovolCtl"
        assert binding["go_nr_const"] == "NrAirySysRovolCtl"
        assert binding["go_func"] == "AirySysRovolCtl"

    def test_render_outputs_deterministic(self, repo_root: Path) -> None:
        first = syscall_gen.render_outputs(repo_root)
        second = syscall_gen.render_outputs(repo_root)
        assert first == second
        expected = {
            repo_root / syscall_gen.HEADER_REL,
            repo_root / syscall_gen.SDK_RUST_REL,
            repo_root / syscall_gen.SDK_PY_REL,
            repo_root / syscall_gen.SDK_GO_REL,
            repo_root / syscall_gen.SDK_TS_REL,
        }
        assert set(first) == expected
        assert all(content.strip() for content in first.values())

    def test_render_tbl_section(self, repo_root: Path) -> None:
        section = syscall_gen.render_tbl_section(repo_root)
        assert "airy_sys_call" in section
        assert "airy_reserved_23" in section
        assert section == syscall_gen.render_tbl_section(repo_root)


class TestCheckEntrypoints:
    def test_check_success(self, repo_root: Path) -> None:
        assert syscall_gen.check(repo_root) == int(ExitCode.SUCCESS)

    def test_check_tbl_success(self, repo_root: Path) -> None:
        assert syscall_gen.check_tbl(repo_root) == int(ExitCode.SUCCESS)

    def test_main_check(self, repo_root: Path) -> None:
        assert syscall_gen.main(["--repo-root", str(repo_root), "--check"]) == int(ExitCode.SUCCESS)

    def test_main_check_tbl(self, repo_root: Path) -> None:
        assert syscall_gen.main(["--repo-root", str(repo_root), "--check-tbl"]) == int(
            ExitCode.SUCCESS
        )
