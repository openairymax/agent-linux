# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_report 单元测试：JSON 归档、落盘与控制台摘要。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import farm_report as fr
import farm_types as ftp
import farm_verdict as fv


def make_job(job_id: str, log_path: Path | None = None) -> ftp.FarmJob:
    """构造指定 ID 与日志落点的作业。"""
    machine = ftp.Machine(ftp.Arch.X86_64, "q35", "max", "ttyS0")
    kernel = ftp.KernelImage(Path("bzImage"), ftp.Arch.X86_64)
    return ftp.FarmJob(
        job_id,
        machine,
        kernel,
        (ftp.Suite("seccomp"),),
        log_path=log_path,
    )


def make_passed(job_id: str = "smoke") -> ftp.JobResult:
    """构造通过结果。"""
    return ftp.JobResult(
        make_job(job_id),
        ftp.JobState.PASSED,
        1.5,
        0,
        ftp.TapSummary(3, 3, 0),
        qemu_argv=("qemu-system-x86_64", "-accel", "tcg"),
    )


def make_failed(job_id: str = "net") -> ftp.JobResult:
    """构造 TAP 失败结果。"""
    return ftp.JobResult(
        make_job(job_id),
        ftp.JobState.FAILED,
        2.5,
        1,
        ftp.TapSummary(2, 1, 1),
        "TAP failures: 1 not ok in seccomp",
    )


def make_skipped(job_id: str = "hw") -> ftp.JobResult:
    """构造全跳过结果。"""
    return ftp.JobResult(
        make_job(job_id),
        ftp.JobState.SKIPPED,
        0.5,
        0,
        ftp.TapSummary(2, 0, 0, 2),
        "all 2 cases skipped",
    )


def test_report_json_structure() -> None:
    """文档应含版本、门禁、计数、状态分布与作业明细。"""
    report = fv.GateReport((make_passed(), make_failed()))

    payload = json.loads(fr.report_json(report))

    assert payload["version"] == ftp.FARM_VERSION
    assert payload["gate"] == "failed"
    assert payload["blocked"] == ["net"]
    assert payload["counts"] == {"total": 2, "ok": 1, "blocking": 1}
    assert payload["states"] == {"passed": 1, "failed": 1}
    assert [job["job_id"] for job in payload["jobs"]] == ["smoke", "net"]


def test_report_json_is_deterministic() -> None:
    """同一门禁结果应产生逐字节相同的输出且键有序。"""
    report = fv.GateReport((make_passed(), make_failed()))

    first = fr.report_json(report)

    assert first == fr.report_json(report)
    assert list(json.loads(first)) == sorted(json.loads(first))
    assert first.endswith("\n")


def test_report_json_records_tap_counts() -> None:
    """TAP 计数与派生判定必须完整落档。"""
    payload = json.loads(fr.report_json(fv.GateReport((make_failed(),))))

    tap = payload["jobs"][0]["tap"]
    assert tap == {
        "plan_total": 2,
        "ok_count": 1,
        "not_ok_count": 1,
        "skip_count": 0,
        "executed": 2,
        "missing": 0,
        "complete": True,
        "passed": False,
    }


def test_report_json_keeps_argv_and_suites() -> None:
    """QEMU 命令行与套件列表是复现失败所必需的证据。"""
    payload = json.loads(fr.report_json(fv.GateReport((make_passed(),))))

    job = payload["jobs"][0]
    assert job["qemu_argv"] == ["qemu-system-x86_64", "-accel", "tcg"]
    assert job["suites"] == ["seccomp"]


def test_report_json_omits_absent_evidence() -> None:
    """无 TAP、无日志时不应写出空字段。"""
    result = ftp.JobResult(
        make_job("err"),
        ftp.JobState.ERROR,
        0.0,
        reason="missing TAP",
    )

    job = json.loads(fr.report_json(fv.GateReport((result,))))["jobs"][0]

    assert job["tap"] is None
    assert job["exit_code"] is None
    assert "log_path" not in job


def test_report_json_includes_log_path(tmp_path: Path) -> None:
    """作业声明的日志路径应写入文档。"""
    log = tmp_path / "smoke.log"
    result = ftp.JobResult(
        make_job("smoke", log),
        ftp.JobState.PASSED,
        1.0,
        0,
        ftp.TapSummary(1, 1, 0),
    )

    job = json.loads(fr.report_json(fv.GateReport((result,))))["jobs"][0]

    assert job["log_path"] == str(log)


def test_write_json_creates_parents(tmp_path: Path) -> None:
    """落盘应按需创建父目录并写入完整内容。"""
    report = fv.GateReport((make_passed(),))
    target = tmp_path / "artifacts" / "farm.json"

    written = fr.write_json(report, target)

    assert written == target
    assert target.read_text(encoding="utf-8") == fr.report_json(report)


def test_write_json_accepts_str_path(tmp_path: Path) -> None:
    """字符串路径应被规范化为 Path。"""
    target = fr.write_json(fv.GateReport((make_passed(),)), str(tmp_path / "r.json"))

    assert isinstance(target, Path)
    assert target.is_file()


def test_write_json_rejects_empty_name(tmp_path: Path) -> None:
    """无法命名文件的路径必须拒绝。"""
    with pytest.raises(ftp.FarmConfigError, match="must name a file"):
        fr.write_json(fv.GateReport((make_passed(),)), tmp_path)


def test_write_json_rejects_non_path() -> None:
    """路径类型非法时拒绝。"""
    with pytest.raises(ftp.FarmConfigError, match="Path or str"):
        fr.write_json(fv.GateReport((make_passed(),)), 42)  # type: ignore[arg-type]


def test_summary_lists_jobs_in_order() -> None:
    """摘要应逐作业列出明细，末行给出门禁结论。"""
    report = fv.GateReport((make_passed(), make_failed(), make_skipped()))

    lines = fr.summary_lines(report)

    assert lines[0] == "farm: 3 jobs, 2 ok, 1 blocking"
    assert lines[1].strip().startswith("PASSED")
    assert "smoke" in lines[1]
    assert "tap 3/3" in lines[1]
    assert lines[2].strip().startswith("FAILED")
    assert "TAP failures" in lines[2]
    assert lines[3].strip().startswith("SKIPPED")
    assert lines[4] == "gate: FAIL blocked: net"


def test_summary_skipped_does_not_block() -> None:
    """全部跳过不阻断门禁。"""
    lines = fr.summary_lines(fv.GateReport((make_passed(), make_skipped())))

    assert lines[0] == "farm: 2 jobs, 2 ok, 0 blocking"
    assert lines[-1] == "gate: PASS"


def test_summary_omits_reason_when_empty() -> None:
    """无 reason 时摘要行不留尾随分隔符。"""
    lines = fr.summary_lines(fv.GateReport((make_passed(),)))

    assert lines[1].endswith("tap 3/3")


def test_rejects_non_report() -> None:
    """非 GateReport 输入必须拒绝。"""
    with pytest.raises(ftp.FarmConfigError, match="must be GateReport"):
        fr.report_json((make_passed(),))  # type: ignore[arg-type]
    with pytest.raises(ftp.FarmConfigError, match="must be GateReport"):
        fr.summary_lines(None)  # type: ignore[arg-type]
