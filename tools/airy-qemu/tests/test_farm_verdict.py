# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_verdict 单元测试：终态判定、证据缺失与门禁汇总。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import farm_preflight as fp
import farm_runner as fr
import farm_verdict as fv
import farm_types as ftp

ENTRIES = (("seccomp", 2), ("pidfd", 1))
PASS_TAP = """\
TAP version 13
1..2
# selftests: seccomp: seccomp_bpf
ok 1 selftests: seccomp: seccomp_bpf
ok 2 selftests: seccomp: seccomp_benchmark
TAP version 13
1..1
# selftests: pidfd: pidfd_open
ok 1 selftests: pidfd: pidfd_open
"""
NO_PLAN_TAP = "TAP version 13\nok 1 selftests: seccomp: seccomp_bpf\n"


def make_job(job_id: str, names: tuple[str, ...]) -> ftp.FarmJob:
    """构造覆盖指定套件的作业。"""
    machine = ftp.Machine(ftp.Arch.X86_64, "q35", "max", "ttyS0")
    kernel = ftp.KernelImage(Path("bzImage"), ftp.Arch.X86_64)
    return ftp.FarmJob(
        job_id,
        machine,
        kernel,
        tuple(ftp.Suite(name) for name in names),
    )


def make_pre(
    job_id: str = "smoke",
    entries: tuple[tuple[str, int], ...] = ENTRIES,
) -> fp.Preflight:
    """构造与套件描述一致的预检证据。"""
    names = tuple(name for name, _ in entries)
    return fp.Preflight(
        make_job(job_id, names),
        fp.KernelFormat.BZIMAGE,
        4096,
        entries,
        None,
    )


def make_raw(
    root: Path,
    text: str | None,
    job_id: str = "smoke",
    entries: tuple[tuple[str, int], ...] = ENTRIES,
    exit_code: int = 0,
    timed_out: bool = False,
) -> fr.RawRun:
    """把 TAP 文本落盘并包装成运行器证据；text 为 None 时不落盘。"""
    root.mkdir(parents=True, exist_ok=True)
    tap = root / f"{job_id}.tap"
    if text is not None:
        tap.write_text(text, encoding="utf-8")
    return fr.RawRun(
        job_id,
        ("vng", "--run", "bzImage"),
        exit_code,
        1.5,
        timed_out,
        tap,
        root / f"{job_id}.log",
        make_pre(job_id, entries),
    )


def test_judge_passing_run(tmp_path: Path) -> None:
    result = fv.judge_run(make_raw(tmp_path, PASS_TAP))
    assert result.state is ftp.JobState.PASSED
    assert result.exit_code == 0
    assert result.tap is not None
    assert result.tap.ok_count == 3
    assert result.reason == ""
    assert result.qemu_argv == ("vng", "--run", "bzImage")
    assert result.duration_s == 1.5


def test_judge_propagates_job_id(tmp_path: Path) -> None:
    result = fv.judge_run(make_raw(tmp_path, PASS_TAP, job_id="arm64"))
    assert result.job.job_id == "arm64"


def test_judge_partial_skip_still_passes(tmp_path: Path) -> None:
    text = PASS_TAP.replace(
        "ok 1 selftests: pidfd: pidfd_open",
        "ok 1 selftests: pidfd: pidfd_open # SKIP no kvm",
    )
    result = fv.judge_run(make_raw(tmp_path, text))
    assert result.state is ftp.JobState.PASSED
    assert result.tap is not None
    assert result.tap.skip_count == 1


def test_judge_all_skipped_is_skipped(tmp_path: Path) -> None:
    text = """\
TAP version 13
1..1
ok 1 selftests: seccomp: seccomp_bpf # SKIP hardware
TAP version 13
1..1
ok 1 selftests: pidfd: pidfd_open # SKIP hardware
"""
    entries = (("seccomp", 1), ("pidfd", 1))
    result = fv.judge_run(make_raw(tmp_path, text, entries=entries))
    assert result.state is ftp.JobState.SKIPPED
    assert result.reason == "all 2 cases skipped"


def test_judge_not_ok_fails(tmp_path: Path) -> None:
    text = PASS_TAP.replace(
        "ok 1 selftests: pidfd: pidfd_open",
        "not ok 1 selftests: pidfd: pidfd_open # exit=1",
    )
    result = fv.judge_run(make_raw(tmp_path, text))
    assert result.state is ftp.JobState.FAILED
    assert "1 not ok" in result.reason
    assert "pidfd" in result.reason


def test_judge_missing_cases_fails(tmp_path: Path) -> None:
    text = """\
TAP version 13
1..2
ok 1 selftests: seccomp: seccomp_bpf
TAP version 13
1..1
ok 1 selftests: pidfd: pidfd_open
"""
    result = fv.judge_run(make_raw(tmp_path, text))
    assert result.state is ftp.JobState.FAILED
    assert "1 of 3 cases missing" in result.reason


def test_judge_silent_suite_fails(tmp_path: Path) -> None:
    text = """\
TAP version 13
1..2
ok 1 selftests: seccomp: seccomp_bpf
ok 2 selftests: seccomp: seccomp_benchmark
"""
    result = fv.judge_run(make_raw(tmp_path, text))
    assert result.state is ftp.JobState.FAILED
    assert "silent suites: pidfd" in result.reason


def test_judge_timeout_keeps_partial_tap(tmp_path: Path) -> None:
    raw = make_raw(tmp_path, PASS_TAP, exit_code=-9, timed_out=True)
    result = fv.judge_run(raw)
    assert result.state is ftp.JobState.TIMEOUT
    assert result.reason.startswith("timeout after ")
    assert result.tap is not None


def test_judge_timeout_without_tap(tmp_path: Path) -> None:
    raw = make_raw(tmp_path, None, exit_code=-9, timed_out=True)
    result = fv.judge_run(raw)
    assert result.state is ftp.JobState.TIMEOUT
    assert result.tap is None


def test_judge_signal_fails(tmp_path: Path) -> None:
    raw = make_raw(tmp_path, PASS_TAP, exit_code=-11)
    result = fv.judge_run(raw)
    assert result.state is ftp.JobState.FAILED
    assert result.reason == "runner terminated by signal 11"


def test_judge_nonzero_exit_fails(tmp_path: Path) -> None:
    raw = make_raw(tmp_path, PASS_TAP, exit_code=3)
    result = fv.judge_run(raw)
    assert result.state is ftp.JobState.FAILED
    assert result.reason == "runner exited with code 3"


def test_judge_missing_evidence_is_error(tmp_path: Path) -> None:
    raw = make_raw(tmp_path, None)
    result = fv.judge_run(raw)
    assert result.state is ftp.JobState.ERROR
    assert "cannot read TAP evidence" in result.reason
    assert result.tap is None


def test_judge_bad_tap_is_error(tmp_path: Path) -> None:
    result = fv.judge_run(make_raw(tmp_path, NO_PLAN_TAP))
    assert result.state is ftp.JobState.ERROR
    assert "plan line" in result.reason


def test_judge_unknown_suite_is_error(tmp_path: Path) -> None:
    text = PASS_TAP.replace("selftests: pidfd:", "selftests: net:")
    result = fv.judge_run(make_raw(tmp_path, text))
    assert result.state is ftp.JobState.ERROR
    assert "unknown suite" in result.reason


def test_judge_rejects_non_raw() -> None:
    with pytest.raises(ftp.FarmConfigError):
        fv.judge_run("smoke")


def test_gate_passes_when_all_jobs_ok(tmp_path: Path) -> None:
    first = fv.judge_run(make_raw(tmp_path, PASS_TAP, job_id="x86"))
    second = fv.judge_run(make_raw(tmp_path, PASS_TAP, job_id="arm"))
    gate = fv.GateReport((first, second))
    assert gate.count == 2
    assert gate.ok is True
    assert gate.failed == ()


def test_gate_blocks_on_failed_job(tmp_path: Path) -> None:
    good = fv.judge_run(make_raw(tmp_path, PASS_TAP, job_id="x86"))
    bad = fv.judge_run(make_raw(tmp_path, None, job_id="arm"))
    gate = fv.GateReport((good, bad))
    assert gate.ok is False
    assert gate.failed == ("arm",)


def test_gate_skipped_job_does_not_block(tmp_path: Path) -> None:
    text = """\
TAP version 13
1..1
ok 1 selftests: seccomp: seccomp_bpf # SKIP hardware
TAP version 13
1..1
ok 1 selftests: pidfd: pidfd_open # SKIP hardware
"""
    entries = (("seccomp", 1), ("pidfd", 1))
    skipped = fv.judge_run(make_raw(tmp_path, text, entries=entries))
    gate = fv.GateReport((skipped,))
    assert gate.ok is True


@pytest.mark.parametrize(
    "results",
    [
        (),
        (object(),),
        ("smoke",),
    ],
)
def test_gate_rejects_bad_results(results: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        fv.GateReport(results)


def test_gate_rejects_duplicate_jobs(tmp_path: Path) -> None:
    first = fv.judge_run(make_raw(tmp_path, PASS_TAP, job_id="x86"))
    second = fv.judge_run(make_raw(tmp_path, PASS_TAP, job_id="x86"))
    with pytest.raises(ValueError, match="duplicate job result"):
        fv.GateReport((first, second))
