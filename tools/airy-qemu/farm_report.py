# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_report — QEMU 测试农场的报告输出。

门禁结论需要同时服务两类消费者：CI 流水线要机器可读的归档证据，
开发者要能直接阅读的结论摘要。本模块把 GateReport 渲染为稳定的
JSON 文档与控制台摘要行，不做任何判定，也不重新解释 TAP 计数。

JSON 文档只包含确定性字段（无时间戳、无宿主路径探测），同一份门禁
结果总得到逐字节相同的输出，便于归档比对与回归测试；作业的日志路径
仅在作业声明时写入。排序键固定，字段顺序不影响消费者解析。

控制台摘要按输入顺序逐作业列出状态、耗时与 TAP 计数，末行给出门禁
结论与阻断作业列表，使失败原因无需翻看 JSON 即可定位。

权威依据：
  docs-linux/70-build-system/03-ci-cd-pipeline.md §5.3（OS-TEST-010）
  docs-linux/80-testing/02-kselftest.md OS-TEST-021（SKIP 单独计数）
  farm_verdict.GateReport（门禁结论的唯一事实源）

Example:
    >>> path = write_json(report, Path("out/farm.json"))
    >>> summary_lines(report)[-1]
    'gate: PASS'

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

# 2. 本工具模块导入
from farm_types import FARM_VERSION, FarmConfigError, JobResult, JobState, TapSummary
from farm_verdict import GateReport

# 3. 公开导出
__all__ = [
    "report_json",
    "summary_lines",
    "write_json",
]


def report_json(report: GateReport) -> str:
    """把门禁结果渲染为确定性 JSON 文档。

    Args:
        report: 已汇总的终态结果。

    Returns:
        以换行结尾的 JSON 文本。

    Raises:
        FarmConfigError: report 不是 GateReport。
    """
    _check(report)
    payload = {
        "version": FARM_VERSION,
        "gate": "passed" if report.ok else "failed",
        "blocked": list(report.failed),
        "counts": _counts(report),
        "states": _states(report),
        "jobs": [_job_doc(result) for result in report.results],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_json(report: GateReport, path: Path | str) -> Path:
    """把 JSON 报告写入文件，按需创建父目录。

    Args:
        report: 已汇总的终态结果。
        path: 目标文件路径，字符串会转换为 Path。

    Returns:
        实际写入的文件路径。

    Raises:
        FarmConfigError: report 非法或路径未指向文件。
    """
    target = _need_path(path)
    text = report_json(report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def summary_lines(report: GateReport) -> tuple[str, ...]:
    """渲染控制台摘要行。

    Args:
        report: 已汇总的终态结果。

    Returns:
        首行为汇总、中间为逐作业明细、末行为门禁结论。

    Raises:
        FarmConfigError: report 不是 GateReport。
    """
    _check(report)
    ok = sum(1 for result in report.results if result.ok)
    lines = [f"farm: {report.count} jobs, {ok} ok, {report.count - ok} blocking"]
    lines.extend(_job_line(result) for result in report.results)
    blocked = ", ".join(report.failed)
    lines.append("gate: " + ("PASS" if report.ok else f"FAIL blocked: {blocked}"))
    return tuple(lines)


def _counts(report: GateReport) -> dict[str, int]:
    """汇总作业总数、放行数与阻断数。"""
    ok = sum(1 for result in report.results if result.ok)
    return {"total": report.count, "ok": ok, "blocking": report.count - ok}


def _states(report: GateReport) -> dict[str, int]:
    """按状态枚举顺序汇总各终态出现次数（仅含非零项）。"""
    counts: Counter[JobState] = Counter(result.state for result in report.results)
    return {state.value: counts[state] for state in JobState if counts[state]}


def _job_doc(result: JobResult) -> dict[str, object]:
    """渲染单个作业的 JSON 字段。"""
    doc: dict[str, object] = {
        "job_id": result.job.job_id,
        "state": result.state.value,
        "duration_s": result.duration_s,
        "exit_code": result.exit_code,
        "reason": result.reason,
        "suites": list(result.job.suite_names),
        "tap": _tap_doc(result.tap),
        "qemu_argv": list(result.qemu_argv),
    }
    if result.job.log_path is not None:
        doc["log_path"] = str(result.job.log_path)
    return doc


def _tap_doc(tap: TapSummary | None) -> dict[str, object] | None:
    """渲染 TAP 汇总，保留计数与派生判定。"""
    if tap is None:
        return None
    return {
        "plan_total": tap.plan_total,
        "ok_count": tap.ok_count,
        "not_ok_count": tap.not_ok_count,
        "skip_count": tap.skip_count,
        "executed": tap.executed,
        "missing": tap.missing,
        "complete": tap.complete,
        "passed": tap.passed,
    }


def _job_line(result: JobResult) -> str:
    """渲染单作业摘要行。"""
    tap = (
        f"tap {result.tap.executed}/{result.tap.plan_total}"
        if result.tap is not None
        else "tap -"
    )
    parts = [
        f"  {result.state.value.upper():<8}",
        f"{result.job.job_id:<20}",
        f"{result.duration_s:7.2f}s",
        tap,
    ]
    if result.reason:
        parts.append(result.reason)
    return "  ".join(parts)


def _check(report: object) -> None:
    """校验门禁结果类型。"""
    if not isinstance(report, GateReport):
        raise FarmConfigError(
            f"report must be GateReport, got {type(report).__name__}"
        )


def _need_path(path: Path | str) -> Path:
    """规范化并校验报告落点。"""
    target = Path(path) if isinstance(path, str) else path
    if not isinstance(target, Path):
        raise FarmConfigError(
            f"report path must be Path or str, got {type(target).__name__}"
        )
    if not target.name or target.is_dir():
        raise FarmConfigError(f"report path must name a file: {target}")
    return target
