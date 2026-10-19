# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""tools/repo-scaffold 单元测试：清单 schema / 子仓登记 / 检出 / 公共骨架 /
许可证标识 / 构建入口 / 可编译入口（CMake、Kbuild、Linux 原生）/ 报告与
CLI / 真实仓库端到端。

测试以真实清单为基线，在临时目录合成最小合规仓库，再逐项施加受控变异，
断言各检查器只对目标漂移给出失败证据。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

import repo_scaffold as rs

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = rs.load_yaml(TOOL_DIR / "manifest.yaml")

KERNEL = "kernel"
SECURITY = "security"
SERVICES = "services"
GITMODULES = MANIFEST["gitmodules"]
EXCLUDED = MANIFEST["excluded"][0]

_BUILD = {
    "cmake": ("CMakeLists.txt",
              "add_library(demo STATIC demo.c)\n", "demo.c"),
    "kbuild": ("Kbuild", "obj-m := demo.o\n", "demo.c"),
    "native": ("Makefile", "all:\n\t@true\n", None),
}


def _man() -> dict:
    return copy.deepcopy(MANIFEST)


def _spec(man: dict, path: str) -> dict:
    return next(item for item in man["subrepos"] if item["path"] == path)


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build(base: Path, entry: dict) -> None:
    name, text, source = _BUILD[rs.KINDS_BY_ENTRY[entry["build_entry"]]]
    _w(base / name, text)
    if source:
        _w(base / source, "int demo(void) { return 0; }\n")
    probe = entry.get(rs.PROBE_KEY)
    if probe:
        _w(base / probe, "int main(void) { return 0; }\n")


def _fill(base: Path, man: dict, entry: dict) -> None:
    for rel in man["skeletons"][entry["skeleton"]]:
        _w(base / rel, "# skeleton\n")
    lic = man["licenses"][entry["license"]]
    _w(base / lic["file"], f"{lic['marker']}\n")
    _build(base, entry)


def _modules(man: dict) -> str:
    rows = [f'[submodule "{item["path"]}"]\n'
            f'\tpath = {item["path"]}\n\turl = x\n'
            for item in man["subrepos"]]
    rows += [f'[submodule "{path}"]\n\tpath = {path}\n\turl = x\n'
             for path in man["excluded"]]
    return "".join(rows)


def _materialize(root: Path, man: dict) -> None:
    _w(root / man["gitmodules"], _modules(man))
    manifest = root / rs.DEFAULT_MANIFEST_REL
    manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TOOL_DIR / "manifest.yaml", manifest)
    for entry in man["subrepos"]:
        _fill(root / entry["path"], man, entry)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _materialize(tmp_path, _man())
    return tmp_path


# ─── Schema ────────────────────────────────────────────────────────────────


def test_schema_accepts_real_manifest() -> None:
    assert rs.check_schema(_man()).passed


def test_schema_short_circuits_other_checks(tmp_path: Path) -> None:
    man = _man()
    del man["subrepos"]
    results = rs.build_checks(man, tmp_path)
    assert [item.name for item in results] == [rs.NAME_SCHEMA]
    assert not results[0].passed


def test_schema_rejects_unknown_top_key() -> None:
    man = _man()
    man["worker"] = 1
    result = rs.check_schema(man)
    assert not result.passed
    assert "worker" in result.details


def test_schema_rejects_wrong_repo_count() -> None:
    man = _man()
    man["subrepos"].pop()
    result = rs.check_schema(man)
    assert not result.passed
    assert "恰好 8 项" in result.details


def test_schema_rejects_duplicate_path() -> None:
    man = _man()
    man["subrepos"][1]["path"] = man["subrepos"][0]["path"]
    result = rs.check_schema(man)
    assert not result.passed
    assert "重复" in result.details


def test_schema_rejects_unknown_build_entry() -> None:
    man = _man()
    _spec(man, SERVICES)["build_entry"] = "meson.build"
    result = rs.check_schema(man)
    assert not result.passed
    assert "build_entry" in result.details


def test_schema_rejects_unknown_skeleton() -> None:
    man = _man()
    _spec(man, SERVICES)["skeleton"] = "minimal"
    result = rs.check_schema(man)
    assert not result.passed
    assert "skeleton" in result.details


def test_schema_rejects_unknown_license() -> None:
    man = _man()
    _spec(man, SERVICES)["license"] = "mit"
    result = rs.check_schema(man)
    assert not result.passed
    assert "license" in result.details


def test_schema_rejects_probe_on_cmake_entry() -> None:
    man = _man()
    _spec(man, SERVICES)[rs.PROBE_KEY] = "main.c"
    result = rs.check_schema(man)
    assert not result.passed
    assert "仅 Makefile 入口可用" in result.details


def test_schema_requires_probe_for_native_entry() -> None:
    man = _man()
    del _spec(man, KERNEL)[rs.PROBE_KEY]
    result = rs.check_schema(man)
    assert not result.passed
    assert "entry_probe" in result.details


def test_schema_rejects_unsafe_path() -> None:
    man = _man()
    _spec(man, SERVICES)["path"] = "../escape"
    result = rs.check_schema(man)
    assert not result.passed
    assert "非法" in result.details


def test_schema_rejects_empty_excluded() -> None:
    man = _man()
    man["excluded"] = ""
    result = rs.check_schema(man)
    assert not result.passed
    assert "excluded" in result.details


# ─── Registry ──────────────────────────────────────────────────────────────


def test_registry_accepts_materialized_repo(repo: Path) -> None:
    assert rs.check_registry(_man(), repo).passed


def test_registry_detects_missing_module(repo: Path) -> None:
    path = repo / GITMODULES
    text = path.read_text(encoding="utf-8").replace(
        f"path = {SECURITY}\n", "")
    _w(path, text)
    result = rs.check_registry(_man(), repo)
    assert not result.passed
    assert SECURITY in result.details


def test_registry_detects_extra_module(repo: Path) -> None:
    path = repo / GITMODULES
    _w(path, path.read_text(encoding="utf-8")
       + '[submodule "extra"]\n\tpath = extra\n\turl = x\n')
    result = rs.check_registry(_man(), repo)
    assert not result.passed
    assert "extra" in result.details


def test_registry_detects_unregistered_excluded(repo: Path) -> None:
    path = repo / GITMODULES
    text = path.read_text(encoding="utf-8").replace(
        f"path = {EXCLUDED}\n", "")
    _w(path, text)
    result = rs.check_registry(_man(), repo)
    assert not result.passed
    assert EXCLUDED in result.details


def test_registry_detects_excluded_overlap(repo: Path) -> None:
    man = _man()
    man["excluded"] = [SERVICES]
    result = rs.check_registry(man, repo)
    assert not result.passed
    assert "冲突" in result.details


def test_registry_reports_missing_gitmodules(repo: Path) -> None:
    (repo / GITMODULES).unlink()
    result = rs.check_registry(_man(), repo)
    assert not result.passed
    assert ".gitmodules" in result.details


# ─── Checkout / skeleton / license / build ─────────────────────────────────


def test_checkout_detects_missing_repo(repo: Path) -> None:
    shutil.rmtree(repo / SECURITY)
    result = rs.check_checkout(_man(), repo)
    assert not result.passed
    assert SECURITY in result.details


def test_skeleton_detects_missing_file(repo: Path) -> None:
    (repo / SERVICES / "NOTICE").unlink()
    result = rs.check_skeleton(_man(), repo)
    assert not result.passed
    assert "NOTICE" in result.details


def test_skeleton_detects_empty_file(repo: Path) -> None:
    _w(repo / SERVICES / "MAINTAINERS", "")
    result = rs.check_skeleton(_man(), repo)
    assert not result.passed
    assert "空文件" in result.details


def test_license_detects_missing_file(repo: Path) -> None:
    (repo / SERVICES / "LICENSE").unlink()
    result = rs.check_license(_man(), repo)
    assert not result.passed
    assert "LICENSE" in result.details


def test_license_detects_missing_marker(repo: Path) -> None:
    _w(repo / SERVICES / "LICENSE", "placeholder\n")
    result = rs.check_license(_man(), repo)
    assert not result.passed
    assert "标识" in result.details


def test_build_detects_missing_entry(repo: Path) -> None:
    (repo / SERVICES / "CMakeLists.txt").unlink()
    result = rs.check_build(_man(), repo)
    assert not result.passed
    assert "CMakeLists.txt" in result.details


def test_build_detects_empty_entry(repo: Path) -> None:
    _w(repo / SECURITY / "Kbuild", "")
    result = rs.check_build(_man(), repo)
    assert not result.passed
    assert "空入口" in result.details


# ─── Entry: CMake ──────────────────────────────────────────────────────────


def test_entry_accepts_cmake(repo: Path) -> None:
    assert rs.check_entry(_man(), repo).passed


def test_entry_detects_missing_cmake_source(repo: Path) -> None:
    (repo / SERVICES / "demo.c").unlink()
    result = rs.check_entry(_man(), repo)
    assert not result.passed
    assert "源文件不存在 demo.c" in result.details


def test_entry_detects_empty_cmake_source(repo: Path) -> None:
    _w(repo / SERVICES / "demo.c", "")
    result = rs.check_entry(_man(), repo)
    assert not result.passed
    assert "空源文件" in result.details


def test_entry_requires_cmake_subdir(repo: Path) -> None:
    _w(repo / SERVICES / "CMakeLists.txt", "add_subdirectory(absent)\n")
    result = rs.check_entry(_man(), repo)
    assert not result.passed
    assert "目录不存在 absent" in result.details


def test_entry_requires_subdir_cmakelists(repo: Path) -> None:
    _w(repo / SERVICES / "CMakeLists.txt", "add_subdirectory(sub)\n")
    (repo / SERVICES / "sub").mkdir()
    result = rs.check_entry(_man(), repo)
    assert not result.passed
    assert "缺少 CMakeLists.txt sub" in result.details


def test_entry_resolves_cmake_subdir_targets(repo: Path) -> None:
    _w(repo / SERVICES / "CMakeLists.txt", "add_subdirectory(sub)\n")
    _w(repo / SERVICES / "sub" / "CMakeLists.txt",
       "add_executable(demo demo.c)\n")
    _w(repo / SERVICES / "sub" / "demo.c", "int main(void) { return 0; }\n")
    assert rs.check_entry(_man(), repo).passed


def test_entry_requires_any_cmake_source(repo: Path) -> None:
    _w(repo / SERVICES / "CMakeLists.txt", "project(demo)\n")
    result = rs.check_entry(_man(), repo)
    assert not result.passed
    assert "未引用任何源文件" in result.details


# ─── Entry: Kbuild ─────────────────────────────────────────────────────────


def test_entry_accepts_kbuild(repo: Path) -> None:
    assert rs.check_entry(_man(), repo).passed


def test_entry_detects_missing_kbuild_source(repo: Path) -> None:
    (repo / SECURITY / "demo.c").unlink()
    result = rs.check_entry(_man(), repo)
    assert not result.passed
    assert "源文件不存在 demo.c" in result.details


def test_entry_accepts_kbuild_module_dir(repo: Path) -> None:
    base = repo / SECURITY
    _w(base / "Kbuild", "obj-m := mod/\n")
    _w(base / "mod" / "Kbuild", "obj-m := inner.o\n")
    _w(base / "mod" / "inner.c", "int inner(void) { return 0; }\n")
    assert rs.check_entry(_man(), repo).passed


def test_entry_detects_missing_module_dir(repo: Path) -> None:
    _w(repo / SECURITY / "Kbuild", "obj-m := absent/\n")
    result = rs.check_entry(_man(), repo)
    assert not result.passed
    assert "模块目录缺少 Kbuild" in result.details


def test_entry_accepts_composed_module(repo: Path) -> None:
    base = repo / SECURITY
    _w(base / "Kbuild", "obj-m := demo.o\ndemo-objs := a.o b.o\n")
    _w(base / "a.c", "int a(void) { return 0; }\n")
    _w(base / "b.c", "int b(void) { return 0; }\n")
    (base / "demo.c").unlink()
    assert rs.check_entry(_man(), repo).passed


def test_entry_detects_empty_composed_source(repo: Path) -> None:
    base = repo / SECURITY
    _w(base / "Kbuild", "obj-m := demo.o\ndemo-objs := a.o\n")
    _w(base / "a.c", "")
    (base / "demo.c").unlink()
    result = rs.check_entry(_man(), repo)
    assert not result.passed
    assert "空源文件 a.c" in result.details


# ─── Entry: native ─────────────────────────────────────────────────────────


def test_entry_detects_missing_probe(repo: Path) -> None:
    (repo / KERNEL / "init" / "main.c").unlink()
    result = rs.check_entry(_man(), repo)
    assert not result.passed
    assert "入口探针不存在" in result.details


def test_entry_detects_empty_probe(repo: Path) -> None:
    _w(repo / KERNEL / "init" / "main.c", "")
    result = rs.check_entry(_man(), repo)
    assert not result.passed
    assert "入口探针为空" in result.details


# ─── Report / CLI ──────────────────────────────────────────────────────────


def test_report_lists_failures(capsys) -> None:
    result = rs.CheckResult(rs.NAME_BUILD, False, "missing")
    rs.print_report([result], True)
    out = capsys.readouterr().out
    assert "REPO-SCAFFOLD: FAIL (0/1 checks)" in out
    assert "FAIL 构建入口: missing" in out


def test_report_succeeds_quietly(repo: Path, capsys) -> None:
    results = rs.build_checks(_man(), repo)
    rs.print_report(results, True)
    assert "REPO-SCAFFOLD: PASS (7/7 checks)" in capsys.readouterr().out


def test_main_passes_on_materialized_repo(repo: Path) -> None:
    assert rs.main(["--repo-root", str(repo), "--check"]) == rs.EXIT_PASS


def test_main_fails_on_broken_repo(repo: Path) -> None:
    (repo / SERVICES / "LICENSE").unlink()
    assert rs.main(["--repo-root", str(repo), "--check"]) == rs.EXIT_FAIL


def test_main_rejects_missing_root(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    assert rs.main(["--repo-root", str(missing)]) == rs.EXIT_USAGE


def test_main_rejects_missing_manifest(repo: Path, tmp_path: Path) -> None:
    absent = tmp_path / "absent.yaml"
    argv = ["--repo-root", str(repo), "--manifest", str(absent)]
    assert rs.main(argv) == rs.EXIT_USAGE


def test_real_repository_passes() -> None:
    assert rs.main(["--repo-root", str(REPO_ROOT), "--check"]) == rs.EXIT_PASS
