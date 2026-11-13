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
from pathlib import Path
from typing import Any, Iterator, Mapping

# tools/lib 是契约检查工具的共享层：结果类型与报告格式只能有一份。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from check_report import CheckResult, lead, print_report, yaml
from manifest_io import open_gate

__all__ = ["NightlyError", "main"]

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_MANIFEST_REL = "tools/nightly/manifest.yaml"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

MANIFEST_KEYS = frozenset({
    "version", "workflow", "helper_gate", "helper_surface", "triggers",
    "concurrency", "permissions", "runner", "jobs", "helpers", "budget",
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
TITLE = "agent-linux AirymaxOS nightly workflow 契约检查报告"

_EXPR_RE = re.compile(r"\$\{\{.*?\}\}", re.S)
_VAR_RE = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)\}?")
_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=", re.M)
_VAR_PREFIXES = ("GITHUB_", "RUNNER_", "LC_")
_VAR_ALLOWED = frozenset({
    "CI", "HOME", "PATH", "PWD", "SHELL", "TMPDIR", "USER", "LANG", "IFS",
})


# ─── Data model ────────────────────────────────────────────────────────────


class NightlyError(Exception):
    """清单或 workflow 不可读取、不可解析。"""


# ─── Loading ───────────────────────────────────────────────────────────────


def _open_yaml(path: Path) -> Any:
    """打开并解析 YAML；缺失或语法错误转成 NightlyError。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError:
        raise NightlyError(f"文件不存在：{path}") from None
    except yaml.YAMLError as exc:
        raise NightlyError(f"YAML 解析失败（{path}）：{exc}") from exc


def load_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 映射；缺失或解析失败时抛 NightlyError。"""
    if yaml is None:
        raise NightlyError(
            "PyYAML 不可用：请安装 pyyaml（见 requirements-dev.txt）")
    data = _open_yaml(path)
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


def _step_map(step: Any) -> bool:
    """step 必须是映射才纳入。"""
    return isinstance(step, dict)


def _raw_steps(job: Any) -> Any:
    """job 原样的 steps 值；job 不是映射时为 None。"""
    if not isinstance(job, dict):
        return None
    return job.get("steps")


def _step_list(steps: list[Any]) -> list[dict[str, Any]]:
    """过滤非映射条目，只留可用的 step。"""
    return [step for step in steps if _step_map(step)]


def _steps(job: Any) -> list[dict[str, Any]]:
    """取 job 的 step 列表，过滤非映射条目。"""
    value = _raw_steps(job)
    if not isinstance(value, list):
        return []
    return _step_list(value)


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


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _need(mapping: Mapping[str, Any], keys: frozenset[str],
          scope: str) -> list[str]:
    """报告缺失的必填字段。"""
    return [f"{scope}缺少字段: {key}" for key in sorted(keys - set(mapping))]


def _result(name: str, problems: list[str]) -> CheckResult:
    return CheckResult(name, not problems, "; ".join(problems))


def _opt_type(value: Mapping[str, Any], key: str, kind: type,
              scope: str, label: str) -> list[str]:
    """可选字段存在时校验类型。"""
    if key not in value or isinstance(value[key], kind):
        return []
    return [f"{scope}.{key} 必须是{label}"]


def _opt_list(value: Mapping[str, Any], key: str, scope: str) -> list[str]:
    """可选字段存在时校验字符串列表。"""
    if key not in value or _is_str_list(value[key]):
        return []
    return [f"{scope}.{key} 必须是字符串列表"]


def _schema_trigger(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["triggers 必须是映射"]
    problems = _need(value, TRIGGER_KEYS, "triggers.")
    problems += _opt_type(value, "cron", str, "triggers", "字符串")
    problems += _opt_type(value, "workflow_dispatch", bool, "triggers", "布尔值")
    problems += _opt_list(value, "forbidden", "triggers")
    return problems


def _schema_flight(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["concurrency 必须是映射"]
    problems = _need(value, FLIGHT_KEYS, "concurrency.")
    problems += _opt_type(value, "group", str, "concurrency", "字符串")
    problems += _opt_type(value, "cancel_in_progress", bool, "concurrency",
                          "布尔值")
    return problems


def _schema_perms(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["permissions 必须是映射"]
    return [f"permissions.{key} 必须是字符串"
            for key, item in value.items() if not isinstance(item, str)]


def _label_probs(value: Mapping[str, Any]) -> list[str]:
    """label 可选；出现即须为字符串。"""
    if "label" in value and not isinstance(value["label"], str):
        return ["runner.label 必须是字符串"]
    return []


def _ceiling_probs(value: Mapping[str, Any]) -> list[str]:
    """hosted_ceiling_minutes 出现时须为正整数。"""
    if "hosted_ceiling_minutes" not in value:
        return []
    size = value.get("hosted_ceiling_minutes")
    return [] if _is_int(size) and size > 0 else [
        "runner.hosted_ceiling_minutes 必须是正整数"]


def _schema_runner(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["runner 必须是映射"]
    return (_need(value, RUNNER_KEYS, "runner.")
            + _label_probs(value) + _ceiling_probs(value))


def _schema_jobs(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["jobs 必须是非空列表"]
    problems: list[str] = []
    names: set[str] = set()
    for entry in value:
        problems.extend(_schema_job(entry, names))
    return problems


def _job_name(entry: Any, names: set[str]) -> tuple[str | None, list[str]]:
    """取合法的 job 名并登记；名字非法时返回 None 与一条问题。"""
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        return None, ["jobs[].name 必须是非空字符串"]
    problems = [f"jobs[].name 重复: {name}"] if name in names else []
    names.add(name)
    return name, problems


def _schema_job(entry: Any, names: set[str]) -> list[str]:
    """单个 job 条目：名字合法才继续核对字段，重名也照样登记。"""
    if not isinstance(entry, dict):
        return ["jobs 条目必须是映射"]
    problems = _need(entry, JOB_KEYS, "jobs[].")
    name, name_probs = _job_name(entry, names)
    if name is None:
        return problems + name_probs
    return problems + name_probs + _job_fields(entry, name)


def _job_fields(entry: Mapping[str, Any], name: str) -> list[str]:
    """timeout/needs/helpers/always 四项都以 job 名为前缀报错。"""
    problems: list[str] = []
    _need_timeout(entry, name, problems)
    _need_str_lists(entry, name, problems)
    _need_always(entry, name, problems)
    return problems


def _need_timeout(entry: Mapping[str, Any], name: str,
                  problems: list[str]) -> None:
    """timeout_minutes 必须是整数。"""
    if not _is_int(entry.get("timeout_minutes")):
        problems.append(f"jobs[{name}].timeout_minutes 必须是整数")


def _need_str_lists(entry: Mapping[str, Any], name: str,
                    problems: list[str]) -> None:
    """needs 与 helpers 必须是字符串列表。"""
    for key in ("needs", "helpers"):
        if not _is_str_list(entry.get(key)):
            problems.append(f"jobs[{name}].{key} 必须是字符串列表")


def _need_always(entry: Mapping[str, Any], name: str,
                 problems: list[str]) -> None:
    """always 出现时必须是布尔值。"""
    if "always" in entry and not isinstance(entry["always"], bool):
        problems.append(f"jobs[{name}].always 必须是布尔值")


def _opt_ok(entry: Mapping[str, Any], key: str, scope: str,
            ok: Any, msg: str) -> list[str]:
    """可选字段：缺省即跳过，出现时须通过形状判据。"""
    if key not in entry or ok(entry[key]):
        return []
    return [f"{scope}{key} {msg}"]


HELPER_FIELDS = (
    ("path", _is_str, "必须是字符串"),
    ("argv", _is_str_list, "必须是字符串列表"),
)


def _helper_schema(key: str, entry: Any) -> list[str]:
    """单个 helper 条目：字段齐全、path/argv 形状、status 取值。"""
    scope = f"helpers.{key}."
    if not isinstance(entry, dict):
        return [f"helpers.{key} 必须是映射"]
    problems = _need(entry, HELPER_KEYS, scope)
    for field, ok, msg in HELPER_FIELDS:
        problems.extend(_opt_ok(entry, field, scope, ok, msg))
    if entry.get("status") not in HELPER_STATES:
        problems.append(f"{scope}status 必须是 {sorted(HELPER_STATES)} 之一")
    return problems


def _schema_helpers(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["helpers 必须是映射"]
    problems: list[str] = []
    for key, entry in value.items():
        problems.extend(_helper_schema(key, entry))
    return problems


def _budget_size(value: Mapping[str, Any],
                 problems: list[str]) -> int | None:
    """max_minutes 必须是正整数；整数原样返回供 argv 关联校验。"""
    size = value.get("max_minutes")
    if not (_is_int(size) and size > 0):
        problems.append("budget.max_minutes 必须是正整数")
    return size if _is_int(size) else None


def _budget_argv(value: Mapping[str, Any],
                 problems: list[str]) -> list[str] | None:
    """argv 可省略；出现时必须是字符串列表，合法值供关联校验复用。"""
    if "argv" not in value:
        return None
    argv = value["argv"]
    if _is_str_list(argv):
        return argv
    problems.append("budget.argv 必须是字符串列表")
    return None


def _budget_helper(value: Mapping[str, Any], problems: list[str],
                   helpers: Any) -> None:
    """helper 引用必须登记在 helpers 段；未提供 helpers 时按空集处理。"""
    ref = value.get("helper")
    known = helpers if isinstance(helpers, dict) else {}
    if isinstance(ref, str) and ref not in known:
        problems.append(f"budget.helper 未登记: {ref}")


def _budget_link(size: int | None, argv: list[str] | None,
                 problems: list[str]) -> None:
    """max_minutes 的取值必须出现在 argv 中，避免两处数字各自漂移。"""
    if size is None or argv is None:
        return
    if str(size) not in argv:
        problems.append("budget.argv 必须包含 max_minutes 的取值")


def _schema_budget(value: Any, helpers: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["budget 必须是映射"]
    problems = _need(value, BUDGET_KEYS, "budget.")
    size = _budget_size(value, problems)
    argv = _budget_argv(value, problems)
    _budget_helper(value, problems, helpers)
    _budget_link(size, argv, problems)
    return problems


STR_KEYS = ("version", "workflow", "helper_surface", "helper_gate")

SCHEMAS = (
    ("triggers", _schema_trigger),
    ("concurrency", _schema_flight),
    ("permissions", _schema_perms),
    ("runner", _schema_runner),
    ("jobs", _schema_jobs),
    ("helpers", _schema_helpers),
)


def _str_prob(key: str, value: Any) -> list[str]:
    """单个身份字段：出现时必须是非空字符串。"""
    if isinstance(value, str) and value:
        return []
    return [f"{key} 必须是非空字符串"]


def _schema_strings(man: Mapping[str, Any]) -> list[str]:
    """标量身份字段：一旦出现就必须是非空字符串。"""
    return [problem for key in STR_KEYS if key in man
            for problem in _str_prob(key, man[key])]


def check_schema(man: Mapping[str, Any]) -> CheckResult:
    """校验清单 schema；失败时其余检查短路。"""
    problems = _need(man, MANIFEST_KEYS, "清单")
    problems.extend(_schema_strings(man))
    for key, validator in SCHEMAS:
        if key in man:
            problems.extend(validator(man[key]))
    if "budget" in man:
        problems.extend(_schema_budget(man["budget"], man.get("helpers")))
    return _result(NAME_SCHEMA, problems)


# ─── Checks ────────────────────────────────────────────────────────────────


def _cron_list(triggers: Mapping[str, Any]) -> list[Any]:
    """workflow 的 schedule.cron 取值序列；非列表时按空序列处理。"""
    schedule = triggers.get("schedule")
    if not isinstance(schedule, list):
        return []
    return [item.get("cron") for item in schedule if isinstance(item, dict)]


def _forbid_probs(rules: Mapping[str, Any],
                  triggers: Mapping[str, Any]) -> list[str]:
    """清单声明的禁止触发方式一旦出现在 workflow 中即为违规。"""
    return [f"nightly 不得由 {key} 触发（结果不得阻断 PR）"
            for key in rules["forbidden"] if key in triggers]


def check_trigger(man: Mapping[str, Any],
                  wf: Mapping[str, Any]) -> CheckResult:
    """校验触发条件：唯一 cron、可手动触发、禁止 PR 触发。"""
    rules = man["triggers"]
    triggers = _triggers(wf)
    if not isinstance(triggers, dict):
        return CheckResult(NAME_TRIGGER, False, "on 段必须是映射")
    problems: list[str] = []
    crons = _cron_list(triggers)
    if crons != [rules["cron"]]:
        problems.append(f"schedule.cron 期望 {[rules['cron']]}，实际 {crons}")
    if "workflow_dispatch" not in triggers:
        problems.append("缺少 workflow_dispatch 手动触发")
    problems.extend(_forbid_probs(rules, triggers))
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


def _perm_expected(value: Mapping[str, Any],
                   expected: Mapping[str, Any]) -> list[str]:
    """清单登记的权限逐项对齐；缺项与取值不符一并报出。"""
    return [f"permissions.{key} 期望 {expected[key]}，"
            f"实际 {value.get(key)}"
            for key in expected if value.get(key) != expected[key]]


def _perm_writes(value: Mapping[str, Any]) -> list[str]:
    """workflow 级权限不得含任何写权限。"""
    return [f"permissions.{key} 不得授予写权限"
            for key, item in value.items()
            if isinstance(item, str) and item.endswith("write")]


def check_perms(man: Mapping[str, Any],
                wf: Mapping[str, Any]) -> CheckResult:
    """校验 workflow 级最小权限。"""
    value = wf.get("permissions")
    if not isinstance(value, dict):
        return CheckResult(NAME_PERMS, False, "缺少 workflow 级 permissions")
    problems = _perm_expected(value, man["permissions"]) + _perm_writes(value)
    return _result(NAME_PERMS, problems)


def _missing_jobs(expected: list[str],
                  jobs: Mapping[str, Any]) -> list[str]:
    """清单登记但 workflow 缺失的 job。"""
    return [f"缺少 job: {name}" for name in expected if name not in jobs]


def _extra_jobs(expected: list[str],
                jobs: Mapping[str, Any]) -> list[str]:
    """workflow 出现但清单未登记的 job。"""
    return [f"未登记 job: {name}" for name in jobs if name not in expected]


def _job_name_probs(expected: list[str],
                    jobs: Mapping[str, Any]) -> list[str]:
    """job 集合与清单的双向差异：缺失与未登记。"""
    return _missing_jobs(expected, jobs) + _extra_jobs(expected, jobs)


def check_jobs(man: Mapping[str, Any],
               wf: Mapping[str, Any]) -> CheckResult:
    """校验 job 集合、依赖关系与 always() 语义。"""
    jobs = _jobs(wf)
    expected = [item["name"] for item in man["jobs"]]
    problems = _job_name_probs(expected, jobs)
    for spec in man["jobs"]:
        job = jobs.get(spec["name"])
        if isinstance(job, dict):
            problems.extend(_job_probs(spec, job))
    return _result(NAME_JOBS, problems)


def _needs_probs(spec: Mapping[str, Any], job: Mapping[str, Any]) -> list[str]:
    """单个 job 的 needs 与清单一致。"""
    needs = job.get("needs")
    actual = {needs} if isinstance(needs, str) else set(needs or [])
    if actual == set(spec["needs"]):
        return []
    return [f"{spec['name']}.needs 期望 {spec['needs']}，"
            f"实际 {sorted(actual)}"]


def _always_probs(spec: Mapping[str, Any],
                  job: Mapping[str, Any]) -> list[str]:
    """always() 触发条件与清单声明一致。"""
    cond = str(job.get("if", ""))
    if spec.get("always"):
        if "always()" in cond:
            return []
        return [f"{spec['name']} 缺少 if: always()"]
    return [f"{spec['name']} 不应无条件执行"] if "always()" in cond else []


def _job_probs(spec: Mapping[str, Any], job: Mapping[str, Any]) -> list[str]:
    """校验单个 job 的 depends 与触发条件。"""
    return _needs_probs(spec, job) + _always_probs(spec, job)


def _runs_labels(runs: Any) -> set[str]:
    """runs-on 取值归一为标签集合。"""
    if isinstance(runs, str):
        return {runs}
    return set(runs or [])


def _runner_prob(spec: Mapping[str, Any], runs: Any,
                 label: str) -> list[str]:
    """单个 job 的 runner 标签是否命中清单。"""
    if label in _runs_labels(runs):
        return []
    return [f"{spec['name']}.runs-on 期望 {label}，实际 {runs}"]


def check_runner(man: Mapping[str, Any],
                 wf: Mapping[str, Any]) -> CheckResult:
    """校验每个 job 的 runner 标签。"""
    label = man["runner"]["label"]
    jobs = _jobs(wf)
    problems: list[str] = []
    for spec in man["jobs"]:
        runs = jobs.get(spec["name"], {}).get("runs-on")
        problems.extend(_runner_prob(spec, runs, label))
    return _result(NAME_RUNNER, problems)


def check_timeout(man: Mapping[str, Any],
                  wf: Mapping[str, Any]) -> CheckResult:
    """校验 job 超时：与清单相等且不超过托管 runner 硬上限。

    上限无条件生效。曾经用 label 是否以 ``-latest`` 结尾来推断「托管」，
    于是把标签换成固定版本号（``ubuntu-24.04``）后整条约束静默失效——
    托管 runner 的标签本来就有两种合法写法，而 self-hosted 的硬上限同样
    不该被越过。上限是清单声明的输入，不该再从标签形状反推。
    """
    ceiling = man["runner"]["hosted_ceiling_minutes"]
    jobs = _jobs(wf)
    problems: list[str] = []
    for spec in man["jobs"]:
        actual = jobs.get(spec["name"], {}).get("timeout-minutes")
        problems.extend(_timeout_probs(spec, actual, ceiling))
    return _result(NAME_TIMEOUT, problems)


def _timeout_decl(spec: Mapping[str, Any], actual: int) -> list[str]:
    """超时值与清单声明逐字相等。"""
    if actual == spec["timeout_minutes"]:
        return []
    return [f"{spec['name']}.timeout-minutes 期望 "
            f"{spec['timeout_minutes']}，实际 {actual}"]


def _timeout_cap(spec: Mapping[str, Any], actual: int,
                 ceiling: int) -> list[str]:
    """超时值不得超过托管 runner 硬上限。"""
    if actual <= ceiling:
        return []
    return [f"{spec['name']}.timeout-minutes 超过托管 runner 上限 {ceiling}"]


def _timeout_probs(spec: Mapping[str, Any], actual: Any,
                   ceiling: int) -> list[str]:
    """单个 job 的超时：类型、与清单相等、不超硬上限。"""
    if not _is_int(actual):
        return [f"{spec['name']} 缺少整数 timeout-minutes"]
    return _timeout_decl(spec, actual) + _timeout_cap(spec, actual, ceiling)


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


def _var_denied(name: str) -> bool:
    """不在放行名单、也不带放行前缀的变量名。"""
    return name not in _VAR_ALLOWED and not name.startswith(_VAR_PREFIXES)


def _undef_names(script: str, defined: set[str]) -> set[str]:
    """脚本引用但既未定义、也不在放行名单里的变量。"""
    used = _var_refs(script) - defined - _var_defs(script)
    return {name for name in used if _var_denied(name)}


def _script_probs(job_name: str, job: Mapping[str, Any],
                  base: set[str]) -> set[tuple[str, str]]:
    """单个 job 内全部 step 的未定义变量。"""
    problems: set[tuple[str, str]] = set()
    job_env = _env_keys(job.get("env"))
    for step in _steps(job):
        script = step.get("run")
        if not isinstance(script, str):
            continue
        defined = base | job_env | _env_keys(step.get("env"))
        problems |= {(job_name, name) for name in _undef_names(script, defined)}
    return problems


def check_vars(man: Mapping[str, Any],
               wf: Mapping[str, Any]) -> CheckResult:
    """校验脚本引用的 shell 变量都有定义。"""
    jobs = _jobs(wf)
    base = _env_keys(wf.get("env"))
    problems: set[tuple[str, str]] = set()
    for spec in man["jobs"]:
        job = jobs.get(spec["name"], {})
        problems |= _script_probs(spec["name"], job, base)
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


def read_gate(man: Mapping[str, Any], root: Path) -> str | None:
    """读取 helper 门禁正文；未登记或不可读时返回 None。"""
    rel = man.get("helper_gate")
    if not isinstance(rel, str) or not rel:
        return None
    try:
        return (root / rel).read_text(encoding="utf-8")
    except OSError:
        return None


def _gate_bad(gate_rel: Any, gate: str | None) -> str | None:
    """登记了 helper_gate 却读不出正文时返回失败详情。"""
    if isinstance(gate_rel, str) and gate_rel and gate is None:
        return f"无法读取 helper 门禁 {gate_rel}"
    return None


def _one_helper(spec: Mapping[str, Any], key: str, man: Mapping[str, Any],
                wf_text: str, gate: str | None, text: str,
                root: Path) -> list[str]:
    """单个 helper 的登记与契约问题。"""
    entry = man["helpers"].get(key)
    if entry is None:
        return [f"{spec['name']} 引用了未登记的 helper: {key}"]
    if gate is None:
        return _helper_probs(spec["name"], entry, text, root)
    return (_call_probs(spec["name"], key, wf_text)
            + _helper_probs(spec["name"], entry, text, root))


def _helper_job(spec: Mapping[str, Any], man: Mapping[str, Any],
                gate: str | None, jobs: Mapping[str, Any],
                root: Path) -> list[str]:
    """单个 job 内全部 helper 的契约问题。"""
    wf_text = _text(jobs.get(spec["name"], {}))
    text = wf_text if gate is None else gate
    problems: list[str] = []
    for key in spec["helpers"]:
        problems.extend(
            _one_helper(spec, key, man, wf_text, gate, text, root))
    return problems


def check_helpers(man: Mapping[str, Any],
                  wf: Mapping[str, Any], root: Path) -> CheckResult:
    """校验 helper 的调用、守卫、SKIP 标记与落地状态。

    登记了 ``helper_gate`` 时，守卫与调用参数住在门禁脚本里，workflow 只保留
    ``ci/ci.sh nightly-helpers <key>``；未登记时退回「workflow 内联守卫」的
    旧形态，便于尚未迁移的清单继续受检。
    """
    jobs = _jobs(wf)
    gate = read_gate(man, root)
    bad = _gate_bad(man.get("helper_gate"), gate)
    if bad is not None:
        return CheckResult(NAME_HELPERS, False, bad)
    problems: list[str] = []
    for spec in man["jobs"]:
        problems.extend(_helper_job(spec, man, gate, jobs, root))
    return _result(NAME_HELPERS, problems)


def _call_probs(job: str, key: str, text: str) -> list[str]:
    """校验 workflow 以门禁形态调用了 helper。"""
    call = f"nightly-helpers {key}"
    if call not in text:
        return [f"{job} 未通过门禁调用 {key}（期望 {call}）"]
    return []


def _call_lines(text: str, path: str) -> list[str]:
    """helper 的调用行：正文中提及其路径的所有行。"""
    return [line for line in text.splitlines() if path in line]


def _argv_probs(entry: Mapping[str, Any], call: str) -> list[str]:
    """登记的每个参数都必须出现在调用行里。"""
    return [f"{entry['path']} 缺少参数 {token}"
            for token in entry["argv"] if token not in call]


def _defer_probs(entry: Mapping[str, Any], text: str, guard: str,
                 root: Path) -> list[str]:
    """deferred helper：守卫与 SKIP 标记在位，且脚本尚未落地。"""
    path = entry["path"]
    problems = []
    if guard not in text:
        problems.append(f"{path} 处于 deferred，但缺少存在性守卫")
    if "SKIP:" not in text:
        problems.append(f"{path} 处于 deferred，但缺少 SKIP 标记")
    if (root / path).exists():
        problems.append(
            f"{path} 已落地，清单仍标 deferred"
            "（须转为 available 并移除守卫）")
    return problems


def _avail_probs(entry: Mapping[str, Any], text: str, guard: str,
                 root: Path) -> list[str]:
    """available helper：脚本已落地，且不得再被存在性守卫包裹。"""
    path = entry["path"]
    problems = []
    if guard in text:
        problems.append(f"{path} 已实现，却仍被存在性守卫包裹")
    if not (root / path).exists():
        problems.append(f"{path} 标为 available，但文件不存在")
    return problems


def _helper_probs(job: str, entry: Mapping[str, Any],
                  text: str, root: Path) -> list[str]:
    """校验单个 helper 在指定 job 内的契约。"""
    path = entry["path"]
    lines = _call_lines(text, path)
    if not lines:
        return [f"{job} 未调用 {path}"]
    call = " ".join(lines)
    guard = f"if [ -f {path} ]"
    problems = _argv_probs(entry, call)
    if entry["status"] == "deferred":
        problems.extend(_defer_probs(entry, text, guard, root))
    else:
        problems.extend(_avail_probs(entry, text, guard, root))
    return problems


def check_budget(man: Mapping[str, Any], wf: Mapping[str, Any],
                 root: Path | None = None) -> CheckResult:
    """校验 CI 总时长检测的调用契约（OS-STD-TEST-011）。"""
    rules = man["budget"]
    key = rules["helper"]
    entry = man["helpers"][key]
    job = _budget_job(man, wf)
    gate = read_gate(man, root) if root is not None else None
    problems = _gate_probs(gate, key, job)
    text = _text(job) if gate is None else gate
    _budget_args(rules, entry, text, problems)
    return _result(NAME_BUDGET, problems)


def _gate_probs(gate: str | None, key: str,
                job: Any) -> list[str]:
    """仅有 gate 时校验 job 命令里的调用契约。"""
    if gate is None:
        return []
    return _call_probs("预算检测", key, _text(job))


def _budget_args(rules: Mapping[str, Any], entry: Mapping[str, Any],
                 text: str, problems: list[str]) -> None:
    """预算脚本被调用，且参数与清单一致。"""
    if entry["path"] not in text:
        problems.append(f"未调用预算检测 {entry['path']}")
        return
    expected = " ".join(rules["argv"])
    if expected not in text:
        problems.append(f"预算检测参数不匹配: {expected}")


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
    return lead(check_schema(man), _checks(man, wf, root))


def _checks(man: Mapping[str, Any], wf: Mapping[str, Any],
            root: Path) -> Iterator[CheckResult]:
    """生成 schema 之后的检查结果，由 lead 在 schema 通过后消费。"""
    yield check_trigger(man, wf)
    yield check_flight(man, wf)
    yield check_perms(man, wf)
    yield check_jobs(man, wf)
    yield check_runner(man, wf)
    yield check_timeout(man, wf)
    yield check_vars(man, wf)
    yield check_surface(man, wf, root)
    yield check_helpers(man, wf, root)
    yield check_budget(man, wf, root)


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
    gate = open_gate(parser, argv, DEFAULT_MANIFEST_REL, load_yaml,
                     NightlyError)
    if gate is None:
        return EXIT_USAGE

    man = gate.manifest
    if not _schema_ok(gate, man):
        return EXIT_FAIL
    wf = _load_wf(gate, man)
    if wf is None:
        return EXIT_FAIL
    return _report(gate, man, wf)


def _schema_ok(gate: Any, man: Mapping[str, Any]) -> bool:
    """schema 不通过时先打印单条报告。"""
    schema = check_schema(man)
    if schema.passed:
        return True
    print_report([schema], "NIGHTLY", gate.args.check, TITLE,
                 str(man.get("version", "?")))
    return False


def _load_wf(gate: Any, man: Mapping[str, Any]) -> Any:
    """读取 workflow；失败时打印单条报告并返回 None。"""
    try:
        return load_yaml(gate.root / man["workflow"])
    except NightlyError as exc:
        print_report([CheckResult("workflow 可解析", False, str(exc))],
                     "NIGHTLY", gate.args.check, TITLE,
                     str(man.get("version", "?")))
        return None


def _report(gate: Any, man: Mapping[str, Any],
            wf: Mapping[str, Any]) -> int:
    """执行全部检查、打印报告并给出退出码。"""
    results = build_checks(man, wf, gate.root)
    print_report(results, "NIGHTLY", gate.args.check, TITLE,
                 str(man.get("version", "?")))
    return EXIT_FAIL if any(not item.passed for item in results) else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
