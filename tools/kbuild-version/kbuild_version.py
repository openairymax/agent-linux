#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
kbuild_version.py — AirymaxOS Kbuild 版本注入一致性检查器

以 tools/kbuild-version/manifest.yaml 为唯一事实源（SSoT），静态校验
「版本四元组 × kernel/Makefile.airymaxos × 顶层 Makefile 的 include ×
filechk_version.h 注入宏 × 契约快照」五者彼此一致，把散落在版本文件、顶层
Makefile 与生成契约中的隐含约束收敛到一处清单，并检出任何一侧的漂移。

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
  docs/docs-linux/70-build-system/01-kbuild-system.md
    §5    filechk 版本注入与 Makefile.airymaxos
    §5.2  Makefile.airymaxos 供应商版本注入（include 指令与注入宏清单）
    OS-STD-049    Makefile.airymaxos 只放版本四元组，特性开关走 Kconfig
    OS-BUILD-009  四元组变更须经发布管理评审并同步 kernel 子仓发行说明
    OS-BUILD-010  version.h 必须经 filechk 生成，禁止裸 echo 覆盖
  docs/docs-linux/160-compatibility/03-upstream-tracking.md §8
    内核发布串 <kernel_version>-airymax.<airy_release>[.<patch>]

Usage:
    python3 tools/kbuild-version/kbuild_version.py [options]

Options:
    --repo-root PATH    仓库根（默认：自动探测）
    --manifest PATH     版本清单（默认：tools/kbuild-version/manifest.yaml）
    --version-h PATH    真实生成的 version.h；给出时追加第 8 项契约比对
    --check             CI 模式：精简输出，仅退出码

Exit codes:
    0 — 全部检查通过
    1 — 一项或多项检查失败
    2 — 用法/环境错误（清单缺失、YAML 非法、内核源码目录缺失等）
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺失时在入口统一报错
    yaml = None

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST_REL = "tools/kbuild-version/manifest.yaml"
DEFAULT_KERNEL_DIR = "kernel"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

# 版本文件存在性门禁的检查名（schema/清单失败时复用同一名字报告阻塞原因）。
GUARD_NAME = "版本文件存在（清单 airymaxos_makefile ↔ 内核树）"

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


# ─── Data model ────────────────────────────────────────────────────────────


class CheckResult:
    """单项检查结果。"""

    def __init__(self, name, passed, details=""):
        self.name = name
        self.passed = passed
        self.details = details


class ManifestError(Exception):
    """清单不可读取或 schema 非法。"""


# ─── Helpers ───────────────────────────────────────────────────────────────


def find_repo_root():
    """从脚本位置自动探测 agent-linux 仓库根。

    脚本位于 <repo_root>/tools/kbuild-version/kbuild_version.py。
    """
    return Path(__file__).resolve().parent.parent.parent


def load_manifest(path):
    """读取并解析清单；缺失或 YAML 非法时抛 ManifestError。"""
    if yaml is None:
        raise ManifestError("PyYAML 不可用：请安装 pyyaml（见 requirements-dev.txt）")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        raise ManifestError(f"清单文件不存在：{path}")
    except yaml.YAMLError as exc:
        raise ManifestError(f"清单 YAML 解析失败：{exc}")
    if not isinstance(data, dict):
        raise ManifestError("清单顶层必须是映射（mapping）")
    return data


def parse_assignments(text):
    """返回 Makefile 顶层赋值行 [(var, value), ...]，跳过注释与空行。"""
    assignments = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ASSIGN_RE.match(line)
        if match:
            assignments.append((match.group(1), match.group(2).strip()))
    return assignments


def expected_assignments(tuple_map):
    """由四元组推导版本文件中每个变量应有的字面值。"""
    return {
        "AIRYMAXOS_LTS": str(tuple_map["lts"]),
        "AIRYMAXOS_MAJOR": str(tuple_map["major"]),
        "AIRYMAXOS_MINOR": str(tuple_map["minor"]),
        "AIRYMAXOS_RELEASE": f'"{tuple_map["release"]}"',
    }


def render_contract(tuple_map):
    """由四元组渲染契约快照（期待被注入的 AIRYMAXOS_* 宏块）。"""
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


def extract_filechk_block(text):
    """截取顶层 Makefile 中 define filechk_version.h ... endef 的块。"""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.strip().startswith(f"define {FILECHK_NAME}")), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() == "endef"), None)
    return None if end is None else "\n".join(lines[start:end + 1])


def _significant_lines(text):
    """返回去除空行/首尾空白后的行列表，用于内容比对。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def _mismatch_detail(expected, actual):
    """生成「期望 vs 实为」的可读差异，用于失败报告。"""
    return (f"期望:\n{expected}\n实为:\n{actual}")


# ─── Checks ────────────────────────────────────────────────────────────────


def _tuple_problems(tuple_map):
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


def check_manifest_schema(manifest):
    """校验清单 schema：必填字段、四元组取值域、路径形态。"""
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

    return CheckResult("清单 schema 合法", not problems, "\n".join(problems))


def check_version_file_present(manifest, repo_root):
    """清单登记的版本文件在内核树中真实存在。"""
    rel = manifest["airymaxos_makefile"]
    ok = (repo_root / rel).is_file()
    return CheckResult(GUARD_NAME, ok, "" if ok else f"版本文件缺失: {rel}")


def check_tuple_matches(assignments, manifest):
    """版本文件中的四元组取值与清单一致。"""
    actual = dict(assignments)
    expected = expected_assignments(manifest["version_tuple"])
    problems = [
        f"{name} 期望 {want}，实为 {actual.get(name, '<缺失>')}"
        for name, want in expected.items()
        if actual.get(name) != want
    ]
    return CheckResult("四元组取值与清单一致", not problems, "\n".join(problems))


def check_only_version_vars(assignments):
    """版本文件只允许出现版本四元组变量（OS-STD-049）。"""
    extra = sorted({name for name, _ in assignments} - set(VERSION_VARS))
    problems = [f"存在四元组以外的变量赋值: {name}" for name in extra]
    return CheckResult("版本文件仅含版本四元组 (OS-STD-049)",
                       not problems, "\n".join(problems))


def check_include_directive(manifest, repo_root):
    """顶层 Makefile 经 include 引入版本文件（OS-BUILD-010）。"""
    rel = manifest["top_makefile"]
    path = repo_root / rel
    if not path.is_file():
        return CheckResult("顶层 Makefile 引入版本文件 (OS-BUILD-010)",
                           False, f"顶层 Makefile 缺失: {rel}")
    ok = INCLUDE_DIRECTIVE in path.read_text(encoding="utf-8")
    return CheckResult("顶层 Makefile 引入版本文件 (OS-BUILD-010)", ok,
                       "" if ok else f"{rel} 缺少指令: {INCLUDE_DIRECTIVE}")


def check_filechk_macros(manifest, repo_root):
    """filechk_version.h 注入全部 AIRYMAXOS_* 宏（OS-BUILD-010）。"""
    path = repo_root / manifest["top_makefile"]
    block = extract_filechk_block(path.read_text(encoding="utf-8"))
    if block is None:
        return CheckResult("filechk 注入全部 AIRYMAXOS_* 宏 (OS-BUILD-010)",
                           False, f"未找到 define {FILECHK_NAME} 块")
    cleaned = block.replace("\\#", "#")
    missing = [name for name in INJECTED_MACROS
               if not re.search(r"#define\s+" + name + r"\b", cleaned)]
    problems = [f"filechk_version.h 未注入宏: {name}" for name in missing]
    return CheckResult("filechk 注入全部 AIRYMAXOS_* 宏 (OS-BUILD-010)",
                       not problems, "\n".join(problems))


def check_contract(manifest, repo_root):
    """契约快照与清单四元组渲染结果一致。"""
    rel = manifest["contract"]
    path = repo_root / rel
    if not path.is_file():
        return CheckResult("契约快照与四元组一致", False, f"契约文件缺失: {rel}")
    actual = _significant_lines(path.read_text(encoding="utf-8"))
    expected = _significant_lines(render_contract(manifest["version_tuple"]))
    ok = actual == expected
    return CheckResult("契约快照与四元组一致", ok,
                       "" if ok else _mismatch_detail("\n".join(expected),
                                                      "\n".join(actual)))


def check_real_version_h(manifest, version_h):
    """真实生成的 version.h 中的 AIRYMAXOS_* 宏块与契约一致（OS-BUILD-010）。"""
    name = "真实 version.h 与契约一致 (OS-BUILD-010)"
    path = Path(version_h)
    if not path.is_file():
        return CheckResult(name, False, f"version.h 不存在: {version_h}")
    actual = _significant_lines(path.read_text(encoding="utf-8"))
    actual = [line for line in actual if line.startswith(AIRYMAXOS_DEFINE_PREFIX)]
    expected = _significant_lines(render_contract(manifest["version_tuple"]))
    ok = actual == expected
    return CheckResult(name, ok,
                       "" if ok else _mismatch_detail("\n".join(expected),
                                                      "\n".join(actual)))


# ─── Report ────────────────────────────────────────────────────────────────


def print_report(results, manifest, check_mode):
    """打印检查报告。"""
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


def build_checks(manifest, repo_root, version_h=None):
    """按清单 schema 与版本文件存在性决定是否继续执行内容类检查。"""
    schema = check_manifest_schema(manifest)
    if not schema.passed:
        return [schema]
    guard = check_version_file_present(manifest, repo_root)
    if not guard.passed:
        return [schema, guard]
    text = (repo_root / manifest["airymaxos_makefile"]).read_text(encoding="utf-8")
    assignments = parse_assignments(text)
    results = [
        schema,
        guard,
        check_tuple_matches(assignments, manifest),
        check_only_version_vars(assignments),
        check_include_directive(manifest, repo_root),
        check_filechk_macros(manifest, repo_root),
        check_contract(manifest, repo_root),
    ]
    if version_h is not None:
        results.append(check_real_version_h(manifest, version_h))
    return results


def main(argv=None):
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
