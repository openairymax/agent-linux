#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
nightly.py — AirymaxOS nightly workflow 契约检查器

以 tools/nightly/manifest.yaml 为唯一事实源（SSoT），静态校验
.github/workflows/nightly.yml 的触发条件、单实例并发、最小权限、
job 拓扑与超时预算、shell 变量定义、deferred helper 守卫以及
CI 总时长检测（OS-STD-TEST-011）是否与清单逐项一致。

设计原则（与项目铁律一致）：
  * 清单是唯一事实源；workflow 的任何结构性漂移都由本工具检出，
    不依赖人工复核。
  * 只做「清单 ↔ workflow ↔ 文档」的交叉比对，不代替 CI 执行测试。
  * 严禁桩实现：所有检查读取真实文件，判定可复现，失败给出证据。
  * deferred helper 必须显式登记；脚本一旦落地而清单仍标 deferred，
    检查立即失败，避免「存在性守卫长期掩盖已实现能力」的技术债。

检查项（--check）：
  1. 清单 schema 合法（必填字段、类型、取值）
  2. 触发条件一致（cron / workflow_dispatch / 禁止 PR 触发）
  3. 单实例并发（固定 group、cancel-in-progress 关闭）
  4. 最小权限（contents: read，且无 write 授权）
  5. job 集合、依赖与 always() 语义一致
  6. runner 标签与清单一致
  7. 每个 job 声明 timeout-minutes 且不超过托管 runner 硬上限
  8. shell 变量均已定义（禁止引用未定义的 $COMMIT 等）
  9. helper 已登记在 tools/README.md 的 helper surface
 10. helper 的守卫、SKIP 标记、argv 契约与落地状态一致
 11. CI 总时长检测调用携带 --max-minutes 60（OS-STD-TEST-011）

Usage:
    python3 tools/nightly/nightly.py [options]

Options:
    --repo-root PATH    仓库根（默认：自动探测）
    --manifest PATH     nightly 清单（默认：tools/nightly/manifest.yaml）
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
from typing import Any, Mapping

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺失时在入口统一报错
    yaml = None

__all__ = ["CheckResult", "NightlyError", "main"]

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST_REL = "tools/nightly/manifest.yaml"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

MANIFEST_KEYS = frozenset({
    "version", "workflow", "helper_surface", "triggers", "concurrency",
    "permissions", "runner", "jobs", "helpers", "budget",
})
TRIGGER_KEYS = frozenset({"cron", "workflow_dispatch", "forbidden"})
FLIGHT_KEYS = frozenset({"group", "cancel_in_progress"})
RUNNER_KEYS = frozenset({"label", "hosted_ceiling_minutes"})
JOB_KEYS = frozenset({"name", "timeout_minutes", "needs", "helpers"})
HELPER_KEYS = frozenset({"path", "status", "argv"})
BUDGET_KEYS = frozenset({"max_minutes", "helper", "argv"})
HELPER_STATES = frozenset({"deferred", "available"})

NAME_SCHEMA = "清单 schema"
NAME_TRIGGER = "触发条件"
NAME_FLIGHT = "单实例并发"
NAME_PERMS = "最小权限"
NAME_JOBS = "job 拓扑"
NAME_RUNNER = "runner 选择"
NAME_TIMEOUT = "超时预算"
NAME_VARS = "shell 变量"
NAME_SURFACE = "helper 登记"
NAME_HELPERS = "helper 契约"
NAME_BUDGET = "CI 总时长预算"

_EXPR_RE = re.compile(r"\$\{\{.*?\}\}", re.S)
_VAR_RE = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)\}?")
_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=", re.M)
_VAR_PREFIXES = ("GITHUB_", "RUNNER_", "LC_")
_VAR_ALLOWED = frozenset({
    "CI", "HOME", "PATH", "PWD", "SHELL", "TMPDIR", "USER", "LANG", "IFS",
})


# ─── Data model ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckResult:
    """单项检查结果。

    Attributes:
        name: 检查项名称。
        passed: 是否通过。
        details: 失败证据或补充说明。
    """

    name: str
    passed: bool
    details: str = ""


class NightlyError(Exception):
    """清单或 workflow 不可读取、不可解析。"""


# ─── Loading ───────────────────────────────────────────────────────────────


def find_root() -> Path:
    """从脚本位置自动探测 agent-linux 仓库根。"""
    return Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 映射；缺失或解析失败时抛 NightlyError。"""
    if yaml is None:
        raise NightlyError(
            "PyYAML 不可用：请安装 pyyaml（见 requirements-dev.txt）")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError:
        raise NightlyError(f"文件不存在：{path}") from None
    except yaml.YAMLError as exc:
        raise NightlyError(f"YAML 解析失败（{path}）：{exc}") from exc
    if not isinstance(data, dict):
        raise NightlyError(f"顶层必须是映射（mapping）：{path}")
    return data


def _triggers(wf: Mapping[str, Any]) -> Any:
    """取 on 段；PyYAML 1.1 会把裸 on 解析为布尔真。"""
    value = wf.get("on")
    return wf.get(True) if value is None else value


def _jobs(wf: Mapping[str, Any]) -> dict[str, Any]:
    """取 jobs 段，非映射时返回空表。"""
    value = wf.get("jobs")
    return value if isinstance(value, dict) else {}


def _steps(job: Any) -> list[dict[str, Any]]:
    """取 job 的 step 列表，过滤非映射条目。"""
    value = job.get("steps") if isinstance(job, dict) else None
    if not isinstance(value, list):
        return []
    return [step for step in value if isinstance(step, dict)]


def _scripts(job: Any) -> list[str]:
    """取 job 内全部 run 脚本。"""
    return [step["run"] for step in _steps(job)
            if isinstance(step.get("run"), str)]


def _text(job: Any) -> str:
    """把 job 内全部 run 脚本拼为单个文本，供契约匹配。"""
    return "\n".join(_scripts(job))


# ─── Schema ────────────────────────────────────────────────────────────────


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str_list(value: Any) -> bool:
    return (isinstance(value, list)
            and all(isinstance(item, str) for item in value))


def _need(mapping: Mapping[str, Any], keys: frozenset[str],
          scope: str) -> list[str]:
    """报告缺失的必填字段。"""
    return [f"{scope}缺少字段: {key}" for key in sorted(keys - set(mapping))]


def _result(name: str, problems: list[str]) -> CheckResult:
    return CheckResult(name, not problems, "; ".join(problems))


def _schema_trigger(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["triggers 必须是映射"]
    problems = _need(value, TRIGGER_KEYS, "triggers.")
    if "cron" in value and not isinstance(value["cron"], str):
        problems.append("triggers.cron 必须是字符串")
    if ("workflow_dispatch" in value
            and not isinstance(value["workflow_dispatch"], bool)):
        problems.append("triggers.workflow_dispatch 必须是布尔值")
    if "forbidden" in value and not _is_str_list(value["forbidden"]):
        problems.append("triggers.forbidden 必须是字符串列表")
    return problems


def _schema_flight(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["concurrency 必须是映射"]
    problems = _need(value, FLIGHT_KEYS, "concurrency.")
    if "group" in value and not isinstance(value["group"], str):
        problems.append("concurrency.group 必须是字符串")
    if ("cancel_in_progress" in value
            and not isinstance(value["cancel_in_progress"], bool)):
        problems.append("concurrency.cancel_in_progress 必须是布尔值")
    return problems


def _schema_perms(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["permissions 必须是映射"]
    return [f"permissions.{key} 必须是字符串"
            for key, item in value.items() if not isinstance(item, str)]


def _schema_runner(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["runner 必须是映射"]
    problems = _need(value, RUNNER_KEYS, "runner.")
    if "label" in value and not isinstance(value["label"], str):
        problems.append("runner.label 必须是字符串")
    size = value.get("hosted_ceiling_minutes")
    if "hosted_ceiling_minutes" in value and (not _is_int(size) or size <= 0):
        problems.append("runner.hosted_ceiling_minutes 必须是正整数")
    return problems


def _schema_jobs(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["jobs 必须是非空列表"]
    problems: list[str] = []
    names: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            problems.append("jobs 条目必须是映射")
            continue
        problems.extend(_need(entry, JOB_KEYS, "jobs[]."))
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            problems.append("jobs[].name 必须是非空字符串")
            continue
        if name in names:
            problems.append(f"jobs[].name 重复: {name}")
        names.add(name)
        if not _is_int(entry.get("timeout_minutes")):
            problems.append(f"jobs[{name}].timeout_minutes 必须是整数")
        for key in ("needs", "helpers"):
            if not _is_str_list(entry.get(key)):
                problems.append(f"jobs[{name}].{key} 必须是字符串列表")
        if "always" in entry and not isinstance(entry["always"], bool):
            problems.append(f"jobs[{name}].always 必须是布尔值")
    return problems


def _schema_helpers(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["helpers 必须是映射"]
    problems: list[str] = []
    for key, entry in value.items():
        scope = f"helpers.{key}."
        if not isinstance(entry, dict):
            problems.append(f"helpers.{key} 必须是映射")
            continue
        problems.extend(_need(entry, HELPER_KEYS, scope))
        if "path" in entry and not isinstance(entry["path"], str):
            problems.append(f"{scope}path 必须是字符串")
        if entry.get("status") not in HELPER_STATES:
            problems.append(
                f"{scope}status 必须是 {sorted(HELPER_STATES)} 之一")
        if "argv" in entry and not _is_str_list(entry["argv"]):
            problems.append(f"{scope}argv 必须是字符串列表")
    return problems


def _schema_budget(value: Any, helpers: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["budget 必须是映射"]
    problems = _need(value, BUDGET_KEYS, "budget.")
    size = value.get("max_minutes")
    if not _is_int(size) or size <= 0:
        problems.append("budget.max_minutes 必须是正整数")
    if "argv" in value and not _is_str_list(value["argv"]):
        problems.append("budget.argv 必须是字符串列表")
    ref = value.get("helper")
    known = helpers if isinstance(helpers, dict) else {}
    if isinstance(ref, str) and ref not in known:
        problems.append(f"budget.helper 未登记: {ref}")
    if _is_int(size) and _is_str_list(value.get("argv")):
        if str(size) not in value["argv"]:
            problems.append("budget.argv 必须包含 max_minutes 的取值")
    return problems


def check_schema(man: Mapping[str, Any]) -> CheckResult:
    """校验清单 schema；失败时其余检查短路。"""
    problems = _need(man, MANIFEST_KEYS, "清单")
    for key in ("version", "workflow", "helper_surface"):
        if key in man and (not isinstance(man[key], str) or not man[key]):
            problems.append(f"{key} 必须是非空字符串")
    sections = (
        ("triggers", _schema_trigger),
        ("concurrency", _schema_flight),
        ("permissions", _schema_perms),
        ("runner", _schema_runner),
        ("jobs", _schema_jobs),
        ("helpers", _schema_helpers),
    )
    for key, validator in sections:
        if key in man:
            problems.extend(validator(man[key]))
    if "budget" in man:
        problems.extend(_schema_budget(man["budget"], man.get("helpers")))
    return _result(NAME_SCHEMA, problems)


# ─── Checks ────────────────────────────────────────────────────────────────


def check_trigger(man: Mapping[str, Any],
                  wf: Mapping[str, Any]) -> CheckResult:
    """校验触发条件：唯一 cron、可手动触发、禁止 PR 触发。"""
    rules = man["triggers"]
    triggers = _triggers(wf)
    if not isinstance(triggers, dict):
        return CheckResult(NAME_TRIGGER, False, "on 段必须是映射")
    problems: list[str] = []
    schedule = triggers.get("schedule")
    crons = [item.get("cron") for item in schedule
             if isinstance(item, dict)] if isinstance(schedule, list) else []
    if crons != [rules["cron"]]:
        problems.append(f"schedule.cron 期望 {[rules['cron']]}，实际 {crons}")
    if "workflow_dispatch" not in triggers:
        problems.append("缺少 workflow_dispatch 手动触发")
    for key in rules["forbidden"]:
        if key in triggers:
            problems.append(f"nightly 不得由 {key} 触发（结果不得阻断 PR）")
    return _result(NAME_TRIGGER, problems)


def check_flight(man: Mapping[str, Any],
                 wf: Mapping[str, Any]) -> CheckResult:
    """校验 nightly 全局单实例：固定 group 且不取消进行中的运行。"""
    rules = man["concurrency"]
    value = wf.get("concurrency")
    if not isinstance(value, dict):
        return CheckResult(NAME_FLIGHT, False, "缺少 workflow 级 concurrency")
    problems: list[str] = []
    if value.get("group") != rules["group"]:
        problems.append(
            f"concurrency.group 期望 {rules['group']}，"
            f"实际 {value.get('group')}")
    if value.get("cancel-in-progress") is not rules["cancel_in_progress"]:
        problems.append("nightly 必须显式 cancel-in-progress: false")
    return _result(NAME_FLIGHT, problems)


def check_perms(man: Mapping[str, Any],
                wf: Mapping[str, Any]) -> CheckResult:
    """校验 workflow 级最小权限。"""
    value = wf.get("permissions")
    if not isinstance(value, dict):
        return CheckResult(NAME_PERMS, False, "缺少 workflow 级 permissions")
    problems: list[str] = []
    for key, expected in man["permissions"].items():
        if value.get(key) != expected:
            problems.append(
                f"permissions.{key} 期望 {expected}，实际 {value.get(key)}")
    for key, item in value.items():
        if isinstance(item, str) and item.endswith("write"):
            problems.append(f"permissions.{key} 不得授予写权限")
    return _result(NAME_PERMS, problems)


def check_jobs(man: Mapping[str, Any],
               wf: Mapping[str, Any]) -> CheckResult:
    """校验 job 集合、依赖关系与 always() 语义。"""
    jobs = _jobs(wf)
    expected = [item["name"] for item in man["jobs"]]
    problems = [f"缺少 job: {name}" for name in expected if name not in jobs]
    problems += [f"未登记 job: {name}" for name in jobs if name not in expected]
    for spec in man["jobs"]:
        job = jobs.get(spec["name"])
        if not isinstance(job, dict):
            continue
        problems.extend(_job_probs(spec, job))
    return _result(NAME_JOBS, problems)


def _job_probs(spec: Mapping[str, Any], job: Mapping[str, Any]) -> list[str]:
    """校验单个 job 的 depends 与触发条件。"""
    name = spec["name"]
    problems: list[str] = []
    needs = job.get("needs")
    actual = {needs} if isinstance(needs, str) else set(needs or [])
    if actual != set(spec["needs"]):
        problems.append(
            f"{name}.needs 期望 {spec['needs']}，实际 {sorted(actual)}")
    cond = str(job.get("if", ""))
    if spec.get("always") and "always()" not in cond:
        problems.append(f"{name} 缺少 if: always()")
    if not spec.get("always") and "always()" in cond:
        problems.append(f"{name} 不应无条件执行")
    return problems


def check_runner(man: Mapping[str, Any],
                 wf: Mapping[str, Any]) -> CheckResult:
    """校验每个 job 的 runner 标签。"""
    label = man["runner"]["label"]
    jobs = _jobs(wf)
    problems: list[str] = []
    for spec in man["jobs"]:
        runs = jobs.get(spec["name"], {}).get("runs-on")
        labels = {runs} if isinstance(runs, str) else set(runs or [])
        if label not in labels:
            problems.append(
                f"{spec['name']}.runs-on 期望 {label}，实际 {runs}")
    return _result(NAME_RUNNER, problems)


def check_timeout(man: Mapping[str, Any],
                  wf: Mapping[str, Any]) -> CheckResult:
    """校验 job 超时：与清单相等且不超过托管 runner 硬上限。"""
    runner = man["runner"]
    hosted = runner["label"].endswith("-latest")
    ceiling = runner["hosted_ceiling_minutes"]
    jobs = _jobs(wf)
    problems: list[str] = []
    for spec in man["jobs"]:
        actual = jobs.get(spec["name"], {}).get("timeout-minutes")
        if not _is_int(actual):
            problems.append(f"{spec['name']} 缺少整数 timeout-minutes")
            continue
        if actual != spec["timeout_minutes"]:
            problems.append(
                f"{spec['name']}.timeout-minutes 期望 "
                f"{spec['timeout_minutes']}，实际 {actual}")
        if hosted and actual > ceiling:
            problems.append(
                f"{spec['name']}.timeout-minutes 超过托管 runner 上限 "
                f"{ceiling}")
    return _result(NAME_TIMEOUT, problems)


def _env_keys(*layers: Any) -> set[str]:
    """合并多层 env 的键。"""
    keys: set[str] = set()
    for layer in layers:
        if isinstance(layer, dict):
            keys.update(str(key) for key in layer)
    return keys


def _var_refs(script: str) -> set[str]:
    """提取脚本引用的全大写 shell 变量（忽略 GitHub 表达式）。"""
    return set(_VAR_RE.findall(_EXPR_RE.sub("", script)))


def _var_defs(script: str) -> set[str]:
    """提取脚本内联赋值的全大写 shell 变量。"""
    return set(_ASSIGN_RE.findall(script))


def check_vars(man: Mapping[str, Any],
               wf: Mapping[str, Any]) -> CheckResult:
    """校验脚本引用的 shell 变量都有定义。"""
    jobs = _jobs(wf)
    base = _env_keys(wf.get("env"))
    problems: set[tuple[str, str]] = set()
    for spec in man["jobs"]:
        job = jobs.get(spec["name"], {})
        job_env = _env_keys(job.get("env"))
        for step in _steps(job):
            script = step.get("run")
            if not isinstance(script, str):
                continue
            defined = base | job_env | _env_keys(step.get("env"))
            for name in _var_refs(script) - defined - _var_defs(script):
                if name in _VAR_ALLOWED or name.startswith(_VAR_PREFIXES):
                    continue
                problems.add((spec["name"], name))
    details = [f"{job} 引用了未定义的变量 ${name}"
               for job, name in sorted(problems)]
    return _result(NAME_VARS, details)


def check_surface(man: Mapping[str, Any],
                  wf: Mapping[str, Any], root: Path) -> CheckResult:
    """校验 helper 脚本已登记在 helper surface 文档。"""
    rel = man["helper_surface"]
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except OSError as exc:
        return CheckResult(NAME_SURFACE, False, f"无法读取 {rel}: {exc}")
    problems = [
        f"{Path(entry['path']).name} 未登记在 {rel}"
        for entry in man["helpers"].values()
        if f"`{Path(entry['path']).name}`" not in text
    ]
    return _result(NAME_SURFACE, problems)


def check_helpers(man: Mapping[str, Any],
                  wf: Mapping[str, Any], root: Path) -> CheckResult:
    """校验 helper 的调用、守卫、SKIP 标记与落地状态。"""
    jobs = _jobs(wf)
    problems: list[str] = []
    for spec in man["jobs"]:
        text = _text(jobs.get(spec["name"], {}))
        for key in spec["helpers"]:
            entry = man["helpers"].get(key)
            if entry is None:
                problems.append(
                    f"{spec['name']} 引用了未登记的 helper: {key}")
                continue
            problems.extend(
                _helper_probs(spec["name"], entry, text, root))
    return _result(NAME_HELPERS, problems)


def _helper_probs(job: str, entry: Mapping[str, Any],
                  text: str, root: Path) -> list[str]:
    """校验单个 helper 在指定 job 内的契约。"""
    path = entry["path"]
    status = entry["status"]
    lines = [line for line in text.splitlines() if path in line]
    if not lines:
        return [f"{job} 未调用 {path}"]
    call = " ".join(lines)
    problems = [f"{path} 缺少参数 {token}"
                for token in entry["argv"] if token not in call]
    guard = f"if [ -f {path} ]"
    if status == "deferred":
        if guard not in text:
            problems.append(f"{path} 处于 deferred，但缺少存在性守卫")
        if "SKIP:" not in text:
            problems.append(f"{path} 处于 deferred，但缺少 SKIP 标记")
        if (root / path).exists():
            problems.append(
                f"{path} 已落地，清单仍标 deferred"
                "（须转为 available 并移除守卫）")
    else:
        if guard in text:
            problems.append(f"{path} 已实现，却仍被存在性守卫包裹")
        if not (root / path).exists():
            problems.append(f"{path} 标为 available，但文件不存在")
    return problems


def check_budget(man: Mapping[str, Any],
                 wf: Mapping[str, Any]) -> CheckResult:
    """校验 CI 总时长检测的调用契约（OS-STD-TEST-011）。"""
    rules = man["budget"]
    entry = man["helpers"][rules["helper"]]
    job = _budget_job(man, wf)
    lines = [line for line in _text(job).splitlines()
             if entry["path"] in line]
    if not lines:
        return CheckResult(NAME_BUDGET, False,
                           f"未调用预算检测 {entry['path']}")
    call = " ".join(lines)
    problems = [f"预算检测缺少参数 {token}"
                for token in rules["argv"] if token not in call]
    return _result(NAME_BUDGET, problems)


def _budget_job(man: Mapping[str, Any],
                wf: Mapping[str, Any]) -> Any:
    """定位承载预算检测的 job。"""
    key = man["budget"]["helper"]
    jobs = _jobs(wf)
    for spec in man["jobs"]:
        if key in spec["helpers"]:
            return jobs.get(spec["name"], {})
    return {}


def build_checks(man: Mapping[str, Any], wf: Mapping[str, Any],
                 root: Path) -> list[CheckResult]:
    """按序构建全部检查；schema 失败时短路。"""
    schema = check_schema(man)
    if not schema.passed:
        return [schema]
    return [
        schema,
        check_trigger(man, wf),
        check_flight(man, wf),
        check_perms(man, wf),
        check_jobs(man, wf),
        check_runner(man, wf),
        check_timeout(man, wf),
        check_vars(man, wf),
        check_surface(man, wf, root),
        check_helpers(man, wf, root),
        check_budget(man, wf),
    ]


# ─── Report ────────────────────────────────────────────────────────────────


def print_report(results: list[CheckResult], check_mode: bool) -> None:
    """输出检查报告；CI 模式仅输出汇总与失败证据。"""
    total = len(results)
    failed = [result for result in results if not result.passed]
    state = "FAIL" if failed else "PASS"
    if check_mode:
        print(f"NIGHTLY: {state} ({total - len(failed)}/{total} checks)")
        for result in failed:
            print(f"  FAIL {result.name}: {result.details}")
        return
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        print(f"[{mark}] {result.name}")
        if result.details:
            print(f"       {result.details}")
    print(f"\nNIGHTLY: {state} ({total - len(failed)}/{total} checks)")


# ─── Main ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """解析参数并执行检查。"""
    parser = argparse.ArgumentParser(
        description="AirymaxOS nightly workflow 契约检查器 "
                    "(OS-STD-TEST-011)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", default=None,
                        help="agent-linux 仓库根（默认：自动探测）")
    parser.add_argument("--manifest", default=None,
                        help="nightly 清单（默认：tools/nightly/manifest.yaml）")
    parser.add_argument("--check", action="store_true",
                        help="CI 模式：精简输出")
    args = parser.parse_args(argv)

    if yaml is None:
        print("ERROR: PyYAML 不可用，请安装 pyyaml", file=sys.stderr)
        return EXIT_USAGE

    root = Path(args.repo_root) if args.repo_root else find_root()
    if not root.is_dir():
        print(f"ERROR: 仓库根不存在: {root}", file=sys.stderr)
        return EXIT_USAGE

    manifest_path = (Path(args.manifest) if args.manifest
                     else root / DEFAULT_MANIFEST_REL)
    try:
        man = load_yaml(manifest_path)
    except NightlyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE

    schema = check_schema(man)
    if not schema.passed:
        print_report([schema], args.check)
        return EXIT_FAIL

    try:
        wf = load_yaml(root / man["workflow"])
    except NightlyError as exc:
        print_report([CheckResult("workflow 可解析", False, str(exc))],
                     args.check)
        return EXIT_FAIL

    results = build_checks(man, wf, root)
    print_report(results, args.check)
    return EXIT_FAIL if any(not item.passed for item in results) else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
