#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
repo_scaffold.py — AirymaxOS 八子仓脚手架一致性检查器

以 tools/repo-scaffold/manifest.yaml 为唯一事实源（SSoT），校验
.gitmodules 登记的八子仓是否具备公共骨架、许可证标识、构建入口，
以及构建入口是否真实引用存在且非空的源文件（决策 F1：最小可编译
骨架，非桩）。

设计原则（与项目铁律一致）：
  * 清单是唯一事实源；骨架、许可证与构建入口的任何漂移由本工具检出。
  * kernel 为 Linux 6.6 完整 fork（Model A），采用上游原生布局，
    以 entry_probe 证明源码树非空壳，不另建 README.md/LICENSE 副本。
  * 严禁桩实现：所有检查读取真实文件，失败给出具体路径证据。

检查项（--check）：
  1. 清单 schema 合法（必填字段、类型、取值、八仓唯一）
  2. .gitmodules 登记与八子仓清单一致（excluded 之外无遗漏/多余）
  3. 八子仓均已检出为目录
  4. 公共骨架文件存在且非空
  5. 许可证标识出现在许可证文件首部
  6. 构建入口存在且非空
  7. 构建入口引用真实源文件（CMake add_subdirectory/add_executable/
     add_library；Kbuild obj-m/*-objs；Linux 原生 entry_probe）

Usage:
    python3 tools/repo-scaffold/repo_scaffold.py [options]

Options:
    --repo-root PATH    仓库根（默认：自动探测）
    --manifest PATH     脚手架清单（默认：tools/repo-scaffold/manifest.yaml）
    --check             CI 模式：精简输出，仅退出码

Exit codes:
    0 — 全部检查通过
    1 — 检查失败
    2 — 用法或环境错误（清单缺失、YAML 非法等）
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

# tools/lib 是契约检查工具的共享层：结果类型与报告格式只能有一份。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from check_report import CheckResult, lead, print_report, yaml
from manifest_io import open_gate

__all__ = ["RepoSpec", "ScaffoldError", "main"]

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST_REL = "tools/repo-scaffold/manifest.yaml"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

EXPECTED_REPOS = 8

MANIFEST_KEYS = frozenset({
    "version", "gitmodules", "excluded", "skeletons", "licenses", "subrepos",
})
REPO_KEYS = frozenset({"path", "skeleton", "license", "build_entry"})
PROBE_KEY = "entry_probe"
LICENSE_KEYS = frozenset({"file", "marker"})

KINDS_BY_ENTRY = {
    "CMakeLists.txt": "cmake",
    "Kbuild": "kbuild",
    "Makefile": "native",
}

NAME_SCHEMA = "清单 schema"
NAME_REGISTRY = "子仓登记"
NAME_CHECKOUT = "子仓检出"
NAME_SKELETON = "公共骨架"
NAME_LICENSE = "许可证标识"
NAME_BUILD = "构建入口"
NAME_ENTRY = "可编译入口"
TITLE = "agent-linux AirymaxOS 八子仓脚手架一致性报告"

_PATH_RE = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._/-]*$")
_SUBDIR_RE = re.compile(r"add_subdirectory\s*\(\s*([\w./-]+)")
_TARGET_RE = re.compile(
    r"add_(?:executable|library)\s*\(\s*([\w.-]+)(.*?)\)", re.S)
_OBJ_RE = re.compile(r"^\s*obj-[my]\s*[:+]?=\s*(.+)$", re.M)
_OBJS_RE = re.compile(r"^\s*([\w-]+)-objs\s*[:+]?=\s*(.+)$", re.M)
_CMAKE_SKIP = frozenset({
    "STATIC", "SHARED", "MODULE", "OBJECT", "INTERFACE", "ALIAS",
    "IMPORTED", "EXCLUDE_FROM_ALL",
})
_SRC_SUFFIXES = (".c", ".cc", ".cpp", ".S", ".rs")
_SRC_CANDIDATES = (".c", ".S")


# ─── Data model ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RepoSpec:
    """八子仓之一的骨架契约。

    Attributes:
        path: 子仓目录（相对仓库根）。
        skeleton: 必须存在的公共骨架文件。
        license_file: 许可证文件（相对子仓根）。
        marker: 许可证文件必须包含的标识。
        build_entry: 构建入口文件（决定解析方式）。
        probe: Linux 原生入口探针（相对子仓根）。
    """

    path: str
    skeleton: tuple[str, ...]
    license_file: str
    marker: str
    build_entry: str
    probe: str = ""


class ScaffoldError(Exception):
    """清单、.gitmodules 或骨架文件不可读取、不可解析。"""


# ─── Loading ───────────────────────────────────────────────────────────────


def load_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 映射；缺失或解析失败时抛 ScaffoldError。"""
    if yaml is None:
        raise ScaffoldError(
            "PyYAML 不可用：请安装 pyyaml（见 requirements-dev.txt）")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError:
        raise ScaffoldError(f"文件不存在：{path}") from None
    except yaml.YAMLError as exc:
        raise ScaffoldError(f"YAML 解析失败（{path}）：{exc}") from exc
    if not isinstance(data, dict):
        raise ScaffoldError(f"顶层必须是映射（mapping）：{path}")
    return data


def _read_text(path: Path) -> str:
    """读取 UTF-8 文本；解码失败抛 ScaffoldError。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ScaffoldError(f"不可读取：{path}（{exc}）") from exc


def _strip_comments(text: str) -> str:
    """移除行尾 # 注释，避免注释中的示例被解析。"""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _gitmodules(path: Path) -> set[str]:
    """解析 .gitmodules 的 path 集合。"""
    paths: set[str] = set()
    for line in _read_text(path).splitlines():
        match = re.match(r"^\s*path\s*=\s*(\S+)\s*$", line)
        if match:
            paths.add(match.group(1))
    if not paths:
        raise ScaffoldError(f".gitmodules 未登记任何 path：{path}")
    return paths


# ─── Schema ────────────────────────────────────────────────────────────────


def _result(name: str, problems: list[str]) -> CheckResult:
    return CheckResult(name, not problems, "; ".join(problems))


def _need(mapping: Mapping[str, Any], keys: frozenset[str],
          scope: str) -> list[str]:
    """报告缺失的必填字段。"""
    return [f"{scope}缺少字段: {key}" for key in sorted(keys - set(mapping))]


def _is_str_list(value: Any) -> bool:
    return (isinstance(value, list)
            and all(isinstance(item, str) and item for item in value))


def _rel_ok(value: Any) -> bool:
    """相对路径合法：无绝对路径、无 ..、无空段。"""
    if not isinstance(value, str) or not _PATH_RE.match(value):
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def _skel_entry(name: str, files: Any) -> list[str]:
    """单个骨架项的校验：文件列表非空，且每项都是合法相对路径。"""
    if not _is_str_list(files):
        return [f"skeletons.{name} 必须是非空字符串列表"]
    return [f"skeletons.{name} 路径非法: {item}"
            for item in files if not _rel_ok(item)]


def _schema_skels(value: Any) -> list[str]:
    if not isinstance(value, dict) or not value:
        return ["skeletons 必须是非空映射"]
    problems: list[str] = []
    for name, files in value.items():
        problems.extend(_skel_entry(name, files))
    return problems


def _lic_entry(name: str, entry: Any) -> list[str]:
    if not isinstance(entry, dict):
        return [f"licenses.{name} 必须是映射"]
    problems = _need(entry, LICENSE_KEYS, f"licenses.{name}.")
    if not _rel_ok(entry.get("file")):
        problems.append(f"licenses.{name}.file 路径非法")
    marker = entry.get("marker")
    if not isinstance(marker, str) or not marker:
        problems.append(f"licenses.{name}.marker 必须是非空字符串")
    return problems


def _schema_lics(value: Any) -> list[str]:
    if not isinstance(value, dict) or not value:
        return ["licenses 必须是非空映射"]
    problems: list[str] = []
    for name, entry in value.items():
        problems.extend(_lic_entry(name, entry))
    return problems


def _repo_path(entry: Mapping[str, Any], problems: list[str]) -> str:
    """path 必须是相对路径；非法时仍给出可读标签，便于定位条目。"""
    path = entry.get("path")
    if not _rel_ok(path):
        problems.append(f"subrepos[].path 非法: {path!r}")
    return path if isinstance(path, str) else "?"


def _repo_refs(entry: Mapping[str, Any], label: str, names: set[str],
               lics: Any, problems: list[str]) -> None:
    """skeleton 与 license 必须引用清单里已经定义的条目。"""
    if entry.get("skeleton") not in names:
        problems.append(
            f"subrepos[{label}].skeleton 未定义: "
            f"{entry.get('skeleton')!r}")
    known = lics if isinstance(lics, dict) else {}
    if entry.get("license") not in known:
        problems.append(
            f"subrepos[{label}].license 未定义: "
            f"{entry.get('license')!r}")


def _repo_probe(entry: Mapping[str, Any], label: str,
                problems: list[str]) -> None:
    """entry_probe 只对 native 入口有意义；其余入口出现即为误配。"""
    entry_file = entry.get("build_entry")
    if entry_file not in KINDS_BY_ENTRY:
        problems.append(
            f"subrepos[{label}].build_entry 必须是 "
            f"{sorted(KINDS_BY_ENTRY)} 之一")
    probe = entry.get(PROBE_KEY)
    if KINDS_BY_ENTRY.get(entry_file) == "native":
        if not _rel_ok(probe):
            problems.append(f"subrepos[{label}].entry_probe 必须合法且非空")
    elif probe is not None:
        problems.append(f"subrepos[{label}].entry_probe 仅 Makefile 入口可用")


def _schema_repo(entry: Any, names: set[str],
                 lics: Any) -> list[str]:
    if not isinstance(entry, dict):
        return ["subrepos 条目必须是映射"]
    problems = _need(entry, REPO_KEYS, "subrepos[].")
    unknown = set(entry) - REPO_KEYS - {PROBE_KEY}
    problems += [f"subrepos[] 未登记字段: {key}" for key in sorted(unknown)]
    label = _repo_path(entry, problems)
    _repo_refs(entry, label, names, lics, problems)
    _repo_probe(entry, label, problems)
    return problems


def _scalar_probs(man: Mapping[str, Any], key: str) -> list[str]:
    """可选标量字段出现时须为非空字符串。"""
    if key in man and (not isinstance(man[key], str) or not man[key]):
        return [f"{key} 必须是非空字符串"]
    return []


def _head_scalars(man: Mapping[str, Any]) -> list[str]:
    """version 与 gitmodules 出现即须为非空字符串，后者还须是合法路径。"""
    problems = _scalar_probs(man, "version") + _scalar_probs(man, "gitmodules")
    if "gitmodules" in man and not _rel_ok(man["gitmodules"]):
        problems.append("gitmodules 路径非法")
    return problems


def _head_extras(man: Mapping[str, Any]) -> list[str]:
    """excluded 是可选的排除清单，出现即须为非空字符串列表。"""
    if "excluded" in man and not _is_str_list(man["excluded"]):
        return ["excluded 必须是字符串列表"]
    return []


def _schema_head(man: Mapping[str, Any]) -> list[str]:
    """顶层字段：必填项、未登记项、标量类型与路径形态。"""
    problems = _need(man, MANIFEST_KEYS, "清单")
    unknown = set(man) - MANIFEST_KEYS
    problems += [f"清单未登记字段: {key}" for key in sorted(unknown)]
    problems += _head_scalars(man)
    problems += _head_extras(man)
    return problems


def _schema_maps(man: Mapping[str, Any]) -> list[str]:
    """两张映射表：skeletons 被逐仓引用，licenses 逐项校验。"""
    problems: list[str] = []
    if "skeletons" in man:
        problems.extend(_schema_skels(man["skeletons"]))
    if "licenses" in man:
        problems.extend(_schema_lics(man["licenses"]))
    return problems


def _entry_probs(entry: Any, names: set[str], licenses: Any,
                 seen: set[str]) -> list[str]:
    """单个仓条目：契约校验 + path 唯一性（首次出现才登记）。"""
    problems = _schema_repo(entry, names, licenses)
    path = entry.get("path") if isinstance(entry, dict) else None
    if not isinstance(path, str):
        return problems
    if path in seen:
        problems.append(f"subrepos[].path 重复: {path}")
    seen.add(path)
    return problems


def _schema_list(man: Mapping[str, Any], repos: list) -> list[str]:
    """八仓清单：数量、逐仓契约与 path 唯一性。"""
    problems: list[str] = []
    if len(repos) != EXPECTED_REPOS:
        problems.append(
            f"subrepos 必须恰好 {EXPECTED_REPOS} 项，实际 {len(repos)}")
    names = (set(man["skeletons"])
             if isinstance(man.get("skeletons"), dict) else set())
    seen: set[str] = set()
    for entry in repos:
        problems.extend(_entry_probs(entry, names, man.get("licenses"), seen))
    return problems


def check_schema(man: Mapping[str, Any]) -> CheckResult:
    """校验清单 schema；失败时其余检查短路。"""
    problems = _schema_head(man) + _schema_maps(man)
    repos = man.get("subrepos")
    if not isinstance(repos, list) or not repos:
        problems.append("subrepos 必须是非空列表")
        return _result(NAME_SCHEMA, problems)
    problems += _schema_list(man, repos)
    return _result(NAME_SCHEMA, problems)


def _repo_specs(man: Mapping[str, Any]) -> list[RepoSpec]:
    """把清单展开为逐仓骨架契约。"""
    specs: list[RepoSpec] = []
    for entry in man["subrepos"]:
        lic = man["licenses"][entry["license"]]
        specs.append(RepoSpec(
            path=entry["path"],
            skeleton=tuple(man["skeletons"][entry["skeleton"]]),
            license_file=lic["file"],
            marker=lic["marker"],
            build_entry=entry["build_entry"],
            probe=entry.get(PROBE_KEY, ""),
        ))
    return specs


# ─── Checks ────────────────────────────────────────────────────────────────


def _reg_problems(modules: set[str], expected: set[str],
                  excluded: set[str]) -> list[str]:
    """四类不一致各出一条：缺登记、未纳入、排除未登记、排除与八仓冲突。"""
    problems: list[str] = []
    missing = expected - modules
    if missing:
        problems.append(f".gitmodules 未登记: {', '.join(sorted(missing))}")
    extra = modules - expected - excluded
    if extra:
        problems.append(f"未纳入清单的子模块: {', '.join(sorted(extra))}")
    if excluded - modules:
        problems.append(
            f"excluded 未在 .gitmodules 登记: "
            f"{', '.join(sorted(excluded - modules))}")
    overlap = expected & excluded
    if overlap:
        problems.append(
            f"excluded 与八子仓冲突: {', '.join(sorted(overlap))}")
    return problems


def check_registry(man: Mapping[str, Any], root: Path) -> CheckResult:
    """校验 .gitmodules 与八子仓清单一致。"""
    try:
        modules = _gitmodules(root / man["gitmodules"])
    except ScaffoldError as exc:
        return _result(NAME_REGISTRY, [str(exc)])
    expected = {spec.path for spec in _repo_specs(man)}
    return _result(NAME_REGISTRY, _reg_problems(
        modules, expected, set(man["excluded"])))


def check_checkout(man: Mapping[str, Any], root: Path) -> CheckResult:
    """校验八子仓均已检出为目录。"""
    problems = [f"{spec.path}: 未检出或不是目录"
                for spec in _repo_specs(man)
                if not (root / spec.path).is_dir()]
    return _result(NAME_CHECKOUT, problems)


def check_skeleton(man: Mapping[str, Any], root: Path) -> CheckResult:
    """校验公共骨架文件存在且非空。"""
    problems: list[str] = []
    for spec in _repo_specs(man):
        base = root / spec.path
        for rel in spec.skeleton:
            path = base / rel
            if not path.is_file():
                problems.append(f"{spec.path}: 缺少 {rel}")
            elif path.stat().st_size == 0:
                problems.append(f"{spec.path}: 空文件 {rel}")
    return _result(NAME_SKELETON, problems)


def check_license(man: Mapping[str, Any], root: Path) -> CheckResult:
    """校验许可证文件存在且含规定标识。"""
    problems: list[str] = []
    for spec in _repo_specs(man):
        path = root / spec.path / spec.license_file
        if not path.is_file():
            problems.append(f"{spec.path}: 缺少 {spec.license_file}")
            continue
        try:
            text = _read_text(path)
        except ScaffoldError as exc:
            problems.append(f"{spec.path}: {exc}")
            continue
        if spec.marker not in text:
            problems.append(
                f"{spec.path}: {spec.license_file} 缺少标识 {spec.marker!r}")
    return _result(NAME_LICENSE, problems)


def check_build(man: Mapping[str, Any], root: Path) -> CheckResult:
    """校验构建入口存在且非空。"""
    problems: list[str] = []
    for spec in _repo_specs(man):
        path = root / spec.path / spec.build_entry
        if not path.is_file():
            problems.append(f"{spec.path}: 缺少 {spec.build_entry}")
        elif path.stat().st_size == 0:
            problems.append(f"{spec.path}: 空入口 {spec.build_entry}")
    return _result(NAME_BUILD, problems)


def _object_src(label: str, base: Path, obj: str,
                sources: list[Path]) -> list[str]:
    """把 Kbuild 目标文件解析为源文件并校验。"""
    stem = obj[:-2] if obj.endswith(".o") else obj
    for suffix in _SRC_CANDIDATES:
        path = base / f"{stem}{suffix}"
        if path.is_file():
            if path.stat().st_size == 0:
                return [f"{label}: 空源文件 {path.relative_to(base)}"]
            sources.append(path)
            return []
    return [f"{label}: 源文件不存在 {stem}.c"]


def _cmake_skip(name: str) -> bool:
    """生成产物、带变量引用或越界路径的 token 不参与校验。"""
    if not name.endswith(_SRC_SUFFIXES) or name in _CMAKE_SKIP:
        return True
    return "$" in name or not _rel_ok(name)


def _cmake_token(label: str, base: Path,
                 token: str) -> tuple[list[str], Path | None]:
    """单个 CMake 源文件 token：须真实存在且非空。"""
    name = token.strip("\"'")
    if _cmake_skip(name):
        return [], None
    source = base / name
    if not source.is_file():
        return [f"{label}: 源文件不存在 {name}"], None
    if source.stat().st_size == 0:
        return [f"{label}: 空源文件 {name}"], None
    return [], source


def _cmake_file(label: str, base: Path,
                path: Path) -> tuple[list[str], list[Path]]:
    """解析单个 CMakeLists.txt 的目标源文件。"""
    text = _strip_comments(_read_text(path))
    problems: list[str] = []
    sources: list[Path] = []
    for _, body in _TARGET_RE.findall(text):
        for token in body.split():
            found, source = _cmake_token(label, base, token)
            problems += found
            if source is not None:
                sources.append(source)
    return problems, sources


def _cmake_srcs(spec: RepoSpec, base: Path,
                entry: Path) -> list[str]:
    """校验 CMake 入口及其子目录引用的源文件。"""
    problems, sources = _cmake_file(spec.path, base, entry)
    text = _strip_comments(_read_text(entry))
    for name in _SUBDIR_RE.findall(text):
        sub = base / name
        sub_entry = sub / "CMakeLists.txt"
        if not sub.is_dir():
            problems.append(f"{spec.path}: add_subdirectory 目录不存在 {name}")
            continue
        if not sub_entry.is_file():
            problems.append(f"{spec.path}: 子目录缺少 CMakeLists.txt {name}")
            continue
        sub_problems, sub_sources = _cmake_file(spec.path, sub, sub_entry)
        problems += sub_problems
        sources += sub_sources
    if not sources:
        problems.append(f"{spec.path}: 构建入口未引用任何源文件")
    return problems


def _add_objs(label: str, base: Path, text: str,
              sources: list[Path]) -> list[str]:
    """`<mod>-objs := a.o b.o` 聚合行里的对象逐个校验。"""
    problems: list[str] = []
    for _, value in _OBJS_RE.findall(text):
        for obj in value.split():
            problems += _object_src(label, base, obj, sources)
    return problems


def _plain_objs(label: str, base: Path, text: str, composed: set[str],
                sources: list[Path]) -> list[str]:
    """普通 `obj-y += x.o`：聚合名不当独立模块，其余逐个校验。"""
    problems: list[str] = []
    for value in _OBJ_RE.findall(text):
        for obj in value.split():
            if not obj.endswith(".o") or obj[:-2] in composed:
                continue
            problems += _object_src(label, base, obj, sources)
    return problems


def _kbuild_file(label: str, base: Path,
                 path: Path) -> tuple[list[str], list[Path]]:
    """解析单个 Kbuild/Makefile 的模块对象与源文件。"""
    text = _strip_comments(_read_text(path))
    composed = {stem for stem, _ in _OBJS_RE.findall(text)}
    sources: list[Path] = []
    problems = _add_objs(label, base, text, sources)
    problems += _plain_objs(label, base, text, composed, sources)
    return problems, sources


def _sub_entry(sub: Path) -> Path | None:
    """子目录的 Kbuild/Makefile 入口；两者都没有返回 None。"""
    for name in ("Kbuild", "Makefile"):
        candidate = sub / name
        if candidate.is_file():
            return candidate
    return None


def _sub_srcs(spec: RepoSpec, base: Path,
              obj: str) -> tuple[list[str], list[str]]:
    """单个子目录引用：找到真实入口再递归收集源文件。"""
    sub = base / obj.rstrip("/")
    sub_entry = _sub_entry(sub)
    if sub_entry is None:
        return [f"{spec.path}: 模块目录缺少 Kbuild {obj}"], []
    return _kbuild_file(spec.path, sub, sub_entry)


def _kbuild_srcs(spec: RepoSpec, base: Path,
                 entry: Path) -> list[str]:
    """校验 Kbuild 入口及其子目录引用的源文件。"""
    problems, sources = _kbuild_file(spec.path, base, entry)
    text = _strip_comments(_read_text(entry))
    for value in _OBJ_RE.findall(text):
        for obj in value.split():
            if obj.endswith("/"):
                sub_probs, sub_sources = _sub_srcs(spec, base, obj)
                problems += sub_probs
                sources += sub_sources
    if not sources:
        problems.append(f"{spec.path}: 构建入口未引用任何源文件")
    return problems


def _native_entry(spec: RepoSpec, base: Path) -> list[str]:
    """校验 Linux 原生入口探针（证明源码树非空壳）。"""
    path = base / spec.probe
    if not path.is_file():
        return [f"{spec.path}: 入口探针不存在 {spec.probe}"]
    if path.stat().st_size == 0:
        return [f"{spec.path}: 入口探针为空 {spec.probe}"]
    return []


def check_entry(man: Mapping[str, Any], root: Path) -> CheckResult:
    """校验构建入口真实引用存在且非空的源文件。"""
    problems: list[str] = []
    for spec in _repo_specs(man):
        base = root / spec.path
        entry = base / spec.build_entry
        if not entry.is_file():
            continue
        kind = KINDS_BY_ENTRY[spec.build_entry]
        if kind == "native":
            problems += _native_entry(spec, base)
        elif kind == "cmake":
            problems += _cmake_srcs(spec, base, entry)
        else:
            problems += _kbuild_srcs(spec, base, entry)
    return _result(NAME_ENTRY, problems)


def build_checks(man: Mapping[str, Any],
                 root: Path) -> list[CheckResult]:
    """按序构建全部检查；schema 失败时短路。"""
    return lead(check_schema(man), _checks(man, root))


def _checks(man: Mapping[str, Any],
            root: Path) -> Iterator[CheckResult]:
    """生成 schema 之后的检查结果，由 lead 在 schema 通过后消费。"""
    yield check_registry(man, root)
    yield check_checkout(man, root)
    yield check_skeleton(man, root)
    yield check_license(man, root)
    yield check_build(man, root)
    yield check_entry(man, root)


# ─── Main ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """解析参数并执行检查。"""
    parser = argparse.ArgumentParser(
        description="AirymaxOS 八子仓脚手架一致性检查器（决策 F1）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", default=None,
                        help="agent-linux 仓库根（默认：自动探测）")
    parser.add_argument("--manifest", default=None,
                        help="脚手架清单（默认：tools/repo-scaffold/"
                             "manifest.yaml）")
    parser.add_argument("--check", action="store_true",
                        help="CI 模式：精简输出")
    gate = open_gate(parser, argv, DEFAULT_MANIFEST_REL, load_yaml,
                     ScaffoldError)
    if gate is None:
        return EXIT_USAGE

    man = gate.manifest
    results = build_checks(man, gate.root)
    print_report(results, "REPO-SCAFFOLD", gate.args.check, TITLE,
                 str(man.get("version", "?")))
    return EXIT_FAIL if any(not item.passed for item in results) else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
