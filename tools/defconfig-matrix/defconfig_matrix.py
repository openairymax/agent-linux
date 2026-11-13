#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
defconfig_matrix.py — AirymaxOS defconfig 矩阵一致性检查器

以 tools/defconfig-matrix/manifest.yaml 为唯一事实源（SSoT），静态校验
「支持架构 × airy_defconfig 形态 × CI 构建矩阵 × 必备 CONFIG × Kbuild 引用」
五者彼此一致，把分散在 airy-verify.yml / kernel-verify.yml 的架构映射与
ci/gates/kernel-*.sh 的必备 CONFIG 断言收敛到一处清单，并由本工具检出
任何一侧的漂移。

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
  6. CI 构建矩阵与清单一致（airy-verify 精确匹配 / kernel-verify 登记子集）
  7. 门禁的必备 CONFIG 清单与清单一致（OS-BUILD-026）
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

# tools/lib 是契约检查工具的共享层：结果类型与报告格式只能有一份。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from check_report import CheckResult, ManifestError, yaml
from manifest_io import load_manifest, run_gate

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST_REL = "tools/defconfig-matrix/manifest.yaml"
CI_AIRY_REL = ".github/workflows/airy-verify.yml"
CI_KERNEL_REL = ".github/workflows/kernel-verify.yml"
# 必备 CONFIG 断言落在门禁里而非 workflow 内联块：workflow 只留一行调用。
# 契约数组由 ci/lib/kernel.sh 持有，kernel-airy --kind assert 与
# kernel-refbuild 两条腿都引用同一份，因此这里只需与那一个载体对账。
CI_CONF_LIB_REL = "ci/lib/kernel.sh"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

KIND_FRAGMENT = "fragment"
KIND_FULL = "full"
KIND_CHOICES = (KIND_FRAGMENT, KIND_FULL)

MANIFEST_KEYS = frozenset(
    {"version", "architectures", "required_configs", "lsm_entry"})
ARCH_KEYS = frozenset({"arch", "cross", "kind", "build", "configs"})

# Airymax 专属源码落点（与 ci/gates/kernel-airy.sh 的 SPDX 审计保持一致）。
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
REQUIRED_ARRAY_RE = re.compile(r"REQUIRED_CONF=\((.*?)^\s*\)", re.DOTALL | re.MULTILINE)
REQUIRED_ITEM_RE = re.compile(r"'([^']+)'")


# ─── Helpers ───────────────────────────────────────────────────────────────


def arch_kdir(entry):
    """从 configs 路径推导内核源码架构目录名（arch/<kdir>/configs/...）。"""
    parts = PurePosixPath(str(entry.get("configs", ""))).parts
    return parts[1] if len(parts) >= 2 else ""


def cfg_lines(path):
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


def arch_cfgs(manifest, kernel_dir):
    """逐项产出已登记的 airy_defconfig；磁盘上缺失的条目跳过。"""
    for entry in manifest.get("architectures", []):
        configs = entry.get("configs")
        path = kernel_dir / str(configs)
        if path.is_file():
            yield configs, path


def raw_lines(path):
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


def mf_schema(manifest):
    """校验清单 schema：必填字段、arch 唯一、取值与路径形态。"""
    problems = []
    _mf_head(manifest, problems)
    _mf_archs(manifest, problems)
    _mf_required(manifest, problems)
    return CheckResult("清单 schema 合法", not problems, "\n".join(problems))


def _mf_head(manifest, problems):
    """顶层字段集合与必填标量：未知字段或空值都说明清单与校验器版本不符。"""
    unknown = set(manifest) - MANIFEST_KEYS
    if unknown:
        problems.append(f"顶层未知字段: {', '.join(sorted(unknown))}")
    for key in ("version", "required_configs", "lsm_entry"):
        if not manifest.get(key):
            problems.append(f"缺少或为空值的必填字段: {key}")


def _mf_archs(manifest, problems):
    """逐条校验 architectures；arch 唯一性靠 seen 跨条目累积。"""
    architectures = manifest.get("architectures")
    if not isinstance(architectures, list) or not architectures:
        problems.append("architectures 必须是非空列表")
        return
    seen = set()
    for idx, entry in enumerate(architectures):
        problems.extend(_arch_schema(idx, entry, seen))


def _mf_required(manifest, problems):
    """required_configs 仅在形态为列表时逐项核对 CONFIG_ 前缀。"""
    required = manifest.get("required_configs")
    if not isinstance(required, list):
        return
    if not required:
        problems.append("required_configs 不能为空")
    for item in required:
        if not isinstance(item, str) or not item.startswith("CONFIG_"):
            problems.append(f"required_configs 含非 CONFIG_ 项: {item!r}")


def _arch_schema(idx, entry, seen):
    """校验单条 architectures 记录，返回问题列表。"""
    where = f"architectures[{idx}]"
    if not isinstance(entry, dict):
        return [f"{where} 必须是映射"]
    problems = _arch_keys(entry, where)
    _arch_ident(entry, where, seen, problems)
    _arch_paths(entry, where, problems)
    return problems


def _arch_keys(entry, where):
    """字段集合：未知字段与缺失字段都说明这份清单与校验器不同版本。"""
    problems = []
    unknown = set(entry) - ARCH_KEYS
    if unknown:
        problems.append(f"{where} 未知字段: {', '.join(sorted(unknown))}")
    for key in sorted(ARCH_KEYS):
        if key not in entry:
            problems.append(f"{where} 缺少字段: {key}")
    return problems


def _arch_key(entry, where, seen, problems):
    """arch 是唯一键：非空且不重复。"""
    arch = entry.get("arch")
    if not isinstance(arch, str) or not arch:
        problems.append(f"{where}.arch 必须是非空字符串")
    elif arch in seen:
        problems.append(f"{where}.arch 重复: {arch}")
    else:
        seen.add(arch)


def _arch_flags(entry, where, problems):
    """kind/build/cross 的取值与类型。"""
    if "kind" in entry and entry.get("kind") not in KIND_CHOICES:
        problems.append(f"{where}.kind 必须是 {' 或 '.join(KIND_CHOICES)}")
    if "build" in entry and not isinstance(entry.get("build"), bool):
        problems.append(f"{where}.build 必须是布尔值")
    if not isinstance(entry.get("cross"), str):
        problems.append(f"{where}.cross 必须是字符串")


def _arch_ident(entry, where, seen, problems):
    """arch 是唯一键；kind/build/cross 决定这条记录怎么被构建。"""
    _arch_key(entry, where, seen, problems)
    _arch_flags(entry, where, problems)


def _arch_paths(entry, where, problems):
    """configs 路径形态，以及 build=true 与 kind=fragment 的固定搭配。"""
    configs = entry.get("configs")
    if not isinstance(configs, str) or not configs.startswith("arch/") \
            or not configs.endswith("/configs/airy_defconfig"):
        problems.append(
            f"{where}.configs 必须是 arch/<kdir>/configs/airy_defconfig 形态")
    if entry.get("build") is True and entry.get("kind") != KIND_FRAGMENT:
        problems.append(
            f"{where}: build=true 要求 kind=fragment（CI 固定走 merge_config 三步法）")


def check_fs(manifest, kernel_dir):
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


def check_fmt(manifest, kernel_dir):
    """首行 SPDX、CONFIG 语法合法、无重复配置键。"""
    problems = []
    for configs, path in arch_cfgs(manifest, kernel_dir):
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or not lines[0].startswith("# SPDX-License-Identifier:"):
            problems.append(f"{configs}: 第 1 行缺少 SPDX 许可证标识")
        keys = {}
        for num, stripped in raw_lines(path):
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


def check_req_cfg(manifest, kernel_dir):
    """每个 airy_defconfig 均含全部必备 CONFIG（OS-BUILD-026）。"""
    required = manifest.get("required_configs", [])
    problems = []
    for configs, path in arch_cfgs(manifest, kernel_dir):
        present = set(cfg_lines(path))
        for want in required:
            if want not in present:
                problems.append(f"{configs}: 缺少必备配置 {want}")

    return CheckResult("必备 CONFIG 齐备 (OS-BUILD-026)", not problems,
                       "\n".join(problems))


def check_lsm_entry(manifest, kernel_dir):
    """每个 airy_defconfig 的 CONFIG_LSM 含清单登记的 LSM 名。"""
    entry_name = manifest.get("lsm_entry", "")
    problems = []
    for configs, path in arch_cfgs(manifest, kernel_dir):
        value = None
        for line in cfg_lines(path):
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


def check_ci_mtx(manifest, repo_root):
    """CI 构建矩阵与清单一致（airy-verify 精确匹配 / kernel-verify 为登记子集）。"""
    name = "CI 构建矩阵与清单一致 (OS-BUILD-028)"
    try:
        include, karch = ci_mtx(repo_root)
    except (ManifestError, KeyError, TypeError) as exc:
        return CheckResult(name, False, f"读取 CI 工作流失败: {exc}")

    entries = manifest.get("architectures", [])
    build = [e for e in entries if e.get("build")]
    problems = (mtx_drift(build, include) + mtx_extra(include, build)
                + mtx_unk(karch, entries))
    return CheckResult(name, not problems, "\n".join(problems))


def ci_mtx(repo_root):
    """两份 CI 工作流的矩阵字段：airy 为 include 条目，kernel 为架构名。"""
    airy = load_workflow(repo_root / CI_AIRY_REL)
    kernel = load_workflow(repo_root / CI_KERNEL_REL)
    include = airy["jobs"]["airy-build"]["strategy"]["matrix"]["include"]
    karch = kernel["jobs"]["kernel-build"]["strategy"]["matrix"]["arch"]
    return include, karch


def mtx_map(include):
    """架构 → airy-verify 矩阵条目。"""
    return {item.get("arch"): item for item in include}


def mtx_drift(entries, include):
    """清单 build=true 的每个架构，在 airy-verify 矩阵里逐一核对两个字段。"""
    matrix = mtx_map(include)
    problems = []
    for entry in entries:
        arch = entry.get("arch")
        item = matrix.get(arch)
        if item is None:
            problems.append(f"airy-verify 构建矩阵缺少清单 build=true 架构: {arch}")
            continue
        problems.extend(mtx_row(arch, entry, item))
    return problems


def mtx_row(arch, entry, item):
    """单个架构：kdir 由清单推导，cross 直接比对，两者独立报错。"""
    kdir = arch_kdir(entry)
    problems = []
    if item.get("kdir") != kdir:
        problems.append(
            f"{arch}: kdir 漂移（清单 {kdir} / airy-verify {item.get('kdir')}）")
    if item.get("cross") != entry.get("cross"):
        problems.append(
            f"{arch}: cross 漂移（清单 '{entry.get('cross')}' / "
            f"airy-verify '{item.get('cross')}'）")
    return problems


def mtx_extra(include, entries):
    """airy-verify 矩阵不得含清单未标 build=true 的架构。"""
    build_arches = {e.get("arch") for e in entries}
    return [f"airy-verify 构建矩阵含清单未标 build=true 的架构: {arch}"
            for arch in sorted(set(mtx_map(include)) - build_arches)]


def mtx_unk(karch, entries):
    """kernel-verify 矩阵只允许清单已登记的架构，build 与否不影响登记。"""
    known = {e.get("arch") for e in entries}
    return [f"kernel-verify 构建矩阵含清单未登记架构: {arch}"
            for arch in sorted(set(karch) - known)]


def check_ci_req(manifest, repo_root):
    """CI 的必备 CONFIG 清单与清单逐项相等（OS-BUILD-026）。"""
    name = "CI 必备 CONFIG 清单与清单一致 (OS-BUILD-026)"
    path = repo_root / CI_CONF_LIB_REL
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckResult(name, False, f"{CI_CONF_LIB_REL}: 无法读取: {exc}")
    blocks = REQUIRED_ARRAY_RE.findall(text)
    if not blocks:
        return CheckResult(
            name, False,
            f"{CI_CONF_LIB_REL}: 未找到 REQUIRED_CONF=(...) 必备 CONFIG 清单块")
    want = set(manifest.get("required_configs", []))
    found = set()
    for block in blocks:
        found.update(REQUIRED_ITEM_RE.findall(block))
    problems = []
    missing = sorted(want - found)
    extra = sorted(found - want)
    if missing:
        problems.append(
            f"{CI_CONF_LIB_REL}: required 列表缺少清单项: {', '.join(missing)}")
    if extra:
        problems.append(
            f"{CI_CONF_LIB_REL}: required 列表含清单外项: {', '.join(extra)}")
    return CheckResult(name, not problems, "\n".join(problems))


def kbuild_refs(kernel_dir):
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


def declared_cfg(kernel_dir):
    """扫描内核树全部 Kconfig*，收集已声明的 config 符号（带 CONFIG_）。"""
    declared = set()
    for kconfig in kernel_dir.rglob("Kconfig*"):
        if not kconfig.is_file():
            continue
        text = kconfig.read_text(encoding="utf-8", errors="replace")
        for name in KCONFIG_DECL_RE.findall(text):
            declared.add(f"CONFIG_{name}")
    return declared


def check_kdecl(kernel_dir):
    """obj-$(CONFIG_*) 引用的宏须有 config 声明（OS-KER-025）。"""
    refs = kbuild_refs(kernel_dir)
    if not refs:
        return CheckResult("obj-$(CONFIG_*) 均有 Kconfig 声明 (OS-KER-025)",
                           True, "Airymax 路径下无 CONFIG 引用")
    declared = declared_cfg(kernel_dir)
    problems = [
        f"{symbol} 被引用但无 Kconfig 声明"
        f"（引用处: {', '.join(sorted(refs[symbol]))}）"
        for symbol in sorted(refs) if symbol not in declared
    ]
    return CheckResult("obj-$(CONFIG_*) 均有 Kconfig 声明 (OS-KER-025)",
                       not problems, "\n".join(problems))


# ─── Main ──────────────────────────────────────────────────────────────────


def build_checks(manifest, repo_root, kernel_dir):
    """按清单 schema 结果决定是否继续执行内容类检查。"""
    schema = mf_schema(manifest)
    if not schema.passed:
        return [schema]
    return [
        schema,
        check_fs(manifest, kernel_dir),
        check_fmt(manifest, kernel_dir),
        check_req_cfg(manifest, kernel_dir),
        check_lsm_entry(manifest, kernel_dir),
        check_ci_mtx(manifest, repo_root),
        check_ci_req(manifest, repo_root),
        check_kdecl(kernel_dir),
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
    run_gate(
        parser, argv, DEFAULT_MANIFEST_REL,
        lambda gate: build_checks(gate.manifest, gate.root, gate.kernel_dir),
        "DEFCONFIG-MATRIX",
        "agent-linux AirymaxOS defconfig 矩阵一致性报告",
        kernel=True,
        tail=lambda gate:
            f"架构数: {len(gate.manifest.get('architectures', []))}")


if __name__ == "__main__":
    main()
