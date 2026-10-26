# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_runner — QEMU 测试农场的作业执行与原始取证。

本模块把已通过预检的作业交给 virtme-ng 执行，并完整保留原始证据：
stdout 单独落盘为 TAP 流，stderr 落盘为控制台日志。stdout 与 stderr
分离直接映射 CI 基线（.github/workflows/kernel-verify.yml 以
``vng --run ... > kselftest.tap`` 收集 TAP），TAP 文件必须保持纯净，
诊断信息不得混入其中。

执行器只负责“跑完并取证”，不做 PASS/FAIL 判定：退出码、耗时与 TAP
文件交给判定层解释，避免把判定策略耦合进进程管理。

超时以作业墙钟预算为准。触发超时后先向整个进程组发送 SIGTERM，
等待 KILL_GRACE_S 秒仍未退出则发送 SIGKILL。QEMU 与 virtme-ng 会派生
子进程，必须按进程组回收；否则超时后残留的 QEMU 会持续占用内存与
CPU，污染后续作业的测量结果。

权威依据：
  docs-linux/70-build-system/03-ci-cd-pipeline.md §5.3
    OS-TEST-013~022（QEMU 运行 kselftest 与 TAP 解析）
  docs-linux/80-testing/02-kselftest.md
    OS-STD-009（测试超时预算）
  .github/workflows/kernel-verify.yml（virtme-ng 实际调用基线）

Example:
    >>> run = run_job(target, job, env, report, Path("out"))
    >>> run.exit_code
    0

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

# 2. 本工具模块导入
from farm_cmdline import RunEnv, build_run
from farm_preflight import Preflight
from farm_types import (
    AIRY_QEMU_ERROR,
    Argv,
    FarmConfigError,
    FarmError,
    FarmJob,
    Target,
)

# 3. 公开导出
__all__ = [
    "KILL_GRACE_S",
    "RawRun",
    "run_job",
]

# 4. 契约常量
KILL_GRACE_S: Final[float] = 5.0


@dataclass(frozen=True)
class RawRun:
    """一次执行的原始证据（未解释的退出码、耗时与落盘文件）。"""

    job_id: str
    argv: Argv
    exit_code: int
    duration_s: float
    timed_out: bool
    tap_path: Path
    log_path: Path
    preflight: Preflight

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id:
            raise ValueError("job_id must be a non-empty string")
        if not isinstance(self.argv, tuple) or not self.argv:
            raise ValueError("argv must be a non-empty tuple")
        if any(not isinstance(arg, str) for arg in self.argv):
            raise ValueError("argv must contain only strings")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError(f"exit_code must be int, got {self.exit_code!r}")
        if isinstance(self.duration_s, bool) or self.duration_s < 0:
            raise ValueError(
                f"duration_s must be non-negative, got {self.duration_s!r}"
            )
        if not isinstance(self.timed_out, bool):
            raise ValueError("timed_out must be bool")
        for name in ("tap_path", "log_path"):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.name:
                raise ValueError(f"{name} must be a non-empty Path")
        if not isinstance(self.preflight, Preflight):
            raise ValueError("preflight must be Preflight")

    @property
    def signal_no(self) -> int | None:
        """进程被信号终止时的信号编号，正常退出时为 None。"""
        return -self.exit_code if self.exit_code < 0 else None


def run_job(
    target: Target,
    job: FarmJob,
    env: RunEnv,
    preflight: Preflight,
    out_dir: Path,
) -> RawRun:
    """执行一次 virtme-ng 作业并返回原始证据。

    Args:
        target: 已解析目标（机器定义 + 生效加速器）。
        job: 待执行作业（内核镜像、套件、资源与墙钟预算）。
        env: 宿主运行环境（kselftest 目录与 virtme-ng 载体）。
        preflight: 该作业的预检证据，必须与作业完全一致。
        out_dir: TAP 与缺省控制台日志的输出目录，不存在时自动创建。

    Returns:
        原始执行结果；非零退出码与超时均作为证据返回，不抛异常。

    Raises:
        FarmConfigError: 输入类型、预检证据或输出路径非法。
        FarmError: 进程无法启动或输出文件无法写入。
    """
    _check(target, job, env, preflight)
    argv = build_run(target, job, env)
    tap, log = _paths(job, out_dir)
    code, spent, expired = _spawn(argv, tap, log, job.timeout_s)
    return RawRun(
        job.job_id,
        argv,
        code,
        spent,
        expired,
        tap,
        log,
        preflight,
    )


def _check(
    target: Target,
    job: FarmJob,
    env: RunEnv,
    preflight: Preflight,
) -> None:
    """校验执行输入与预检证据的一致性。"""
    for value, kind in (
        (target, Target),
        (job, FarmJob),
        (env, RunEnv),
        (preflight, Preflight),
    ):
        if not isinstance(value, kind):
            raise _cfg(
                f"expected {kind.__name__}, got {type(value).__name__}"
            )
    if preflight.job != job:
        raise _cfg("preflight job does not match job")
    if preflight.suites != job.suite_names:
        raise _cfg(
            f"preflight suites {preflight.suites} do not match "
            f"job suites {job.suite_names}"
        )


def _paths(job: FarmJob, out_dir: object) -> tuple[Path, Path]:
    """解析并准备 TAP 与控制台日志路径。"""
    root = _dir(out_dir)
    tap = _file(root / f"{job.job_id}.tap", "tap_path")
    log = job.log_path
    if log is None:
        log = root / f"{job.job_id}.log"
    log = _file(log, "log_path")
    if tap.resolve() == log.resolve():
        raise _cfg(f"tap_path and log_path must differ: {tap}")
    return tap, log


def _dir(value: object) -> Path:
    """规范化输出目录并按需创建。"""
    path = Path(value) if isinstance(value, str) else value
    if not isinstance(path, Path) or not path.name:
        raise _cfg(f"out_dir must be a non-empty path, got {value!r}")
    if path.exists() and not path.is_dir():
        raise _cfg(f"out_dir is not a directory: {path}")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FarmError(
            f"cannot create out_dir {path}: {exc}",
            AIRY_QEMU_ERROR,
        ) from exc
    return path


def _file(value: object, name: str) -> Path:
    """规范化证据文件路径，拒绝目录并创建父目录。"""
    path = Path(value) if isinstance(value, str) else value
    if not isinstance(path, Path) or not path.name:
        raise _cfg(f"{name} must be a non-empty path, got {value!r}")
    if path.exists() and path.is_dir():
        raise _cfg(f"{name} is a directory: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FarmError(
            f"cannot create {name} parent {path.parent}: {exc}",
            AIRY_QEMU_ERROR,
        ) from exc
    return path


def _spawn(
    argv: Argv,
    tap: Path,
    log: Path,
    timeout: int,
) -> tuple[int, float, bool]:
    """启动子进程、限时等待并返回退出码、耗时与超时标记。"""
    started = time.monotonic()
    out = _open(tap, "tap")
    try:
        err = _open(log, "log")
    except FarmError:
        out.close()
        raise
    proc: subprocess.Popen[bytes] | None = None
    code: int | None = None
    expired = False
    try:
        with out, err:
            try:
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                    close_fds=True,
                )
            except OSError as exc:
                raise FarmError(
                    f"cannot start {argv[0]}: {exc}",
                    AIRY_QEMU_ERROR,
                ) from exc
            expired = _wait(proc, timeout)
            code = proc.returncode
    finally:
        if proc is not None and proc.poll() is None:
            _kill(proc)
    spent = time.monotonic() - started
    if code is None:
        raise FarmError(
            f"{argv[0]} produced no exit status",
            AIRY_QEMU_ERROR,
        )
    return code, spent, expired


def _open(path: Path, name: str) -> BinaryIO:
    """打开截断写入的证据文件。"""
    try:
        return path.open("wb")
    except OSError as exc:
        raise FarmError(
            f"cannot open {name} {path}: {exc}",
            AIRY_QEMU_ERROR,
        ) from exc


def _wait(proc: subprocess.Popen[bytes], timeout: int) -> bool:
    """等待子进程退出；超时则回收进程组并返回 True。"""
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        proc.wait()
        return True
    return False


def _kill(proc: subprocess.Popen[bytes]) -> None:
    """先 SIGTERM 后 SIGKILL 地终止整个进程组。"""
    _signal(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        _signal(proc.pid, signal.SIGKILL)
        proc.wait()


def _signal(pgid: int, sig: int) -> None:
    """向进程组发送信号；进程已消失时静默返回。"""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return


def _cfg(message: str) -> FarmConfigError:
    """构造统一错误码的配置异常。"""
    return FarmConfigError(message)
