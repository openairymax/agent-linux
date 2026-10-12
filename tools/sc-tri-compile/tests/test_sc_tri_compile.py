#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""sc_tri_compile 单元测试 — 三路编译、类型大小与结构体布局一致性。

覆盖 tools/sc-tri-compile/sc_tri_compile.py：常量矩阵（三路编译路径、
定宽类型大小、契约结构体布局）、Kbuild include 顺序镜像、探针源码生成与
解析、一致性判定、以及 CLI 退出码契约（0=通过 / 1=失败 / 2=输入或环境
错误）。真实仓库级校验需 gcc 与完整 kernel 子树，缺失时自动跳过。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import sc_tri_compile as sc

_GCC = shutil.which("gcc")


class TestConstants:
    def test_compile_paths_three_labels(self) -> None:
        assert [label for label, _flags in sc.COMPILE_PATHS] == [
            "kernel-space",
            "linux-user-space",
            "third-party",
        ]

    def test_compile_path_flags(self) -> None:
        flags = dict(sc.COMPILE_PATHS)
        assert "-D__KERNEL__" in flags["kernel-space"]
        assert "-D__linux__" in flags["kernel-space"]
        assert "-D__KERNEL__" not in flags["linux-user-space"]
        assert "-D__linux__" in flags["linux-user-space"]
        assert "-U__linux__" in flags["third-party"]
        assert "-std=c99" in flags["third-party"]

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

    def test_parse_probe_collects_all_kinds(self) -> None:
        stdout = "T __u32 4\nS airy_log_record 128\nO airy_log_record magic 0\n"
        values = sc.parse_probe(stdout)
        assert values[("T", "__u32", "")] == 4
        assert values[("S", "airy_log_record", "")] == 128
        assert values[("O", "airy_log_record", "magic")] == 0

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
