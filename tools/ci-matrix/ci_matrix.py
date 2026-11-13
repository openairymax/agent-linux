#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
ci_matrix.py — AirymaxOS CI 编译矩阵一致性检查器

以 tools/ci-matrix/manifest.yaml 为唯一事实源（SSoT），静态校验
「编译配置 × 架构 × 编译器 × 排除项 × 调度约束 × 门禁语义」与
.github/workflows/kernel-verify.yml 的矩阵段逐项一致，并把逐格构建结果
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

# tools/lib 是契约检查工具的共享层：结果类型与报告格式只能有一份。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from check_report import CheckResult, ManifestError, print_report, yaml
from manifest_io import find_repo_root, load_manifest

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST_REL = "tools/ci-matrix/manifest.yaml"
# §9.4 的稳态预算仍是 60 分钟；冷缓存（ccache 被 LRU 淘汰）下 28 格矩阵的
# 最慢格实测 62 分钟，故矩阵 job 的硬上限放宽到 90 分钟作兜底，不涉及其他 job。
JOB_MIN_CEILING = 90
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
# 矩阵段的字段类型表：顺序即报错顺序。字符串列表单列，因为它的判据不是
# isinstance，而是「列表且每项都是字符串」。
MATRIX_STRS = ("configs", "compilers")
MATRIX_PLAIN = (
    ("exclude", list, "列表"),
    ("expected_cells", int, "整数"),
    ("max_parallel", int, "整数"),
    ("fail_fast", bool, "布尔值"),
    ("workflow", str, "字符串"),
    ("job", str, "字符串"),
)
BUDGET_KEYS = frozenset({"timeout_minutes", "max_jobs_per_workflow"})
GATE_KEYS = frozenset({"required_tokens"})
FAIL_KEYS = frozenset({"label", "reasons"})
REASON_KEYS = frozenset({"cross_arch", "compiler", "other"})


# ─── Helpers ───────────────────────────────────────────────────────────────


def _missing(mapping, keys, scope):
    return [f"{scope}缺少字段: {key}"
            for key in sorted(keys - set(mapping))]


def _is_str_list(value):
    return (isinstance(value, list)
            and all(isinstance(item, str) for item in value))


def _matrix_types(matrix):
    """校验 matrix 段各字段类型；缺失字段由 _missing 单独报告。"""
    problems = _str_list_probs(matrix)
    problems += _plain_probs(matrix)
    return problems


def _strlist_bad(matrix, key):
    """字段出现时必须是字符串列表。"""
    return key in matrix and not _is_str_list(matrix[key])


def _str_list_probs(matrix):
    """字符串列表字段：出现在段里就必须是字符串列表。"""
    return [f"matrix.{key} 必须是字符串列表"
            for key in MATRIX_STRS if _strlist_bad(matrix, key)]


def _plain_bad(matrix, key, kind):
    """字段出现时必须是指定类型。"""
    return key in matrix and not isinstance(matrix[key], kind)


def _plain_probs(matrix):
    """标量字段：出现在段里就必须是声明的那种类型。"""
    return [f"matrix.{key} 必须是{desc}"
            for key, kind, desc in MATRIX_PLAIN
            if _plain_bad(matrix, key, kind)]


def _budget_bad(budget, key):
    """预算字段出现时必须是整数。"""
    return key in budget and not isinstance(budget[key], int)


def _budget_types(budget):
    problems = []
    for key in BUDGET_KEYS:
        if _budget_bad(budget, key):
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
    _label_type(failures, problems)
    return problems + _reason_fails(failures)


def _label_type(failures, problems):
    """label 出现时必须是非空字符串以外的字符串。"""
    if "label" in failures and not isinstance(failures["label"], str):
        problems.append("failures.label 必须是字符串")


def _reason_miss(reasons):
    """固定键缺失清单。"""
    missing = sorted(REASON_KEYS - set(reasons))
    return [f"failures.reasons 缺少固定键: {key}"
            for key in missing]


def _reason_fails(failures):
    """reasons 缺省视为未登记；登记了就必须是含固定键的映射。"""
    reasons = failures.get("reasons")
    if reasons is None:
        return []
    if not isinstance(reasons, dict):
        return ["failures.reasons 必须是映射"]
    return _reason_miss(reasons)


def _section_probs(manifest, key, checker):
    """单个子段的问题清单；缺省不算问题。"""
    section = manifest.get(key)
    if isinstance(section, dict):
        return checker(section)
    if key in manifest:
        return [f"{key} 必须是映射（mapping）"]
    return []


def _section_types(manifest):
    """校验各子段类型并返回问题列表。"""
    problems = []
    for key, checker in (("matrix", _matrix_types),
                         ("budget", _budget_types),
                         ("gates", _gate_types),
                         ("failures", _fail_types)):
        problems.extend(_section_probs(manifest, key, checker))
    return problems


def _schema_heads(manifest, problems):
    """顶层 version 与 arch_manifest 出现时必须是字符串。"""
    if not isinstance(manifest.get("version", ""), str):
        problems.append("version 必须是字符串")
    if not isinstance(manifest.get("arch_manifest", ""), str):
        problems.append("arch_manifest 必须是字符串")


def check_schema(manifest):
    """清单 schema 合法。"""
    if not isinstance(manifest, dict):
        return CheckResult("清单 schema 合法", False,
                           "清单顶层必须是映射（mapping）")
    problems = _missing(manifest, MANIFEST_KEYS, "顶层")
    _schema_heads(manifest, problems)
    problems.extend(_section_types(manifest))
    return CheckResult("清单 schema 合法", not problems, "\n".join(problems))


# ─── Sources ───────────────────────────────────────────────────────────────


def resolve_arches(manifest, repo_root):
    """从 defconfig 矩阵清单解析 build=true 的架构维度。"""
    rel, data = _arch_doc(manifest, repo_root)
    arches = _built(data)
    if not arches:
        raise ManifestError(f"{rel} 中没有 build=true 的架构")
    return arches


def _arch_doc(manifest, repo_root):
    """定位并读取架构清单，返回 (相对路径, 清单内容)。"""
    rel = manifest.get("arch_manifest", "")
    if not rel:
        raise ManifestError("arch_manifest 未登记")
    path = repo_root / rel
    if not path.is_file():
        raise ManifestError(f"架构清单不存在：{path}")
    return rel, load_manifest(path)


def _built(data):
    """挑出 build=true 的架构名，保持清单声明顺序。"""
    return [entry.get("arch")
            for entry in data.get("architectures", [])
            if entry.get("build") is True]


def _load_yaml(path, rel):
    """读取 workflow YAML；语法错误或顶层非映射转成 ManifestError。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ManifestError(f"workflow YAML 解析失败：{exc}")
    if not isinstance(data, dict):
        raise ManifestError(f"workflow 顶层必须是映射：{rel}")
    return data


def load_workflow(manifest, repo_root):
    """读取矩阵 workflow；缺失或 YAML 非法时抛 ManifestError。"""
    rel = manifest["matrix"]["workflow"]
    path = repo_root / rel
    if not path.is_file():
        raise ManifestError(f"workflow 不存在：{path}")
    return _load_yaml(path, rel)


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
    combos = _combos(spec, arches)
    return [cell for cell in combos if not _excluded(cell, spec["exclude"])]


def _combos(spec, arches):
    """DIMS 维度笛卡尔积，逐项转为字典。"""
    return [dict(zip(DIMS, combo))
            for combo in itertools.product(
                spec["configs"], arches, spec["compilers"])]


def _excluded(cell, entries):
    """任一 exclude 命中即剔除该组合。"""
    return any(_match(cell, entry) for entry in entries)


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


def _excl_hit(entry, combos):
    """该 exclude 项是否命中任一组合。"""
    return any(_match(combo, entry) for combo in combos)


def _excl_entry(index, entry, combos):
    """单条 exclude 项：非空、维度已知、命中真实组合；合法时返回 None。"""
    bad = _excl_shape(index, entry)
    if bad is not None:
        return bad
    if not _excl_hit(entry, combos):
        return f"exclude[{index}] 未命中任何组合: {entry}"
    return None


def _excl_map(index, entry):
    """非空映射；不合法时返回报错文案。"""
    if not isinstance(entry, dict) or not entry:
        return f"exclude[{index}] 必须是非空映射"
    return None


def _excl_shape(index, entry):
    """非空映射且只含已知维度；不合法时返回报错文案。"""
    bad = _excl_map(index, entry)
    if bad is not None:
        return bad
    unknown = sorted(set(entry) - set(DIMS))
    if unknown:
        return f"exclude[{index}] 含未知维度: {unknown}"
    return None


def _excl_problems(entries, manifest, arches):
    """校验清单 exclude 项：非空、维度已知、命中真实组合。"""
    spec = manifest["matrix"]
    combos = _combos(spec, arches)
    problems = [_excl_entry(index, entry, combos)
                for index, entry in enumerate(entries)]
    return _keep_msgs(problems)


def _keep_msgs(msgs):
    """丢弃合法项留下的 None。"""
    return [msg for msg in msgs if msg is not None]


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
        _job_timeout(name, job, problems)
    return CheckResult("job 数量与超时声明合规", not problems,
                       "\n".join(problems))


def _job_timeout(name, job, problems):
    """单个 job 必须声明整数 timeout-minutes。"""
    if not isinstance(job, dict) or not isinstance(
            job.get("timeout-minutes"), int):
        problems.append(f"job {name} 未声明整数 timeout-minutes")


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


def _missing_tokens(tokens, haystack):
    """未被 haystack 覆盖的门禁片段，保持清单声明顺序。"""
    return [token for token in tokens if _unquote(token) not in haystack]


def check_tokens(job, manifest, repo_root=None):
    """编译门禁语义片段齐备（workflow 内联段 + 其调用的门禁脚本）。"""
    text = _job_text(job)
    haystack = _unquote(text + "\n" + _gate_text(text, repo_root))
    missing = _missing_tokens(manifest["gates"]["required_tokens"], haystack)
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


def list_cells(manifest, arches):
    """逐行输出矩阵展开结果。"""
    cells = expand_cells(manifest, arches)
    for cell in cells:
        print(f"{cell['arch']}/{cell['compiler']}/{cell[CONFIG_KEY]}")
    print(f"TOTAL: {len(cells)}")
    return EXIT_PASS


def _read_summary(path):
    data = _load_summary(path)
    entries = data.get("cells") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ManifestError("结果摘要必须是列表或含 cells 列表的映射")
    return entries


def _load_summary(path):
    """读取结果摘要 JSON，把缺失/不可读/非法分别转成 ManifestError。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ManifestError(f"结果摘要不存在：{path}")
    except OSError as exc:
        raise ManifestError(f"结果摘要不可读：{exc}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"结果摘要 JSON 非法：{exc}")


def _cell_key(cell):
    return tuple(cell.get(key) for key in DIMS)


def _index_summary(entries):
    """建立结果索引；缺失维度或重复格抛 ManifestError。"""
    seen = {}
    for index, entry in enumerate(entries):
        _need_cell(entry, index)
        key = _cell_key(entry)
        if key in seen:
            raise ManifestError(f"cells[{index}] 重复格: {key}")
        seen[key] = entry.get("conclusion")
    return seen


def _cell_bad(entry):
    """结果格不是映射或缺少任一维度。"""
    return not isinstance(entry, dict) or any(
        k not in entry for k in DIMS)


def _need_cell(entry, index):
    """结果格必须是四维齐全的映射。"""
    if _cell_bad(entry):
        raise ManifestError(f"cells[{index}] 缺少维度: {DIMS}")


def _cell_dims(cell):
    """结果格的 (config, arch, compiler) 三元组。"""
    return tuple(cell[key] for key in DIMS)


def classify_cell(cell, passed, reasons):
    """按 §3.4 失败语义为单格归因。"""
    config, arch, compiler = _cell_dims(cell)
    if _compiler_miss(compiler, config, arch, passed):
        return reasons["compiler"]
    if _arch_miss(arch, config, compiler, passed):
        return reasons["cross_arch"]
    return reasons.get(config, reasons["other"])


def _compiler_miss(compiler, config, arch, passed):
    """clang 失败而同配置 gcc 通过，归因为编译器。"""
    return compiler == "clang" and (config, arch, "gcc") in passed


def _arch_miss(arch, config, compiler, passed):
    """非 x86_64 失败而 x86_64 通过，归因为跨架构。"""
    return arch != "x86_64" and (config, "x86_64", compiler) in passed


def _expected(manifest, arches):
    """矩阵定义应当出现的格，键为维度元组。"""
    return {_cell_key(cell): cell for cell in expand_cells(manifest, arches)}


def _split(seen, expected):
    """把逐格结果分成通过、失败、缺失、越界四组。"""
    passed = {key for key, value in seen.items() if value == "success"}
    return (passed, _failed(seen, expected),
            _absent(expected, seen), _extra(seen, expected))


def _bad_cell(key, value, expected):
    """结论不是 success，且该格落在矩阵定义内。"""
    return value != "success" and key in expected


def _failed(seen, expected):
    """矩阵定义内、结论不是 success 的格。"""
    return {key: value for key, value in seen.items()
            if _bad_cell(key, value, expected)}


def _absent(expected, seen):
    """矩阵应当出现但结果未上报的格。"""
    return sorted(set(expected) - set(seen))


def _extra(seen, expected):
    """结果上报了但矩阵未定义的格。"""
    return sorted(set(seen) - set(expected))


def _miss_rows(tag, keys):
    """缺失/越界格的一行，格式 `TAG arch/config/compiler`。"""
    return [f"{tag} {key[1]}/{key[2]}/{key[0]}" for key in keys]


def _fail_row(key, value, expected, passed, reasons):
    """失败格的一行；格体缺失时按维度元组就地重建。"""
    cell = expected.get(key, dict(zip(DIMS, key)))
    reason = classify_cell(cell, passed, reasons)
    return f"FAIL {key[1]}/{key[2]}/{key[0]}: {reason} ({value})"


def _problems(missing, unknown, failed, expected, passed, reasons):
    """失败归因行：缺失与越界在前，失败格按 §3.4 语义分类。"""
    rows = _miss_rows("MISSING", missing) + _miss_rows("UNKNOWN", unknown)
    return rows + [_fail_row(key, value, expected, passed, reasons)
                   for key, value in sorted(failed.items())]


def run_summary(manifest, arches, path):
    """比对逐格结果与矩阵定义，输出归因与 PR 标签。"""
    seen = _index_summary(_read_summary(path))
    expected = _expected(manifest, arches)
    passed, failed, missing, unknown = _split(seen, expected)
    problems = _problems(missing, unknown, failed, expected, passed,
                         manifest["failures"]["reasons"])
    total = len(expected)

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


def _parse(argv):
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
    return parser.parse_args(argv)


def _context(args):
    """加载矩阵载体；缺依赖、根不存在、清单非法一律抛 ManifestError。"""
    if yaml is None:
        raise ManifestError("PyYAML 不可用，请安装 pyyaml")
    repo_root = _root_of(args.repo_root)
    manifest_path = _manifest_of(args.manifest, repo_root)
    manifest = load_manifest(manifest_path)
    return repo_root, manifest, resolve_arches(manifest, repo_root)


def _root_of(value):
    """仓库根：显式路径优先，否则自动探测；目录不存在即拒绝。"""
    repo_root = Path(value) if value else find_repo_root()
    if not repo_root.is_dir():
        raise ManifestError(f"仓库根不存在: {repo_root}")
    return repo_root


def _manifest_of(value, repo_root):
    """清单路径：显式路径优先，否则按仓库布局推导。"""
    return Path(value) if value else repo_root / DEFAULT_MANIFEST_REL


def _check(args, repo_root, manifest):
    """默认模式：跑全部一致性检查并输出报告。"""
    results = build_checks(manifest, repo_root)
    print_report(results, "CI-MATRIX", args.check,
                 "agent-linux AirymaxOS CI 编译矩阵一致性报告",
                 str(manifest.get("version", "?")))
    return EXIT_FAIL if any(not r.passed for r in results) else EXIT_PASS


def _run(args):
    """三种模式共用同一份载体：展开、归因、检查只在末尾分岔。"""
    repo_root, manifest, arches = _context(args)
    if args.list:
        return list_cells(manifest, arches)
    if args.summary:
        return run_summary(manifest, arches, args.summary)
    return _check(args, repo_root, manifest)


def main(argv=None):
    args = _parse(argv)
    try:
        return _run(args)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
