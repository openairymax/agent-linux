#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
btf_integration.py — AirymaxOS BTF / eBPF 基线一致性检查器

以 tools/btf-integration/manifest.yaml 为唯一事实源（SSoT），静态校验
「受检架构 × eBPF 基线 × BTF 基线 × tracing 链路 × CI 供给」五者彼此一致，
把散落在 kernel/arch/*/configs/airy_defconfig 与 .github/workflows/ci-kernel.yml
中的隐含约束收敛到一处清单，并由本工具检出任何一侧的漂移。

设计原则（与项目铁律一致）：
  * 清单是唯一事实源；受检架构不在本表重复登记，统一复用
    tools/defconfig-matrix/manifest.yaml 中 build=true 的条目，避免第二事实源。
    本工具只做「清单 ↔ 内核片段/工作流」的交叉比对，不重复实现构建逻辑。
  * 严禁桩实现：所有检查均读取真实文件内容，给出可复现的判定与证据。

检查项：
  1. 清单 schema 合法（必填字段、取值与路径形态）
  2. 受检片段存在（清单 build=true 的 configs ↔ 内核树）
  3. eBPF 基线齐备（OS-KER-106）
  4. BTF 基线齐备（OS-OBS-012）
  5. tracing 链路前置齐备（OS-KER-106）
  6. CI 提供 pahole 且断言 BTF 产物（OS-OBS-012）

权威依据：
  docs/docs-linux/90-observability/02-ebpf-probes.md
    OS-KER-106  kernel 的 defconfig 必须开启 CONFIG_BPF、CONFIG_BPF_SYSCALL、
                CONFIG_BPF_JIT、CONFIG_BPF_EVENTS、CONFIG_DEBUG_INFO_BTF
    OS-OBS-012  kernel 必须开启 CONFIG_DEBUG_INFO_BTF，并在构建产物中提供
                vmlinux 的 BTF；BPF 程序须基于 BTF 重定位（CO-RE）
  kernel/lib/Kconfig.debug
    DEBUG_INFO_BTF `depends on !DEBUG_INFO_SPLIT && !DEBUG_INFO_REDUCED`；
    DEBUG_INFO 为无 prompt 的隐藏 bool，只由 DWARF choice 项 select
  kernel/kernel/trace/Kconfig
    BPF_EVENTS 位于 `if FTRACE` 区块内，`depends on BPF_SYSCALL` 与
    `depends on (KPROBE_EVENTS || UPROBE_EVENTS) && PERF_EVENTS`
  kernel/scripts/link-vmlinux.sh
    Linux 6.6 的 BTF 由 gen_btf() 以 .BTF 段内嵌进 vmlinux，不产出独立的
    vmlinux.btf 文件；故 CI 断言的是 vmlinux 中的 .BTF 段

Example:
    >>> from btf_integration import build_checks, load_manifest
    >>> manifest = load_manifest("tools/btf-integration/manifest.yaml")
    >>> results = build_checks(manifest, repo_root, repo_root / "kernel")
    >>> all(result.passed for result in results)
    True

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
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺失时在入口统一报错
    yaml = None

__all__ = [
    "CheckResult",
    "ManifestError",
    "build_checks",
    "check_bpf",
    "check_btf",
    "check_ci_supply",
    "check_frags",
    "check_schema",
    "check_trace",
    "find_repo_root",
    "load_arches",
    "load_manifest",
    "main",
    "print_report",
    "read_configs",
]

DEFAULT_MANIFEST_REL = "tools/btf-integration/manifest.yaml"
DEFAULT_KERNEL_DIR = "kernel"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

GUARD_NAME = "受检片段存在（清单 build=true ↔ 内核树）"

MANIFEST_KEYS = frozenset(
    {"version", "defconfig_matrix_manifest", "bpf_configs", "btf_configs",
     "btf_mutex_unset", "debug_info_choice", "tracing_chain",
     "pahole_package", "btf_artifacts", "ci_workflows"})

CONFIG_LIST_KEYS = ("bpf_configs", "btf_configs", "tracing_chain",
                    "debug_info_choice")
SYMBOL_LIST_KEYS = ("btf_mutex_unset",)
STR_LIST_KEYS = ("btf_artifacts",)
WORKFLOW_LIST_KEYS = ("ci_workflows",)

MUTEX_FMT = "# {key} is not set"

CONFIG_VAL_RE = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$")
CONFIG_SET_RE = re.compile(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$")
CONFIG_SYM_RE = re.compile(r"^CONFIG_[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class CheckResult:
    """单项检查结果。

    Attributes:
        name: 检查项名称，同时作为报告中的稳定标识。
        passed: 该项是否通过。
        details: 失败证据，多行文本；通过时为空串。
    """

    name: str
    passed: bool
    details: str = ""


class ManifestError(Exception):
    """清单不可读取或 schema 非法。"""


Manifest = dict[str, Any]
ArchEntry = tuple[str, str]
Results = list[CheckResult]


def find_repo_root() -> Path:
    """从脚本位置自动探测 agent-linux 仓库根。

    脚本位于 <repo_root>/tools/btf-integration/btf_integration.py。

    Returns:
        仓库根的绝对路径。
    """
    return Path(__file__).resolve().parent.parent.parent


def load_manifest(path: Path) -> Manifest:
    """读取并解析 YAML 清单。

    Args:
        path: 清单文件路径。

    Returns:
        清单顶层映射。

    Raises:
        ManifestError: PyYAML 不可用、文件缺失、YAML 非法或顶层非映射。
    """
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


def read_configs(path: Path) -> list[str]:
    """读取文件中的有效配置行。

    Args:
        path: 内核 defconfig 片段路径。

    Returns:
        形如 ``CONFIG_X=y`` 或 ``# CONFIG_X is not set`` 的行，保持原序。
    """
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if CONFIG_VAL_RE.match(stripped) or CONFIG_SET_RE.match(stripped):
            lines.append(stripped)
    return lines


def load_arches(manifest: Manifest, repo_root: Path) -> list[ArchEntry]:
    """读取 defconfig 矩阵清单中的受检架构。

    Args:
        manifest: BTF 基线清单。
        repo_root: agent-linux 仓库根。

    Returns:
        ``(arch, configs_rel)`` 列表，仅含矩阵中 build=true 的条目。

    Raises:
        ManifestError: 矩阵路径非法、文件缺失、结构非法或无受检条目。
    """
    rel = manifest.get("defconfig_matrix_manifest")
    if not isinstance(rel, str) or not rel:
        raise ManifestError("defconfig_matrix_manifest 必须是相对路径字符串")
    path = repo_root / rel
    if not path.is_file():
        raise ManifestError(f"defconfig 矩阵清单不存在：{rel}")
    data = load_manifest(path)
    arches = data.get("architectures")
    if not isinstance(arches, list):
        raise ManifestError(f"{rel}: architectures 必须是列表")

    entries: list[ArchEntry] = []
    for idx, entry in enumerate(arches):
        if not isinstance(entry, dict) or entry.get("build") is not True:
            continue
        arch = entry.get("arch")
        configs = entry.get("configs")
        if not isinstance(arch, str) or not arch:
            raise ManifestError(f"{rel}: architectures[{idx}].arch 非法")
        if not isinstance(configs, str) or not configs:
            raise ManifestError(f"{rel}: architectures[{idx}].configs 非法")
        entries.append((arch, configs))
    if not entries:
        raise ManifestError(f"{rel}: 无 build=true 的架构条目")
    return entries


def check_schema(manifest: Manifest) -> CheckResult:
    """校验清单 schema：必填字段、取值与路径形态。

    Args:
        manifest: 待校验的 BTF 基线清单。

    Returns:
        schema 检查结果；失败时 details 汇总全部问题。
    """
    problems: list[str] = []

    unknown = set(manifest) - MANIFEST_KEYS
    if unknown:
        problems.append(f"顶层未知字段: {', '.join(sorted(unknown))}")

    for key in ("version", "defconfig_matrix_manifest", "pahole_package"):
        if not manifest.get(key):
            problems.append(f"缺少或为空值的必填字段: {key}")

    matrix_rel = manifest.get("defconfig_matrix_manifest")
    if isinstance(matrix_rel, str) and matrix_rel \
            and not matrix_rel.endswith(".yaml"):
        problems.append("defconfig_matrix_manifest 必须是 .yaml 相对路径")

    for key in CONFIG_LIST_KEYS:
        problems.extend(_cfg_list_errs(
            key, manifest.get(key), CONFIG_VAL_RE,
            "（须为 CONFIG_x=y 形态）"))
    for key in SYMBOL_LIST_KEYS:
        problems.extend(_cfg_list_errs(
            key, manifest.get(key), CONFIG_SYM_RE, "符号"))
    for key in STR_LIST_KEYS:
        problems.extend(_str_list_errs(key, manifest.get(key)))
    for key in WORKFLOW_LIST_KEYS:
        problems.extend(_path_list_errs(key, manifest.get(key)))

    return CheckResult("清单 schema 合法", not problems, "\n".join(problems))


def _cfg_list_errs(key: str, values: Any, pattern: re.Pattern[str],
                   hint: str) -> list[str]:
    """校验一个配置项列表。

    Args:
        key: 清单字段名。
        values: 字段取值。
        pattern: 合法条目的正则。
        hint: 追加到错误信息后的形态提示。

    Returns:
        问题描述列表；空列表表示合法。
    """
    if not isinstance(values, list) or not values:
        return [f"{key} 必须是非空列表"]
    return [
        f"{key} 含非法项{hint}: {item!r}"
        for item in values
        if not isinstance(item, str) or not pattern.match(item)
    ]


def _str_list_errs(key: str, values: Any) -> list[str]:
    """校验一个非空字符串列表。

    Args:
        key: 清单字段名。
        values: 字段取值。

    Returns:
        问题描述列表；空列表表示合法。
    """
    if not isinstance(values, list) or not values:
        return [f"{key} 必须是非空列表"]
    return [f"{key} 含空项" for item in values
            if not isinstance(item, str) or not item]


def _path_list_errs(key: str, values: Any) -> list[str]:
    """校验一个 ``.yml`` 相对路径列表。

    Args:
        key: 清单字段名。
        values: 字段取值。

    Returns:
        问题描述列表；空列表表示合法。
    """
    if not isinstance(values, list) or not values:
        return [f"{key} 必须是非空列表"]
    return [f"{key} 含非 .yml 路径: {item!r}" for item in values
            if not isinstance(item, str) or not item.endswith(".yml")]


def check_frags(entries: list[ArchEntry], kernel_dir: Path) -> CheckResult:
    """确认受检片段在内核树中真实存在。

    Args:
        entries: 受检架构与片段的相对路径。
        kernel_dir: 内核源码目录。

    Returns:
        片段存在性检查结果。
    """
    missing = [
        f"{arch}: 片段缺失 {configs}"
        for arch, configs in entries
        if not (kernel_dir / configs).is_file()
    ]
    return CheckResult(GUARD_NAME, not missing, "\n".join(missing))


def _check_present(name: str, entries: list[ArchEntry], kernel_dir: Path,
                   expected: list[str]) -> CheckResult:
    """确认每个受检片段逐项含 expected 中的配置行。

    Args:
        name: 检查项名称。
        entries: 受检架构与片段的相对路径。
        kernel_dir: 内核源码目录。
        expected: 必须出现的完整配置行。

    Returns:
        检查结果；缺失项按架构逐条记入 details。
    """
    problems: list[str] = []
    for arch, configs in entries:
        path = kernel_dir / configs
        if not path.is_file():
            continue
        present = set(read_configs(path))
        for want in expected:
            if want not in present:
                problems.append(f"{arch} ({configs}): 缺少 {want}")
    return CheckResult(name, not problems, "\n".join(problems))


def check_bpf(entries: list[ArchEntry], kernel_dir: Path,
              manifest: Manifest) -> CheckResult:
    """校验每个受检片段均含 eBPF 基线配置（OS-KER-106）。

    Args:
        entries: 受检架构与片段的相对路径。
        kernel_dir: 内核源码目录。
        manifest: BTF 基线清单。

    Returns:
        eBPF 基线检查结果。
    """
    return _check_present(
        "eBPF 基线齐备 (OS-KER-106)", entries, kernel_dir,
        manifest.get("bpf_configs", []))


def check_trace(entries: list[ArchEntry], kernel_dir: Path,
                manifest: Manifest) -> CheckResult:
    """校验每个受检片段均含 BPF_EVENTS 的前置链路（OS-KER-106）。

    Args:
        entries: 受检架构与片段的相对路径。
        kernel_dir: 内核源码目录。
        manifest: BTF 基线清单。

    Returns:
        tracing 链路检查结果。
    """
    return _check_present(
        "tracing 链路前置齐备 (OS-KER-106)", entries, kernel_dir,
        manifest.get("tracing_chain", []))


def check_btf(entries: list[ArchEntry], kernel_dir: Path,
              manifest: Manifest) -> CheckResult:
    """校验每个受检片段均满足 BTF 基线与 Kconfig 前置条件。

    CONFIG_DEBUG_INFO_BTF `depends on !DEBUG_INFO_SPLIT && !DEBUG_INFO_REDUCED`，
    且 DEBUG_INFO 只由 DWARF choice 项 select；三者缺其一，olddefconfig 都会
    丢弃 CONFIG_DEBUG_INFO_BTF（OS-OBS-012）。

    Args:
        entries: 受检架构与片段的相对路径。
        kernel_dir: 内核源码目录。
        manifest: BTF 基线清单。

    Returns:
        BTF 基线检查结果。
    """
    expected = list(manifest.get("btf_configs", []))
    expected += [MUTEX_FMT.format(key=key)
                 for key in manifest.get("btf_mutex_unset", [])]
    choice = manifest.get("debug_info_choice", [])

    problems: list[str] = []
    for arch, configs in entries:
        path = kernel_dir / configs
        if not path.is_file():
            continue
        present = set(read_configs(path))
        for want in expected:
            if want not in present:
                problems.append(f"{arch} ({configs}): 缺少 {want}")
        if choice and not any(item in present for item in choice):
            problems.append(
                f"{arch} ({configs}): DEBUG_INFO 形态缺失"
                f"（需命中 {', '.join(choice)} 之一）")
    return CheckResult("BTF 基线齐备 (OS-OBS-012)", not problems,
                       "\n".join(problems))


def check_ci_supply(manifest: Manifest, repo_root: Path) -> CheckResult:
    """校验受检 CI 工作流同时提供 pahole 与 BTF 产物断言。

    gen_btf() 在 CONFIG_DEBUG_INFO_BTF=y 时要求 pahole 可用，否则构建直接
    失败；且必须对构建产物断言 BTF 标识，否则「开启 BTF」无法被证明落地
    （OS-OBS-012）。

    Args:
        manifest: BTF 基线清单。
        repo_root: agent-linux 仓库根。

    Returns:
        CI 供给检查结果。
    """
    package = manifest.get("pahole_package")
    tokens = manifest.get("btf_artifacts", [])
    problems: list[str] = []
    for rel in manifest.get("ci_workflows", []):
        try:
            text = (repo_root / rel).read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{rel}: 无法读取: {exc}")
            continue
        if package not in text:
            problems.append(f"{rel}: 未安装生成 BTF 所需的工具包 '{package}'")
        for token in tokens:
            if token not in text:
                problems.append(f"{rel}: 未断言 BTF 产物标识 '{token}'")
    return CheckResult("CI 提供 pahole 且断言 BTF 产物 (OS-OBS-012)",
                       not problems, "\n".join(problems))


def print_report(results: Results, manifest: Manifest,
                 check_mode: bool) -> None:
    """打印检查报告。

    Args:
        results: 已执行的检查结果。
        manifest: BTF 基线清单，用于展示版本号。
        check_mode: 为真时输出 CI 精简格式，否则输出完整报告。
    """
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    if check_mode:
        if failed == 0:
            print(f"BTF-INTEGRATION: PASS ({passed}/{total} checks)")
        else:
            print(f"BTF-INTEGRATION: FAIL ({passed}/{total} checks, "
                  f"{failed} failed)")
            for result in results:
                if not result.passed:
                    print(f"  FAIL: {result.name}")
                    for line in result.details.splitlines():
                        print(f"    {line}")
        return

    print("=" * 72)
    print("agent-linux AirymaxOS BTF / eBPF 基线一致性报告")
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


def build_checks(manifest: Manifest, repo_root: Path,
                 kernel_dir: Path) -> Results:
    """按清单 schema 与片段存在性决定是否继续执行内容类检查。

    Args:
        manifest: BTF 基线清单。
        repo_root: agent-linux 仓库根。
        kernel_dir: 内核源码目录。

    Returns:
        检查结果列表；前置检查失败时短路，仅返回已能判定的项。
    """
    schema = check_schema(manifest)
    if not schema.passed:
        return [schema]
    try:
        entries = load_arches(manifest, repo_root)
    except ManifestError as exc:
        return [schema, CheckResult(GUARD_NAME, False, str(exc))]
    guard = check_frags(entries, kernel_dir)
    if not guard.passed:
        return [schema, guard]
    return [
        schema,
        guard,
        check_bpf(entries, kernel_dir, manifest),
        check_btf(entries, kernel_dir, manifest),
        check_trace(entries, kernel_dir, manifest),
        check_ci_supply(manifest, repo_root),
    ]


def main(argv: list[str] | None = None) -> None:
    """命令入口。

    Args:
        argv: 命令行参数；None 表示使用 ``sys.argv[1:]``。

    Raises:
        SystemExit: 以 ``EXIT_PASS`` / ``EXIT_FAIL`` / ``EXIT_USAGE`` 退出。
    """
    parser = argparse.ArgumentParser(
        description="AirymaxOS BTF / eBPF 基线一致性检查器 "
                    "(OS-KER-106, OS-OBS-012)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", default=None,
                        help="agent-linux 仓库根（默认：自动探测）")
    parser.add_argument("--manifest", default=None,
                        help="基线清单（默认：tools/btf-integration/manifest.yaml）")
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

    results = build_checks(manifest, repo_root, kernel_dir)
    print_report(results, manifest, args.check)

    failed = sum(1 for result in results if not result.passed)
    sys.exit(EXIT_FAIL if failed else EXIT_PASS)


if __name__ == "__main__":
    main()
