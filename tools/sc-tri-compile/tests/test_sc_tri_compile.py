#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""sc_tri_compile 单元测试 — 五路编译、类型大小、结构体布局与 syscall ABI。

覆盖 tools/sc-tri-compile/sc_tri_compile.py：常量矩阵（常规三路 + [DSL]
降级两路编译路径、定宽类型大小、契约结构体布局）、Kbuild include 顺序镜像、
探针源码生成与解析、一致性判定、[DSL] 两路桥接契约、syscall ABI 三源
（syscalls.h ↔ syscall.xml ↔ syscall_gen.h）符号名与编号双向一致性，以及
CLI 退出码契约（0=通过 / 1=失败 / 2=输入或环境错误）。真实仓库级校验需
gcc 与完整 kernel 子树，缺失时自动跳过。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.1.0
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest
import sc_tri_compile as sc

_GCC = shutil.which("gcc")

_VALID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<syscalls version="0x0100" core_count="4" reserved_count="20"
          total_slots="24" linux_base="548">
  <meta>
    <prefix_symbol>AIRY_SYS_</prefix_symbol>
    <prefix_function>airy_sys_</prefix_function>
  </meta>
  <syscall><name>airy_sys_call</name><number>0</number></syscall>
  <syscall><name>airy_sys_rovol_ctl</name><number>1</number></syscall>
  <syscall><name>airy_sys_sched_ctl</name><number>2</number></syscall>
  <syscall><name>airy_sys_clt_notify</name><number>3</number></syscall>
  <reserved from="4" to="23" reason="future extension"/>
</syscalls>
"""

_XML_ROOT = (
    '<syscalls version="0x0100" core_count="4" reserved_count="20" '
    'total_slots="24" linux_base="548">'
)
_XML_META = (
    "<meta><prefix_symbol>AIRY_SYS_</prefix_symbol>"
    "<prefix_function>airy_sys_</prefix_function></meta>"
)
_XML_CALL = "<syscall><name>airy_sys_call</name><number>0</number></syscall>"
_XML_RESERVED = '<reserved from="4" to="23"/>'



class TestConstants:
    def test_compile_paths_five_labels(self) -> None:
        assert [label for label, _flags in sc.COMPILE_PATHS] == [
            "kernel-space",
            "linux-user-space",
            "third-party",
            "dsl-kernel-space",
            "dsl-linux-user-space",
        ]

    def test_compile_path_flags(self) -> None:
        flags = dict(sc.COMPILE_PATHS)
        assert "-D__KERNEL__" in flags["kernel-space"]
        assert "-D__linux__" in flags["kernel-space"]
        assert "-D__KERNEL__" not in flags["linux-user-space"]
        assert "-D__linux__" in flags["linux-user-space"]
        assert "-U__linux__" in flags["third-party"]
        assert "-std=c99" in flags["third-party"]
        for label in ("dsl-kernel-space", "dsl-linux-user-space"):
            assert "-DAIRY_SC_FALLBACK" in flags[label]
            assert "-Wno-error=cpp" in flags[label]

    def test_type_sizes_pinned(self) -> None:
        assert sc.TYPE_SIZES == {
            "__u8": 1,
            "__u16": 2,
            "__u32": 4,
            "__u64": 8,
            "__s8": 1,
            "__s16": 2,
            "__s32": 4,
            "__s64": 8,
        }

    def test_layout_struct_names(self) -> None:
        assert [struct for struct, _size, _fields in sc.LAYOUT] == [
            "airy_ipc_msg_hdr",
            "airy_log_record",
            "airy_task_desc",
            "bpf_struct_ops_common_val",
            "airy_struct_ops_value",
        ]

    def test_layout_field_count(self) -> None:
        assert sum(len(fields) for _s, _z, fields in sc.LAYOUT) == 27

    def test_probe_headers_are_canonical(self) -> None:
        assert "uapi_compat.h" in sc.PROBE_HEADERS
        assert all("structures_" not in header for header in sc.PROBE_HEADERS)


class TestHelpers:
    def test_find_repo_root_points_at_repo_root(self, repo_root: Path) -> None:
        assert sc.find_repo_root() == repo_root

    def test_build_include_flags_mirrors_kbuild_order(self, tmp_path: Path) -> None:
        flags = sc.build_include_flags(tmp_path)
        assert flags.index(f"-I{tmp_path / 'include'}") < flags.index(
            f"-I{tmp_path / 'include' / 'uapi'}"
        )

    def test_first_error_extracts_error_line(self) -> None:
        stderr = "warning: noop\nfoo.c:1:2: error: unknown type name 'x'\nmore\n"
        assert sc.first_error(stderr) == "foo.c:1:2: error: unknown type name 'x'"

    def test_first_error_falls_back_to_first_line(self) -> None:
        assert sc.first_error("plain message\nsecond\n") == "plain message"

    def test_first_error_handles_empty(self) -> None:
        assert sc.first_error("") == "unknown error"


class TestProbe:
    def test_build_probe_source_includes_probe_headers(self) -> None:
        source = sc.build_probe_source()
        for header in sc.PROBE_HEADERS:
            assert f"#include <linux/airymax/{header}>" in source

    def test_build_probe_source_emits_type_and_layout_prints(self) -> None:
        source = sc.build_probe_source()
        assert 'printf("T __u32 %zu\\n", sizeof(__u32));' in source
        assert 'printf("S airy_log_record %zu\\n", sizeof(struct airy_log_record));' in source
        assert (
            'printf("O airy_log_record magic %zu\\n", offsetof(struct airy_log_record, magic));'
            in source
        )

    def test_build_probe_source_pins_dsl_branch_contract(self) -> None:
        source = sc.build_probe_source()
        assert "AIRY_COMPILE_ASSERT(AIRY_DSL_UAPI_BRANCHES == 2" in source
        assert 'printf("D branches %d\\n", AIRY_PROBE_BRANCHES);' in source

    def test_parse_probe_collects_all_kinds(self) -> None:
        stdout = (
            "T __u32 4\nS airy_log_record 128\n"
            "O airy_log_record magic 0\nD branches 2\n"
        )
        values = sc.parse_probe(stdout)
        assert values[("T", "__u32", "")] == 4
        assert values[("S", "airy_log_record", "")] == 128
        assert values[("O", "airy_log_record", "magic")] == 0
        assert values[("D", "branches", "")] == 2

    def test_parse_probe_ignores_blank_and_malformed(self) -> None:
        assert sc.parse_probe("\n\nT __u32 4\nbogus line here too\n") == {("T", "__u32", ""): 4}


class TestConsistency:
    def test_no_failure_when_all_paths_agree(self) -> None:
        per_path = {"kernel-space": {"k": 4}, "linux-user-space": {"k": 4}}
        failures: list[str] = []
        sc.check_consistency("item", 4, per_path, "k", failures)
        assert failures == []

    def test_failure_on_drift(self) -> None:
        per_path = {"kernel-space": {"k": 4}, "third-party": {"k": 8}}
        failures: list[str] = []
        sc.check_consistency("item", 4, per_path, "k", failures)
        assert len(failures) == 1
        assert "期望 4" in failures[0]

    def test_failure_on_missing_probe_value(self) -> None:
        per_path = {"kernel-space": {"k": 4}, "third-party": {}}
        failures: list[str] = []
        sc.check_consistency("item", 4, per_path, "k", failures)
        assert len(failures) == 1
        assert "探针未返回" in failures[0]


class TestFallbackCheck:
    @staticmethod
    def _per_path(branches: dict[str, int]) -> dict[str, dict]:
        return {
            label: {("D", "branches", ""): value}
            for label, value in branches.items()
        }

    def test_passes_on_contracted_paths(self) -> None:
        per_path = self._per_path(
            {
                "kernel-space": -1,
                "linux-user-space": -1,
                "third-party": -1,
                "dsl-kernel-space": 2,
                "dsl-linux-user-space": 2,
            }
        )
        assert sc.fallback_check(per_path).passed

    def test_fails_when_dsl_branch_count_drifts(self) -> None:
        per_path = self._per_path(
            {
                "kernel-space": -1,
                "linux-user-space": -1,
                "third-party": -1,
                "dsl-kernel-space": 3,
                "dsl-linux-user-space": 2,
            }
        )
        result = sc.fallback_check(per_path)
        assert not result.passed
        assert "AIRY_DSL_UAPI_BRANCHES=3" in result.details

    def test_fails_when_regular_path_activates_fallback(self) -> None:
        per_path = self._per_path(
            {
                "kernel-space": 2,
                "linux-user-space": -1,
                "third-party": -1,
                "dsl-kernel-space": 2,
                "dsl-linux-user-space": 2,
            }
        )
        result = sc.fallback_check(per_path)
        assert not result.passed
        assert "降级层意外激活" in result.details

    def test_fails_without_dsl_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sc, "COMPILE_PATHS", [("kernel-space", [])])
        result = sc.fallback_check({})
        assert not result.passed
        assert "未包含" in result.details


def _load_real(repo_root: Path) -> sc.SyscallSources:
    """Load the real [SC] syscall sources, asserting they parse cleanly."""
    sources, failures = sc.load_syscall_sources(repo_root / sc.DEFAULT_HEADERS_REL)
    assert failures == []
    assert sources is not None
    return sources


def _mutate(sources, *, contract=None, generated=None, xml=None):
    """Return a copy of sources with the given mapping/dataclass replaced."""
    return sc.SyscallSources(
        xml=dataclasses.replace(sources.xml) if xml is None else xml,
        contract=dict(sources.contract) if contract is None else contract,
        generated=dict(sources.generated) if generated is None else generated,
    )


class TestSyscallAbiConstants:
    def test_source_file_names(self) -> None:
        assert sc.SYSCALL_CONTRACT_FILE == "syscalls.h"
        assert sc.SYSCALL_XML_FILE == "syscall.xml"
        assert sc.SYSCALL_GENERATED_FILE == "syscall_gen.h"

    def test_prefixes_and_meta_suffixes(self) -> None:
        assert sc.CONTRACT_SYMBOL_PREFIX == "AIRY_SYS_"
        assert sc.GENERATED_NR_PREFIX == "__NR_"
        assert sc.GENERATED_IDX_PREFIX == "AIRY_SYS_IDX_"
        assert sc.CONTRACT_META_SUFFIXES == {"RESERVED_BASE", "RESERVED_END", "SLOTS_MAX"}


class TestParseCDefines:
    def test_numbers_decimal_and_hex(self) -> None:
        text = "#define A 1\n#define B 0x10\n#define C   24  /* c */\n"
        assert sc.parse_c_defines(text) == {"A": 1, "B": 16, "C": 24}

    def test_skips_function_like_and_token_aliases(self) -> None:
        text = (
            "#define AIRY_SYS_CALL 548\n"
            "#define AIRY_SYS_RESERVED_CLAIM(num) \\\n\tAIRY_COMPILE_ASSERT(1)\n"
            "#define AIRY_DSL_SYS_CALL AIRY_SYS_CALL\n"
            "#define AIRY_DSL_SYS_RESERVED (-1)\n"
        )
        assert sc.parse_c_defines(text) == {"AIRY_SYS_CALL": 548}


class TestParseSyscallXml:
    def test_valid_document(self) -> None:
        xml = sc.parse_syscall_xml(_VALID_XML)
        assert xml.version == 256
        assert (xml.core_count, xml.reserved_count, xml.total_slots) == (4, 20, 24)
        assert xml.linux_base == 548
        assert xml.symbol_prefix == "AIRY_SYS_"
        assert xml.function_prefix == "airy_sys_"
        assert xml.core_names == {
            "CALL": "airy_sys_call",
            "ROVOL_CTL": "airy_sys_rovol_ctl",
            "SCHED_CTL": "airy_sys_sched_ctl",
            "CLT_NOTIFY": "airy_sys_clt_notify",
        }
        assert xml.core_numbers == {"CALL": 0, "ROVOL_CTL": 1, "SCHED_CTL": 2, "CLT_NOTIFY": 3}
        assert (xml.reserved_from, xml.reserved_to) == (4, 23)

    def test_malformed_xml_raises(self) -> None:
        with pytest.raises(ValueError, match="XML 解析失败"):
            sc.parse_syscall_xml("<syscalls")

    def test_wrong_root_tag_raises(self) -> None:
        with pytest.raises(ValueError, match="根元素"):
            sc.parse_syscall_xml("<foo/>")

    def test_missing_attribute_raises(self) -> None:
        doc = "<syscalls>" + _XML_META + _XML_CALL + _XML_RESERVED + "</syscalls>"
        with pytest.raises(ValueError, match="缺少属性"):
            sc.parse_syscall_xml(doc)

    def test_missing_meta_raises(self) -> None:
        doc = _XML_ROOT + _XML_CALL + _XML_RESERVED + "</syscalls>"
        with pytest.raises(ValueError, match="缺少 <meta>"):
            sc.parse_syscall_xml(doc)

    def test_missing_prefix_raises(self) -> None:
        doc = _XML_ROOT + "<meta><prefix_symbol>AIRY_SYS_</prefix_symbol></meta>"
        doc += _XML_CALL + _XML_RESERVED + "</syscalls>"
        with pytest.raises(ValueError, match="prefix_symbol/prefix_function"):
            sc.parse_syscall_xml(doc)

    def test_entry_without_prefix_raises(self) -> None:
        doc = _XML_ROOT + _XML_META
        doc += "<syscall><name>call</name><number>0</number></syscall>"
        doc += _XML_RESERVED + "</syscalls>"
        with pytest.raises(ValueError, match="未带前缀"):
            sc.parse_syscall_xml(doc)

    def test_duplicate_entry_raises(self) -> None:
        doc = _XML_ROOT + _XML_META + _XML_CALL + _XML_CALL + _XML_RESERVED + "</syscalls>"
        with pytest.raises(ValueError, match="重复"):
            sc.parse_syscall_xml(doc)

    def test_missing_reserved_raises(self) -> None:
        doc = _XML_ROOT + _XML_META + _XML_CALL + "</syscalls>"
        with pytest.raises(ValueError, match="缺少 <reserved>"):
            sc.parse_syscall_xml(doc)


class TestLoadSyscallSources:
    def test_missing_directory_reports_failures(self, tmp_path: Path) -> None:
        sources, failures = sc.load_syscall_sources(tmp_path / "absent")
        assert sources is None
        assert len(failures) == 3

    def test_bad_xml_reports_parse_failure(self, tmp_path: Path) -> None:
        for name in (sc.SYSCALL_CONTRACT_FILE, sc.SYSCALL_GENERATED_FILE):
            (tmp_path / name).write_text("#define AIRY_SYS_CALL 548\n", encoding="utf-8")
        (tmp_path / sc.SYSCALL_XML_FILE).write_text("<syscalls", encoding="utf-8")
        sources, failures = sc.load_syscall_sources(tmp_path)
        assert sources is None
        assert any("XML 解析失败" in failure for failure in failures)


class TestSyscallDerivations:
    def test_contract_core_defines_excludes_metadata(self, repo_root: Path) -> None:
        assert sc._contract_core_defines(_load_real(repo_root)) == {
            "CALL": 548,
            "ROVOL_CTL": 549,
            "SCHED_CTL": 550,
            "CLT_NOTIFY": 551,
        }

    def test_generated_namespaces(self, repo_root: Path) -> None:
        sources = _load_real(repo_root)
        assert sc._generated_idx_defines(sources) == {
            "CALL": 0,
            "ROVOL_CTL": 1,
            "SCHED_CTL": 2,
            "CLT_NOTIFY": 3,
        }
        assert sc._generated_nr_names(sources) == {
            "__NR_airy_sys_call": 548,
            "__NR_airy_sys_rovol_ctl": 549,
            "__NR_airy_sys_sched_ctl": 550,
            "__NR_airy_sys_clt_notify": 551,
        }

    def test_expected_suffixes(self, repo_root: Path) -> None:
        assert sc._expected_suffixes(_load_real(repo_root)) == {
            "CALL",
            "ROVOL_CTL",
            "SCHED_CTL",
            "CLT_NOTIFY",
        }


class TestSyscallChecks:
    def test_symbol_name_check_passes_on_repo(self, repo_root: Path) -> None:
        assert sc.syscall_symbol_name_check(_load_real(repo_root)) == []

    def test_number_check_passes_on_repo(self, repo_root: Path) -> None:
        assert sc.syscall_number_check(_load_real(repo_root)) == []

    def test_symbol_name_check_detects_missing_macro(self, repo_root: Path) -> None:
        sources = _load_real(repo_root)
        contract = dict(sources.contract)
        del contract["AIRY_SYS_CLT_NOTIFY"]
        failures = sc.syscall_symbol_name_check(_mutate(sources, contract=contract))
        assert failures == ["  syscalls.h 核心宏: 缺失 CLT_NOTIFY"]

    def test_symbol_name_check_detects_extra_macro(self, repo_root: Path) -> None:
        sources = _load_real(repo_root)
        contract = dict(sources.contract)
        contract["AIRY_SYS_EXTRA"] = 999
        failures = sc.syscall_symbol_name_check(_mutate(sources, contract=contract))
        assert any("多出 EXTRA" in failure for failure in failures)

    def test_symbol_name_check_detects_missing_index(self, repo_root: Path) -> None:
        sources = _load_real(repo_root)
        generated = dict(sources.generated)
        del generated["AIRY_SYS_IDX_CALL"]
        failures = sc.syscall_symbol_name_check(_mutate(sources, generated=generated))
        assert any("AIRY_SYS_IDX_" in f and "缺失 CALL" in f for f in failures)

    def test_number_check_detects_registration_drift(self, repo_root: Path) -> None:
        sources = _load_real(repo_root)
        generated = dict(sources.generated)
        generated["__NR_airy_sys_call"] = 999
        failures = sc.syscall_number_check(_mutate(sources, generated=generated))
        assert any("__NR_airy_sys_call 期望 548, 实测 999" in f for f in failures)

    def test_number_check_detects_total_drift(self, repo_root: Path) -> None:
        sources = _load_real(repo_root)
        generated = dict(sources.generated)
        generated["AIRY_SYS_NR_TOTAL"] = 25
        failures = sc.syscall_number_check(_mutate(sources, generated=generated))
        assert any("AIRY_SYS_NR_TOTAL 期望 24, 实测 25" in f for f in failures)

    def test_number_check_detects_contract_drift(self, repo_root: Path) -> None:
        sources = _load_real(repo_root)
        contract = dict(sources.contract)
        contract["AIRY_SYS_SCHED_CTL"] = 700
        failures = sc.syscall_number_check(_mutate(sources, contract=contract))
        assert any("AIRY_SYS_SCHED_CTL 期望 550, 实测 700" in f for f in failures)

    def test_number_check_detects_reserved_span_drift(self, repo_root: Path) -> None:
        sources = _load_real(repo_root)
        xml = dataclasses.replace(sources.xml, reserved_from=5)
        failures = sc.syscall_number_check(_mutate(sources, xml=xml))
        assert any("<reserved> 区间 (5-23)" in f for f in failures)

    def test_number_check_detects_slot_arithmetic_drift(self, repo_root: Path) -> None:
        sources = _load_real(repo_root)
        xml = dataclasses.replace(sources.xml, core_count=5)
        failures = sc.syscall_number_check(_mutate(sources, xml=xml))
        assert any("core_count(5)" in f for f in failures)


class TestSyscallAbiCheck:
    def test_passes_on_repo(self, repo_root: Path) -> None:
        result = sc.syscall_abi_check(_load_real(repo_root), [])
        assert result.passed
        assert "548-571" in result.details

    def test_fails_when_sources_unavailable(self) -> None:
        result = sc.syscall_abi_check(None, ["  boom"])
        assert not result.passed
        assert "boom" in result.details

    def test_fails_on_any_inconsistency(self, repo_root: Path) -> None:
        sources = _load_real(repo_root)
        contract = dict(sources.contract)
        del contract["AIRY_SYS_CALL"]
        result = sc.syscall_abi_check(_mutate(sources, contract=contract), [])
        assert not result.passed


@pytest.mark.skipif(_GCC is None, reason="gcc not available")
class TestRealRepository:
    def _include_flags(self, repo_root: Path) -> list[str]:
        return sc.build_include_flags(repo_root / sc.DEFAULT_KERNEL_REL)

    def test_tri_compile_check_passes(self, repo_root: Path) -> None:
        headers = sorted((repo_root / sc.DEFAULT_HEADERS_REL).glob("*.h"))
        result = sc.tri_compile_check(_GCC, self._include_flags(repo_root), headers)
        assert result.passed, result.details

    def test_type_size_and_layout_checks_pass(self, repo_root: Path, tmp_path: Path) -> None:
        per_path, probe_failures = sc.collect_probe(_GCC, self._include_flags(repo_root), tmp_path)
        assert probe_failures == []
        assert sc.type_size_check(per_path, probe_failures).passed
        assert sc.layout_check(per_path, probe_failures).passed
        assert sc.fallback_check(per_path).passed


class TestMainCli:
    def test_main_pass_exits_zero(self, repo_root: Path, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            sc.main(["--repo-root", str(repo_root)])
        assert exc.value.code == 0
        assert "RESULT: PASS" in capsys.readouterr().out

    def test_main_check_mode_exits_zero(
        self, repo_root: Path, capsys: pytest.CaptureFixture
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            sc.main(["--repo-root", str(repo_root), "--check"])
        assert exc.value.code == 0
        assert "SC TRI-COMPILE: PASS" in capsys.readouterr().out

    def test_main_reports_five_checks(
        self, repo_root: Path, capsys: pytest.CaptureFixture
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            sc.main(["--repo-root", str(repo_root), "--check"])
        assert exc.value.code == 0
        assert "PASS (5/5 checks)" in capsys.readouterr().out

    def test_main_missing_repo_root_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            sc.main(["--repo-root", str(tmp_path / "nonexistent")])
        assert exc.value.code == 2
        assert "repo root not found" in capsys.readouterr().err

    def test_main_missing_headers_dir_exits_two(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            sc.main(["--repo-root", str(tmp_path)])
        assert exc.value.code == 2

    def test_main_help_exits_zero(self) -> None:
        with pytest.raises(SystemExit) as exc:
            sc.main(["--help"])
        assert exc.value.code == 0
