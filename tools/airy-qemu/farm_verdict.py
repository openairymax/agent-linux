# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_verdict — QEMU 测试农场的结果判定与门禁。

运行器只负责执行与取证，聚合层只负责计数；本模块把两者转化为可阻断
合并的结论：单作业的 JobResult 与整批作业的 GateReport。

判定以 TAP 证据为准，退出码作为补充证据：
  * 超时判 TIMEOUT，TAP 仅作为尽力而为的附加证据；
  * TAP 缺失、不可读或不合法判 ERROR，不允许静默通过；
  * 计划不完整（缺失用例或静默套件）判 FAILED；
  * 存在 not ok 判 FAILED；
  * 全部用例跳过判 SKIPPED（不阻断门禁）；
  * TAP 全绿但退出码非零判 FAILED，避免掩盖运行器异常。

门禁只汇总终态结果：任一作业失败即阻断。缺失作业的检测由调度层负责，
该层才完整持有作业集合，本模块不猜测未上报的作业。

权威依据：
  docs-linux/80-testing/02-kselftest.md OS-TEST-014/015/021
  docs-linux/70-build-system/03-ci-cd-pipeline.md §2.3
  tools/parse-tap.py（TAP 计数契约）

Example:
    >>> result = judge_run(raw_run)
    >>> result.state.value
    'passed'
    >>> GateReport((result,)).ok
    True

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# 2. 本工具模块导入
from farm_runner import RawRun
from farm_tapagg import TapReport, collect_tap
from farm_types import FarmConfigError, FarmError, JobResult, JobState

# 3. 公开导出
__all__ = [
    "GateReport",
    "judge_run",
]

# 4. 契约常量
_EXIT_PASS: Final[int] = 0


@dataclass(frozen=True)
class GateReport:
    """一批作业的终态结果与门禁结论。"""

    results: tuple[JobResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple) or not self.results:
            raise ValueError("results must be a non-empty tuple")
        ids: set[str] = set()
        for result in self.results:
            if not isinstance(result, JobResult):
                raise ValueError("results must contain JobResult entries")
            job_id = result.job.job_id
            if job_id in ids:
                raise ValueError(f"duplicate job result: {job_id!r}")
            ids.add(job_id)

    @property
    def count(self) -> int:
        """作业总数。"""
        return len(self.results)

    @property
    def ok(self) -> bool:
        """是否全部作业都不阻断门禁。"""
        return all(result.ok for result in self.results)

    @property
    def failed(self) -> tuple[str, ...]:
        """阻断门禁的作业 ID（按上报顺序）。"""
        return tuple(
            result.job.job_id for result in self.results if not result.ok
        )


def judge_run(raw: RawRun) -> JobResult:
    """判定一次作业运行的终态。

    Args:
        raw: 运行器返回的原始证据。

    Returns:
        终态 JobResult；证据不可读时返回 ERROR 而不是抛异常。

    Raises:
        FarmConfigError: raw 类型非法（属于调用方编程错误）。
    """
    _check(raw)
    if raw.timed_out:
        return _timeout(raw)
    try:
        report = collect_tap(raw)
    except FarmConfigError:
        raise
    except FarmError as exc:
        return _make(raw, JobState.ERROR, None, exc.message)
    return _verdict(raw, report)


def _verdict(raw: RawRun, report: TapReport) -> JobResult:
    """按 TAP 证据与退出码给出终态。"""
    tap = report.summary
    if not tap.complete:
        return _make(raw, JobState.FAILED, report, _incomplete(report))
    if tap.not_ok_count:
        return _make(raw, JobState.FAILED, report, _fail_reason(report))
    if tap.executed > 0 and tap.ok_count == 0:
        return _make(
            raw,
            JobState.SKIPPED,
            report,
            f"all {tap.executed} cases skipped",
        )
    if raw.exit_code != _EXIT_PASS:
        return _make(raw, JobState.FAILED, report, _exit_reason(raw))
    return _make(raw, JobState.PASSED, report)


def _timeout(raw: RawRun) -> JobResult:
    """超时判定：TAP 只作附加证据，解析失败不影响终态。"""
    try:
        report = collect_tap(raw)
    except FarmError:
        report = None
    reason = f"timeout after {raw.preflight.job.timeout_s}s"
    return _make(raw, JobState.TIMEOUT, report, reason)


def _make(
    raw: RawRun,
    state: JobState,
    report: TapReport | None,
    reason: str = "",
) -> JobResult:
    """由原始证据与判定结论构造 JobResult。"""
    return JobResult(
        job=raw.preflight.job,
        state=state,
        duration_s=raw.duration_s,
        exit_code=raw.exit_code,
        tap=report.summary if report is not None else None,
        reason=reason,
        qemu_argv=raw.argv,
    )


def _incomplete(report: TapReport) -> str:
    """说明缺失用例与静默套件。"""
    tap = report.summary
    text = (
        f"TAP incomplete: {tap.missing} of {tap.plan_total} "
        "cases missing"
    )
    if report.silent:
        return f"{text}; silent suites: {', '.join(report.silent)}"
    return text


def _fail_reason(report: TapReport) -> str:
    """说明 not ok 的数量与所属套件。"""
    text = f"TAP failures: {report.summary.not_ok_count} not ok"
    return f"{text} in {', '.join(report.failed)}"


def _exit_reason(raw: RawRun) -> str:
    """说明非零退出：信号终止与普通退出码区分开。"""
    signal_no = raw.signal_no
    if signal_no is not None:
        return f"runner terminated by signal {signal_no}"
    return f"runner exited with code {raw.exit_code}"


def _check(raw: object) -> None:
    """校验运行证据类型。"""
    if not isinstance(raw, RawRun):
        raise FarmConfigError(
            f"raw must be RawRun, got {type(raw).__name__}"
        )
