#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""AirymaxOS Kbuild 版本注入一致性检查器。

以 tools/kbuild-version/manifest.yaml 为唯一事实源（SSoT），静态校验
「版本四元组 × kernel/Makefile.airymaxos × 顶层 Makefile 的 include ×
filechk_version.h 注入宏 × 契约快照」五者彼此一致，把散落在版本文件、
顶层 Makefile 与生成契约中的隐含约束收敛到一处清单，并检出任何一侧
的漂移。

设计原则（与项目铁律一致）：

* 清单是唯一事实源；四元组只在本表登记一次，工具的期望值全部由它推导，
  避免第二事实源。
* 严禁桩实现：所有检查均读取真实文件内容，给出可复现的判定与证据。

检查项：

1. 清单 schema 合法（必填字段、四元组取值域、路径形态）
2. 版本文件存在（清单 airymaxos_makefile ↔ 内核树）
3. 四元组取值与清单一致
4. 版本文件仅含版本四元组（OS-STD-049）
5. 顶层 Makefile 经 include 引入版本文件（OS-BUILD-010）
6. filechk_version.h 注入全部 AIRYMAXOS_* 宏（OS-BUILD-010）
7. 契约快照与四元组一致
8. 真实 version.h 与契约一致（给出 --version-h 时追加）

权威依据：

* docs/docs-linux/70-build-system/01-kbuild-system.md §5
* OS-STD-049 / OS-BUILD-009 / OS-BUILD-010
* docs/docs-linux/160-compatibility/03-upstream-tracking.md §8

Example:
    $ python3 tools/kbuild-version/kbuild_version.py --check
    KBUILD-VERSION: PASS (7/7 checks)

    $ python3 tools/kbuild-version/kbuild_version.py
    ...（逐项报告）...
    RESULT: PASS — 全部 7 项检查通过

Exit codes:
    0 — 全部检查通过
    1 — 一项或多项检查失败
    2 — 用法/环境错误（清单缺失、YAML 非法、内核源码目录缺失等）

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.1.0
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypeAlias

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺失时在入口统一报错
    yaml = None

__all__ = [
    "CheckResult",
    "ManifestError",
    "build_checks",
    "check_contract",
    "check_include",
    "check_macros",
    "check_schema",
    "check_tuple",
    "check_vars",
    "check_version_h",
    "check_vfile",
    "expect_vars",
    "filechk_block",
    "find_repo_root",
    "load_manifest",
    "main",
    "parse_vars",
    "print_report",
    "render_contract",
]

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST_REL = "tools/kbuild-version/manifest.yaml"
DEFAULT_KERNEL_DIR = "kernel"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

# 版本文件存在性门禁的检查名（schema/清单失败时复用同一名字报告阻塞原因）。
GUARD_NAME = "版本文件存在（清单 airymaxos_makefile ↔ 内核树）"

SCHEMA_NAME = "清单 schema 合法"
INCLUDE_NAME = "顶层 Makefile 引入版本文件 (OS-BUILD-010)"
MACRO_NAME = "filechk 注入全部 AIRYMAXOS_* 宏 (OS-BUILD-010)"
CONTRACT_NAME = "契约快照与四元组一致"
VERSION_H_NAME = "真实 version.h 与契约一致 (OS-BUILD-010)"

MANIFEST_KEYS = frozenset(
    {"version", "airymaxos_makefile", "top_makefile", "contract",
     "version_tuple"})

TUPLE_KEYS = ("lts", "major", "minor", "release")

PATH_SUFFIX_KEYS = (("airymaxos_makefile", ".airymaxos"),
                    ("top_makefile", "Makefile"),
                    ("contract", ".txt"))

# OS-STD-049：版本文件只允许这四个变量（顺序即注入顺序）。
VERSION_VARS = ("AIRYMAXOS_LTS", "AIRYMAXOS_MAJOR",
                "AIRYMAXOS_MINOR", "AIRYMAXOS_RELEASE")

# filechk_version.h 必须注入的六类宏（含组合宏与版本码）。
INJECTED_MACROS = ("AIRYMAXOS_LTS", "AIRYMAXOS_MAJOR", "AIRYMAXOS_MINOR",
                   "AIRYMAXOS_VERSION", "AIRYMAXOS_VERSION_CODE",
                   "AIRYMAXOS_RELEASE")

INCLUDE_DIRECTIVE = "include $(srctree)/Makefile.airymaxos"
FILECHK_NAME = "filechk_version.h"

ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:+?]?=\s*(.*)$")
AIRYMAXOS_DEFINE_PREFIX = "#define AIRYMAXOS_"

TupleMap: TypeAlias = Dict[str, Any]
Assigns: TypeAlias = List[Tuple[str, str]]


# ─── Data model ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckResult:
    """单项检查结果。

    Attributes:
        name: 检查项名称，用于报告输出。
        passed: 是否通过。
        details: 失败时的可读证据；通过时为空串。
    """

    name: str
    passed: bool
    details: str = ""


class ManifestError(Exception):
    """清单不可读取或 schema 非法。"""


# ─── Helpers ───────────────────────────────────────────────────────────────


def find_repo_root() -> Path:
    """从脚本位置自动探测 agent-linux 仓库根。

    脚本位于 <repo_root>/tools/kbuild-version/kbuild_version.py。

    Returns:
        仓库根目录的绝对路径。
    """
    return Path(__file__).resolve().parent.parent.parent


def load_manifest(path: str | Path) -> TupleMap:
    """读取并解析清单。

    Args:
        path: 清单文件路径。

    Returns:
        清单映射。

    Raises:
        ManifestError: 文件缺失、不可读、YAML 非法或顶层非映射。
    """
    if yaml is None:
        raise ManifestError(
            "PyYAML 不可用：请安装 pyyaml（见 requirements-dev.txt）")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ManifestError(f"清单文件不存在：{path}") from exc
    except OSError as exc:
        raise ManifestError(f"清单文件不可读：{path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"清单 YAML 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("清单顶层必须是映射（mapping）")
    return data


def parse_vars(text: str) -> Assigns:
    """解析 Makefile 顶层赋值行。

    Args:
        text: Makefile 文本。

    Returns:
        [(变量名, 字面值), ...]，跳过注释与空行，保持出现顺序。
    """
    assigns: Assigns = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ASSIGN_RE.match(line)
        if match:
            assigns.append((match.group(1), match.group(2).strip()))
    return assigns


def expect_vars(tuple_map: TupleMap) -> Dict[str, str]:
    """由四元组推导版本文件中每个变量应有的字面值。

    Args:
        tuple_map: 版本四元组映射。

    Returns:
        变量名到期望字面值的映射（release 带双引号）。
    """
    return {
        "AIRYMAXOS_LTS": str(tuple_map["lts"]),
        "AIRYMAXOS_MAJOR": str(tuple_map["major"]),
        "AIRYMAXOS_MINOR": str(tuple_map["minor"]),
        "AIRYMAXOS_RELEASE": f'"{tuple_map["release"]}"',
    }


def render_contract(tuple_map: TupleMap) -> str:
    """由四元组渲染契约快照。

    Args:
        tuple_map: 版本四元组映射。

    Returns:
        期待被注入的 AIRYMAXOS_* 宏块文本（以换行结尾）。
    """
    major = tuple_map["major"]
    minor = tuple_map["minor"]
    lines = [
        f"#define AIRYMAXOS_LTS {tuple_map['lts']}",
        f"#define AIRYMAXOS_MAJOR {major}",
        f"#define AIRYMAXOS_MINOR {minor}",
        "#define AIRYMAXOS_VERSION(a,b) (((a) << 8) + (b))",
        f"#define AIRYMAXOS_VERSION_CODE {major * 256 + minor}",
        f'#define AIRYMAXOS_RELEASE "{tuple_map["release"]}"',
    ]
    return "\n".join(lines) + "\n"


def filechk_block(text: str) -> Optional[str]:
    """截取顶层 Makefile 中的 filechk 注入块。

    Args:
        text: 顶层 Makefile 文本。

    Returns:
        `define filechk_version.h ... endef` 块；不存在或不闭合时为 None。
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.strip().startswith(f"define {FILECHK_NAME}")), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() == "endef"), None)
    return None if end is None else "\n".join(lines[start:end + 1])


def _norm_lines(text: str) -> List[str]:
    """返回去除空行与首尾空白的行列表，用于内容比对。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def _mismatch(expected: str, actual: str) -> str:
    """生成「期望 vs 实为」的可读差异。"""
    return f"期望:\n{expected}\n实为:\n{actual}"


def _read_text(path: Path) -> Optional[str]:
    """读取 UTF-8 文本；文件缺失或不可读时返回 None。"""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


# ─── Checks ────────────────────────────────────────────────────────────────


def _tuple_problems(tuple_map: Any) -> List[str]:
    """校验四元组字段与取值域（0..255，适配 ((a) << 8) + (b) 打包）。"""
    if not isinstance(tuple_map, dict):
        return ["version_tuple 必须是映射（mapping）"]
    missing = [key for key in TUPLE_KEYS if key not in tuple_map]
    if missing:
        return [f"version_tuple 缺少字段: {', '.join(missing)}"]
    problems = []
    for key in ("lts", "major", "minor"):
        value = tuple_map[key]
        if isinstance(value, bool) or not isinstance(value, int) \
                or not 0 <= value <= 255:
            problems.append(f"version_tuple.{key} 必须是 0..255 的整数")
    if not isinstance(tuple_map["release"], str):
        problems.append("version_tuple.release 必须是字符串")
    return problems


def check_schema(manifest: TupleMap) -> CheckResult:
    """校验清单 schema：必填字段、四元组取值域、路径形态。

    Args:
        manifest: 清单映射。

    Returns:
        检查结果。
    """
    problems = []

    unknown = set(manifest) - MANIFEST_KEYS
    if unknown:
        problems.append(f"顶层未知字段: {', '.join(sorted(unknown))}")

    for key in ("version", "airymaxos_makefile", "top_makefile", "contract"):
        if not manifest.get(key):
            problems.append(f"缺少或为空值的必填字段: {key}")

    problems.extend(_tuple_problems(manifest.get("version_tuple")))

    for key, suffix in PATH_SUFFIX_KEYS:
        value = manifest.get(key)
        if isinstance(value, str) and value and not value.endswith(suffix):
            problems.append(f"{key} 形态非法（应以 {suffix} 结尾）")

    return CheckResult(SCHEMA_NAME, not problems, "\n".join(problems))


def check_vfile(manifest: TupleMap, repo_root: Path) -> CheckResult:
    """确认清单登记的版本文件在内核树中真实存在。

    Args:
        manifest: 清单映射。
        repo_root: 仓库根目录。

    Returns:
        检查结果。
    """
    rel = manifest["airymaxos_makefile"]
    ok = (repo_root / rel).is_file()
    return CheckResult(GUARD_NAME, ok, "" if ok else f"版本文件缺失: {rel}")


def check_tuple(assigns: Assigns, manifest: TupleMap) -> CheckResult:
    """校验版本文件中的四元组取值与清单一致。

    Args:
        assigns: 版本文件的变量赋值列表。
        manifest: 清单映射。

    Returns:
        检查结果。
    """
    actual = dict(assigns)
    expected = expect_vars(manifest["version_tuple"])
    problems = [
        f"{name} 期望 {want}，实为 {actual.get(name, '<缺失>')}"
        for name, want in expected.items()
        if actual.get(name) != want
    ]
    return CheckResult("四元组取值与清单一致", not problems,
                       "\n".join(problems))


def check_vars(assigns: Assigns) -> CheckResult:
    """校验版本文件只出现版本四元组变量（OS-STD-049）。

    Args:
        assigns: 版本文件的变量赋值列表。

    Returns:
        检查结果。
    """
    extra = sorted({name for name, _ in assigns} - set(VERSION_VARS))
    problems = [f"存在四元组以外的变量赋值: {name}" for name in extra]
    return CheckResult("版本文件仅含版本四元组 (OS-STD-049)",
                       not problems, "\n".join(problems))


def check_include(manifest: TupleMap, repo_root: Path) -> CheckResult:
    """校验顶层 Makefile 经 include 引入版本文件（OS-BUILD-010）。

    Args:
        manifest: 清单映射。
        repo_root: 仓库根目录。

    Returns:
        检查结果；顶层 Makefile 缺失时判失败而不是抛异常。
    """
    rel = manifest["top_makefile"]
    text = _read_text(repo_root / rel)
    if text is None:
        return CheckResult(INCLUDE_NAME, False, f"顶层 Makefile 缺失: {rel}")
    ok = INCLUDE_DIRECTIVE in text
    return CheckResult(INCLUDE_NAME, ok,
                       "" if ok else f"{rel} 缺少指令: {INCLUDE_DIRECTIVE}")


def check_macros(manifest: TupleMap, repo_root: Path) -> CheckResult:
    """校验 filechk_version.h 注入全部 AIRYMAXOS_* 宏（OS-BUILD-010）。

    Args:
        manifest: 清单映射。
        repo_root: 仓库根目录。

    Returns:
        检查结果；顶层 Makefile 缺失时判失败而不是抛异常。
    """
    rel = manifest["top_makefile"]
    text = _read_text(repo_root / rel)
    if text is None:
        return CheckResult(MACRO_NAME, False, f"顶层 Makefile 缺失: {rel}")
    block = filechk_block(text)
    if block is None:
        return CheckResult(MACRO_NAME, False,
                           f"未找到 define {FILECHK_NAME} 块")
    cleaned = block.replace("\\#", "#")
    missing = [name for name in INJECTED_MACROS
               if not re.search(r"#define\s+" + name + r"\b", cleaned)]
    problems = [f"filechk_version.h 未注入宏: {name}" for name in missing]
    return CheckResult(MACRO_NAME, not problems, "\n".join(problems))


def check_contract(manifest: TupleMap, repo_root: Path) -> CheckResult:
    """校验契约快照与清单四元组渲染结果一致。

    Args:
        manifest: 清单映射。
        repo_root: 仓库根目录。

    Returns:
        检查结果。
    """
    rel = manifest["contract"]
    text = _read_text(repo_root / rel)
    if text is None:
        return CheckResult(CONTRACT_NAME, False, f"契约文件缺失: {rel}")
    actual = _norm_lines(text)
    expected = _norm_lines(render_contract(manifest["version_tuple"]))
    ok = actual == expected
    return CheckResult(CONTRACT_NAME, ok,
                       "" if ok else _mismatch("\n".join(expected),
                                               "\n".join(actual)))


def check_version_h(manifest: TupleMap, version_h: str | Path) -> CheckResult:
    """校验真实生成的 version.h 中的 AIRYMAXOS_* 宏块与契约一致。

    Args:
        manifest: 清单映射。
        version_h: 真实生成的 version.h 路径。

    Returns:
        检查结果。
    """
    path = Path(version_h)
    text = _read_text(path)
    if text is None:
        return CheckResult(VERSION_H_NAME, False,
                           f"version.h 不存在: {version_h}")
    actual = [line for line in _norm_lines(text)
              if line.startswith(AIRYMAXOS_DEFINE_PREFIX)]
    expected = _norm_lines(render_contract(manifest["version_tuple"]))
    ok = actual == expected
    return CheckResult(VERSION_H_NAME, ok,
                       "" if ok else _mismatch("\n".join(expected),
                                               "\n".join(actual)))


# ─── Report ────────────────────────────────────────────────────────────────


def print_report(results: Sequence[CheckResult], manifest: TupleMap,
                 check_mode: bool) -> None:
    """打印检查报告。

    Args:
        results: 检查结果序列。
        manifest: 清单映射，用于输出清单版本。
        check_mode: 为 True 时只输出单行汇总，供 CI 消费。
    """
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    if check_mode:
        if failed == 0:
            print(f"KBUILD-VERSION: PASS ({passed}/{total} checks)")
        else:
            print(f"KBUILD-VERSION: FAIL ({passed}/{total} checks, "
                  f"{failed} failed)")
            for result in results:
                if not result.passed:
                    print(f"  FAIL: {result.name}")
                    for line in result.details.splitlines():
                        print(f"    {line}")
        return

    print("=" * 72)
    print("agent-linux AirymaxOS Kbuild 版本注入一致性报告")
    print(f"清单版本: {manifest.get('version', '?')}")
    print("=" * 72)
    print()
    for result in results:
        marker = "[+]" if result.passed else "[-]"
        status = "PASS" if result.passed else "FAIL"
        print(f"{marker} {status}: {result.name}")
        if result.details:
            for line in result.details.splitlines():
                print(f"      {line}")
    print()
    print("-" * 72)
    if failed == 0:
        print(f"RESULT: PASS — 全部 {total} 项检查通过")
    else:
        print(f"RESULT: FAIL — {failed} 项检查未通过")
    print("-" * 72)


# ─── Main ──────────────────────────────────────────────────────────────────


def build_checks(manifest: TupleMap, repo_root: Path,
                 version_h: Optional[str] = None) -> List[CheckResult]:
    """按清单 schema 与版本文件存在性决定是否继续执行内容类检查。

    Args:
        manifest: 清单映射。
        repo_root: 仓库根目录。
        version_h: 真实 version.h 路径；给出时追加第 8 项契约比对。

    Returns:
        检查结果列表；schema 或版本文件门禁失败时提前短路。
    """
    schema = check_schema(manifest)
    if not schema.passed:
        return [schema]
    guard = check_vfile(manifest, repo_root)
    if not guard.passed:
        return [schema, guard]
    text = _read_text(repo_root / manifest["airymaxos_makefile"]) or ""
    assigns = parse_vars(text)
    results = [
        schema,
        guard,
        check_tuple(assigns, manifest),
        check_vars(assigns),
        check_include(manifest, repo_root),
        check_macros(manifest, repo_root),
        check_contract(manifest, repo_root),
    ]
    if version_h is not None:
        results.append(check_version_h(manifest, version_h))
    return results


def main(argv: Optional[Sequence[str]] = None) -> None:
    """命令行入口。

    Args:
        argv: 参数列表；None 时取 sys.argv[1:]。
    """
    parser = argparse.ArgumentParser(
        description="AirymaxOS Kbuild 版本注入一致性检查器 "
                    "(OS-STD-049, OS-BUILD-010)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", default=None,
                        help="agent-linux 仓库根（默认：自动探测）")
    parser.add_argument("--manifest", default=None,
                        help="版本清单（默认：tools/kbuild-version/manifest.yaml）")
    parser.add_argument("--version-h", default=None,
                        help="真实生成的 version.h；给出时追加第 8 项契约比对")
    parser.add_argument("--check", action="store_true",
                        help="CI 模式：精简输出，仅退出码")
    args = parser.parse_args(argv)

    if yaml is None:
        print("ERROR: PyYAML 不可用，请安装 pyyaml", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    repo_root = Path(args.repo_root) if args.repo_root else find_repo_root()
    if not repo_root.is_dir():
        print(f"ERROR: 仓库根不存在: {repo_root}", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    kernel_dir = repo_root / DEFAULT_KERNEL_DIR
    if not kernel_dir.is_dir():
        print(f"ERROR: 内核源码目录不存在: {kernel_dir}", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    manifest_path = (Path(args.manifest) if args.manifest
                     else repo_root / DEFAULT_MANIFEST_REL)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    results = build_checks(manifest, repo_root, args.version_h)
    print_report(results, manifest, args.check)

    failed = sum(1 for result in results if not result.passed)
    sys.exit(EXIT_FAIL if failed else EXIT_PASS)


if __name__ == "__main__":
    main()
