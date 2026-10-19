# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_tapagg 单元测试：多套件计划聚合、SKIP 计数与证据校验。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import farm_preflight as fp
import farm_runner as fr
import farm_tapagg as ta
import farm_types as ftp

ENTRIES = (("seccomp", 2), ("pidfd", 1))
TWO_BLOCKS = """\
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


def make_job(names: tuple[str, ...]) -> ftp.FarmJob:
    """构造覆盖指定套件的作业。"""
    machine = ftp.Machine(ftp.Arch.X86_64, "q35", "max", "ttyS0")
    kernel = ftp.KernelImage(Path("bzImage"), ftp.Arch.X86_64)
    return ftp.FarmJob(
        "smoke",
        machine,
        kernel,
        tuple(ftp.Suite(name) for name in names),
    )


def make_pre(entries: tuple[tuple[str, int], ...] = ENTRIES) -> fp.Preflight:
    """构造与套件描述一致的预检证据。"""
    names = tuple(name for name, _ in entries)
    return fp.Preflight(
        make_job(names),
        fp.KernelFormat.BZIMAGE,
        4096,
        entries,
        None,
    )


def make_raw(
    root: Path,
    text: str,
    entries: tuple[tuple[str, int], ...] = ENTRIES,
) -> fr.RawRun:
    """把 TAP 文本落盘并包装成运行器证据。"""
    root.mkdir(parents=True, exist_ok=True)
    tap = root / "smoke.tap"
    tap.write_text(text, encoding="utf-8")
    return fr.RawRun(
        "smoke",
        ("vng",),
        0,
        1.0,
        False,
        tap,
        root / "smoke.log",
        make_pre(entries),
    )


def parse(text: str, entries=ENTRIES) -> ta.TapReport:
    """以默认套件描述聚合 TAP 文本。"""
    return ta.parse_tap(text, entries)


def test_parse_aggregates_plan_blocks() -> None:
    report = parse(TWO_BLOCKS)
    assert report.summary.plan_total == 3
    assert report.summary.ok_count == 3
    assert report.summary.not_ok_count == 0
    assert report.summary.skip_count == 0
    assert report.complete is True
    assert report.passed is True
    assert report.silent == ()


def test_parse_keeps_suite_order_and_counts() -> None:
    report = parse(TWO_BLOCKS)
    assert [tally.name for tally in report.suites] == ["seccomp", "pidfd"]
    seccomp, pidfd = report.suites
    assert (seccomp.plan_total, seccomp.ok_count) == (2, 2)
    assert (pidfd.plan_total, pidfd.ok_count) == (1, 1)
    assert pidfd.passed is True


def test_parse_counts_skip_separately() -> None:
    text = TWO_BLOCKS.replace(
        "ok 2 selftests: seccomp: seccomp_benchmark",
        "ok 2 selftests: seccomp: seccomp_benchmark # SKIP",
    )
    report = parse(text)
    assert report.summary.ok_count == 2
    assert report.summary.skip_count == 1
    assert report.summary.executed == 3
    assert report.passed is True
    assert report.suites[0].skip_count == 1


def test_parse_all_skipped_suite_is_complete() -> None:
    text = "TAP version 13\n1..1\n" \
        "ok 1 selftests: pidfd: pidfd_open # SKIP\n"
    report = parse(text, (("pidfd", 1),))
    assert report.summary.skip_count == 1
    assert report.complete is True


def test_parse_counts_failure_and_names_suite() -> None:
    text = TWO_BLOCKS.replace(
        "ok 1 selftests: pidfd: pidfd_open",
        "not ok 1 selftests: pidfd: pidfd_open # exit=1",
    )
    report = parse(text)
    assert report.summary.not_ok_count == 1
    assert report.failed == ("pidfd",)
    assert report.passed is False


def test_parse_counts_timeout_directive_as_failure() -> None:
    text = "TAP version 13\n1..1\n" \
        "not ok 1 selftests: pidfd: pidfd_open # TIMEOUT 45 seconds\n"
    report = parse(text, (("pidfd", 1),))
    assert report.summary.not_ok_count == 1
    assert report.silent == ()


def test_parse_marks_partial_results_missing() -> None:
    text = "TAP version 13\n1..2\n" \
        "ok 1 selftests: seccomp: seccomp_bpf\n"
    report = parse(text, (("seccomp", 2),))
    assert report.suites[0].missing == 1
    assert report.complete is False
    assert report.passed is False


def test_parse_marks_silent_suite_missing() -> None:
    text = "TAP version 13\n1..2\n" \
        "ok 1 selftests: seccomp: seccomp_bpf\n" \
        "ok 2 selftests: seccomp: seccomp_benchmark\n"
    report = parse(text)
    assert report.silent == ("pidfd",)
    assert report.summary.missing == 1
    assert report.complete is False


def test_parse_accepts_dash_prefix() -> None:
    text = "TAP version 13\n1..1\n" \
        "ok 1 - selftests: pidfd: pidfd_open\n"
    report = parse(text, (("pidfd", 1),))
    assert report.summary.ok_count == 1


def test_parse_ignores_comments_and_foreign_lines() -> None:
    text = "TAP version 13\n# diagnostic\n1..1\n" \
        "    ok 9 selftests: pidfd: nested\n" \
        "ok 1 selftests: pidfd: pidfd_open\n"
    report = parse(text, (("pidfd", 1),))
    assert report.summary.ok_count == 1


def test_parse_rejects_missing_plan() -> None:
    with pytest.raises(ftp.FarmError, match="plan line"):
        parse("ok 1 selftests: seccomp: seccomp_bpf\n")


def test_parse_rejects_empty_text() -> None:
    with pytest.raises(ftp.FarmError, match="plan line"):
        parse("")


def test_parse_rejects_unknown_suite() -> None:
    text = "TAP version 13\n1..1\nok 1 selftests: net: forwarding\n"
    with pytest.raises(ftp.FarmError, match="unknown suite"):
        parse(text, (("pidfd", 1),))


def test_parse_rejects_unlabelled_result() -> None:
    text = "TAP version 13\n1..1\nok 1 seccomp_bpf\n"
    with pytest.raises(ftp.FarmError, match="unknown suite"):
        parse(text, (("seccomp", 1),))


def test_parse_rejects_declared_overflow() -> None:
    text = "TAP version 13\n1..5\nok 1 selftests: pidfd: pidfd_open\n"
    with pytest.raises(ftp.FarmError, match="declares 5"):
        parse(text, (("pidfd", 1),))


def test_parse_rejects_suite_overflow() -> None:
    text = "TAP version 13\n1..1\n" \
        "ok 1 selftests: pidfd: a\n" \
        "ok 2 selftests: pidfd: b\n"
    with pytest.raises(ftp.FarmError, match="pidfd"):
        parse(text, (("pidfd", 1),))


@pytest.mark.parametrize(
    "entries",
    [
        (),
        [("pidfd", 1)],
        (("pidfd", 0),),
        (("pidfd", True),),
        (("pidfd",),),
        (("", 1),),
        (("pidfd", 1), ("pidfd", 1)),
    ],
)
def test_parse_rejects_bad_suites(entries) -> None:
    with pytest.raises(ftp.FarmConfigError):
        ta.parse_tap("1..1\n", entries)


def test_parse_rejects_non_text() -> None:
    with pytest.raises(ftp.FarmConfigError, match="text must be str"):
        ta.parse_tap(b"1..1\n", (("pidfd", 1),))


def test_read_tap_reads_evidence(tmp_path: Path) -> None:
    path = tmp_path / "smoke.tap"
    path.write_text(TWO_BLOCKS, encoding="utf-8")
    report = ta.read_tap(path, ENTRIES)
    assert report.summary.plan_total == 3
    assert report.passed is True


def test_read_tap_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ftp.FarmError) as caught:
        ta.read_tap(tmp_path / "absent.tap", ENTRIES)
    assert caught.value.code == ftp.AIRY_QEMU_ERROR
    assert "cannot read TAP evidence" in str(caught.value)


def test_read_tap_rejects_path_type() -> None:
    with pytest.raises(ftp.FarmConfigError, match="path must be"):
        ta.read_tap(1, ENTRIES)


def test_collect_tap_uses_raw_evidence(tmp_path: Path) -> None:
    raw = make_raw(tmp_path, TWO_BLOCKS)
    report = ta.collect_tap(raw)
    assert report.summary.ok_count == 3
    assert report.passed is True


def test_collect_tap_rejects_type() -> None:
    with pytest.raises(ftp.FarmConfigError, match="raw must be RawRun"):
        ta.collect_tap("smoke")


def test_tally_rejects_bad_counts() -> None:
    with pytest.raises(ValueError, match="plan_total"):
        ta.SuiteTally("pidfd", 1, 2, 0)
    with pytest.raises(ValueError, match="plan_total"):
        ta.SuiteTally("pidfd", 1, 0, 0, 2)
    with pytest.raises(ValueError, match="non-negative int"):
        ta.SuiteTally("pidfd", True, 0, 0)
    with pytest.raises(ValueError, match="non-empty"):
        ta.SuiteTally("", 1, 0, 0)


def test_report_rejects_inconsistent_suites() -> None:
    summary = ftp.TapSummary(3, 3, 0, 0)
    one = ta.SuiteTally("pidfd", 1, 1, 0)
    with pytest.raises(ValueError, match="duplicate suite"):
        ta.TapReport(summary, (one, one))
    with pytest.raises(ValueError, match="plan_total mismatch"):
        ta.TapReport(summary, (one,))
    with pytest.raises(ValueError, match="must be TapSummary"):
        ta.TapReport("summary", (one,))


def test_summary_counts_skips_as_executed() -> None:
    summary = ftp.TapSummary(2, 1, 0, 1)
    assert summary.executed == 2
    assert summary.complete is True
    with pytest.raises(ValueError, match="executed exceeds"):
        ftp.TapSummary(1, 0, 0, 2)


def test_parse_matches_canonical_counts(repo_root: Path) -> None:
    parser = load_canonical(repo_root)
    text = "TAP version 13\n1..2\n" \
        "ok 1 selftests: pidfd: a # SKIP\n" \
        "not ok 2 selftests: pidfd: b # exit=1\n"
    canonical = parser.parse_tap(text)
    report = parse(text, (("pidfd", 2),))
    assert canonical["plan_total"] == report.summary.plan_total
    assert canonical["not_ok_count"] == report.summary.not_ok_count
    assert canonical["ok_count"] == (
        report.summary.ok_count + report.summary.skip_count
    )


def load_canonical(repo_root: Path):
    """按路径加载 CI 使用的规范 TAP 解析器。"""
    path = repo_root / "tools" / "parse-tap.py"
    spec = importlib.util.spec_from_file_location("canonical_tap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
