#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
ci_matrix.py — AirymaxOS CI 编译矩阵一致性检查器

以 tools/ci-matrix/manifest.yaml 为唯一事实源（SSoT），静态校验
「编译配置 × 架构 × 编译器 × 排除项 × 调度约束 × 门禁语义」与
.github/workflows/ci-kernel.yml 的矩阵段逐项一致，并把逐格构建结果
按 03-ci-cd-pipeline.md §3.4/§10.1 归因、汇聚为 PR 标签。

设计原则（与项目铁律一致）：
  * 清单是唯一事实源；架构维度复用 tools/defconfig-matrix/manifest.yaml
    中 build=true 的条目，避免两处定义漂移。
  * 本工具只做「清单 ↔ workflow ↔ 结果摘要」的交叉比对，不重复实现
    构建逻辑，也不代替 CI 执行编译。
  * 严禁桩实现：所有检查读取真实文件，判定可复现，失败给出证据。

检查项（--check）：
  1. 清单 schema 合法（必填字段、类型、取值）
  2. 架构维度可解析且非空（复用 defconfig 矩阵清单）
  3. workflow 与其矩阵 job 存在
  4. 矩阵三维与清单逐项相等
  5. 展开格数等于 expected_cells
  6. exclude 项合法、命中真实组合且与 workflow 一致
  7. fail-fast 关闭（单格失败不取消其余格）
  8. max-parallel 等于清单值
  9. 矩阵 job 超时等于清单值且不超过 60 分钟
 10. 每 workflow ≤2 job 且每个 job 均声明 timeout-minutes
 11. 编译门禁语义片段齐备（error 阻断 / Airymax warning 收敛）

Usage:
    python3 tools/ci-matrix/ci_matrix.py [options]

Options:
    --repo-root PATH    仓库根（默认：自动探测）
    --manifest PATH     矩阵清单（默认：tools/ci-matrix/manifest.yaml）
    --check             CI 模式：精简输出，仅退出码
    --list              展开矩阵，逐行输出 arch/compiler/config
    --summary FILE      读取逐格结果 JSON，归因失败并输出 PR 标签

Exit codes:
    0 — 全部检查通过 / 摘要全绿
    1 — 检查失败 / 摘要存在失败或缺失格
    2 — 用法或环境错误（清单缺失、YAML/JSON 非法、架构源不可读等）
"""

import argparse
import itertools
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺失时在入口统一报错
    yaml = None

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST_REL = "tools/ci-matrix/manifest.yaml"
JOB_MIN_CEILING = 60
DIMS = ("config", "arch", "compiler")
CONFIG_KEY = "config"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

MANIFEST_KEYS = frozenset(
    {"version", "arch_manifest", "matrix", "budget", "gates", "failures"})
MATRIX_KEYS = frozenset(
    {"workflow", "job", "configs", "compilers", "exclude",
     "expected_cells", "fail_fast", "max_parallel"})
BUDGET_KEYS = frozenset({"timeout_minutes", "max_jobs_per_workflow"})
GATE_KEYS = frozenset({"required_tokens"})
FAIL_KEYS = frozenset({"label", "reasons"})
REASON_KEYS = frozenset({"cross_arch", "compiler", "other"})


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
    """从脚本位置自动探测 agent-linux 仓库根。"""
    return Path(__file__).resolve().parents[2]


def load_manifest(path):
    """读取并解析矩阵清单；缺失或 YAML 非法时抛 ManifestError。"""
    if yaml is None:
        raise ManifestError(
            "PyYAML 不可用：请安装 pyyaml（见 requirements-dev.txt）")
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


def _missing(mapping, keys, scope):
    return [f"{scope}缺少字段: {key}"
            for key in sorted(keys - set(mapping))]


def _is_str_list(value):
    return (isinstance(value, list)
            and all(isinstance(item, str) for item in value))


def _matrix_types(matrix):
    """校验 matrix 段各字段类型；缺失字段由 _missing 单独报告。"""
    problems = []
    for key in ("configs", "compilers"):
        if key in matrix and not _is_str_list(matrix[key]):
            problems.append(f"matrix.{key} 必须是字符串列表")
    if "exclude" in matrix and not isinstance(matrix["exclude"], list):
        problems.append("matrix.exclude 必须是列表")
    for key in ("expected_cells", "max_parallel"):
        if key in matrix and not isinstance(matrix[key], int):
            problems.append(f"matrix.{key} 必须是整数")
    if "fail_fast" in matrix and not isinstance(matrix["fail_fast"], bool):
        problems.append("matrix.fail_fast 必须是布尔值")
    for key in ("workflow", "job"):
        if key in matrix and not isinstance(matrix[key], str):
            problems.append(f"matrix.{key} 必须是字符串")
    return problems


def _budget_types(budget):
    problems = []
    for key in BUDGET_KEYS:
        if key in budget and not isinstance(budget[key], int):
            problems.append(f"budget.{key} 必须是整数")
    return problems


def _gate_types(gates):
    if "required_tokens" not in gates:
        return []
    if not _is_str_list(gates["required_tokens"]):
        return ["gates.required_tokens 必须是字符串列表"]
    return []


def _fail_types(failures):
    problems = []
    if "label" in failures and not isinstance(failures["label"], str):
        problems.append("failures.label 必须是字符串")
    reasons = failures.get("reasons")
    if reasons is None:
        return problems
    if not isinstance(reasons, dict):
        return problems + ["failures.reasons 必须是映射"]
    missing = sorted(REASON_KEYS - set(reasons))
    return problems + [f"failures.reasons 缺少固定键: {key}"
                       for key in missing]


def _section_types(manifest):
    """校验各子段类型并返回问题列表。"""
    problems = []
    for key, checker in (("matrix", _matrix_types),
                         ("budget", _budget_types),
                         ("gates", _gate_types),
                         ("failures", _fail_types)):
        section = manifest.get(key)
        if isinstance(section, dict):
            problems.extend(checker(section))
        elif key in manifest:
            problems.append(f"{key} 必须是映射（mapping）")
    return problems


def check_schema(manifest):
    """清单 schema 合法。"""
    if not isinstance(manifest, dict):
        return CheckResult("清单 schema 合法", False,
                           "清单顶层必须是映射（mapping）")
    problems = _missing(manifest, MANIFEST_KEYS, "顶层")
    if not isinstance(manifest.get("version", ""), str):
        problems.append("version 必须是字符串")
    if not isinstance(manifest.get("arch_manifest", ""), str):
        problems.append("arch_manifest 必须是字符串")
    problems.extend(_section_types(manifest))
    return CheckResult("清单 schema 合法", not problems, "\n".join(problems))


# ─── Sources ───────────────────────────────────────────────────────────────


def resolve_arches(manifest, repo_root):
    """从 defconfig 矩阵清单解析 build=true 的架构维度。"""
    rel = manifest.get("arch_manifest", "")
    if not rel:
        raise ManifestError("arch_manifest 未登记")
    path = repo_root / rel
    if not path.is_file():
        raise ManifestError(f"架构清单不存在：{path}")
    data = load_manifest(path)
    arches = [entry.get("arch")
              for entry in data.get("architectures", [])
              if entry.get("build") is True]
    if not arches:
        raise ManifestError(f"{rel} 中没有 build=true 的架构")
    return arches


def load_workflow(manifest, repo_root):
    """读取矩阵 workflow；缺失或 YAML 非法时抛 ManifestError。"""
    rel = manifest["matrix"]["workflow"]
    path = repo_root / rel
    if not path.is_file():
        raise ManifestError(f"workflow 不存在：{path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ManifestError(f"workflow YAML 解析失败：{exc}")
    if not isinstance(data, dict):
        raise ManifestError(f"workflow 顶层必须是映射：{rel}")
    return data


def _jobs(workflow):
    jobs = workflow.get("jobs")
    return jobs if isinstance(jobs, dict) else {}


def _job(workflow, name):
    job = _jobs(workflow).get(name)
    return job if isinstance(job, dict) else None


def _strategy(job):
    strategy = job.get("strategy")
    return strategy if isinstance(strategy, dict) else {}


def _matrix(job):
    matrix = _strategy(job).get("matrix")
    return matrix if isinstance(matrix, dict) else {}


# ─── Checks ────────────────────────────────────────────────────────────────


def check_job(workflow, job, manifest):
    """workflow 与其矩阵 job 存在且声明 matrix。"""
    name = manifest["matrix"]["job"]
    if job is None:
        return CheckResult("workflow 与矩阵 job 存在", False,
                           f"{manifest['matrix']['workflow']} 中无 job: {name}")
    if not _matrix(job):
        return CheckResult("workflow 与矩阵 job 存在", False,
                           f"job {name} 未声明 strategy.matrix")
    return CheckResult("workflow 与矩阵 job 存在", True,
                       f"{name}: {len(_jobs(workflow))} job(s)")


def check_dims(job, manifest, arches):
    """矩阵三维与清单逐项相等。"""
    matrix = _matrix(job)
    spec = manifest["matrix"]
    problems = []
    for key, want in ((CONFIG_KEY, spec["configs"]),
                      ("arch", arches),
                      ("compiler", spec["compilers"])):
        got = matrix.get(key)
        if got != want:
            problems.append(f"{key}: 期望 {want}，实际 {got}")
    return CheckResult("矩阵维度与清单逐项相等", not problems,
                       "\n".join(problems))


def expand_cells(manifest, arches):
    """按清单展开矩阵单元，剔除 exclude 命中的组合。"""
    spec = manifest["matrix"]
    combos = [dict(zip(DIMS, combo))
              for combo in itertools.product(
                  spec["configs"], arches, spec["compilers"])]
    return [cell for cell in combos
            if not any(_match(cell, entry) for entry in spec["exclude"])]


def _match(cell, pattern):
    return (isinstance(pattern, dict)
            and all(cell.get(key) == value
                    for key, value in pattern.items()))


def check_cells(manifest, arches):
    """展开格数等于 expected_cells。"""
    spec = manifest["matrix"]
    cells = expand_cells(manifest, arches)
    total = (len(arches) * len(spec["configs"]) * len(spec["compilers"]))
    if len(cells) == spec["expected_cells"]:
        return CheckResult("展开格数等于 expected_cells", True,
                           f"{len(cells)} = {len(arches)}×"
                           f"{len(spec['configs'])}×"
                           f"{len(spec['compilers'])} − "
                           f"{total - len(cells)}")
    return CheckResult(
        "展开格数等于 expected_cells", False,
        f"展开 {len(cells)} 格，expected_cells {spec['expected_cells']}"
        f"（{len(arches)}×{len(spec['configs'])}×"
        f"{len(spec['compilers'])} − {total - len(cells)}）")


def _norm_excl(entries):
    normalized = []
    for entry in entries:
        if isinstance(entry, dict):
            normalized.append(tuple(sorted(entry.items())))
        else:
            normalized.append(("invalid", repr(entry)))
    return sorted(normalized)


def _excl_problems(entries, manifest, arches):
    """校验清单 exclude 项：非空、维度已知、命中真实组合。"""
    spec = manifest["matrix"]
    combos = [dict(zip(DIMS, combo))
              for combo in itertools.product(
                  spec["configs"], arches, spec["compilers"])]
    problems = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry:
            problems.append(f"exclude[{index}] 必须是非空映射")
            continue
        unknown = sorted(set(entry) - set(DIMS))
        if unknown:
            problems.append(f"exclude[{index}] 含未知维度: {unknown}")
            continue
        if not any(_match(combo, entry) for combo in combos):
            problems.append(f"exclude[{index}] 未命中任何组合: {entry}")
    return problems


def check_exclude(job, manifest, arches):
    """exclude 项合法且与 workflow 一致。"""
    spec = manifest["matrix"]
    problems = _excl_problems(spec["exclude"], manifest, arches)
    wf_entries = _matrix(job).get("exclude", [])
    if _norm_excl(wf_entries) != _norm_excl(spec["exclude"]):
        problems.append(f"workflow exclude 与清单不一致: {wf_entries}")
    return CheckResult("exclude 合法且与 workflow 一致", not problems,
                       "\n".join(problems))


def check_failfast(job, manifest):
    """fail-fast 关闭，单格失败不取消其余格。"""
    want = manifest["matrix"]["fail_fast"]
    got = _strategy(job).get("fail-fast")
    return CheckResult("fail-fast 关闭", got == want,
                       f"期望 {want}，实际 {got}")


def check_parallel(job, manifest):
    """max-parallel 等于清单值。"""
    want = manifest["matrix"]["max_parallel"]
    got = _strategy(job).get("max-parallel")
    return CheckResult("max-parallel 等于清单值", got == want,
                       f"期望 {want}，实际 {got}")


def check_timeout(job, manifest):
    """矩阵 job 超时等于清单值且不超过 OS-STD-TEST-011 上限。"""
    want = manifest["budget"]["timeout_minutes"]
    got = job.get("timeout-minutes")
    passed = isinstance(got, int) and got == want and got <= JOB_MIN_CEILING
    return CheckResult("矩阵 job 超时预算合规", passed,
                       f"期望 {want} 且 ≤{JOB_MIN_CEILING}，实际 {got}")


def check_jobcount(workflow, manifest):
    """每 workflow ≤2 job 且每个 job 均声明 timeout-minutes。"""
    limit = manifest["budget"]["max_jobs_per_workflow"]
    jobs = _jobs(workflow)
    problems = []
    if len(jobs) > limit:
        problems.append(f"job 数 {len(jobs)} 超过上限 {limit}")
    for name, job in sorted(jobs.items()):
        if not isinstance(job, dict) or not isinstance(
                job.get("timeout-minutes"), int):
            problems.append(f"job {name} 未声明整数 timeout-minutes")
    return CheckResult("job 数量与超时声明合规", not problems,
                       "\n".join(problems))


def _job_text(job):
    """拼接 job 内全部 run 脚本，保留原始引号与转义。"""
    steps = job.get("steps", [])
    return "\n".join(str(step.get("run", ""))
                     for step in steps if isinstance(step, dict))


GATE_CALL_RE = re.compile(r"ci/ci\.sh\s+([A-Za-z0-9][A-Za-z0-9_-]*)")
QUOTES_RE = re.compile(r"[\"']")


def _gate_text(text, repo_root):
    """并入 workflow 实际调用到的门禁脚本正文。

    编译门禁的判定逻辑已从 workflow 抽取到 ci/gates/*.sh（见 §3.3 的
    「内联逻辑只能在云端验证」约束），语义片段因此落在门禁脚本里。这里
    按 `ci/ci.sh <gate>` 调用反查 ci/gates/<gate>.sh，使门禁语义仍可被
    静态证明；调用关系来自 workflow 自身，不另设映射表。
    """
    if repo_root is None:
        return ""
    chunks = []
    for name in sorted(set(GATE_CALL_RE.findall(text))):
        path = Path(repo_root) / "ci" / "gates" / f"{name}.sh"
        try:
            chunks.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(chunks)


def _unquote(text):
    """去掉 shell 引号：`grep -E "error:"` 与 `grep -E 'error:'` 语义相同。"""
    return QUOTES_RE.sub("", text)


def check_tokens(job, manifest, repo_root=None):
    """编译门禁语义片段齐备（workflow 内联段 + 其调用的门禁脚本）。"""
    text = _job_text(job)
    haystack = _unquote(text + "\n" + _gate_text(text, repo_root))
    missing = [token for token in manifest["gates"]["required_tokens"]
               if _unquote(token) not in haystack]
    return CheckResult("编译门禁语义片段齐备", not missing,
                       "\n".join(f"缺少片段: {token}" for token in missing))


def build_checks(manifest, repo_root):
    """按清单 schema 结果决定是否继续执行内容类检查。"""
    schema = check_schema(manifest)
    if not schema.passed:
        return [schema]
    try:
        arches = resolve_arches(manifest, repo_root)
        workflow = load_workflow(manifest, repo_root)
    except ManifestError as exc:
        return [schema, CheckResult("矩阵源可解析", False, str(exc))]
    source = CheckResult("架构维度可解析", True,
                         "构建架构: " + ", ".join(arches))
    job = _job(workflow, manifest["matrix"]["job"])
    job_check = check_job(workflow, job, manifest)
    if job is None:
        return [schema, source, job_check]
    return [
        schema,
        source,
        job_check,
        check_dims(job, manifest, arches),
        check_cells(manifest, arches),
        check_exclude(job, manifest, arches),
        check_failfast(job, manifest),
        check_parallel(job, manifest),
        check_timeout(job, manifest),
        check_jobcount(workflow, manifest),
        check_tokens(job, manifest, repo_root),
    ]


# ─── Reports ───────────────────────────────────────────────────────────────


def print_report(results, manifest, check_mode):
    """打印检查报告。"""
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    failed = total - passed

    if check_mode:
        if failed == 0:
            print(f"CI-MATRIX: PASS ({passed}/{total} checks)")
        else:
            print(f"CI-MATRIX: FAIL ({passed}/{total} checks, "
                  f"{failed} failed)")
            for result in results:
                if not result.passed:
                    print(f"  FAIL: {result.name}")
                    for line in result.details.splitlines():
                        print(f"    {line}")
        return

    print("=" * 72)
    print("agent-linux AirymaxOS CI 编译矩阵一致性报告")
    print(f"清单版本: {manifest.get('version', '?')}")
    print("=" * 72)
    for result in results:
        marker = "[+]" if result.passed else "[-]"
        status = "PASS" if result.passed else "FAIL"
        print(f"{marker} {status}: {result.name}")
        if result.details:
            for line in result.details.splitlines():
                print(f"      {line}")
    print("-" * 72)
    if failed == 0:
        print(f"RESULT: PASS — 全部 {total} 项检查通过")
    else:
        print(f"RESULT: FAIL — {failed} 项检查未通过")
    print("-" * 72)


def list_cells(manifest, arches):
    """逐行输出矩阵展开结果。"""
    cells = expand_cells(manifest, arches)
    for cell in cells:
        print(f"{cell['arch']}/{cell['compiler']}/{cell[CONFIG_KEY]}")
    print(f"TOTAL: {len(cells)}")
    return EXIT_PASS


def _read_summary(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"结果摘要不存在：{path}")
    except OSError as exc:
        raise ManifestError(f"结果摘要不可读：{exc}")
    except json.JSONDecodeError as exc:
        raise ManifestError(f"结果摘要 JSON 非法：{exc}")
    entries = data.get("cells") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ManifestError("结果摘要必须是列表或含 cells 列表的映射")
    return entries


def _cell_key(cell):
    return tuple(cell.get(key) for key in DIMS)


def _index_summary(entries):
    """建立结果索引；缺失维度或重复格抛 ManifestError。"""
    seen = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or any(k not in entry for k in DIMS):
            raise ManifestError(f"cells[{index}] 缺少维度: {DIMS}")
        key = _cell_key(entry)
        if key in seen:
            raise ManifestError(f"cells[{index}] 重复格: {key}")
        seen[key] = entry.get("conclusion")
    return seen


def classify_cell(cell, passed, reasons):
    """按 §3.4 失败语义为单格归因。"""
    config, arch, compiler = (cell[key] for key in DIMS)
    if compiler == "clang" and (config, arch, "gcc") in passed:
        return reasons["compiler"]
    if arch != "x86_64" and (config, "x86_64", compiler) in passed:
        return reasons["cross_arch"]
    return reasons.get(config, reasons["other"])


def run_summary(manifest, arches, path):
    """比对逐格结果与矩阵定义，输出归因与 PR 标签。"""
    seen = _index_summary(_read_summary(path))
    expected = {_cell_key(cell): cell
                for cell in expand_cells(manifest, arches)}
    passed = {key for key, value in seen.items() if value == "success"}
    failed = {key: value for key, value in seen.items()
              if value != "success" and key in expected}
    missing = sorted(set(expected) - set(seen))
    unknown = sorted(set(seen) - set(expected))
    reasons = manifest["failures"]["reasons"]
    total = len(expected)
    problems = []

    for key in missing:
        problems.append(f"MISSING {key[1]}/{key[2]}/{key[0]}")
    for key in unknown:
        problems.append(f"UNKNOWN {key[1]}/{key[2]}/{key[0]}")
    for key, value in sorted(failed.items()):
        cell = expected.get(key, dict(zip(DIMS, key)))
        reason = classify_cell(cell, passed, reasons)
        problems.append(f"FAIL {key[1]}/{key[2]}/{key[0]}: {reason} "
                        f"({value})")

    if not problems:
        print(f"CI-MATRIX SUMMARY: PASS ({len(passed)}/{total} cells)")
        return EXIT_PASS
    print(f"CI-MATRIX SUMMARY: FAIL ({len(passed)}/{total} cells, "
          f"{len(failed)} failed, {len(missing)} missing, "
          f"{len(unknown)} unknown)")
    for line in problems:
        print(f"  {line}")
    print(f"  LABELS: {manifest['failures']['label']}")
    return EXIT_FAIL


# ─── Main ──────────────────────────────────────────────────────────────────


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="AirymaxOS CI 编译矩阵一致性检查器 "
                    "(OS-STD-TOOL-161, OS-STD-TEST-011)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", default=None,
                        help="agent-linux 仓库根（默认：自动探测）")
    parser.add_argument("--manifest", default=None,
                        help="矩阵清单（默认：tools/ci-matrix/manifest.yaml）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="CI 模式：精简输出")
    group.add_argument("--list", action="store_true",
                       help="展开矩阵并逐行输出")
    group.add_argument("--summary", metavar="FILE",
                       help="逐格结果 JSON，归因失败")
    args = parser.parse_args(argv)

    if yaml is None:
        print("ERROR: PyYAML 不可用，请安装 pyyaml", file=sys.stderr)
        return EXIT_USAGE

    repo_root = Path(args.repo_root) if args.repo_root else find_repo_root()
    if not repo_root.is_dir():
        print(f"ERROR: 仓库根不存在: {repo_root}", file=sys.stderr)
        return EXIT_USAGE

    manifest_path = (Path(args.manifest) if args.manifest
                     else repo_root / DEFAULT_MANIFEST_REL)
    try:
        manifest = load_manifest(manifest_path)
        arches = resolve_arches(manifest, repo_root)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.list:
        return list_cells(manifest, arches)
    if args.summary:
        try:
            return run_summary(manifest, arches, args.summary)
        except ManifestError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return EXIT_USAGE
    results = build_checks(manifest, repo_root)
    print_report(results, manifest, args.check)
    failed = sum(1 for result in results if not result.passed)
    return EXIT_FAIL if failed else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
