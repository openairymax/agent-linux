# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_sched — QEMU 测试农场的并发与资源调度。

农场需要在有限的 CPU 与内存预算内并行执行多个作业。单纯按作业数限制
并发会超出宿主资源：QEMU 的内存与 vCPU 是逐作业累加的，因此调度必须
同时约束并发数、内存总量与 CPU 总量。

调度规则：
  * 运行前先做静态校验：作业非空、ID 唯一，且每个作业单独即可放入
    空闲农场；任一不满足即拒绝整批，避免跑到一半才发现配置错误；
  * 按输入顺序派生（FIFO），只在当前运行集合的累计资源加新作业仍落在
    预算内时才启动，因此不会出现资源超卖；
  * 结果按输入顺序返回，与完成先后无关，保证报告可复现；
  * 单个作业的执行异常降级为 ERROR 结果，不中断整批运行；配置错误
    属于调用方缺陷，直接向上抛出。

作业超时由运行器按 FarmJob.timeout_s 执行，调度层不重复计时。

权威依据：
  docs-linux/70-build-system/03-ci-cd-pipeline.md §3.2（矩阵生成与调度）
  farm_types.FarmLimits（并行预算的唯一事实源）

Example:
    >>> results = run_batch(jobs, limits, execute)
    >>> [result.state.value for result in results]
    ['passed', 'failed']

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Callable

# 2. 本工具模块导入
from farm_types import (
    FarmConfigError,
    FarmError,
    FarmJob,
    FarmLimits,
    JobResult,
    JobState,
)

# 3. 公开导出
__all__ = ["run_batch"]

# 4. 类型别名
Execute = Callable[[FarmJob], JobResult]


def run_batch(
    jobs: tuple[FarmJob, ...],
    limits: FarmLimits,
    execute: Execute,
) -> tuple[JobResult, ...]:
    """在资源预算内并发执行一批作业。

    Args:
        jobs: 待执行作业，顺序决定派发顺序与结果顺序。
        limits: 并发数、内存与 CPU 预算。
        execute: 单作业执行器，返回终态 JobResult。

    Returns:
        与 jobs 同序的终态结果。

    Raises:
        FarmConfigError: 输入非法、作业超出预算或执行器返回错配结果。
    """
    _check(jobs, limits, execute)
    results: list[JobResult | None] = [None] * len(jobs)
    pending = list(enumerate(jobs))
    active: dict[Future[JobResult], tuple[int, FarmJob]] = {}
    workers = min(limits.max_jobs, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending or active:
            _dispatch(pool, pending, active, limits, execute)
            if not active:
                raise FarmConfigError(
                    f"cannot schedule job {pending[0][1].job_id!r}"
                )
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                index, _ = active.pop(future)
                results[index] = future.result()
    return tuple(_need_result(item) for item in results)


def _dispatch(
    pool: ThreadPoolExecutor,
    pending: list[tuple[int, FarmJob]],
    active: dict[Future[JobResult], tuple[int, FarmJob]],
    limits: FarmLimits,
    execute: Execute,
) -> None:
    """按 FIFO 派发此刻放得下的作业。"""
    while pending and _fits(limits, active, pending[0][1]):
        index, job = pending.pop(0)
        future = pool.submit(_guard, execute, job)
        active[future] = (index, job)


def _fits(
    limits: FarmLimits,
    active: dict[Future[JobResult], tuple[int, FarmJob]],
    job: FarmJob,
) -> bool:
    """判断新作业加入当前运行集合后是否仍在预算内。"""
    if len(active) >= limits.max_jobs:
        return False
    running = tuple(item for _, item in active.values())
    mem_mb = job.mem_mb + sum(item.mem_mb for item in running)
    cpus = job.smp + sum(item.smp for item in running)
    return mem_mb <= limits.mem_mb and cpus <= limits.cpus


def _guard(execute: Execute, job: FarmJob) -> JobResult:
    """执行单作业并把执行期错误降级为 ERROR 结果。"""
    try:
        result = execute(job)
    except FarmConfigError:
        raise
    except FarmError as exc:
        return _error(job, exc)
    if not isinstance(result, JobResult):
        raise FarmConfigError(
            f"execute must return JobResult, got {type(result).__name__}"
        )
    if result.job != job:
        raise FarmConfigError(
            f"result job {result.job.job_id!r} does not match "
            f"{job.job_id!r}"
        )
    return result


def _error(job: FarmJob, exc: FarmError) -> JobResult:
    """把执行期异常转成终态 ERROR 结果。"""
    return JobResult(
        job=job,
        state=JobState.ERROR,
        duration_s=0.0,
        reason=exc.message,
    )


def _check(
    jobs: object,
    limits: object,
    execute: object,
) -> None:
    """静态校验整批作业与预算。"""
    if not isinstance(jobs, tuple) or not jobs:
        raise FarmConfigError("jobs must be a non-empty tuple")
    if any(not isinstance(job, FarmJob) for job in jobs):
        raise FarmConfigError("jobs must contain FarmJob entries")
    if not isinstance(limits, FarmLimits):
        raise FarmConfigError(
            f"limits must be FarmLimits, got {type(limits).__name__}"
        )
    if not callable(execute):
        raise FarmConfigError("execute must be callable")
    seen: set[str] = set()
    for job in jobs:
        if job.job_id in seen:
            raise FarmConfigError(f"duplicate job id: {job.job_id!r}")
        seen.add(job.job_id)
        if not limits.can_host(job):
            raise FarmConfigError(
                f"job {job.job_id!r} exceeds farm limits"
            )


def _need_result(value: JobResult | None) -> JobResult:
    """结果槽位必须全部填满（调度闭环不变式）。"""
    if value is None:
        raise FarmConfigError("missing job result")
    return value
