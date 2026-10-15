#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
defconfig_matrix.py — AirymaxOS defconfig 矩阵一致性检查器

以 tools/defconfig-matrix/manifest.yaml 为唯一事实源（SSoT），静态校验
「支持架构 × airy_defconfig 形态 × CI 构建矩阵 × 必备 CONFIG × Kbuild 引用」
五者彼此一致，把分散在 .github/workflows/ci-airy.yml 与 ci-kernel.yml 的
重复映射收敛到一处清单，并由本工具检出任何一侧的漂移。

设计原则（与项目铁律一致）：
  * 清单是唯一事实源；CI 的构建矩阵与必备 CONFIG 清单必须与之逐项相等。
    本工具只做「清单 ↔ 仓库/工作流」的交叉比对，不重复实现构建逻辑。
  * 严禁桩实现：所有检查均读取真实文件内容，给出可复现的判定与证据。

检查项：
  1. 清单 schema 合法（必填字段、arch 唯一、kind/build 取值、路径形态）
  2. 清单 ↔ 文件系统双向一致（OS-BUILD-028）
  3. defconfig 格式（首行 SPDX / CONFIG 语法 / 无重复键）
  4. 必备 CONFIG 齐备（OS-BUILD-026）
  5. CONFIG_LSM 含清单登记的 LSM 名
  6. CI 构建矩阵与清单一致（ci-airy 精确匹配 / ci-kernel 登记子集）
  7. CI 必备 CONFIG 清单与清单一致（OS-BUILD-026）
  8. obj-$(CONFIG_*) 引用均有 Kconfig 声明（OS-KER-025）

权威依据：
  docs/docs-linux/70-build-system/02-kconfig-system.md
    §7.1 三步法 / §10 规则汇总
      OS-BUILD-026（各架构 airy_defconfig 为全配置基线）
      OS-BUILD-027（allmodconfig/allnoconfig + KCONFIG_ALLCONFIG 可求解）
      OS-BUILD-028（每支持架构必须有 airy_defconfig）
      OS-KER-025（obj-$(CONFIG_*) 引用的宏须有 config 声明）
  docs/docs-linux/70-build-system/03-ci-cd-pipeline.md（编译验证矩阵）

Usage:
    python3 tools/defconfig-matrix/defconfig_matrix.py [options]

Options:
    --repo-root PATH    仓库根（默认：自动探测）
    --manifest PATH     矩阵清单（默认：tools/defconfig-matrix/manifest.yaml）
    --check             CI 模式：精简输出，仅退出码

Exit codes:
    0 — 全部检查通过
    1 — 一项或多项检查失败
    2 — 用法/环境错误（清单缺失、YAML 非法、内核源码目录缺失等）
"""

import argparse
import re
import sys
from pathlib import Path, PurePosixPath

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺失时在入口统一报错
    yaml = None

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST_REL = "tools/defconfig-matrix/manifest.yaml"
DEFAULT_KERNEL_DIR = "kernel"
CI_AIRY_REL = ".github/workflows/ci-airy.yml"
CI_KERNEL_REL = ".github/workflows/ci-kernel.yml"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

KIND_FRAGMENT = "fragment"
KIND_FULL = "full"
KIND_CHOICES = (KIND_FRAGMENT, KIND_FULL)

MANIFEST_KEYS = frozenset(
    {"version", "architectures", "required_configs", "lsm_entry"})
ARCH_KEYS = frozenset({"arch", "cross", "kind", "build", "configs"})

# Airymax 专属源码落点（与 ci-airy.yml / SPDX 审计保持一致）。
AIRY_KBUILD_DIRS = (
    "security/airy",
    "kernel/superv",
    "kernel/syscalls",
    "kernel/corekern",
    "kernel/log",
    "kernel/ipc",
)

CONFIG_VAL_RE = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$")
CONFIG_SET_RE = re.compile(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$")
CONFIG_REF_RE = re.compile(r"\$\((CONFIG_[A-Za-z0-9_]+)\)")
KCONFIG_DECL_RE = re.compile(
    r"^\s*(?:menuconfig|config)\s+([A-Za-z0-9_]+)\s*$", re.MULTILINE)
REQUIRED_BLOCK_RE = re.compile(r"required=\((.*?)\)", re.DOTALL)
REQUIRED_ITEM_RE = re.compile(r"'([^']+)'")


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

    脚本位于 <repo_root>/tools/defconfig-matrix/defconfig_matrix.py。
    """
    return Path(__file__).resolve().parent.parent.parent


def load_manifest(path):
    """读取并解析矩阵清单；缺失或 YAML 非法时抛 ManifestError。"""
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


def arch_kdir(entry):
    """从 configs 路径推导内核源码架构目录名（arch/<kdir>/configs/...）。"""
    parts = PurePosixPath(str(entry.get("configs", ""))).parts
    return parts[1] if len(parts) >= 2 else ""


def read_config_lines(path):
    """返回文件中的有效配置行（CONFIG_*=... 或 '# CONFIG_... is not set'）。"""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if CONFIG_VAL_RE.match(stripped) or CONFIG_SET_RE.match(stripped):
            lines.append(stripped)
    return lines


def config_key(line):
    """取配置行的键名（带 CONFIG_ 前缀）。"""
    return (CONFIG_VAL_RE.match(line) or CONFIG_SET_RE.match(line)).group(1)


def non_comment_lines(path):
    """逐行产出 (行号, 去空白行)；仅保留非空行。"""
    for num, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if stripped:
            yield num, stripped


def load_workflow(path):
    """读取并解析一个 workflow；失败时抛 ManifestError。"""
    if yaml is None:
        raise ManifestError("PyYAML 不可用：请安装 pyyaml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError:
        raise ManifestError(f"工作流文件不存在：{path}")
    except yaml.YAMLError as exc:
        raise ManifestError(f"工作流 YAML 解析失败：{path}: {exc}")


# ─── Checks ────────────────────────────────────────────────────────────────


def check_manifest_schema(manifest):
    """校验清单 schema：必填字段、arch 唯一、取值与路径形态。"""
    problems = []

    unknown = set(manifest) - MANIFEST_KEYS
    if unknown:
        problems.append(f"顶层未知字段: {', '.join(sorted(unknown))}")

    architectures = manifest.get("architectures")
    required = manifest.get("required_configs")
    lsm_entry = manifest.get("lsm_entry")
    for key, value in (("version", manifest.get("version")),
                       ("required_configs", required),
                       ("lsm_entry", lsm_entry)):
        if not value:
            problems.append(f"缺少或为空值的必填字段: {key}")
    if not isinstance(architectures, list) or not architectures:
        problems.append("architectures 必须是非空列表")
    else:
        seen = set()
        for idx, entry in enumerate(architectures):
            problems.extend(_arch_schema_problems(idx, entry, seen))

    if isinstance(required, list):
        if not required:
            problems.append("required_configs 不能为空")
        for item in required:
            if not isinstance(item, str) or not item.startswith("CONFIG_"):
                problems.append(
                    f"required_configs 含非 CONFIG_ 项: {item!r}")

    return CheckResult("清单 schema 合法", not problems, "\n".join(problems))


def _arch_schema_problems(idx, entry, seen):
    """校验单条 architectures 记录，返回问题列表。"""
    where = f"architectures[{idx}]"
    problems = []
    if not isinstance(entry, dict):
        return [f"{where} 必须是映射"]

    unknown = set(entry) - ARCH_KEYS
    if unknown:
        problems.append(f"{where} 未知字段: {', '.join(sorted(unknown))}")
    for key in sorted(ARCH_KEYS):
        if key not in entry:
            problems.append(f"{where} 缺少字段: {key}")

    arch = entry.get("arch")
    if not isinstance(arch, str) or not arch:
        problems.append(f"{where}.arch 必须是非空字符串")
    elif arch in seen:
        problems.append(f"{where}.arch 重复: {arch}")
    else:
        seen.add(arch)

    if "kind" in entry and entry.get("kind") not in KIND_CHOICES:
        problems.append(f"{where}.kind 必须是 {' 或 '.join(KIND_CHOICES)}")
    if "build" in entry and not isinstance(entry.get("build"), bool):
        problems.append(f"{where}.build 必须是布尔值")
    if not isinstance(entry.get("cross"), str):
        problems.append(f"{where}.cross 必须是字符串")

    configs = entry.get("configs")
    if not isinstance(configs, str) or not configs.startswith("arch/") \
            or not configs.endswith("/configs/airy_defconfig"):
        problems.append(
            f"{where}.configs 必须是 arch/<kdir>/configs/airy_defconfig 形态")
    if entry.get("build") is True and entry.get("kind") != KIND_FRAGMENT:
        problems.append(
            f"{where}: build=true 要求 kind=fragment（CI 固定走 merge_config 三步法）")
    return problems


def check_manifest_fs(manifest, kernel_dir):
    """清单登记项与磁盘上的 airy_defconfig 双向一致（OS-BUILD-028）。"""
    problems = []
    registered = set()
    for entry in manifest.get("architectures", []):
        configs = entry.get("configs")
        if not isinstance(configs, str):
            continue
        registered.add(configs)
        if not (kernel_dir / configs).is_file():
            problems.append(f"清单已登记但文件缺失: {configs}")

    on_disk = {
        str(path.relative_to(kernel_dir))
        for path in kernel_dir.glob("arch/*/configs/airy_defconfig")
    }
    for configs in sorted(on_disk - registered):
        problems.append(f"磁盘存在但清单未登记: {configs}")

    return CheckResult(
        "清单 ↔ 文件系统双向一致 (OS-BUILD-028)", not problems,
        "\n".join(problems))


def check_defconfig_format(manifest, kernel_dir):
    """首行 SPDX、CONFIG 语法合法、无重复配置键。"""
    problems = []
    for entry in manifest.get("architectures", []):
        configs = entry.get("configs")
        path = kernel_dir / str(configs)
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or not lines[0].startswith("# SPDX-License-Identifier:"):
            problems.append(f"{configs}: 第 1 行缺少 SPDX 许可证标识")
        keys = {}
        for num, stripped in non_comment_lines(path):
            is_config = bool(CONFIG_VAL_RE.match(stripped)
                             or CONFIG_SET_RE.match(stripped))
            if not is_config:
                if stripped.startswith("#"):
                    continue
                problems.append(
                    f"{configs}:{num}: 非法行（既非注释亦非 CONFIG 赋值）: {stripped}")
                continue
            key = config_key(stripped)
            if key in keys:
                problems.append(
                    f"{configs}:{num}: 重复配置键 {key}（首次见于第 {keys[key]} 行）")
            else:
                keys[key] = num

    return CheckResult(
        "defconfig 格式（SPDX / 语法 / 无重复键）", not problems,
        "\n".join(problems))


def check_required_configs(manifest, kernel_dir):
    """每个 airy_defconfig 均含全部必备 CONFIG（OS-BUILD-026）。"""
    required = manifest.get("required_configs", [])
    problems = []
    for entry in manifest.get("architectures", []):
        configs = entry.get("configs")
        path = kernel_dir / str(configs)
        if not path.is_file():
            continue
        present = set(read_config_lines(path))
        for want in required:
            if want not in present:
                problems.append(f"{configs}: 缺少必备配置 {want}")

    return CheckResult("必备 CONFIG 齐备 (OS-BUILD-026)", not problems,
                       "\n".join(problems))


def check_lsm_entry(manifest, kernel_dir):
    """每个 airy_defconfig 的 CONFIG_LSM 含清单登记的 LSM 名。"""
    entry_name = manifest.get("lsm_entry", "")
    problems = []
    for entry in manifest.get("architectures", []):
        configs = entry.get("configs")
        path = kernel_dir / str(configs)
        if not path.is_file():
            continue
        value = None
        for line in read_config_lines(path):
            match = CONFIG_VAL_RE.match(line)
            if match and match.group(1) == "CONFIG_LSM":
                value = match.group(2).strip().strip('"')
        if value is None:
            problems.append(f"{configs}: 未设置 CONFIG_LSM")
            continue
        names = [name.strip() for name in value.split(",") if name.strip()]
        if entry_name not in names:
            problems.append(f"{configs}: CONFIG_LSM 不含 {entry_name}: {value}")

    return CheckResult(f"CONFIG_LSM 含 {entry_name}", not problems,
                       "\n".join(problems))


def check_ci_matrix_sync(manifest, repo_root):
    """CI 构建矩阵与清单一致（ci-airy 精确匹配 / ci-kernel 为登记子集）。"""
    name = "CI 构建矩阵与清单一致 (OS-BUILD-028)"
    try:
        airy = load_workflow(repo_root / CI_AIRY_REL)
        kernel = load_workflow(repo_root / CI_KERNEL_REL)
        include = airy["jobs"]["airy-build"]["strategy"]["matrix"]["include"]
        karch = kernel["jobs"]["kernel-build"]["strategy"]["matrix"]["arch"]
    except (ManifestError, KeyError, TypeError) as exc:
        return CheckResult(name, False, f"读取 CI 工作流失败: {exc}")

    problems = []
    build_entries = [e for e in manifest.get("architectures", [])
                     if e.get("build")]
    build_arches = {e.get("arch") for e in build_entries}
    matrix = {item.get("arch"): item for item in include}

    for entry in build_entries:
        arch = entry.get("arch")
        item = matrix.get(arch)
        if item is None:
            problems.append(f"ci-airy 构建矩阵缺少清单 build=true 架构: {arch}")
            continue
        kdir = arch_kdir(entry)
        if item.get("kdir") != kdir:
            problems.append(
                f"{arch}: kdir 漂移（清单 {kdir} / ci-airy {item.get('kdir')}）")
        if item.get("cross") != entry.get("cross"):
            problems.append(
                f"{arch}: cross 漂移（清单 '{entry.get('cross')}' / "
                f"ci-airy '{item.get('cross')}'）")

    for arch in sorted(set(matrix) - build_arches):
        problems.append(f"ci-airy 构建矩阵含清单未标 build=true 的架构: {arch}")

    known = {e.get("arch") for e in manifest.get("architectures", [])}
    for arch in sorted(set(karch) - known):
        problems.append(f"ci-kernel 构建矩阵含清单未登记架构: {arch}")

    return CheckResult(name, not problems, "\n".join(problems))


def check_ci_required_sync(manifest, repo_root):
    """CI 的 required=(...) 必备 CONFIG 清单与清单逐项相等（OS-BUILD-026）。"""
    name = "CI 必备 CONFIG 清单与清单一致 (OS-BUILD-026)"
    want = set(manifest.get("required_configs", []))
    problems = []
    for rel in (CI_AIRY_REL, CI_KERNEL_REL):
        path = repo_root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{rel}: 无法读取: {exc}")
            continue
        blocks = REQUIRED_BLOCK_RE.findall(text)
        if not blocks:
            problems.append(f"{rel}: 未找到 required=(...) 必备 CONFIG 清单块")
            continue
        found = set()
        for block in blocks:
            found.update(REQUIRED_ITEM_RE.findall(block))
        missing = sorted(want - found)
        extra = sorted(found - want)
        if missing:
            problems.append(f"{rel}: required 列表缺少清单项: {', '.join(missing)}")
        if extra:
            problems.append(f"{rel}: required 列表含清单外项: {', '.join(extra)}")

    return CheckResult(name, not problems, "\n".join(problems))


def collect_airy_kbuild_refs(kernel_dir):
    """收集 Airymax 路径下 Kbuild/Makefile 中的 $(CONFIG_*) 引用。"""
    refs = {}
    for rel in AIRY_KBUILD_DIRS:
        directory = kernel_dir / rel
        if not directory.is_dir():
            continue
        for name in ("Kbuild", "Makefile"):
            path = directory / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for symbol in CONFIG_REF_RE.findall(text):
                refs.setdefault(symbol, set()).add(f"{rel}/{name}")
    return refs


def collect_declared_configs(kernel_dir):
    """扫描内核树全部 Kconfig*，收集已声明的 config 符号（带 CONFIG_）。"""
    declared = set()
    for kconfig in kernel_dir.rglob("Kconfig*"):
        if not kconfig.is_file():
            continue
        text = kconfig.read_text(encoding="utf-8", errors="replace")
        for name in KCONFIG_DECL_RE.findall(text):
            declared.add(f"CONFIG_{name}")
    return declared


def check_kconfig_declared(kernel_dir):
    """obj-$(CONFIG_*) 引用的宏须有 config 声明（OS-KER-025）。"""
    refs = collect_airy_kbuild_refs(kernel_dir)
    if not refs:
        return CheckResult("obj-$(CONFIG_*) 均有 Kconfig 声明 (OS-KER-025)",
                           True, "Airymax 路径下无 CONFIG 引用")
    declared = collect_declared_configs(kernel_dir)
    problems = [
        f"{symbol} 被引用但无 Kconfig 声明"
        f"（引用处: {', '.join(sorted(refs[symbol]))}）"
        for symbol in sorted(refs) if symbol not in declared
    ]
    return CheckResult("obj-$(CONFIG_*) 均有 Kconfig 声明 (OS-KER-025)",
                       not problems, "\n".join(problems))


# ─── Report ────────────────────────────────────────────────────────────────


def print_report(results, manifest, check_mode):
    """打印检查报告。"""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    if check_mode:
        if failed == 0:
            print(f"DEFCONFIG-MATRIX: PASS ({passed}/{total} checks)")
        else:
            print(f"DEFCONFIG-MATRIX: FAIL ({passed}/{total} checks, "
                  f"{failed} failed)")
            for result in results:
                if not result.passed:
                    print(f"  FAIL: {result.name}")
                    for line in result.details.splitlines():
                        print(f"    {line}")
        return

    print("=" * 72)
    print("agent-linux AirymaxOS defconfig 矩阵一致性报告")
    print(f"清单版本: {manifest.get('version', '?')}    "
          f"架构数: {len(manifest.get('architectures', []))}")
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
    """按清单 schema 结果决定是否继续执行内容类检查。"""
    schema = check_manifest_schema(manifest)
    if not schema.passed:
        return [schema]
    return [
        schema,
        check_manifest_fs(manifest, kernel_dir),
        check_defconfig_format(manifest, kernel_dir),
        check_required_configs(manifest, kernel_dir),
        check_lsm_entry(manifest, kernel_dir),
        check_ci_matrix_sync(manifest, repo_root),
        check_ci_required_sync(manifest, repo_root),
        check_kconfig_declared(kernel_dir),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="AirymaxOS defconfig 矩阵一致性检查器 "
                    "(OS-BUILD-026/027/028, OS-KER-025)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", default=None,
                        help="agent-linux 仓库根（默认：自动探测）")
    parser.add_argument("--manifest", default=None,
                        help="矩阵清单（默认：tools/defconfig-matrix/manifest.yaml）")
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
