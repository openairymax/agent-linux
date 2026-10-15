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

Usage:
    python3 tools/btf-integration/btf_integration.py [options]

Options:
    --repo-root PATH    仓库根（默认：自动探测）
    --manifest PATH     基线清单（默认：tools/btf-integration/manifest.yaml）
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

DEFAULT_MANIFEST_REL = "tools/btf-integration/manifest.yaml"
DEFAULT_KERNEL_DIR = "kernel"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

# 受检片段存在性门禁的检查名（schema/清单失败时复用同一名字报告阻塞原因）。
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

    脚本位于 <repo_root>/tools/btf-integration/btf_integration.py。
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


def read_config_lines(path):
    """返回文件中的有效配置行（CONFIG_*=... 或 '# CONFIG_... is not set'）。"""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if CONFIG_VAL_RE.match(stripped) or CONFIG_SET_RE.match(stripped):
            lines.append(stripped)
    return lines


def load_build_entries(manifest, repo_root):
    """读取 defconfig 矩阵清单，返回 [(arch, configs_rel), ...]。

    仅取 build=true 的条目作为受检片段来源；矩阵清单缺失或结构非法时抛
    ManifestError。受检架构复用矩阵清单，避免在本表重复登记。
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

    entries = []
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


# ─── Checks ────────────────────────────────────────────────────────────────


def check_manifest_schema(manifest):
    """校验清单 schema：必填字段、取值与路径形态。"""
    problems = []

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
        problems.extend(_config_list_problems(
            key, manifest.get(key), CONFIG_VAL_RE,
            "（须为 CONFIG_x=y 形态）"))
    for key in SYMBOL_LIST_KEYS:
        problems.extend(_config_list_problems(
            key, manifest.get(key), CONFIG_SYM_RE, "符号"))
    for key in STR_LIST_KEYS:
        problems.extend(_str_list_problems(key, manifest.get(key)))
    for key in WORKFLOW_LIST_KEYS:
        problems.extend(_path_list_problems(key, manifest.get(key)))

    return CheckResult("清单 schema 合法", not problems, "\n".join(problems))


def _config_list_problems(key, values, pattern, hint):
    """校验一个配置项列表：非空列表且每项匹配给定形态。"""
    if not isinstance(values, list) or not values:
        return [f"{key} 必须是非空列表"]
    return [
        f"{key} 含非法项{hint}: {item!r}"
        for item in values
        if not isinstance(item, str) or not pattern.match(item)
    ]


def _str_list_problems(key, values):
    """校验一个非空字符串列表。"""
    if not isinstance(values, list) or not values:
        return [f"{key} 必须是非空列表"]
    return [f"{key} 含空项" for item in values
            if not isinstance(item, str) or not item]


def _path_list_problems(key, values):
    """校验一个 .yml 相对路径列表。"""
    if not isinstance(values, list) or not values:
        return [f"{key} 必须是非空列表"]
    return [f"{key} 含非 .yml 路径: {item!r}" for item in values
            if not isinstance(item, str) or not item.endswith(".yml")]


def check_fragments_present(entries, kernel_dir):
    """受检片段（清单 build=true 的 configs）在内核树中真实存在。"""
    missing = [
        f"{arch}: 片段缺失 {configs}"
        for arch, configs in entries
        if not (kernel_dir / configs).is_file()
    ]
    return CheckResult(GUARD_NAME, not missing, "\n".join(missing))


def _check_expected_present(name, entries, kernel_dir, expected):
    """每个受检片段均逐项含 expected 中的配置行。"""
    problems = []
    for arch, configs in entries:
        path = kernel_dir / configs
        if not path.is_file():
            continue
        present = set(read_config_lines(path))
        for want in expected:
            if want not in present:
                problems.append(f"{arch} ({configs}): 缺少 {want}")
    return CheckResult(name, not problems, "\n".join(problems))


def check_bpf_baseline(entries, kernel_dir, manifest):
    """每个受检片段均含 eBPF 基线配置（OS-KER-106）。"""
    return _check_expected_present(
        "eBPF 基线齐备 (OS-KER-106)", entries, kernel_dir,
        manifest.get("bpf_configs", []))


def check_tracing_chain(entries, kernel_dir, manifest):
    """每个受检片段均含 BPF_EVENTS 的前置链路（OS-KER-106）。"""
    return _check_expected_present(
        "tracing 链路前置齐备 (OS-KER-106)", entries, kernel_dir,
        manifest.get("tracing_chain", []))


def check_btf_baseline(entries, kernel_dir, manifest):
    """每个受检片段均含 BTF 基线：开启 BTF、关闭互斥项、命中 DWARF 形态。

    CONFIG_DEBUG_INFO_BTF `depends on !DEBUG_INFO_SPLIT && !DEBUG_INFO_REDUCED`，
    且 DEBUG_INFO 只由 DWARF choice 项 select；三者缺其一，olddefconfig 都会
    丢弃 CONFIG_DEBUG_INFO_BTF（OS-OBS-012）。
    """
    expected = list(manifest.get("btf_configs", []))
    expected += [MUTEX_FMT.format(key=key)
                 for key in manifest.get("btf_mutex_unset", [])]
    choice = manifest.get("debug_info_choice", [])

    problems = []
    for arch, configs in entries:
        path = kernel_dir / configs
        if not path.is_file():
            continue
        present = set(read_config_lines(path))
        for want in expected:
            if want not in present:
                problems.append(f"{arch} ({configs}): 缺少 {want}")
        if choice and not any(item in present for item in choice):
            problems.append(
                f"{arch} ({configs}): DEBUG_INFO 形态缺失"
                f"（需命中 {', '.join(choice)} 之一）")
    return CheckResult("BTF 基线齐备 (OS-OBS-012)", not problems,
                       "\n".join(problems))


def check_ci_supply(manifest, repo_root):
    """受检 CI 工作流同时提供 pahole 与 BTF 产物断言（OS-OBS-012）。

    gen_btf() 在 CONFIG_DEBUG_INFO_BTF=y 时要求 pahole 可用，否则构建直接
    失败；且必须对构建产物断言 BTF 标识，否则「开启 BTF」无法被证明落地。
    """
    package = manifest.get("pahole_package")
    tokens = manifest.get("btf_artifacts", [])
    problems = []
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


# ─── Report ────────────────────────────────────────────────────────────────


def print_report(results, manifest, check_mode):
    """打印检查报告。"""
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


# ─── Main ──────────────────────────────────────────────────────────────────


def build_checks(manifest, repo_root, kernel_dir):
    """按清单 schema 与片段存在性决定是否继续执行内容类检查。"""
    schema = check_manifest_schema(manifest)
    if not schema.passed:
        return [schema]
    try:
        entries = load_build_entries(manifest, repo_root)
    except ManifestError as exc:
        return [schema, CheckResult(GUARD_NAME, False, str(exc))]
    guard = check_fragments_present(entries, kernel_dir)
    if not guard.passed:
        return [schema, guard]
    return [
        schema,
        guard,
        check_bpf_baseline(entries, kernel_dir, manifest),
        check_btf_baseline(entries, kernel_dir, manifest),
        check_tracing_chain(entries, kernel_dir, manifest),
        check_ci_supply(manifest, repo_root),
    ]


def main(argv=None):
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
