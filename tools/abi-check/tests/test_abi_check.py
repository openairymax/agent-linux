#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""abi_check 单元测试 — ABI 常量、多方解析器、十二项检查与 CLI 退出码契约。

覆盖 tools/abi-check/abi_check.py：

  * 常量矩阵：编号起点/范围、__NR_syscalls、预留段（552-571，20 槽）、
    SLOTS_MAX=24、x32 禁止区域。
  * 多方解析器：SSoT markdown、unistd.h、syscall_64.tbl、[SC] syscalls.h、
    sw_64 syscall.tbl。
  * 十二项检查：三方一致性、编号起点、范围无冲突、__NR_syscalls、
    x32 禁止区域、[SC] 契约头一致、预留槽登记、tbl ABI 列与入口点、
    sw_64 号段落位、riscv/loongarch 通用表接线、基线快照一致、
    SSoT 契约快照一致（失败分支以合成输入构造）。
  * 派生契约快照：导出确定性、快照→SSoT 适配、漂移检出、
    docs 缺失时的快照回退与 --require-ssot 失败关闭。
  * CLI 退出码契约（0=通过 / 1=失败 / 2=输入或环境错误）与端到端
    真实仓库校验（权威源 + 基线）。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.3
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import abi_check as ab

# agent-linux 仓库根（tests/ → abi-check/ → tools/ → repo root），供
# 采集期 skipif 判断权威 SSoT 是否检出。
REPO_ROOT = Path(__file__).resolve().parents[3]

# --check 模式摘要行；检查项数量随工具演进，断言必须解析而非硬编码。
CHECK_SUMMARY_RE = re.compile(r"ABI CHECK: PASS \((\d+)/(\d+) checks\)")

# ─── 合成输入构造器 ────────────────────────────────────────────────────────

CORE = list(ab.CORE_SYSCALLS)
RESERVED_NUMS = list(range(552, 572))


def ssot_text(version="v1.0.1", reserved=(552, 571)):
    """构造最小可解析的 SSoT markdown（含 §3.1 核心表 + §3.2 预留段）。"""
    rows = "\n".join(
        f"| {num} | `{macro}` | `{name}` | x | 2 | 0.1.1 | 已分配 |"
        for name, macro, num in CORE)
    return (
        f"> **文档版本**：{version}\\\n\n"
        "| 编号 | 宏定义 | C 符号 | 功能 | 参数数 | 引入版本 | 状态 |\n"
        "|------|--------|--------|------|--------|---------|------|\n"
        f"{rows}\n\n"
        "| 编号 | 宏定义 | 状态 | 说明 |\n"
        "|------|--------|------|------|\n"
        f"| {reserved[0]}-{reserved[1]} | "
        "`AIRY_SYS_RESERVED_BASE` ~ `AIRY_SYS_RESERVED_END`"
        "（`AIRY_SYS_SLOTS_MAX=24`） | 预留 | x |\n"
    )


def unistd_text(nr=572, reserved=RESERVED_NUMS):
    """构造最小可解析的 unistd.h（核心 + 预留 + __NR_syscalls）。"""
    lines = [f"#define __NR_{name} {num}" for name, _macro, num in CORE]
    lines += [f"#define __NR_airy_reserved_{n - 548} {n}" for n in reserved]
    lines.append(f"#define __NR_syscalls {nr}")
    return "\n".join(lines) + "\n"


def tbl_text(reserved=RESERVED_NUMS, abi="common", core_entry=None):
    """构造最小可解析的 syscall_64.tbl。"""
    overrides = core_entry or {}
    lines = [
        f"{num}\t{abi}\t{name}\t\t{overrides.get(name, 'sys_' + name)}"
        for name, _macro, num in CORE
    ]
    lines += [
        f"{n}\t{abi}\tairy_reserved_{n - 548}\t\tsys_ni_syscall"
        for n in reserved
    ]
    return "\n".join(lines) + "\n"


def sc_header_text(base=552, end=571, slots=24, skip=()):
    """构造最小可解析的 [SC] 契约头（核心宏 + 预留标记宏）。"""
    lines = [
        f"#define {macro:<24} {num}"
        for name, macro, num in CORE if macro not in skip
    ]
    macros = (("AIRY_SYS_RESERVED_BASE", base),
              ("AIRY_SYS_RESERVED_END", end),
              ("AIRY_SYS_SLOTS_MAX", slots))
    lines += [f"#define {m:<24} {v}" for m, v in macros if m not in skip]
    return "\n".join(lines) + "\n"


def parse_all(ssot=None, unistd=None, tbl=None, sc=None):
    """解析四方源，返回 (ssot, unistd, tbl, sc_header) 字典元组。"""
    return (
        ab.parse_ssot(ssot if ssot is not None else ssot_text()),
        ab.parse_unistd(unistd if unistd is not None else unistd_text()),
        ab.parse_syscall_tbl(tbl if tbl is not None else tbl_text()),
        ab.parse_sc_header(sc if sc is not None else sc_header_text()),
    )


def baseline(nr=572, base=548, reserved=(552, 571)):
    """构造基线快照 dict。"""
    return {
        "version": "v1.0.1",
        "date": "2026-10-15",
        "linux_base": base,
        "syscalls": [
            {"name": name, "internal_number": idx, "linux_number": num}
            for idx, (name, _macro, num) in enumerate(CORE)
        ],
        "reserved_range": list(reserved),
        "nr_syscalls": nr,
    }


# ─── 常量 ──────────────────────────────────────────────────────────────────


class TestConstants:
    def test_base_and_range(self) -> None:
        assert ab.EXPECTED_BASE == 548
        assert ab.AIRY_RANGE_START == 548
        assert ab.AIRY_RANGE_END == 571

    def test_nr_syscalls_is_high_water_plus_one(self) -> None:
        assert ab.EXPECTED_NR_SYSCALLS == ab.AIRY_RANGE_END + 1 == 572

    def test_reserved_segment(self) -> None:
        assert ab.EXPECTED_RESERVED_START == 552
        assert ab.EXPECTED_RESERVED_END == 571
        assert ab.EXPECTED_RESERVED_COUNT == 20
        assert ab.EXPECTED_SLOTS_MAX == 24

    def test_x32_forbidden_zone(self) -> None:
        assert ab.X32_FORBIDDEN_START == 512
        assert ab.X32_FORBIDDEN_END == 547

    def test_core_syscalls(self) -> None:
        assert [n for n, _m, _x in CORE] == [
            "airy_sys_call", "airy_sys_rovol_ctl",
            "airy_sys_sched_ctl", "airy_sys_clt_notify",
        ]
        assert [x for _n, _m, x in CORE] == [548, 549, 550, 551]

    def test_reserved_naming_conventions(self) -> None:
        assert ab.RESERVED_NAME_PREFIX == "airy_reserved_"
        assert ab.RESERVED_TBL_ENTRY == "sys_ni_syscall"


# ─── 解析器 ────────────────────────────────────────────────────────────────


class TestParseSsot:
    def test_version_and_base_fallback(self) -> None:
        ssot = ab.parse_ssot(ssot_text())
        assert ssot["version"] == "v1.0.1"
        assert ssot["linux_base"] == 548

    def test_core_syscalls(self) -> None:
        ssot = ab.parse_ssot(ssot_text())
        assert {s["name"]: s["linux_number"] for s in ssot["syscalls"]} == {
            name: num for name, _macro, num in CORE}
        assert ssot["syscalls"][0]["macro"] == "AIRY_SYS_CALL"

    def test_reserved_range_named_macros(self) -> None:
        # 回归：SSoT 实际用 RESERVED_BASE/END 而非 RESERVED_<digit>。
        ssot = ab.parse_ssot(ssot_text())
        assert ssot["reserved_range"] == [552, 571]

    def test_reserved_range_absent_returns_none(self) -> None:
        ssot = ab.parse_ssot("> **文档版本**：v1.0.1\n\n无表\n")
        assert ssot["reserved_range"] is None
        assert ssot["syscalls"] == []


class TestParseUnistd:
    def test_core_and_nr(self) -> None:
        u = ab.parse_unistd(unistd_text())
        assert {s["name"]: s["linux_number"] for s in u["syscalls"]} == {
            name: num for name, _macro, num in CORE}
        assert u["nr_syscalls"] == 572

    def test_reserved_slots(self) -> None:
        u = ab.parse_unistd(unistd_text())
        assert [s["linux_number"] for s in u["reserved"]] == RESERVED_NUMS
        assert u["reserved"][0]["name"] == "airy_reserved_4"
        assert u["reserved"][-1]["name"] == "airy_reserved_23"


class TestParseScHeader:
    def test_core_macros(self) -> None:
        sc = ab.parse_sc_header(sc_header_text())
        assert sc["core_macros"] == {
            macro: num for _name, macro, num in CORE}

    def test_reserved_markers(self) -> None:
        sc = ab.parse_sc_header(sc_header_text())
        assert sc["reserved"] == {
            "AIRY_SYS_RESERVED_BASE": 552,
            "AIRY_SYS_RESERVED_END": 571,
            "AIRY_SYS_SLOTS_MAX": 24,
        }

    def test_function_like_macro_ignored(self) -> None:
        text = sc_header_text() + "#define AIRY_SYS_RESERVED_CLAIM(num) (num)\n"
        sc = ab.parse_sc_header(text)
        assert "AIRY_SYS_RESERVED_CLAIM" not in sc["reserved"]
        assert "AIRY_SYS_RESERVED_CLAIM" not in sc["core_macros"]


class TestParseSyscallTbl:
    def test_core_entries(self) -> None:
        t = ab.parse_syscall_tbl(tbl_text())
        assert {s["name"]: s["linux_number"] for s in t["syscalls"]} == {
            name: num for name, _macro, num in CORE}
        assert all(s["abi"] == "common" for s in t["syscalls"])
        assert t["syscalls"][0]["entry"] == "sys_airy_sys_call"

    def test_reserved_entries(self) -> None:
        t = ab.parse_syscall_tbl(tbl_text())
        assert [s["linux_number"] for s in t["reserved"]] == RESERVED_NUMS
        assert all(s["entry"] == "sys_ni_syscall" for s in t["reserved"])


# ─── 检查项 ────────────────────────────────────────────────────────────────


class TestThreeWayConsistency:
    def test_pass(self) -> None:
        ssot, u, tbl, _sc = parse_all()
        assert ab.check_three_way_consistency(ssot, u, tbl).passed

    def test_fail_on_number_mismatch(self) -> None:
        ssot, u, _t, _sc = parse_all()
        # 人为把 tbl 中 airy_sys_call 编号改为 600
        bad = ab.parse_syscall_tbl(
            tbl_text().replace("548\tcommon\tairy_sys_call",
                               "600\tcommon\tairy_sys_call"))
        res = ab.check_three_way_consistency(ssot, u, bad)
        assert not res.passed
        assert "airy_sys_call" in res.details


class TestBaseNumber:
    def test_pass(self) -> None:
        ssot, u, tbl, _sc = parse_all()
        assert ab.check_base_number(ssot, u, tbl).passed

    def test_fail_on_wrong_base(self) -> None:
        ssot, u, tbl, _sc = parse_all()
        ssot = dict(ssot, linux_base=500)
        res = ab.check_base_number(ssot, u, tbl)
        assert not res.passed


class TestRangeNoConflict:
    def test_pass(self) -> None:
        ssot, u, tbl, _sc = parse_all()
        assert ab.check_range_no_conflict(ssot, u, tbl).passed

    def test_fail_on_duplicate(self) -> None:
        ssot, u, tbl, _sc = parse_all()
        dup = ab.parse_syscall_tbl(
            tbl_text().replace("549\tcommon\tairy_sys_rovol_ctl",
                               "548\tcommon\tairy_sys_rovol_ctl"))
        res = ab.check_range_no_conflict(ssot, u, dup)
        assert not res.passed
        assert "548" in res.details


class TestNrSyscalls:
    def test_pass_572(self) -> None:
        assert ab.check_nr_syscalls(ab.parse_unistd(unistd_text())).passed

    def test_fail_552_regression(self) -> None:
        u = ab.parse_unistd(unistd_text(nr=552))
        res = ab.check_nr_syscalls(u)
        assert not res.passed
        assert "552" in res.details

    def test_fail_when_missing(self) -> None:
        u = ab.parse_unistd(unistd_text().replace("#define __NR_syscalls", "#define X_NR_syscalls"))
        res = ab.check_nr_syscalls(u)
        assert not res.passed


class TestX32Forbidden:
    def test_pass(self) -> None:
        _ssot, u, tbl, _sc = parse_all()
        assert ab.check_x32_forbidden(u, tbl).passed

    def test_fail_on_x32_entry(self) -> None:
        _ssot, _u, _t, _sc = parse_all()
        u = ab.parse_unistd("#define __NR_airy_sys_call 520\n")
        tbl = ab.parse_syscall_tbl("520\tcommon\tairy_sys_call\tsys_airy_sys_call\n")
        res = ab.check_x32_forbidden(u, tbl)
        assert not res.passed
        assert "520" in res.details


class TestScHeaderConsistency:
    def test_pass(self) -> None:
        ssot, u, tbl, sc = parse_all()
        assert ab.check_sc_header_consistency(sc, ssot, u, tbl).passed

    def test_fail_on_slots_max(self) -> None:
        ssot, u, tbl, _ = parse_all()
        sc = ab.parse_sc_header(sc_header_text(slots=25))
        res = ab.check_sc_header_consistency(sc, ssot, u, tbl)
        assert not res.passed
        assert "SLOTS_MAX" in res.details

    def test_fail_on_missing_core_macro(self) -> None:
        ssot, u, tbl, _ = parse_all()
        sc = ab.parse_sc_header(sc_header_text(skip=("AIRY_SYS_CALL",)))
        res = ab.check_sc_header_consistency(sc, ssot, u, tbl)
        assert not res.passed
        assert "AIRY_SYS_CALL" in res.details

    def test_fail_on_ssot_reserved_mismatch(self) -> None:
        ssot, u, tbl, _ = parse_all(ssot=ssot_text(reserved=(552, 570)))
        sc = ab.parse_sc_header(sc_header_text())
        res = ab.check_sc_header_consistency(sc, ssot, u, tbl)
        assert not res.passed
        assert "预留段" in res.details


class TestReservedSlots:
    def test_pass(self) -> None:
        _ssot, u, tbl, _sc = parse_all()
        assert ab.check_reserved_slots(u, tbl).passed

    def test_fail_on_missing_slot(self) -> None:
        u = ab.parse_unistd(unistd_text(reserved=RESERVED_NUMS[:-1]))
        tbl = ab.parse_syscall_tbl(tbl_text())
        res = ab.check_reserved_slots(u, tbl)
        assert not res.passed
        assert "缺失预留槽" in res.details

    def test_fail_on_bad_name(self) -> None:
        u = ab.parse_unistd(
            unistd_text().replace("airy_reserved_4 552", "airy_reserved_99 552"))
        tbl = ab.parse_syscall_tbl(tbl_text())
        res = ab.check_reserved_slots(u, tbl)
        assert not res.passed
        assert "命名" in res.details

    def test_fail_on_bad_entry(self) -> None:
        u = ab.parse_unistd(unistd_text())
        tbl = ab.parse_syscall_tbl(
            tbl_text().replace("552\tcommon\tairy_reserved_4\t\tsys_ni_syscall",
                               "552\tcommon\tairy_reserved_4\t\tsys_bad"))
        res = ab.check_reserved_slots(u, tbl)
        assert not res.passed
        assert "入口点" in res.details


class TestTblAbiAndEntry:
    def test_pass(self) -> None:
        _ssot, _u, tbl, _sc = parse_all()
        assert ab.check_tbl_abi_and_entry(tbl).passed

    def test_fail_on_non_common_abi(self) -> None:
        tbl = ab.parse_syscall_tbl(tbl_text(abi="x32"))
        res = ab.check_tbl_abi_and_entry(tbl)
        assert not res.passed
        assert "abi" in res.details

    def test_fail_on_core_entry_mismatch(self) -> None:
        tbl = ab.parse_syscall_tbl(
            tbl_text(core_entry={"airy_sys_call": "sys_wrong"}))
        res = ab.check_tbl_abi_and_entry(tbl)
        assert not res.passed
        assert "入口点" in res.details


class TestBaseline:
    def test_pass(self) -> None:
        ssot, u, tbl, _sc = parse_all()
        assert ab.check_baseline(ssot, u, tbl, baseline()).passed

    def test_fail_on_nr_mismatch(self) -> None:
        ssot, u, tbl, _sc = parse_all()
        res = ab.check_baseline(ssot, u, tbl, baseline(nr=552))
        assert not res.passed
        assert "nr_syscalls" in res.details

    def test_fail_on_base_mismatch(self) -> None:
        ssot, u, tbl, _sc = parse_all()
        res = ab.check_baseline(ssot, u, tbl, baseline(base=500))
        assert not res.passed
        assert "linux_base" in res.details

    def test_fail_on_reserved_range_drift(self) -> None:
        ssot, u, tbl, _sc = parse_all()
        res = ab.check_baseline(ssot, u, tbl, baseline(reserved=(552, 570)))
        assert not res.passed
        assert "reserved_range" in res.details

    def test_fail_on_missing_and_extra_syscall(self) -> None:
        ssot, u, tbl, _sc = parse_all()
        bl = baseline()
        bl["syscalls"] = bl["syscalls"][:3]  # 缺 clt_notify
        bl["syscalls"].append(
            {"name": "airy_sys_ghost", "internal_number": 9,
             "linux_number": 560})
        res = ab.check_baseline(ssot, u, tbl, bl)
        assert not res.passed
        assert "airy_sys_clt_notify" in res.details
        assert "airy_sys_ghost" in res.details


# ─── 端到端：真实仓库 + CLI 退出码 ─────────────────────────────────────────


def run_cli(repo_root: Path, *extra: str) -> subprocess.CompletedProcess:
    script = repo_root / "tools" / "abi-check" / "abi_check.py"
    return subprocess.run(
        [sys.executable, str(script), "--repo-root", str(repo_root),
         *extra],
        capture_output=True, text=True,
    )


def assert_check_pass(proc: subprocess.CompletedProcess) -> None:
    """断言 --check 退出 0 且摘要行自洽（passed == total > 0）。"""
    assert proc.returncode == 0, proc.stdout + proc.stderr
    match = CHECK_SUMMARY_RE.search(proc.stdout)
    assert match, proc.stdout + proc.stderr
    passed, total = (int(group) for group in match.groups())
    assert passed == total > 0, proc.stdout


class TestEndToEnd:
    def test_real_sources_all_checks_pass(self, repo_root: Path) -> None:
        assert (repo_root / "kernel").is_dir(), "kernel submodule missing"
        root = repo_root
        ssot_file = root / ab.DEFAULT_SSOT_REL
        snapshot = ab.load_snapshot(root / ab.DEFAULT_SNAPSHOT_REL)
        ssot_available = ssot_file.is_file()
        if ssot_available:
            ssot = ab.parse_ssot(ssot_file.read_text(encoding="utf-8"))
        else:
            assert snapshot is not None, "neither SSoT nor snapshot present"
            ssot = ab.snapshot_to_ssot(snapshot)
        u = ab.parse_unistd(
            (root / ab.DEFAULT_UNISTD_REL).read_text(encoding="utf-8"))
        tbl = ab.parse_syscall_tbl(
            (root / ab.DEFAULT_TBL_REL).read_text(encoding="utf-8"))
        sw_tbl = ab.parse_syscall_tbl(
            (root / ab.DEFAULT_SW64_TBL_REL).read_text(encoding="utf-8"))
        sc = ab.parse_sc_header(
            (root / ab.DEFAULT_SC_HEADER_REL).read_text(encoding="utf-8"))
        bl = json.loads(
            (root / ab.DEFAULT_BASELINE_REL).read_text(encoding="utf-8"))

        results = [
            ab.check_three_way_consistency(ssot, u, tbl),
            ab.check_base_number(ssot, u, tbl),
            ab.check_range_no_conflict(ssot, u, tbl),
            ab.check_nr_syscalls(u),
            ab.check_x32_forbidden(u, tbl),
            ab.check_sc_header_consistency(sc, ssot, u, tbl),
            ab.check_reserved_slots(u, tbl),
            ab.check_tbl_abi_and_entry(tbl),
            ab.check_sw64_tbl(ssot, sw_tbl),
            ab.check_arch_wire(root),
            ab.check_baseline(ssot, u, tbl, bl),
            ab.check_snapshot_drift(ssot, snapshot, ssot_available),
        ]
        failed = [r.name for r in results if not r.passed]
        assert not failed, f"ABI 检查失败: {failed}"
        assert len(results) == 12

    def test_cli_check_mode_passes_all_checks(self, repo_root: Path) -> None:
        assert_check_pass(run_cli(repo_root, "--check"))

    def test_cli_verbose_exit_zero(self, repo_root: Path) -> None:
        proc = run_cli(repo_root)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "RESULT: PASS" in proc.stdout

    def test_cli_missing_file_exit_2(self, repo_root: Path) -> None:
        proc = run_cli(repo_root, "--ssot-path", "/nonexistent/ssot.md",
                       "--check")
        assert proc.returncode == 2

    def test_cli_detects_mutated_nr(self, repo_root: Path, tmp_path: Path) -> None:
        real = (repo_root / ab.DEFAULT_UNISTD_REL).read_text(encoding="utf-8")
        mutated = real.replace("#define __NR_syscalls 572",
                               "#define __NR_syscalls 552")
        assert mutated != real, "无法定位 __NR_syscalls 以构造回归输入"
        bad = tmp_path / "unistd.h"
        bad.write_text(mutated, encoding="utf-8")
        proc = run_cli(repo_root, "--unistd-path", str(bad), "--check")
        assert proc.returncode == 1
        assert "FAIL" in proc.stdout


# ─── 派生契约快照：导出 / 适配 / 漂移 ──────────────────────────────────────


def has_authoritative_ssot(repo_root: Path) -> bool:
    """真实仓库是否检出权威 SSoT（GitHub 镜像未检出 ../docs）。"""
    return (repo_root / ab.DEFAULT_SSOT_REL).is_file()


def mirror_root(repo_root: Path, tmp_path: Path,
                with_snapshot: bool = True) -> Path:
    """构造未检出 ../docs 的镜像仓库根，复现 GitHub 镜像环境。

    仅挂载 ABI 检查宿主所需的最小集合（kernel 子树、脚本、基线、可选
    快照），刻意不创建 ../docs，以触发契约快照回退路径。
    """
    root = tmp_path / "agent-linux"
    tool = root / "tools" / "abi-check"
    tool.mkdir(parents=True)
    (root / "kernel").symlink_to(repo_root / "kernel",
                                 target_is_directory=True)
    (tool / "abi_check.py").symlink_to(
        repo_root / "tools" / "abi-check" / "abi_check.py")
    (tool / "abi_baseline.json").symlink_to(
        repo_root / "tools" / "abi-check" / "abi_baseline.json")
    if with_snapshot:
        contract = tool / "contract"
        contract.mkdir()
        (contract / "syscall_registry.snapshot.json").symlink_to(
            repo_root / "tools" / "abi-check" / "contract"
            / "syscall_registry.snapshot.json")
    return root


def run_git(repo: Path, *args: str) -> str:
    """在临时仓库内执行 git 命令，返回 stdout 去尾空白。"""
    proc = subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.name=abi-test",
         "-c", "user.email=abi-test@example.invalid", *args],
        capture_output=True, text=True, check=True)
    return proc.stdout.strip()


class TestSnapshot:
    def test_build_snapshot_shape(self, tmp_path: Path) -> None:
        ssot_path = tmp_path / "ssot.md"
        ssot_path.write_text(ssot_text(), encoding="utf-8")
        ssot = ab.parse_ssot(ssot_text())
        snap = ab.build_snapshot(ssot, ssot_path, tmp_path, commit="a" * 40)
        assert snap["schema_version"] == ab.SNAPSHOT_SCHEMA_VERSION
        assert snap["generated_by"] == ab.SNAPSHOT_GENERATED_BY
        assert snap["linux_base"] == 548
        assert snap["reserved_range"] == [552, 571]
        assert len(snap["syscalls"]) == 4
        assert snap["source"]["repo"] == "docs-linux"
        assert snap["source"]["path"] == "ssot.md"
        assert snap["source"]["commit"] == "a" * 40
        assert snap["source"]["doc_version"] == "v1.0.1"

    def test_relative_source_path_docs_layout(self, tmp_path: Path) -> None:
        repo = tmp_path / "agent-linux"
        ssot = (tmp_path / "docs" / "docs-linux"
                / "140-application-development" / "07-syscall-registry.md")
        rel = ab.relative_source_path(ssot, repo)
        assert rel == ("../docs/docs-linux/140-application-development"
                       "/07-syscall-registry.md")

    def test_source_sha256_matches_file(self, tmp_path: Path) -> None:
        content = ssot_text()
        f = tmp_path / "ssot.md"
        f.write_text(content, encoding="utf-8")
        snap = ab.build_snapshot(ab.parse_ssot(content), f, tmp_path)
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert snap["source"]["sha256"] == expected

    def test_write_snapshot_is_deterministic(self, tmp_path: Path) -> None:
        src = tmp_path / "ssot.md"
        src.write_text(ssot_text(), encoding="utf-8")
        snap = ab.build_snapshot(ab.parse_ssot(ssot_text()), src, tmp_path)
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        ab.write_snapshot(a, snap)
        ab.write_snapshot(b, snap)
        assert a.read_bytes() == b.read_bytes()
        assert a.read_bytes().endswith(b"\n")

    def test_source_commit_tracks_file_history(self, tmp_path: Path) -> None:
        """溯源 commit 取文件自身最后修改提交，不随无关提交漂移。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        run_git(repo, "init", "-q")
        ssot = repo / "07-syscall-registry.md"
        ssot.write_text("v1\n", encoding="utf-8")
        run_git(repo, "add", "--", ssot.name)
        run_git(repo, "commit", "-q", "-m", "ssot")
        owner = run_git(repo, "rev-parse", "HEAD")
        (repo / "unrelated.md").write_text("x\n", encoding="utf-8")
        run_git(repo, "add", "--", "unrelated.md")
        run_git(repo, "commit", "-q", "-m", "unrelated")
        assert run_git(repo, "rev-parse", "HEAD") != owner
        assert ab.detect_source_commit(ssot) == owner

    def test_snapshot_to_ssot_roundtrip(self, tmp_path: Path) -> None:
        src = tmp_path / "ssot.md"
        src.write_text(ssot_text(), encoding="utf-8")
        ssot = ab.parse_ssot(ssot_text())
        snap = ab.build_snapshot(ssot, src, tmp_path)
        assert ab.snapshot_to_ssot(snap) == ssot

    def _snapshot(self, tmp_path: Path):
        src = tmp_path / "ssot.md"
        src.write_text(ssot_text(), encoding="utf-8")
        ssot = ab.parse_ssot(ssot_text())
        return ssot, ab.build_snapshot(ssot, src, tmp_path)

    def test_drift_identical_passes(self, tmp_path: Path) -> None:
        ssot, snap = self._snapshot(tmp_path)
        res = ab.check_snapshot_drift(ssot, snap, authoritative=True)
        assert res.passed

    def test_drift_number_change_fails(self, tmp_path: Path) -> None:
        ssot, snap = self._snapshot(tmp_path)
        snap["syscalls"][0] = dict(snap["syscalls"][0], linux_number=999)
        res = ab.check_snapshot_drift(ssot, snap, authoritative=True)
        assert not res.passed

    def test_drift_version_change_fails(self, tmp_path: Path) -> None:
        ssot, snap = self._snapshot(tmp_path)
        snap["source"]["doc_version"] = "v9.9.9"
        res = ab.check_snapshot_drift(ssot, snap, authoritative=True)
        assert not res.passed

    def test_drift_missing_snapshot_fails(self) -> None:
        ssot = ab.parse_ssot(ssot_text())
        res = ab.check_snapshot_drift(ssot, None, authoritative=True)
        assert not res.passed

    def test_drift_non_authoritative_records_provenance(self,
                                                        tmp_path: Path) -> None:
        ssot, snap = self._snapshot(tmp_path)
        snap["source"]["commit"] = "deadbeef" * 5
        res = ab.check_snapshot_drift(ssot, snap, authoritative=False)
        assert res.passed
        assert "漂移比对不可用" in res.details
        assert "deadbeefdead" in res.details


# ─── 派生契约快照：CLI 行为（镜像回退 / 失败关闭 / 刷新） ──────────────────


class TestSnapshotCli:
    def test_mirror_fallback_passes(self, repo_root: Path,
                                    tmp_path: Path) -> None:
        root = mirror_root(repo_root, tmp_path)
        proc = run_cli(root, "--check")
        assert_check_pass(proc)
        assert "contract snapshot" in proc.stdout

    def test_mirror_require_ssot_exit_2(self, repo_root: Path,
                                        tmp_path: Path) -> None:
        root = mirror_root(repo_root, tmp_path)
        proc = run_cli(root, "--check", "--require-ssot")
        assert proc.returncode == 2
        assert "authoritative SSoT not found" in proc.stderr

    def test_both_sources_missing_exit_2(self, repo_root: Path,
                                         tmp_path: Path) -> None:
        root = mirror_root(repo_root, tmp_path, with_snapshot=False)
        proc = run_cli(root, "--check")
        assert proc.returncode == 2
        assert "neither SSoT" in proc.stderr

    def test_mirror_update_snapshot_exit_2(self, repo_root: Path,
                                           tmp_path: Path) -> None:
        root = mirror_root(repo_root, tmp_path)
        proc = run_cli(root, "--update-snapshot")
        assert proc.returncode == 2
        assert "--update-snapshot" in proc.stderr

    @pytest.mark.skipif(not has_authoritative_ssot(REPO_ROOT),
                        reason="GitHub 镜像未检出权威 SSoT（../docs）")
    def test_update_snapshot_is_reproducible(self, repo_root: Path,
                                             tmp_path: Path) -> None:
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        assert run_cli(repo_root, "--update-snapshot",
                       "--snapshot-path", str(a)).returncode == 0
        assert run_cli(repo_root, "--update-snapshot",
                       "--snapshot-path", str(b)).returncode == 0
        assert a.read_bytes() == b.read_bytes()

    @pytest.mark.skipif(not has_authoritative_ssot(REPO_ROOT),
                        reason="GitHub 镜像未检出权威 SSoT（../docs）")
    def test_update_snapshot_matches_committed(self, repo_root: Path,
                                               tmp_path: Path) -> None:
        fresh = tmp_path / "fresh.json"
        assert run_cli(repo_root, "--update-snapshot",
                       "--snapshot-path", str(fresh)).returncode == 0
        committed = (repo_root / ab.DEFAULT_SNAPSHOT_REL).read_text(
            encoding="utf-8")
        assert fresh.read_text(encoding="utf-8") == committed
