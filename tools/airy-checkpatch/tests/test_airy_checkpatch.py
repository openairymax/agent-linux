# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/airy-checkpatch 集成测试。

以受控输入驱动真实的 Perl 规则引擎
`kernel/scripts/airy_checkpatch_ext.pl`，逐条验证 AIRY-CHECKPATCH-001~011
的命中行为与严重级语义（error 阻断 / warning 非阻断），并覆盖默认扫描
范围、显式目标裁剪、帮助输出与退出码契约。

本套件不复制规则实现，只断言引擎对既定输入的实际输出，从而与引擎本体
保持单一事实源（SSoT），避免规则逻辑在两处漂移。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SUMMARY_RE = re.compile(
    r"airy_checkpatch_ext:\s*(\d+) files checked,\s*(\d+) errors,"
    r"\s*(\d+) warnings")

# 引擎要求源文件第 1 行携带 SPDX，受控 C 输入统一以此开头。
C_HEADER = "/* SPDX-License-Identifier: GPL-2.0 */\n"

RULE_IDS = tuple(f"AIRY-CHECKPATCH-{n:03d}" for n in range(1, 12))


def run_engine(perl, kernel_root, *args):
    """在 kernel 根目录下调用扩展规则引擎，返回完成进程。"""
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    script = kernel_root / "scripts" / "airy_checkpatch_ext.pl"
    return subprocess.run(
        [perl, str(script), *args],
        cwd=kernel_root, capture_output=True, text=True,
        env=env, timeout=300)


def summarize(proc):
    """解析汇总行，返回 (files, errors, warnings)。"""
    match = SUMMARY_RE.search(proc.stdout)
    assert match, f"缺少汇总行:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def reported_ids(proc):
    """提取输出中命中的规则编号集合。"""
    return {rid for rid in RULE_IDS if rid in proc.stdout}


def c_file(tmp_path, name, *lines):
    """生成带 SPDX 文件头的受控 C 源文件。"""
    path = tmp_path / name
    path.write_text(C_HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def raw_file(tmp_path, name, body):
    """生成不附加文件头的受控文件。"""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def copy_engine(kernel_root, tmp_path):
    """把引擎复制到临时仓库的 scripts/ 下，用于默认落点边界测试。"""
    root = tmp_path / "kernel"
    (root / "scripts").mkdir(parents=True)
    src = kernel_root / "scripts" / "airy_checkpatch_ext.pl"
    (root / "scripts" / src.name).write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8")
    return root


class TestCliContract:
    def test_help_lists_every_rule(self, perl, kernel_root):
        proc = run_engine(perl, kernel_root, "--help")
        assert proc.returncode == 0
        assert "用法:" in proc.stdout
        assert "退出码:" in proc.stdout
        for n in range(1, 12):
            assert f"  {n:03d} " in proc.stdout

    def test_unknown_option_is_usage_error(self, perl, kernel_root):
        proc = run_engine(perl, kernel_root, "--no-such-option")
        assert proc.returncode == 2
        assert "invalid usage" in proc.stderr

    def test_list_rules_is_tsv_registry(self, perl, kernel_root):
        proc = run_engine(perl, kernel_root, "--list-rules")
        assert proc.returncode == 0
        assert "files checked" not in proc.stdout
        rows = [line.split("\t") for line in proc.stdout.splitlines()]
        assert [row[0] for row in rows] == list(RULE_IDS)
        assert all(len(row) == 3 for row in rows)
        assert {row[1] for row in rows} <= {"error", "warning"}
        warnings = {row[0] for row in rows if row[1] == "warning"}
        assert warnings == {"AIRY-CHECKPATCH-005"}

    def test_help_derives_from_registry(self, perl, kernel_root):
        listing = run_engine(perl, kernel_root, "--list-rules")
        help_out = run_engine(perl, kernel_root, "--help")
        assert help_out.returncode == 0
        for rid, sev, brief in (row.split("\t") for row in
                                listing.stdout.splitlines()):
            assert f"{rid[-3:]} {sev:<8} {brief}" in help_out.stdout


class TestErrorPaths:
    def test_quiet_omits_summary_but_keeps_violations(self, perl, kernel_root,
                                                      tmp_path):
        path = c_file(tmp_path, "quiet.c", "void badname(void)", "{", "}")
        proc = run_engine(perl, kernel_root, "-q", str(path))
        assert proc.returncode == 1
        assert "AIRY-CHECKPATCH-002" in proc.stdout
        assert "files checked" not in proc.stdout

    def test_missing_explicit_target_is_usage_error(self, perl, kernel_root,
                                                    tmp_path):
        missing = tmp_path / "missing"
        proc = run_engine(perl, kernel_root, str(missing))
        assert proc.returncode == 2
        assert f"{missing}: no such file or directory" in proc.stderr
        assert "files checked" not in proc.stdout

    def test_unreadable_file_is_io_error(self, perl, kernel_root, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("root 无视文件权限，无法构造读取失败")
        path = c_file(tmp_path, "locked.c", "void airy_probe(void)", "{", "}")
        path.chmod(0o000)
        try:
            proc = run_engine(perl, kernel_root, str(path))
        finally:
            path.chmod(0o644)
        assert proc.returncode == 2
        assert f"{path.name}: cannot read" in proc.stderr

    def test_quiet_does_not_hide_errors(self, perl, kernel_root, tmp_path):
        root = copy_engine(kernel_root, tmp_path)
        proc = run_engine(perl, root, "-q")
        assert proc.returncode == 2
        assert "no input files found" in proc.stderr


class TestDefaultScan:
    def test_shipped_tree_is_clean(self, perl, kernel_root):
        proc = run_engine(perl, kernel_root)
        files, errors, _ = summarize(proc)
        assert errors == 0
        assert proc.returncode == 0
        assert files >= 50

    def test_explicit_target_limits_scan(self, perl, kernel_root):
        proc = run_engine(perl, kernel_root, "security/airy")
        files, errors, _ = summarize(proc)
        scoped = sum(1 for p in (kernel_root / "security" / "airy")
                     .rglob("*") if p.is_file())
        defconfigs = sum(1 for _ in kernel_root.glob(
            "arch/*/configs/airy*_defconfig"))
        assert files == scoped + defconfigs
        assert errors == 0
        assert proc.returncode == 0

    def test_default_scan_reaches_init_and_trace(self, perl, kernel_root,
                                                 tmp_path):
        root = copy_engine(kernel_root, tmp_path)
        (root / "init").mkdir()
        (root / "init" / "airy_core.c").write_text(
            C_HEADER + "void airy_probe(void)\n{\n}\n", encoding="utf-8")
        trace = root / "include" / "trace" / "events"
        trace.mkdir(parents=True)
        (trace / "airy.h").write_text(C_HEADER, encoding="utf-8")
        proc = run_engine(perl, root)
        files, errors, _ = summarize(proc)
        assert files == 2
        assert errors == 0

    def test_vcs_metadata_is_pruned(self, perl, kernel_root, tmp_path):
        tree = tmp_path / "scan"
        (tree / ".git").mkdir(parents=True)
        (tree / ".git" / "config").write_text("CONFIG_BPF_LSM=y\n",
                                              encoding="utf-8")
        (tree / "frag.config").write_text("CONFIG_SCHED_EXT=y\n",
                                          encoding="utf-8")
        proc = run_engine(perl, kernel_root, str(tree))
        files, _, _ = summarize(proc)
        defconfigs = sum(1 for _ in kernel_root.glob(
            "arch/*/configs/airy*_defconfig"))
        assert files == 1 + defconfigs
        assert "AIRY-CHECKPATCH-009" not in reported_ids(proc)
        assert "AIRY-CHECKPATCH-010" in reported_ids(proc)


class TestHeaderRule:
    def test_missing_spdx_on_line_one(self, perl, kernel_root, tmp_path):
        path = raw_file(tmp_path, "no_spdx.c",
                        "/* plain comment */\nstatic int probe;\n")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-011" in reported_ids(proc)
        assert "line 1 must carry an SPDX-License-Identifier" in proc.stdout
        assert proc.returncode == 1

    def test_over_long_leading_comment(self, perl, kernel_root, tmp_path):
        body = (C_HEADER + "/*\n"
                + "".join(f" * filler {i}\n" for i in range(25))
                + " */\n#include <linux/types.h>\n")
        path = raw_file(tmp_path, "long_header.c", body)
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-011" in reported_ids(proc)
        assert "max 20" in proc.stdout
        assert proc.returncode == 1


class TestCodeRules:
    def test_printk_outside_airy_function(self, perl, kernel_root, tmp_path):
        path = c_file(tmp_path, "probe.c",
                      "static void probe(void)", "{",
                      '\tprintk("hi\\n");', "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-001" in reported_ids(proc)
        assert "AIRY-CHECKPATCH-006" not in reported_ids(proc)
        assert proc.returncode == 1

    def test_printk_inside_airy_function(self, perl, kernel_root, tmp_path):
        path = c_file(tmp_path, "inairy.c",
                      "void airy_probe(void)", "{",
                      '\tprintk("hi\\n");', "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-006" in reported_ids(proc)
        assert "AIRY-CHECKPATCH-001" not in reported_ids(proc)
        assert proc.returncode == 1

    def test_printk_inside_static_airy_function(self, perl, kernel_root,
                                                tmp_path):
        path = c_file(tmp_path, "static_airy.c",
                      "static void airy_probe(void)", "{",
                      "\tif (1) {", '\t\tprintk("hi\\n");', "\t}", "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-006" in reported_ids(proc)
        assert "AIRY-CHECKPATCH-001" not in reported_ids(proc)
        assert proc.returncode == 1

    def test_function_without_airy_prefix(self, perl, kernel_root, tmp_path):
        path = c_file(tmp_path, "naming.c",
                      "void badname(void)", "{", "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-002" in reported_ids(proc)
        assert "must use the airy_/stc_ prefix" in proc.stdout
        assert proc.returncode == 1

    def test_agent_state_write_without_once(self, perl, kernel_root, tmp_path):
        path = c_file(tmp_path, "state.c",
                      "void airy_probe(void)", "{",
                      "\tstruct airy_agent *p = NULL;",
                      "\tp->airy_agent_state = 1;", "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-003" in reported_ids(proc)
        assert proc.returncode == 1

    def test_unchecked_ipc_send(self, perl, kernel_root, tmp_path):
        path = c_file(tmp_path, "ipc.c", "\tairy_ipc_send(p);")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-004" in reported_ids(proc)
        assert proc.returncode == 1

    def test_multiline_unchecked_ipc_send(self, perl, kernel_root, tmp_path):
        path = c_file(tmp_path, "ipc_multi.c",
                      "static int airy_probe(void)", "{",
                      "\tairy_ipc_send(p,", "\t\t     q);", "\treturn 0;", "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-004" in reported_ids(proc)
        assert proc.returncode == 1

    def test_checked_ipc_send_is_silent(self, perl, kernel_root, tmp_path):
        path = c_file(tmp_path, "ipc_checked.c",
                      "static int airy_probe(void)", "{",
                      "\tint ret;", "",
                      "\tret = airy_ipc_send(p);", "\treturn ret;", "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-004" not in reported_ids(proc)
        assert proc.returncode == 0

    def test_void_cast_ipc_send_is_still_unchecked(self, perl, kernel_root,
                                                   tmp_path):
        path = c_file(tmp_path, "ipc_void.c",
                      "static void airy_probe(void)", "{",
                      "\t(void)airy_ipc_send(p);", "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-004" in reported_ids(proc)
        assert proc.returncode == 1

    def test_direct_lsm_registration(self, perl, kernel_root, tmp_path):
        path = c_file(tmp_path, "lsm.c",
                      "void airy_lsm_setup(void)", "{",
                      "\tsecurity_hook_heads.probe = NULL;", "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-007" in reported_ids(proc)
        assert proc.returncode == 1

    def test_dma_in_ipc_path(self, perl, kernel_root, tmp_path):
        path = c_file(
            tmp_path, "airy_ipc_probe.c",
            "\tvoid *p = dma_alloc_coherent(dev, len, &addr, GFP_KERNEL);")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-008" in reported_ids(proc)
        assert proc.returncode == 1


class TestWarningSemantics:
    def test_cap_check_is_warning_not_blocking(self, perl, kernel_root,
                                               tmp_path):
        path = c_file(tmp_path, "cap.c", "\tairy_cap_check(p);")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-005" in reported_ids(proc)
        assert "WARNING" in proc.stdout
        _, errors, warnings = summarize(proc)
        assert errors == 0
        assert warnings == 1
        assert proc.returncode == 0

    def test_consumed_cap_check_is_silent(self, perl, kernel_root, tmp_path):
        path = c_file(tmp_path, "cap_ok.c",
                      "static int airy_probe(u32 id)", "{",
                      "\tint ret = airy_cap_check(id, AIRY_CAP_PERM_SEND);",
                      "", "\tif (airy_cap_check(id, AIRY_CAP_PERM_KILL))",
                      "\t\treturn -EPERM;", "\treturn ret;", "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-005" not in reported_ids(proc)
        assert proc.returncode == 0

    def test_multiline_macro_cap_check_is_silent(self, perl, kernel_root,
                                                 tmp_path):
        path = c_file(tmp_path, "cap_macro.c",
                      "static void airy_probe(void)", "{",
                      "\tKUNIT_EXPECT_EQ(test, -EPERM,",
                      "\t\t\tairy_cap_check(id, AIRY_CAP_PERM_KILL));", "}")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-005" not in reported_ids(proc)
        assert proc.returncode == 0


class TestConfigBlacklist:
    def test_bpf_lsm_is_forbidden(self, perl, kernel_root, tmp_path):
        path = raw_file(tmp_path, "frag_bpf.config", "CONFIG_BPF_LSM=y\n")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-009" in reported_ids(proc)
        assert "AIRY-CHECKPATCH-010" not in reported_ids(proc)
        assert proc.returncode == 1

    def test_sched_ext_is_forbidden(self, perl, kernel_root, tmp_path):
        path = raw_file(tmp_path, "frag_sched.config", "CONFIG_SCHED_EXT=y\n")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-010" in reported_ids(proc)
        assert "AIRY-CHECKPATCH-009" not in reported_ids(proc)
        assert proc.returncode == 1

    def test_module_builds_are_also_forbidden(self, perl, kernel_root,
                                              tmp_path):
        path = raw_file(tmp_path, "frag_mod.config",
                        "CONFIG_BPF_LSM=m\nCONFIG_SCHED_EXT=m\n")
        proc = run_engine(perl, kernel_root, str(path))
        ids = reported_ids(proc)
        assert "AIRY-CHECKPATCH-009" in ids
        assert "AIRY-CHECKPATCH-010" in ids
        assert proc.returncode == 1

    def test_explicitly_disabled_options_are_allowed(self, perl, kernel_root,
                                                     tmp_path):
        path = raw_file(tmp_path, "frag_off.config",
                        "# CONFIG_BPF_LSM is not set\n"
                        "# CONFIG_SCHED_EXT is not set\n")
        proc = run_engine(perl, kernel_root, str(path))
        assert "AIRY-CHECKPATCH-009" not in reported_ids(proc)
        assert "AIRY-CHECKPATCH-010" not in reported_ids(proc)
        assert proc.returncode == 0
