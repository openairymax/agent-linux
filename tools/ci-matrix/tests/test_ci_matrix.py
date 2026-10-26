# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/ci-matrix 单元测试：清单 schema / 架构源解析 / 矩阵 job 与维度 /
格数 / exclude / 调度策略 / 超时预算 / job 约束 / 门禁语义 / 结果归因 /
CLI 与真实仓库端到端。

测试以真实清单为基线做受控变异，逐项断言各检查器的通过/拒绝语义；
另在真实仓库上运行 --check 与 --list，作为端到端回归。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import ci_matrix as cm

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = cm.load_manifest(TOOL_DIR / "manifest.yaml")
ARCHES = cm.resolve_arches(MANIFEST, REPO_ROOT)
WORKFLOW = cm.load_workflow(MANIFEST, REPO_ROOT)
JOB = cm._job(WORKFLOW, MANIFEST["matrix"]["job"])


def _manifest():
    return copy.deepcopy(MANIFEST)


def _job():
    return copy.deepcopy(JOB)


def _cells(overrides=None):
    overrides = overrides or {}
    cells = [dict(cell, conclusion="success")
             for cell in cm.expand_cells(MANIFEST, ARCHES)]
    for cell in cells:
        override = overrides.get((cell["config"], cell["arch"],
                                  cell["compiler"]))
        if override:
            cell.update(override)
    return cells


def _write(tmp_path, cells):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"cells": cells}), encoding="utf-8")
    return path


# ─── Schema ────────────────────────────────────────────────────────────────


def test_schema_accepts_real_manifest():
    assert cm.check_schema(_manifest()).passed


def test_schema_rejects_missing_section():
    manifest = _manifest()
    del manifest["matrix"]
    result = cm.check_schema(manifest)
    assert not result.passed
    assert "matrix" in result.details


def test_schema_rejects_wrong_list_type():
    manifest = _manifest()
    manifest["matrix"]["configs"] = "defconfig"
    assert not cm.check_schema(manifest).passed


def test_schema_rejects_wrong_fail_fast_type():
    manifest = _manifest()
    manifest["matrix"]["fail_fast"] = "false"
    assert not cm.check_schema(manifest).passed


def test_schema_rejects_missing_reason_key():
    manifest = _manifest()
    del manifest["failures"]["reasons"]["other"]
    assert not cm.check_schema(manifest).passed


# ─── Sources ───────────────────────────────────────────────────────────────


def test_arches_resolve_to_build_targets():
    assert ARCHES == ["x86_64", "arm64", "riscv", "loongarch"]


def test_arches_reject_missing_manifest(tmp_path):
    manifest = _manifest()
    manifest["arch_manifest"] = "tools/absent/manifest.yaml"
    with pytest.raises(cm.ManifestError):
        cm.resolve_arches(manifest, tmp_path)


def test_arches_reject_empty_build_set(tmp_path):
    path = tmp_path / "arch.yaml"
    path.write_text("architectures:\n  - arch: x86_64\n    build: false\n",
                    encoding="utf-8")
    manifest = _manifest()
    manifest["arch_manifest"] = "arch.yaml"
    with pytest.raises(cm.ManifestError):
        cm.resolve_arches(manifest, tmp_path)


# ─── Job and dimensions ────────────────────────────────────────────────────


def test_job_exists_in_real_workflow():
    assert cm.check_job(WORKFLOW, JOB, MANIFEST).passed


def test_job_missing_is_rejected():
    assert not cm.check_job(WORKFLOW, None, MANIFEST).passed


def test_dims_match_real_workflow():
    assert cm.check_dims(JOB, MANIFEST, ARCHES).passed


def test_dims_detect_config_drift():
    job = _job()
    job["strategy"]["matrix"]["config"] = ["defconfig"]
    assert not cm.check_dims(job, MANIFEST, ARCHES).passed


# ─── Cells and exclude ─────────────────────────────────────────────────────


def test_cell_count_is_twenty_eight():
    assert len(cm.expand_cells(MANIFEST, ARCHES)) == 28


def test_cell_check_detects_expected_drift():
    manifest = _manifest()
    manifest["matrix"]["expected_cells"] = 27
    assert not cm.check_cells(manifest, ARCHES).passed


def test_exclude_matches_real_workflow():
    assert cm.check_exclude(JOB, MANIFEST, ARCHES).passed


def test_exclude_detects_workflow_drift():
    job = _job()
    job["strategy"]["matrix"]["exclude"] = []
    assert not cm.check_exclude(job, MANIFEST, ARCHES).passed


def test_exclude_rejects_unknown_dimension():
    manifest = _manifest()
    manifest["matrix"]["exclude"] = [{"arch": "loongarch", "libc": "musl"}]
    assert not cm.check_exclude(JOB, manifest, ARCHES).passed


def test_exclude_rejects_unmatched_value():
    manifest = _manifest()
    manifest["matrix"]["exclude"] = [{"arch": "mips"}]
    assert not cm.check_exclude(JOB, manifest, ARCHES).passed


# ─── Scheduling and budget ─────────────────────────────────────────────────


def test_strategy_matches_manifest():
    assert cm.check_failfast(JOB, MANIFEST).passed
    assert cm.check_parallel(JOB, MANIFEST).passed


def test_fail_fast_drift_is_rejected():
    job = _job()
    job["strategy"]["fail-fast"] = True
    assert not cm.check_failfast(job, MANIFEST).passed


def test_parallel_drift_is_rejected():
    job = _job()
    job["strategy"]["max-parallel"] = 4
    assert not cm.check_parallel(job, MANIFEST).passed


def test_timeout_matches_budget():
    assert cm.check_timeout(JOB, MANIFEST).passed


def test_timeout_missing_is_rejected():
    job = _job()
    del job["timeout-minutes"]
    assert not cm.check_timeout(job, MANIFEST).passed


def test_timeout_above_ceiling_is_rejected():
    ceiling = cm.JOB_MIN_CEILING + 1
    manifest = _manifest()
    manifest["budget"]["timeout_minutes"] = ceiling
    job = _job()
    job["timeout-minutes"] = ceiling
    assert not cm.check_timeout(job, manifest).passed


def test_job_count_and_declarations_pass():
    assert cm.check_jobcount(WORKFLOW, MANIFEST).passed


def test_job_count_limit_is_enforced():
    workflow = copy.deepcopy(WORKFLOW)
    workflow["jobs"]["extra"] = {"timeout-minutes": 5, "steps": []}
    assert not cm.check_jobcount(workflow, MANIFEST).passed


def test_job_without_timeout_is_rejected():
    workflow = copy.deepcopy(WORKFLOW)
    del workflow["jobs"]["kernel-verify"]["timeout-minutes"]
    assert not cm.check_jobcount(workflow, MANIFEST).passed


def test_gate_tokens_present():
    assert cm.check_tokens(JOB, MANIFEST, REPO_ROOT).passed


def test_gate_tokens_resolve_invoked_gate_script():
    """语义片段可落在 gate 脚本：workflow 只留 ci/ci.sh 调用。"""
    job = {"steps": [{"run": "ci/ci.sh kernel-build --config defconfig"}]}
    assert cm.check_tokens(job, MANIFEST, REPO_ROOT).passed


def test_gate_token_missing_is_rejected():
    job = _job()
    job["steps"] = [{"run": "make all"}]
    assert not cm.check_tokens(job, MANIFEST, REPO_ROOT).passed


# ─── Summary classification ────────────────────────────────────────────────


def test_summary_all_green(tmp_path, capsys):
    rc = cm.run_summary(MANIFEST, ARCHES, _write(tmp_path, _cells()))
    assert rc == cm.EXIT_PASS
    assert "PASS (28/28 cells)" in capsys.readouterr().out


def test_summary_defconfig_failure(tmp_path, capsys):
    cells = _cells({("defconfig", "x86_64", "gcc"): {
        "conclusion": "failure"}})
    rc = cm.run_summary(MANIFEST, ARCHES, _write(tmp_path, cells))
    out = capsys.readouterr().out
    assert rc == cm.EXIT_FAIL
    assert "默认路径损坏" in out
    assert "ci-fail-compile" in out


def test_summary_detects_missing_cell(tmp_path, capsys):
    cells = _cells()
    cells = [cell for cell in cells
             if not (cell["arch"] == "riscv"
                     and cell["compiler"] == "gcc"
                     and cell["config"] == "defconfig")]
    rc = cm.run_summary(MANIFEST, ARCHES, _write(tmp_path, cells))
    assert rc == cm.EXIT_FAIL
    assert "MISSING" in capsys.readouterr().out


def test_summary_detects_unknown_cell(tmp_path, capsys):
    cells = _cells()
    cells.append({"config": "defconfig", "arch": "mips",
                  "compiler": "gcc", "conclusion": "success"})
    rc = cm.run_summary(MANIFEST, ARCHES, _write(tmp_path, cells))
    assert rc == cm.EXIT_FAIL
    assert "UNKNOWN" in capsys.readouterr().out


def test_summary_rejects_duplicate_cell(tmp_path):
    cells = _cells()
    cells.append(dict(cells[0]))
    with pytest.raises(cm.ManifestError):
        cm.run_summary(MANIFEST, ARCHES, _write(tmp_path, cells))


def test_summary_classifies_compiler_gap(tmp_path, capsys):
    cells = _cells({("allmodconfig", "arm64", "clang"): {
        "conclusion": "failure"}})
    rc = cm.run_summary(MANIFEST, ARCHES, _write(tmp_path, cells))
    assert rc == cm.EXIT_FAIL
    assert "编译器可移植性" in capsys.readouterr().out


def test_summary_classifies_arch_gap(tmp_path, capsys):
    cells = _cells({("defconfig", "arm64", "gcc"): {
        "conclusion": "failure"}})
    rc = cm.run_summary(MANIFEST, ARCHES, _write(tmp_path, cells))
    assert rc == cm.EXIT_FAIL
    assert "架构抽象缺失" in capsys.readouterr().out


def test_summary_classifies_allmodconfig(tmp_path, capsys):
    cells = _cells({("allmodconfig", "x86_64", "gcc"): {
        "conclusion": "failure"}})
    rc = cm.run_summary(MANIFEST, ARCHES, _write(tmp_path, cells))
    assert rc == cm.EXIT_FAIL
    assert "符号冲突" in capsys.readouterr().out


def test_summary_rejects_skipped_cell(tmp_path, capsys):
    cells = _cells({("defconfig", "x86_64", "gcc"): {
        "conclusion": "skipped"}})
    rc = cm.run_summary(MANIFEST, ARCHES, _write(tmp_path, cells))
    assert rc == cm.EXIT_FAIL
    assert "skipped" in capsys.readouterr().out


def test_summary_rejects_bad_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(cm.ManifestError):
        cm.run_summary(MANIFEST, ARCHES, path)


# ─── CLI ───────────────────────────────────────────────────────────────────


def test_cli_check_passes_on_real_repo():
    assert cm.main(["--check"]) == cm.EXIT_PASS


def test_cli_list_expands_matrix(capsys):
    assert cm.main(["--list"]) == cm.EXIT_PASS
    assert "TOTAL: 28" in capsys.readouterr().out


def test_cli_missing_manifest_exits_usage(tmp_path):
    rc = cm.main(["--manifest", str(tmp_path / "absent.yaml"), "--check"])
    assert rc == cm.EXIT_USAGE


def test_cli_summary_reports_failure(tmp_path, capsys):
    cells = _cells({("allnoconfig", "x86_64", "gcc"): {
        "conclusion": "failure"}})
    path = _write(tmp_path, cells)
    assert cm.main(["--summary", str(path)]) == cm.EXIT_FAIL
    assert "Kconfig 依赖闭包" in capsys.readouterr().out
