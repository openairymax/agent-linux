# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_types — QEMU 测试农场共享类型与常量。

集中声明 QEMU 测试农场（L-19）在目标解析、作业调度、执行、TAP 聚合与
门禁判定各层之间共享的数据模型：目标架构与加速器、QEMU 机器描述、内核
镜像、kselftest 套件、作业与结果、TAP 汇总、资源预算与农场清单。

本模块是 L-19 十二个分片的公共地板：后续 manifest / target / cmdline /
preflight / runner / tapagg / verdict / sched / report / cli 模块统一
import 本模块，消除跨模块类型重复定义。所有模型均为真实可用的 frozen
dataclass，构造时经 __post_init__ 校验，不含任何占位或桩逻辑。

权威依据：
  docs-linux/70-build-system/03-ci-cd-pipeline.md §2.3
    OS-TEST-013~022（QEMU + kselftest + TAP 解析）
  docs-linux/80-testing/03-kernel-selftests.md
    OS-TEST-030（自检注册）、OS-KER-101（自检不得阻塞）
  .github/workflows/ci-kernel.yml（virtme-ng 实际调用基线）
  tools/parse-tap.py（TAP 解析契约：plan_total / ok_count / not_ok_count）

Example:
    >>> from farm_types import Arch, Machine, Suite, FarmJob
    >>> machine = Machine(Arch.X86_64, "q35", "max", "ttyS0")
    >>> job = FarmJob("smoke", machine, kernel, (Suite("seccomp"),))

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final, TypeAlias

# 2. 公开导出
__all__ = [
    "AIRY_QEMU_CONFIG_ERROR",
    "AIRY_QEMU_ERROR",
    "DEFAULT_JOB_TIMEOUT_S",
    "DEFAULT_MEM_MB",
    "DEFAULT_SMP",
    "DEFAULT_SUITE_TIMEOUT_S",
    "FARM_VERSION",
    "Accel",
    "Arch",
    "Argv",
    "FarmConfigError",
    "FarmError",
    "FarmJob",
    "FarmLimits",
    "FarmManifest",
    "JobResult",
    "JobState",
    "KernelImage",
    "Machine",
    "Suite",
    "TapSummary",
    "Target",
]

# 3. 契约版本与默认资源预算
FARM_VERSION: Final[str] = "1.0.0"
DEFAULT_SUITE_TIMEOUT_S: Final[int] = 60
DEFAULT_JOB_TIMEOUT_S: Final[int] = 1800
DEFAULT_MEM_MB: Final[int] = 2048
DEFAULT_SMP: Final[int] = 2

# 4. 异常错误码（FarmError.code，用于结构化诊断与测试断言）
AIRY_QEMU_ERROR: Final[str] = "AIRY_QEMU_ERROR"
AIRY_QEMU_CONFIG_ERROR: Final[str] = "AIRY_QEMU_CONFIG_ERROR"

Argv: TypeAlias = tuple[str, ...]

# 5. 取值格式（fullmatch 语义）
_CONSOLE_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:,\d+)?")
_SUITE_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9_-]*")
_JOB_ID_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"\d+\.\d+\.\d+")


class Arch(str, Enum):
    """QEMU 农场支持的目标架构（与构建矩阵四条架构一致）。"""

    X86_64 = "x86_64"
    ARM64 = "arm64"
    RISCV = "riscv"
    LOONGARCH = "loongarch"

    @property
    def qemu_bin(self) -> str:
        """该架构对应的 QEMU system 模拟器二进制名。"""
        return QEMU_BIN_BY_ARCH[self]


# 6. QEMU system 模拟器二进制（Arch.qemu_bin 派生源）
QEMU_BIN_BY_ARCH: Final[dict[Arch, str]] = {
    Arch.X86_64: "qemu-system-x86_64",
    Arch.ARM64: "qemu-system-aarch64",
    Arch.RISCV: "qemu-system-riscv64",
    Arch.LOONGARCH: "qemu-system-loongarch64",
}


class Accel(str, Enum):
    """QEMU 加速器（KVM 不可用时回退 TCG，见 OS-TEST-014）。"""

    KVM = "kvm"
    TCG = "tcg"

    @property
    def qemu_arg(self) -> str:
        """qemu -accel 参数值。"""
        return self.value

    @classmethod
    def auto(cls, kvm_usable: bool) -> Accel:
        """按 KVM 可用性选择加速器。

        Args:
            kvm_usable: 宿主是否具备可用的 /dev/kvm 与同架构目标。

        Returns:
            可用时为 KVM，否则回退 TCG。
        """
        return cls.KVM if kvm_usable else cls.TCG


class JobState(str, Enum):
    """单个农场作业的生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    ERROR = "error"

    @property
    def terminal(self) -> bool:
        """是否已进入终态（终态才允许构造 JobResult）。"""
        return self not in (JobState.PENDING, JobState.RUNNING)

    @property
    def success(self) -> bool:
        """是否非失败终态（SKIPPED 不阻断门禁）。"""
        return self in (JobState.PASSED, JobState.SKIPPED)

    @property
    def failure(self) -> bool:
        """是否为失败终态（失败、超时、执行错误）。"""
        return self in (JobState.FAILED, JobState.TIMEOUT, JobState.ERROR)


@dataclass(frozen=True)
class TapSummary:
    """TAP 结果汇总。

    plan_total / ok_count / not_ok_count 与 tools/parse-tap.py 的返回值
    语义一致；skip_count 是携带 ``# SKIP`` 指令的用例数，按
    OS-TEST-021 单独计数，不并入 ok_count。
    """

    plan_total: int
    ok_count: int
    not_ok_count: int
    skip_count: int = 0

    def __post_init__(self) -> None:
        for name in ("plan_total", "ok_count", "not_ok_count", "skip_count"):
            _need_nonneg(getattr(self, name), name)
        if self.executed > self.plan_total:
            raise ValueError(
                "executed exceeds plan_total: "
                f"{self.executed} > {self.plan_total}"
            )

    @property
    def executed(self) -> int:
        """已上报结果的用例数（含跳过）。"""
        return self.ok_count + self.not_ok_count + self.skip_count

    @property
    def missing(self) -> int:
        """计划中缺失结果的用例数（运行中断时为非零）。"""
        return self.plan_total - self.executed

    @property
    def complete(self) -> bool:
        """计划是否完整执行（计划非空且无缺失用例）。"""
        return self.plan_total > 0 and self.missing == 0

    @property
    def passed(self) -> bool:
        """是否完整执行且无 not ok。"""
        return self.complete and self.not_ok_count == 0


@dataclass(frozen=True)
class Machine:
    """QEMU 目标机器描述（架构 + 机器类型 + CPU 模型 + 控制台）。"""

    arch: Arch
    machine: str
    cpu: str
    console: str
    binary: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.arch, Arch):
            raise ValueError(f"arch must be Arch, got {type(self.arch).__name__}")
        _need_text(self.machine, "machine")
        _need_text(self.cpu, "cpu")
        _need_text(self.console, "console")
        if not _CONSOLE_RE.fullmatch(self.console):
            raise ValueError(f"invalid console device: {self.console!r}")
        if self.binary:
            _need_text(self.binary, "binary")

    @property
    def qemu_bin(self) -> str:
        """实际调用的 QEMU 二进制（binary 覆盖优先）。"""
        return self.binary or self.arch.qemu_bin

    @property
    def console_arg(self) -> str:
        """内核命令行中的控制台参数。"""
        return f"console={self.console}"


@dataclass(frozen=True)
class KernelImage:
    """待引导的内核镜像（路径 + 目标架构）。"""

    path: Path
    arch: Arch

    def __post_init__(self) -> None:
        if isinstance(self.path, str):
            object.__setattr__(self, "path", Path(self.path))
        if not isinstance(self.path, Path):
            raise ValueError(f"path must be Path, got {type(self.path).__name__}")
        if not self.path.name:
            raise ValueError(f"path must name an image file: {self.path}")
        if not isinstance(self.arch, Arch):
            raise ValueError(f"arch must be Arch, got {type(self.arch).__name__}")


@dataclass(frozen=True)
class Suite:
    """kselftest 套件（名称 + 单项超时）。"""

    name: str
    timeout_s: int = DEFAULT_SUITE_TIMEOUT_S

    def __post_init__(self) -> None:
        _need_text(self.name, "name")
        if not _SUITE_RE.fullmatch(self.name):
            raise ValueError(f"invalid suite name: {self.name!r}")
        _need_pos(self.timeout_s, "timeout_s")


@dataclass(frozen=True)
class Target:
    """已解析的 QEMU 目标：清单机器定义 + 实际生效的加速器。"""

    machine: Machine
    accel: Accel

    def __post_init__(self) -> None:
        if not isinstance(self.machine, Machine):
            raise ValueError("machine must be Machine")
        if not isinstance(self.accel, Accel):
            raise ValueError(f"accel must be Accel, got {type(self.accel).__name__}")

    @property
    def arch(self) -> Arch:
        """目标架构。"""
        return self.machine.arch

    @property
    def qemu_bin(self) -> str:
        """目标实际调用的 QEMU 二进制。"""
        return self.machine.qemu_bin


@dataclass(frozen=True)
class FarmJob:
    """一次完整的 QEMU 引导 + kselftest 执行作业。"""

    job_id: str
    machine: Machine
    kernel: KernelImage
    suites: tuple[Suite, ...]
    accel: Accel = Accel.TCG
    timeout_s: int = DEFAULT_JOB_TIMEOUT_S
    mem_mb: int = DEFAULT_MEM_MB
    smp: int = DEFAULT_SMP
    log_path: Path | None = None

    def __post_init__(self) -> None:
        _need_text(self.job_id, "job_id")
        if not _JOB_ID_RE.fullmatch(self.job_id):
            raise ValueError(f"invalid job_id: {self.job_id!r}")
        if not isinstance(self.machine, Machine):
            raise ValueError("machine must be Machine")
        if not isinstance(self.kernel, KernelImage):
            raise ValueError("kernel must be KernelImage")
        if self.kernel.arch is not self.machine.arch:
            raise ValueError(
                f"kernel arch {self.kernel.arch.value} does not match "
                f"machine arch {self.machine.arch.value}"
            )
        if not isinstance(self.suites, tuple):
            raise ValueError("suites must be a tuple")
        if not self.suites:
            raise ValueError("suites must not be empty")
        if any(not isinstance(suite, Suite) for suite in self.suites):
            raise ValueError("suites must contain only Suite instances")
        names = [suite.name for suite in self.suites]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate suite names: {names}")
        if not isinstance(self.accel, Accel):
            raise ValueError(f"accel must be Accel, got {type(self.accel).__name__}")
        _need_pos(self.timeout_s, "timeout_s")
        _need_pos(self.mem_mb, "mem_mb")
        _need_pos(self.smp, "smp")
        if isinstance(self.log_path, str):
            object.__setattr__(self, "log_path", Path(self.log_path))

    @property
    def suite_names(self) -> tuple[str, ...]:
        """按注册顺序排列的套件名。"""
        return tuple(suite.name for suite in self.suites)

    @property
    def suite_budget_s(self) -> int:
        """全部套件超时之和（调度上界参考）。"""
        return sum(suite.timeout_s for suite in self.suites)


@dataclass(frozen=True)
class JobResult:
    """单个农场作业的终态结果与证据。"""

    job: FarmJob
    state: JobState
    duration_s: float
    exit_code: int | None = None
    tap: TapSummary | None = None
    reason: str = ""
    qemu_argv: Argv = ()

    def __post_init__(self) -> None:
        if not isinstance(self.job, FarmJob):
            raise ValueError("job must be FarmJob")
        if not isinstance(self.state, JobState):
            raise ValueError(f"state must be JobState, got {type(self.state).__name__}")
        if not self.state.terminal:
            raise ValueError(f"state must be terminal, got {self.state.value}")
        if self.duration_s < 0:
            raise ValueError(f"duration_s must be non-negative, got {self.duration_s}")
        if self.exit_code is not None and not isinstance(self.exit_code, int):
            raise ValueError("exit_code must be int or None")
        if self.tap is not None and not isinstance(self.tap, TapSummary):
            raise ValueError("tap must be TapSummary or None")
        if not isinstance(self.qemu_argv, tuple):
            raise ValueError("qemu_argv must be a tuple")
        if self.state is JobState.PASSED:
            if self.exit_code != 0:
                raise ValueError("passed result requires exit_code 0")
            if self.tap is None or not self.tap.passed:
                raise ValueError(
                    "passed result requires a complete passing TAP summary"
                )
        elif self.state is JobState.FAILED:
            if not self._fail_evid:
                raise ValueError("failed result requires non-zero exit or failing TAP")
        elif not self.reason.strip():
            raise ValueError(f"{self.state.value} result requires a reason")

    @property
    def _fail_evid(self) -> bool:
        """失败判定所需证据（非零退出或 TAP 未通过）。"""
        exit_bad = self.exit_code not in (None, 0)
        tap_bad = self.tap is not None and not self.tap.passed
        return exit_bad or tap_bad

    @property
    def ok(self) -> bool:
        """该结果是否不阻断门禁。"""
        return self.state.success


@dataclass(frozen=True)
class FarmLimits:
    """农场并行预算（作业数 / 内存 / CPU）。"""

    max_jobs: int = 1
    mem_mb: int = 4096
    cpus: int = DEFAULT_SMP

    def __post_init__(self) -> None:
        _need_pos(self.max_jobs, "max_jobs")
        _need_pos(self.mem_mb, "mem_mb")
        _need_pos(self.cpus, "cpus")

    def can_host(self, job: FarmJob) -> bool:
        """单任务资源需求是否落在并行预算内。

        Args:
            job: 待调度作业。

        Returns:
            内存与 CPU 均不超过预算时为 True。
        """
        if not isinstance(job, FarmJob):
            raise ValueError("job must be FarmJob")
        return job.mem_mb <= self.mem_mb and job.smp <= self.cpus


@dataclass(frozen=True)
class FarmManifest:
    """农场清单（机器定义 + 套件目录 + 资源预算的唯一事实源）。"""

    version: str
    machines: tuple[Machine, ...]
    suites: tuple[Suite, ...]
    limits: FarmLimits = field(default_factory=FarmLimits)

    def __post_init__(self) -> None:
        _need_text(self.version, "version")
        if not _VERSION_RE.fullmatch(self.version):
            raise ValueError(f"invalid version: {self.version!r}")
        if not isinstance(self.machines, tuple) or not self.machines:
            raise ValueError("machines must be a non-empty tuple")
        if any(not isinstance(machine, Machine) for machine in self.machines):
            raise ValueError("machines must contain only Machine instances")
        arches = [machine.arch for machine in self.machines]
        if len(arches) != len(set(arches)):
            raise ValueError(f"duplicate machine arches: {[a.value for a in arches]}")
        if not isinstance(self.suites, tuple) or not self.suites:
            raise ValueError("suites must be a non-empty tuple")
        if any(not isinstance(suite, Suite) for suite in self.suites):
            raise ValueError("suites must contain only Suite instances")
        names = [suite.name for suite in self.suites]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate suite names: {names}")
        if not isinstance(self.limits, FarmLimits):
            raise ValueError("limits must be FarmLimits")

    def machine_for(self, arch: Arch) -> Machine:
        """按架构查找机器定义。

        Args:
            arch: 目标架构。

        Returns:
            对应的 Machine。

        Raises:
            FarmConfigError: 清单中不存在该架构。
        """
        for machine in self.machines:
            if machine.arch is arch:
                return machine
        raise FarmConfigError(
            f"no machine for arch: {getattr(arch, 'value', arch)!r}",
            AIRY_QEMU_CONFIG_ERROR,
        )


class FarmError(Exception):
    """QEMU 农场异常基类（message + code 模式）。"""

    def __init__(self, message: str, code: str = AIRY_QEMU_ERROR) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class FarmConfigError(FarmError):
    """清单、目标或作业配置非法。"""

    def __init__(self, message: str, code: str = AIRY_QEMU_CONFIG_ERROR) -> None:
        super().__init__(message, code)


def _need_text(value: str, field_name: str) -> None:
    """校验必填文本字段。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise ValueError(
            f"{field_name} must not have surrounding whitespace: {value!r}"
        )


def _need_pos(value: int, field_name: str) -> None:
    """校验正整数字段（显式拒绝 bool）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer, got {value!r}")


def _need_nonneg(value: int, field_name: str) -> None:
    """校验非负整数字段（显式拒绝 bool）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer, got {value!r}")
