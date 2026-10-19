# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_cmdline — QEMU 测试农场的运行器命令行构造。

按已解析目标与作业生成 virtme-ng 的确定性 argv，由运行器直接交给
subprocess，不经过 shell 拼接。选择 virtme-ng 而非手写 qemu-system
命令行，是因为 CI 基线（.github/workflows/ci-kernel.yml）与
docs-linux/70-build-system/03-ci-cd-pipeline.md §5.3 都以 virtme-ng
作为 kselftest 的 QEMU 载体：guest 根文件系统、9p 共享与 --exec 执行
由 virtme-ng 负责，本模块只做参数装配与契约校验，避免重复实现一套
guest 引导环境。

同架构运行用 --force-9p 复用宿主根文件系统，与 CI 基线一致；跨架构
必须显式提供 rootfs 磁盘镜像（--root-disk），因为异架构 guest 无法
直接使用宿主的 kselftest 安装目录。

权威依据：
  docs-linux/70-build-system/03-ci-cd-pipeline.md §5.3
    OS-TEST-013~022（QEMU 运行 kselftest 与 TAP 解析）
  docs-linux/80-testing/02-kselftest.md
    OS-STD-009（测试超时默认 60 秒）
  .github/workflows/ci-kernel.yml（virtme-ng 实际调用基线）
  kernel/tools/testing/selftests/run_kselftest.sh（-c/--collection）

Example:
    >>> env = RunEnv(Path("kselftest-install"), host=Arch.X86_64)
    >>> build_run(target, job, env)[0]
    'vng'

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# 2. 本工具模块导入
from farm_target import host_arch
from farm_types import (
    Accel,
    Arch,
    Argv,
    FarmConfigError,
    FarmJob,
    Target,
)

# 3. 公开导出
__all__ = [
    "DEFAULT_ROOT_DEV",
    "RUN_SCRIPT",
    "VNG_ARCH",
    "RunEnv",
    "build_run",
    "ksft_cmd",
]

# 4. 常量
RUN_SCRIPT: Final[str] = "./run_kselftest.sh"
DEFAULT_ROOT_DEV: Final[str] = "/dev/vda"

# 5. 农场架构到 virtme-ng --arch 取值的唯一映射
VNG_ARCH: Final[dict[Arch, str]] = {
    Arch.X86_64: "x86_64",
    Arch.ARM64: "arm64",
    Arch.RISCV: "riscv64",
    Arch.LOONGARCH: "loongarch64",
}

_DEV_RE: Final[re.Pattern[str]] = re.compile(r"/dev/[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class RunEnv:
    """运行器宿主环境（宿主机侧路径与可选 rootfs 载体）。"""

    ksft_dir: Path
    vng: str = "vng"
    rootfs: Path | None = None
    root_dev: str = DEFAULT_ROOT_DEV
    root_fstype: str = ""
    busybox: Path | None = None
    host: Arch | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ksft_dir", _as_path(self.ksft_dir, "ksft_dir"))
        _text(self.vng, "vng")
        if self.rootfs is not None:
            object.__setattr__(self, "rootfs", _as_path(self.rootfs, "rootfs"))
        _root_dev(self.root_dev)
        if self.root_fstype:
            _text(self.root_fstype, "root_fstype")
            if self.rootfs is None:
                raise _cfg("root_fstype requires rootfs")
        if self.busybox is not None:
            object.__setattr__(self, "busybox", _as_path(self.busybox, "busybox"))
        if self.host is not None and not isinstance(self.host, Arch):
            raise _cfg(
                f"host must be Arch or None, got {type(self.host).__name__}"
            )


def ksft_cmd(job: FarmJob) -> str:
    """构造 guest 内执行 kselftest 的命令字符串。

    Args:
        job: 农场作业（套件名已由 Suite 校验为安全标识符）。

    Returns:
        形如 ``./run_kselftest.sh --summary --collection seccomp`` 的命令。

    Raises:
        FarmConfigError: job 不是 FarmJob。
    """
    if not isinstance(job, FarmJob):
        raise _cfg(f"job must be FarmJob, got {type(job).__name__}")
    parts = [RUN_SCRIPT, "--summary"]
    for suite in job.suites:
        parts.extend(("--collection", suite.name))
    return " ".join(parts)


def build_run(target: Target, job: FarmJob, env: RunEnv) -> Argv:
    """构造一次 virtme-ng 运行的完整 argv。

    Args:
        target: 已解析目标（机器定义 + 生效加速器）。
        job: 待执行作业（内核镜像、套件、资源预算）。
        env: 宿主环境（kselftest 目录、可选 rootfs 与 busybox）。

    Returns:
        可直接传给 subprocess 的 argv 元组。

    Raises:
        FarmConfigError: 目标与作业不一致，或跨架构缺少 rootfs。
    """
    _check(target, job, env)
    argv: list[str] = [
        env.vng,
        "--run",
        str(job.kernel.path),
        "--arch",
        VNG_ARCH[target.arch],
        "--memory",
        f"{job.mem_mb}M",
        "--cpus",
        str(job.smp),
        "--cwd",
        str(env.ksft_dir),
        "--append",
        job.machine.console_arg,
        "--disable-microvm",
        "--qemu-opts=-M",
        f"--qemu-opts={job.machine.machine}",
        "--qemu-opts=-cpu",
        f"--qemu-opts={job.machine.cpu}",
    ]
    argv.extend(_root_args(env))
    if job.accel is Accel.TCG:
        argv.append("--disable-kvm")
    if env.busybox is not None:
        argv.extend(("--busybox", str(env.busybox)))
    argv.extend(("--exec", ksft_cmd(job), "--quiet"))
    return tuple(argv)


def _check(target: Target, job: FarmJob, env: RunEnv) -> None:
    """校验目标、作业与运行环境的一致性。"""
    if not isinstance(target, Target):
        raise _cfg(f"target must be Target, got {type(target).__name__}")
    if not isinstance(job, FarmJob):
        raise _cfg(f"job must be FarmJob, got {type(job).__name__}")
    if not isinstance(env, RunEnv):
        raise _cfg(f"env must be RunEnv, got {type(env).__name__}")
    if job.machine != target.machine:
        raise _cfg("job machine does not match resolved target")
    if job.accel is not target.accel:
        raise _cfg(
            f"job accel {job.accel.value} does not match target {target.accel.value}"
        )
    if job.kernel.arch is not target.arch:
        raise _cfg(
            f"kernel arch {job.kernel.arch.value} does not match "
            f"target arch {target.arch.value}"
        )
    host = env.host if env.host is not None else host_arch()
    if env.rootfs is None and host is not target.arch:
        where = "unknown host" if host is None else host.value
        raise _cfg(f"{target.arch.value} requires rootfs when running on {where}")


def _root_args(env: RunEnv) -> list[str]:
    """按是否提供 rootfs 生成根文件系统相关参数。"""
    if env.rootfs is None:
        return ["--force-9p"]
    args = ["--root-disk", str(env.rootfs), "--root-dev", env.root_dev]
    if env.root_fstype:
        args.extend(("--root-fstype", env.root_fstype))
    return args


def _cfg(message: str) -> FarmConfigError:
    """构造统一错误码的配置异常。"""
    return FarmConfigError(message)


def _as_path(value: object, name: str) -> Path:
    """把字符串或 Path 规范化为非空 Path。"""
    path = Path(value) if isinstance(value, str) else value
    if not isinstance(path, Path) or not path.name:
        raise _cfg(f"{name} must be a non-empty path, got {value!r}")
    return path


def _text(value: object, name: str) -> str:
    """校验必填文本字段。"""
    if not isinstance(value, str) or not value.strip():
        raise _cfg(f"{name} must be a non-empty string")
    if value != value.strip():
        raise _cfg(f"{name} must not have surrounding whitespace: {value!r}")
    return value


def _root_dev(value: object) -> str:
    """校验 rootfs 设备节点路径。"""
    if not isinstance(value, str) or not _DEV_RE.fullmatch(value):
        raise _cfg(f"root_dev must be a /dev node path, got {value!r}")
    return value
