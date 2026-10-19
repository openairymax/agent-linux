# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_sched 单元测试：资源预算、派发顺序、结果定序与错误降级。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import farm_sched as fs
import farm_types as ftp


def make_job(
    job_id: str,
    mem_mb: int = 2048,
    smp: int = 1,
) -> ftp.FarmJob:
    """构造最小可用作业，内存与 CPU 可按需覆盖。"""
    machine = ftp.Machine(ftp.Arch.X86_64, "q35", "max", "ttyS0")
    kernel = ftp.KernelImage(Path("bzImage"), ftp.Arch.X86_64)
    return ftp.FarmJob(
        job_id,
        machine,
        kernel,
        (ftp.Suite("seccomp"),),
        mem_mb=mem_mb,
        smp=smp,
    )


def pass_result(job: ftp.FarmJob) -> ftp.JobResult:
    """构造通过的终态结果。"""
    return ftp.JobResult(
        job,
        ftp.JobState.PASSED,
        0.01,
        0,
        ftp.TapSummary(1, 1, 0),
    )


class Probe:
    """记录并发峰值与执行顺序的执行器。"""

    def __init__(self, delay: float = 0.1) -> None:
        self.delay = delay
        self.active = 0
        self.peak = 0
        self.seen: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, job: ftp.FarmJob) -> ftp.JobResult:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.seen.append(job.job_id)
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        return pass_result(job)


def limits(max_jobs: int = 4, mem_mb: int = 8192, cpus: int = 8) -> ftp.FarmLimits:
    """构造宽裕预算，便于逐个维度收紧。"""
    return ftp.FarmLimits(max_jobs=max_jobs, mem_mb=mem_mb, cpus=cpus)


def test_runs_all_jobs() -> None:
    """全部作业都应执行，且结果与输入同序。"""
    jobs = (make_job("a"), make_job("b"), make_job("c"))
    probe = Probe()

    results = fs.run_batch(jobs, limits(), probe)

    assert [result.job.job_id for result in results] == ["a", "b", "c"]
    assert all(result.state is ftp.JobState.PASSED for result in results)
    assert sorted(probe.seen) == ["a", "b", "c"]


def test_single_job_runs() -> None:
    """单作业批次应正常返回。"""
    job = make_job("solo")

    results = fs.run_batch((job,), limits(max_jobs=1), Probe(delay=0.0))

    assert len(results) == 1
    assert results[0].job is job


def test_respects_max_jobs() -> None:
    """并发数不得超过 max_jobs。"""
    jobs = tuple(make_job(name) for name in ("a", "b", "c", "d"))
    probe = Probe()

    fs.run_batch(jobs, limits(max_jobs=2), probe)

    assert probe.peak == 2


def test_respects_memory() -> None:
    """累计内存不得超过预算，即使并发数仍有余量。"""
    jobs = tuple(make_job(name, mem_mb=2048) for name in ("a", "b", "c", "d"))
    probe = Probe()

    fs.run_batch(jobs, limits(max_jobs=4, mem_mb=4096), probe)

    assert probe.peak == 2


def test_respects_cpus() -> None:
    """累计 vCPU 不得超过预算。"""
    jobs = tuple(make_job(name, mem_mb=1024, smp=2) for name in ("a", "b", "c"))
    probe = Probe()

    fs.run_batch(jobs, limits(max_jobs=4, mem_mb=8192, cpus=2), probe)

    assert probe.peak == 1


def test_exact_budget_fits() -> None:
    """恰好用满预算应被接受。"""
    job = make_job("exact", mem_mb=2048, smp=2)

    results = fs.run_batch((job,), limits(max_jobs=1, mem_mb=2048, cpus=2), Probe(0.0))

    assert results[0].job is job


def test_rejects_oversized_job() -> None:
    """单个作业超出预算时整批拒绝，且不执行任何作业。"""
    probe = Probe(0.0)

    with pytest.raises(ftp.FarmConfigError, match="exceeds farm limits"):
        fs.run_batch((make_job("big", mem_mb=4096),), limits(mem_mb=2048), probe)

    assert probe.seen == []


def test_rejects_empty_jobs() -> None:
    """空批次视为配置错误。"""
    with pytest.raises(ftp.FarmConfigError, match="non-empty tuple"):
        fs.run_batch((), limits(), Probe(0.0))


def test_rejects_non_job() -> None:
    """批次中出现非 FarmJob 元素时拒绝。"""
    with pytest.raises(ftp.FarmConfigError, match="FarmJob entries"):
        fs.run_batch((make_job("a"), object()), limits(), Probe(0.0))  # type: ignore[arg-type]


def test_rejects_bad_limits() -> None:
    """预算类型非法时拒绝。"""
    with pytest.raises(ftp.FarmConfigError, match="limits must be FarmLimits"):
        fs.run_batch((make_job("a"),), object(), Probe(0.0))  # type: ignore[arg-type]


def test_rejects_non_callable() -> None:
    """执行器不可调用时拒绝。"""
    with pytest.raises(ftp.FarmConfigError, match="execute must be callable"):
        fs.run_batch((make_job("a"),), limits(), object())  # type: ignore[arg-type]


def test_rejects_duplicate_jobs() -> None:
    """重复作业 ID 会破坏结果归属，必须拒绝。"""
    jobs = (make_job("dup"), make_job("dup"))

    with pytest.raises(ftp.FarmConfigError, match="duplicate job id"):
        fs.run_batch(jobs, limits(), Probe(0.0))


def test_worker_error_becomes_result() -> None:
    """执行期 FarmError 降级为 ERROR，其余作业继续执行。"""

    def execute(job: ftp.FarmJob) -> ftp.JobResult:
        if job.job_id == "bad":
            raise ftp.FarmError("boom")
        return pass_result(job)

    results = fs.run_batch((make_job("bad"), make_job("good")), limits(), execute)

    assert results[0].state is ftp.JobState.ERROR
    assert results[0].reason == "boom"
    assert results[1].state is ftp.JobState.PASSED


def test_worker_config_error_propagates() -> None:
    """执行期 FarmConfigError 属于调用方缺陷，向上抛出。"""

    def execute(job: ftp.FarmJob) -> ftp.JobResult:
        raise ftp.FarmConfigError("misconfigured")

    with pytest.raises(ftp.FarmConfigError, match="misconfigured"):
        fs.run_batch((make_job("a"),), limits(), execute)


def test_rejects_wrong_return_type() -> None:
    """执行器返回非 JobResult 时拒绝。"""

    def execute(job: ftp.FarmJob) -> ftp.JobResult:
        return None  # type: ignore[return-value]

    with pytest.raises(ftp.FarmConfigError, match="must return JobResult"):
        fs.run_batch((make_job("a"),), limits(), execute)


def test_rejects_mismatched_job() -> None:
    """结果归属与派发作业不一致时拒绝。"""

    def execute(job: ftp.FarmJob) -> ftp.JobResult:
        return pass_result(make_job("other"))

    with pytest.raises(ftp.FarmConfigError, match="does not match"):
        fs.run_batch((make_job("a"),), limits(), execute)


def test_results_follow_input_order() -> None:
    """慢作业先派发，结果仍按输入顺序返回。"""

    def execute(job: ftp.FarmJob) -> ftp.JobResult:
        time.sleep(0.1 if job.job_id == "slow" else 0.0)
        return pass_result(job)

    jobs = (make_job("slow"), make_job("fast"), make_job("mid"))
    results = fs.run_batch(jobs, limits(), execute)

    assert [result.job.job_id for result in results] == ["slow", "fast", "mid"]


def test_keeps_result_evidence() -> None:
    """执行器返回的结果原样保留，不做重包装。"""

    def execute(job: ftp.FarmJob) -> ftp.JobResult:
        return ftp.JobResult(
            job,
            ftp.JobState.FAILED,
            1.25,
            2,
            ftp.TapSummary(1, 0, 1),
            "not ok",
            ("vng", "--run"),
        )

    result = fs.run_batch((make_job("a"),), limits(), execute)[0]

    assert result.state is ftp.JobState.FAILED
    assert result.duration_s == 1.25
    assert result.qemu_argv == ("vng", "--run")


