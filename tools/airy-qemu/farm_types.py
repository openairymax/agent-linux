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
  .github/workflows/kernel-verify.yml（virtme-ng 实际调用基线）
  tools/tap/parse-tap.py（TAP 解析契约：plan_total / ok_count / not_ok_count）

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
    "TapCounts",
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


class TapCounts:
    """TAP 计数的派生判据。

    字段由使用方的 dataclass 声明：TapSummary 汇总整个作业，SuiteTally
    汇总单个套件。派生量（已执行、缺失、完整、通过）只推导一次——两处
    各写一份时，任何一处口径修正都会让汇总与分套件明细分叉，而二者恰恰
    要互相自洽。
    """

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
class TapSummary(TapCounts):
    """TAP 结果汇总。

    plan_total / ok_count / not_ok_count 与 tools/tap/parse-tap.py 的返回值
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
        _need_id(self.job_id)
        _need_job_parts(self.machine, self.kernel)
        _need_suites(self.suites)
        _need_enum(self.accel, Accel, "accel")
        _need_pos(self.timeout_s, "timeout_s")
        _need_pos(self.mem_mb, "mem_mb")
        _need_pos(self.smp, "smp")
        self._norm_log()

    def _norm_log(self) -> None:
        """字符串日志路径按 Path 归一（frozen 需经 object.__setattr__）。"""
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
        _need_result(self)
        _need_evid(self)

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
        _need_version(self.version)
        _need_machines(self.machines)
        _need_tup(self.suites, "suites")
        _need_suite_mem(self.suites)
        _need_enum(self.limits, FarmLimits, "limits")

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


def _need_enum(value, kind, label: str) -> None:
    """枚举字段的类型判据；报出实际类型，便于定位误传的字符串。"""
    if not isinstance(value, kind):
        raise ValueError(
            f"{label} must be {kind.__name__}, got {type(value).__name__}"
        )


def _need_tup(value, label: str) -> None:
    """非空元组判据（清单级集合字段）。"""
    if not isinstance(value, tuple) or not value:
        raise ValueError(f"{label} must be a non-empty tuple")


def _need_id(value: str) -> None:
    """作业标识：非空且可被清单与其他作业引用。"""
    _need_text(value, "job_id")
    if not _JOB_ID_RE.fullmatch(value):
        raise ValueError(f"invalid job_id: {value!r}")


def _need_version(value: str) -> None:
    """清单版本号：三段数字。"""
    _need_text(value, "version")
    if not _VERSION_RE.fullmatch(value):
        raise ValueError(f"invalid version: {value!r}")


def _need_job_parts(machine, kernel) -> None:
    """机型与内核必须成对：类型正确且目标架构一致。

    架构不一致的作业会在 QEMU 启动后才以「无法引导」的形式暴露，届时
    日志里只剩模拟器报错，故在构造期就拒收。
    """
    if not isinstance(machine, Machine):
        raise ValueError("machine must be Machine")
    if not isinstance(kernel, KernelImage):
        raise ValueError("kernel must be KernelImage")
    if kernel.arch is not machine.arch:
        raise ValueError(
            f"kernel arch {kernel.arch.value} does not match "
            f"machine arch {machine.arch.value}"
        )


def _need_suite_mem(value) -> None:
    """套件成员判据：元素类型与名称唯一。作业与清单共用同一口径。"""
    _suite_types(value)
    _suite_unique(value)


def _suite_types(value) -> None:
    """元素必须全是 Suite。"""
    if any(not isinstance(suite, Suite) for suite in value):
        raise ValueError("suites must contain only Suite instances")


def _suite_unique(value) -> None:
    """套件名不得重复。"""
    names = [suite.name for suite in value]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate suite names: {names}")


def _need_suites(value) -> None:
    """作业级套件元组：类型与空集分开报，以区分「传了列表」与「没给套件」。"""
    if not isinstance(value, tuple):
        raise ValueError("suites must be a tuple")
    if not value:
        raise ValueError("suites must not be empty")
    _need_suite_mem(value)


def _machines_ok(value) -> bool:
    """元组里每个元素都是 Machine。"""
    return all(isinstance(machine, Machine) for machine in value)


def _arch_dup(arches: list) -> bool:
    """架构序列里存在重复。"""
    return len(arches) != len(set(arches))


def _need_machines(value) -> None:
    """清单级机型元组：非空、元素类型、架构唯一。"""
    _need_tup(value, "machines")
    if not _machines_ok(value):
        raise ValueError("machines must contain only Machine instances")
    arches = [machine.arch for machine in value]
    if _arch_dup(arches):
        raise ValueError(f"duplicate machine arches: {[a.value for a in arches]}")


_OPT_FIELDS = (
    ("exit_code", int),
    ("tap", TapSummary),
)


def _need_opt(res) -> None:
    """可选字段：None 或指定类型。"""
    for name, want in _OPT_FIELDS:
        value = getattr(res, name)
        if value is not None and not isinstance(value, want):
            raise ValueError(f"{name} must be {want.__name__} or None")


def _need_result(res) -> None:
    """结果字段的类型与范围（与状态无关的部分）。"""
    if not isinstance(res.job, FarmJob):
        raise ValueError("job must be FarmJob")
    _need_enum(res.state, JobState, "state")
    if not res.state.terminal:
        raise ValueError(f"state must be terminal, got {res.state.value}")
    if res.duration_s < 0:
        raise ValueError(f"duration_s must be non-negative, got {res.duration_s}")
    _need_opt(res)
    if not isinstance(res.qemu_argv, tuple):
        raise ValueError("qemu_argv must be a tuple")


def _need_evid(res) -> None:
    """终态证据：通过需零退出且 TAP 完整，失败需反证，其余需书面说明。"""
    if res.state is JobState.PASSED:
        _need_pass_evid(res)
    elif res.state is JobState.FAILED:
        _need_fail_evid(res)
    elif not res.reason.strip():
        raise ValueError(f"{res.state.value} result requires a reason")


def _need_pass_evid(res) -> None:
    """通过态证据：零退出且 TAP 汇总完整通过。"""
    if res.exit_code != 0:
        raise ValueError("passed result requires exit_code 0")
    if res.tap is None or not res.tap.passed:
        raise ValueError("passed result requires a complete passing TAP summary")


def _need_fail_evid(res) -> None:
    """失败态证据：非零退出或 TAP 未通过，二者至少其一。"""
    if not res._fail_evid:
        raise ValueError("failed result requires non-zero exit or failing TAP")
