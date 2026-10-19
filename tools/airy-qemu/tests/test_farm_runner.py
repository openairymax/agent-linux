# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_runner 单元测试：进程执行、证据分离与超时回收。"""

from __future__ import annotations

import shlex
import signal
import time
from pathlib import Path

import pytest

import farm_cmdline as fc
import farm_preflight as fp
import farm_runner as fr
import farm_types as ftp

KERNEL = "bzImage"
KSFT = "kselftest-install"
SUITES = ("seccomp", "pidfd")
TAP = 'printf "TAP version 13\\n1..1\\nok 1 seccomp\\n"\n'


def target() -> ftp.Target:
    """构造 x86_64 TCG 目标（绕过宿主探测）。"""
    machine = ftp.Machine(ftp.Arch.X86_64, "q35", "max", "ttyS0")
    return ftp.Target(machine, ftp.Accel.TCG)


def make_job(
    names: tuple[str, ...] = SUITES,
    timeout: int = 60,
    log: Path | None = None,
) -> ftp.FarmJob:
    """按目标构造作业。"""
    tgt = target()
    return ftp.FarmJob(
        "smoke",
        tgt.machine,
        ftp.KernelImage(Path(KERNEL), tgt.arch),
        tuple(ftp.Suite(name) for name in names),
        accel=tgt.accel,
        timeout_s=timeout,
        log_path=log,
    )


def report(
    job: ftp.FarmJob,
    names: tuple[str, ...] | None = None,
) -> fp.Preflight:
    """为作业构造预检证据（默认覆盖全部套件）。"""
    covered = job.suite_names if names is None else names
    return fp.Preflight(
        job,
        fp.KernelFormat.BZIMAGE,
        4096,
        tuple((name, 1) for name in covered),
        None,
    )


def fake_vng(root: Path, body: str) -> Path:
    """写入可执行的假 virtme-ng 脚本。"""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "vng"
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def run_env(script: Path) -> fc.RunEnv:
    """构造以假脚本为载体的同架构运行环境。"""
    return fc.RunEnv(Path(KSFT), vng=str(script), host=ftp.Arch.X86_64)


def capture_args(root: Path) -> tuple[Path, str]:
    """返回参数落盘路径与对应的 shell 片段。"""
    path = root / "argv.txt"
    snippet = f'printf "%s\\n" "$@" > {shlex.quote(str(path))}\n'
    return path, snippet


def run(
    tmp: Path,
    body: str,
    job: ftp.FarmJob | None = None,
    sub: str = "",
) -> fr.RawRun:
    """以假脚本执行一次作业并返回证据。"""
    job = make_job() if job is None else job
    root = tmp / sub if sub else tmp
    script = fake_vng(root, body)
    return fr.run_job(
        target(),
        job,
        run_env(script),
        report(job),
        root / "out",
    )


def test_run_captures_tap_and_argv(tmp_path: Path) -> None:
    args, snippet = capture_args(tmp_path)
    run_out = run(tmp_path, snippet + TAP)
    assert run_out.exit_code == 0
    assert run_out.timed_out is False
    assert run_out.signal_no is None
    assert run_out.job_id == "smoke"
    assert run_out.tap_path.name == "smoke.tap"
    assert run_out.log_path.name == "smoke.log"
    assert "ok 1 seccomp" in run_out.tap_path.read_text(encoding="utf-8")
    env = run_env(tmp_path / "vng")
    job = make_job()
    expected = list(fc.build_run(target(), job, env))[1:]
    assert args.read_text(encoding="utf-8").splitlines() == expected


def test_run_propagates_exit_code(tmp_path: Path) -> None:
    run_out = run(tmp_path, "exit 7\n")
    assert run_out.exit_code == 7
    assert run_out.timed_out is False


def test_run_records_signal_exit(tmp_path: Path) -> None:
    run_out = run(tmp_path, "kill -TERM $$\n")
    assert run_out.exit_code == -signal.SIGTERM
    assert run_out.signal_no == signal.SIGTERM
    assert run_out.timed_out is False


def test_run_separates_tap_and_log(tmp_path: Path) -> None:
    body = 'echo "guest boot" >&2\nprintf "ok 1 pidfd\\n"\n'
    run_out = run(tmp_path, body)
    tap = run_out.tap_path.read_text(encoding="utf-8")
    log = run_out.log_path.read_text(encoding="utf-8")
    assert "ok 1 pidfd" in tap
    assert "guest boot" not in tap
    assert "guest boot" in log


def test_run_attaches_empty_stdin(tmp_path: Path) -> None:
    body = 'cat >/dev/null\nprintf "ok 1 seccomp\\n"\n'
    started = time.monotonic()
    run_out = run(tmp_path, body)
    assert run_out.exit_code == 0
    assert time.monotonic() - started < 20


def test_run_times_out_and_reaps(tmp_path: Path) -> None:
    job = make_job(timeout=1)
    started = time.monotonic()
    run_out = run(tmp_path, "exec sleep 30\n", job)
    assert run_out.timed_out is True
    assert run_out.exit_code == -signal.SIGTERM
    assert time.monotonic() - started < 20


def test_run_escalates_to_sigkill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fr, "KILL_GRACE_S", 0.2)
    body = "trap '' TERM\nwhile :; do sleep 0.05; done\n"
    run_out = run(tmp_path, body, make_job(timeout=1))
    assert run_out.timed_out is True
    assert run_out.exit_code == -signal.SIGKILL


def test_run_creates_nested_out_dir(tmp_path: Path) -> None:
    out = tmp_path / "a" / "b"
    script = fake_vng(tmp_path, TAP)
    job = make_job()
    run_out = fr.run_job(
        target(),
        job,
        run_env(script),
        report(job),
        out,
    )
    assert run_out.tap_path == out / "smoke.tap"
    assert out.is_dir()


def test_run_honours_job_log_path(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "custom.log"
    run_out = run(tmp_path, TAP, make_job(log=log))
    assert run_out.log_path == log
    assert log.is_file()


def test_run_truncates_previous_evidence(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "smoke.tap").write_text("stale\n", encoding="utf-8")
    (out / "smoke.log").write_text("stale\n", encoding="utf-8")
    run_out = run(tmp_path, TAP)
    assert "stale" not in run_out.tap_path.read_text(encoding="utf-8")
    assert "stale" not in run_out.log_path.read_text(encoding="utf-8")


def test_run_handles_paths_with_spaces(tmp_path: Path) -> None:
    body = TAP
    run_out = run(tmp_path, body, sub="dir with space")
    assert run_out.exit_code == 0
    assert "ok 1 seccomp" in run_out.tap_path.read_text(encoding="utf-8")


def test_run_rejects_missing_executable(tmp_path: Path) -> None:
    job = make_job()
    env = fc.RunEnv(
        Path(KSFT),
        vng=str(tmp_path / "missing"),
        host=ftp.Arch.X86_64,
    )
    with pytest.raises(ftp.FarmError) as excinfo:
        fr.run_job(target(), job, env, report(job), tmp_path / "out")
    assert excinfo.value.code == ftp.AIRY_QEMU_ERROR
    assert "cannot start" in excinfo.value.message


@pytest.mark.parametrize("bad", [None, "target", 1])
def test_run_rejects_bad_target(tmp_path: Path, bad: object) -> None:
    job = make_job()
    script = fake_vng(tmp_path, TAP)
    with pytest.raises(ftp.FarmConfigError):
        fr.run_job(
            bad,  # type: ignore[arg-type]
            job,
            run_env(script),
            report(job),
            tmp_path / "out",
        )


def test_run_rejects_other_job_preflight(tmp_path: Path) -> None:
    job = make_job()
    other = ftp.FarmJob(
        "other",
        job.machine,
        job.kernel,
        job.suites,
        accel=job.accel,
    )
    script = fake_vng(tmp_path, TAP)
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        fr.run_job(
            target(),
            job,
            run_env(script),
            report(other),
            tmp_path / "out",
        )
    assert "does not match" in excinfo.value.message


def test_run_rejects_partial_preflight(tmp_path: Path) -> None:
    job = make_job()
    script = fake_vng(tmp_path, TAP)
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        fr.run_job(
            target(),
            job,
            run_env(script),
            report(job, ("seccomp",)),
            tmp_path / "out",
        )
    assert "preflight suites" in excinfo.value.message


def test_run_rejects_file_out_dir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.write_text("x", encoding="utf-8")
    job = make_job()
    script = fake_vng(tmp_path, TAP)
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        fr.run_job(target(), job, run_env(script), report(job), out)
    assert "not a directory" in excinfo.value.message


def test_run_rejects_dir_log_path(tmp_path: Path) -> None:
    job = make_job(log=tmp_path)
    script = fake_vng(tmp_path, TAP)
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        fr.run_job(
            target(),
            job,
            run_env(script),
            report(job),
            tmp_path / "out",
        )
    assert "is a directory" in excinfo.value.message


def test_run_rejects_shared_tap_and_log(tmp_path: Path) -> None:
    out = tmp_path / "out"
    job = make_job(log=out / "smoke.tap")
    script = fake_vng(tmp_path, TAP)
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        fr.run_job(target(), job, run_env(script), report(job), out)
    assert "must differ" in excinfo.value.message


def test_raw_run_rejects_bool_exit_code(tmp_path: Path) -> None:
    job = make_job()
    with pytest.raises(ValueError):
        fr.RawRun(
            "smoke",
            ("vng",),
            True,  # type: ignore[arg-type]
            0.0,
            False,
            tmp_path / "smoke.tap",
            tmp_path / "smoke.log",
            report(job),
        )


def test_raw_run_rejects_negative_duration(tmp_path: Path) -> None:
    job = make_job()
    with pytest.raises(ValueError):
        fr.RawRun(
            "smoke",
            ("vng",),
            0,
            -0.1,
            False,
            tmp_path / "smoke.tap",
            tmp_path / "smoke.log",
            report(job),
        )


def test_raw_run_rejects_missing_preflight(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        fr.RawRun(
            "smoke",
            ("vng",),
            0,
            0.0,
            False,
            tmp_path / "smoke.tap",
            tmp_path / "smoke.log",
            None,  # type: ignore[arg-type]
        )
