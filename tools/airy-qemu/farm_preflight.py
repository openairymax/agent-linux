# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_preflight — QEMU 测试农场的执行前预检。

在把作业交给运行器之前，本模块对宿主侧输入做一次完整、可重复的校验：
内核镜像是否为可引导的真实镜像、镜像格式与目标架构是否一致、kselftest
安装目录是否具备 run_kselftest.sh 与清单文件、清单中每个套件目录是否
存在、guest 侧 settings 超时是否落在农场预算内。

预检只读宿主文件，不启动 QEMU、不修改任何输入；所有失败都以
FarmConfigError 拒收，绝不静默修正，避免带着坏输入进入漫长的引导过程
之后才暴露问题。镜像格式判定基于内核源码定义的真实 magic：
arm64 Image 在偏移 56 处为 "ARM\\x64"；riscv Image 在偏移 48 处为
"RISCV\\0\\0\\0"，偏移 56 处为 "RSC\\x05"；x86 bzImage 在偏移 0x1fe
处为 0xAA55，偏移 0x202 处为 "HdrS"。LoongArch 在 EFI stub 下是
PE/COFF，没有可用于架构判别的通用裸镜像 magic，故允许未知格式，但
ELF 镜像仍要求 e_machine 与目标架构一致。

权威依据：
  kernel/arch/arm64/include/asm/image.h（ARM64_IMAGE_MAGIC）
  kernel/arch/riscv/include/asm/image.h（RISCV_IMAGE_MAGIC/MAGIC2）
  kernel/arch/x86/boot/header.S（boot_flag / HdrS）
  kernel/tools/testing/selftests/run_kselftest.sh（集合选择）
  kernel/tools/testing/selftests/kselftest/runner.sh（settings 超时）
  docs-linux/70-build-system/03-ci-cd-pipeline.md §5.3
  docs-linux/80-testing/02-kselftest.md（OS-STD-009）

Example:
    >>> env = PreflightEnv(Path("/tmp/kselftest-install"))
    >>> report = check_job(job, env)
    >>> report.fmt.value
    'bzimage'

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

# 2. 本工具模块导入
from farm_types import Arch, FarmConfigError, FarmJob, KernelImage, Suite

# 3. 公开导出
__all__ = [
    "DEFAULT_GUEST_TIMEOUT_S",
    "HEAD_BYTES",
    "KernelFormat",
    "Preflight",
    "PreflightEnv",
    "check_job",
    "check_kernel",
    "check_ksft",
    "check_suite",
    "detect_fmt",
    "suite_timeout",
]

# 4. 契约常量
HEAD_BYTES: Final[int] = 1024
DEFAULT_GUEST_TIMEOUT_S: Final[int] = 45
LIST_NAME: Final[str] = "kselftest-list.txt"
RUN_SCRIPT: Final[str] = "run_kselftest.sh"
SETTINGS_NAME: Final[str] = "settings"

ELF_MAGIC: Final[bytes] = b"\x7fELF"
ARM64_MAGIC: Final[bytes] = b"ARM\x64"
RISCV_MAGIC: Final[bytes] = b"RISCV\x00\x00\x00"
RISCV_MAGIC2: Final[bytes] = b"RSC\x05"
BZ_FLAG: Final[bytes] = b"\x55\xaa"
BZ_SIG: Final[bytes] = b"HdrS"

ARM64_OFF: Final[int] = 56
RISCV_OFF: Final[int] = 48
RISCV2_OFF: Final[int] = 56
BZ_FLAG_OFF: Final[int] = 0x1FE
BZ_SIG_OFF: Final[int] = 0x202
ELF_MACH_OFF: Final[int] = 18
ELF_MACH_LEN: Final[int] = 2
ELF_MACH_MIN: Final[int] = ELF_MACH_OFF + ELF_MACH_LEN

_NUM_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]+")


class KernelFormat(str, Enum):
    """按头部 magic 判定的内核镜像格式。"""

    ELF = "elf"
    BZIMAGE = "bzimage"
    ARM64 = "arm64"
    RISCV = "riscv"
    UNKNOWN = "unknown"


# 5. 架构可接受格式与 ELF e_machine 映射（内核 uapi elf.h）
_ARCH_FMT: Final[dict[Arch, frozenset[KernelFormat]]] = {
    Arch.X86_64: frozenset({KernelFormat.ELF, KernelFormat.BZIMAGE}),
    Arch.ARM64: frozenset({KernelFormat.ELF, KernelFormat.ARM64}),
    Arch.RISCV: frozenset({KernelFormat.ELF, KernelFormat.RISCV}),
    Arch.LOONGARCH: frozenset({KernelFormat.ELF, KernelFormat.UNKNOWN}),
}

_ELF_MACH: Final[dict[Arch, int]] = {
    Arch.X86_64: 62,
    Arch.ARM64: 183,
    Arch.RISCV: 243,
    Arch.LOONGARCH: 258,
}


@dataclass(frozen=True)
class PreflightEnv:
    """预检环境：kselftest 安装目录与可选的内核源码树。

    Attributes:
        ksft_dir: make install 生成的 kselftest 安装目录。
        source: 可选的内核源码根目录；提供时交叉校验套件出处。
    """

    ksft_dir: Path
    source: Path | None = None

    def __post_init__(self) -> None:
        here = _as_path(self.ksft_dir, "ksft_dir")
        object.__setattr__(self, "ksft_dir", here)
        if self.source is not None:
            object.__setattr__(self, "source", _as_path(self.source, "source"))


@dataclass(frozen=True)
class Preflight:
    """预检通过后留下的证据（供运行器与报告复用）。"""

    job: FarmJob
    fmt: KernelFormat
    kernel_size: int
    suite_tests: tuple[tuple[str, int], ...]
    source: Path | None
    elf_mach: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.job, FarmJob):
            raise _cfg("job must be FarmJob")
        if not isinstance(self.fmt, KernelFormat):
            kind = type(self.fmt).__name__
            raise _cfg(f"fmt must be KernelFormat, got {kind}")
        if isinstance(self.kernel_size, bool) or self.kernel_size <= 0:
            raise _cfg(
                f"kernel_size must be positive, got {self.kernel_size!r}"
            )
        if not isinstance(self.suite_tests, tuple):
            raise _cfg("suite_tests must be a tuple")
        for item in self.suite_tests:
            if not isinstance(item, tuple) or len(item) != 2:
                raise _cfg(f"invalid suite_tests entry: {item!r}")
            name, count = item
            if not isinstance(name, str) or not name:
                raise _cfg(f"invalid suite_tests entry: {item!r}")
            if isinstance(count, bool) or not isinstance(count, int):
                raise _cfg(f"invalid suite_tests entry: {item!r}")
            if count <= 0:
                raise _cfg(f"invalid suite_tests entry: {item!r}")
        if self.source is not None and not isinstance(self.source, Path):
            raise _cfg("source must be Path or None")
        if self.fmt is KernelFormat.ELF:
            if self.elf_mach is None:
                raise _cfg("ELF preflight requires elf_mach")
        elif self.elf_mach is not None:
            raise _cfg("non-ELF preflight must not carry elf_mach")

    @property
    def suites(self) -> tuple[str, ...]:
        """预检覆盖的套件名（保持作业顺序）。"""
        return tuple(name for name, _ in self.suite_tests)


def detect_fmt(head: bytes) -> KernelFormat:
    """按内核镜像头部的真实 magic 判定格式。

    Args:
        head: 镜像文件头部字节（至少 HEAD_BYTES，可短于该值）。

    Returns:
        识别出的格式；无法识别时为 KernelFormat.UNKNOWN。
    """
    if not isinstance(head, bytes):
        raise _cfg(f"head must be bytes, got {type(head).__name__}")
    if head[:4] == ELF_MAGIC:
        return KernelFormat.ELF
    if head[ARM64_OFF : ARM64_OFF + 4] == ARM64_MAGIC:
        return KernelFormat.ARM64
    magics = head[RISCV_OFF : RISCV_OFF + 8] == RISCV_MAGIC
    if magics and head[RISCV2_OFF : RISCV2_OFF + 4] == RISCV_MAGIC2:
        return KernelFormat.RISCV
    boot = head[BZ_FLAG_OFF : BZ_FLAG_OFF + 2] == BZ_FLAG
    if boot and head[BZ_SIG_OFF : BZ_SIG_OFF + 4] == BZ_SIG:
        return KernelFormat.BZIMAGE
    return KernelFormat.UNKNOWN


def check_job(job: FarmJob, env: PreflightEnv) -> Preflight:
    """执行完整预检，返回可审计的证据。

    Args:
        job: 待执行作业。
        env: 预检环境（安装目录与可选源码树）。

    Returns:
        Preflight 证据对象。

    Raises:
        FarmConfigError: 任一输入不满足运行条件。
    """
    if not isinstance(job, FarmJob):
        raise _cfg(f"job must be FarmJob, got {type(job).__name__}")
    if not isinstance(env, PreflightEnv):
        raise _cfg(f"env must be PreflightEnv, got {type(env).__name__}")
    fmt, size, mach = check_kernel(job.kernel)
    tests = check_ksft(env)
    for suite in job.suites:
        check_suite(suite, env, tests)
    pairs = tuple((suite.name, tests[suite.name]) for suite in job.suites)
    return Preflight(job, fmt, size, pairs, env.source, mach)


def check_kernel(kernel: KernelImage) -> tuple[KernelFormat, int, int | None]:
    """校验内核镜像存在、非空、格式与架构一致。

    Args:
        kernel: 内核镜像描述。

    Returns:
        (格式, 字节数, ELF e_machine 或 None)。

    Raises:
        FarmConfigError: 镜像缺失、为空、不可读或架构不符。
    """
    if not isinstance(kernel, KernelImage):
        raise _cfg(f"kernel must be KernelImage, got {type(kernel).__name__}")
    path = _need_file(kernel.path, "kernel image")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _cfg(f"cannot stat kernel image {path}: {exc}") from exc
    if size <= 0:
        raise _cfg(f"kernel image is empty: {path}")
    head = _read_head(path)
    fmt = detect_fmt(head)
    mach = _elf_mach(head) if fmt is KernelFormat.ELF else None
    _require_fmt(kernel.arch, fmt, mach)
    return fmt, size, mach


def check_ksft(env: PreflightEnv) -> dict[str, int]:
    """校验 kselftest 安装目录结构并统计每个套件的用例数。

    Args:
        env: 预检环境。

    Returns:
        套件名到已安装用例数的映射（保持清单出现顺序）。

    Raises:
        FarmConfigError: 目录、脚本、清单或套件目录不符合安装契约。
    """
    root = _need_dir(env.ksft_dir, "kselftest dir")
    _need_file(root / RUN_SCRIPT, RUN_SCRIPT)
    tests = _list_tests(_need_file(root / LIST_NAME, LIST_NAME))
    for name in tests:
        _need_dir(root / name, f"suite directory {name!r}")
    if env.source is not None:
        _check_src(env.source, tests)
    return tests


def check_suite(
    suite: Suite, env: PreflightEnv, tests: dict[str, int]
) -> None:
    """校验单个套件已安装且 guest 超时不超出农场预算。

    Args:
        suite: 作业请求的套件。
        env: 预检环境。
        tests: check_ksft 返回的安装清单。

    Raises:
        FarmConfigError: 套件未安装或超时配置不一致。
    """
    if not isinstance(suite, Suite):
        raise _cfg(f"suite must be Suite, got {type(suite).__name__}")
    if suite.name not in tests:
        raise _cfg(
            f"suite {suite.name!r} is not installed under {env.ksft_dir}"
        )
    limit = suite_timeout(env.ksft_dir / suite.name / SETTINGS_NAME)
    if limit > suite.timeout_s:
        raise _cfg(
            f"suite {suite.name}: guest timeout {limit}s exceeds farm "
            f"budget {suite.timeout_s}s"
        )


def suite_timeout(path: Path) -> int:
    """读取套件 settings 中生效的单项超时。

    settings 由 kselftest/runner.sh 逐行解析为 ``kselftest_<key>``；
    ``timeout=0`` 表示不限时，缺省为 DEFAULT_GUEST_TIMEOUT_S。

    Args:
        path: 套件目录下的 settings 文件路径。

    Returns:
        以秒为单位的单项超时；0 表示不限时。

    Raises:
        FarmConfigError: settings 不是普通文件、不可读或内容非法。
    """
    settings = _as_path(path, "settings")
    if not settings.exists():
        return DEFAULT_GUEST_TIMEOUT_S
    if not settings.is_file():
        raise _cfg(f"settings is not a file: {settings}")
    for lineno, raw in enumerate(_lines(settings), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise _cfg(f"{settings}:{lineno}: expected key=value, got {raw!r}")
        key, value = (part.strip() for part in line.split("=", 1))
        if key != "timeout":
            continue
        if not _NUM_RE.fullmatch(value):
            raise _cfg(f"{settings}:{lineno}: invalid timeout {value!r}")
        return int(value)
    return DEFAULT_GUEST_TIMEOUT_S


def _require_fmt(arch: Arch, fmt: KernelFormat, mach: int | None) -> None:
    """校验格式与架构一致（ELF 还需 e_machine 匹配）。"""
    allowed = _ARCH_FMT[arch]
    if fmt not in allowed:
        names = ", ".join(sorted(item.value for item in allowed))
        raise _cfg(
            f"{arch.value} kernel image has unexpected format {fmt.value}; "
            f"expected one of: {names}"
        )
    if fmt is KernelFormat.ELF:
        want = _ELF_MACH[arch]
        if mach != want:
            raise _cfg(
                f"ELF machine {mach} does not match {arch.value} "
                f"(expected {want})"
            )


def _elf_mach(head: bytes) -> int:
    """读取并校验 ELF64 小端头部的 e_machine。"""
    if len(head) < ELF_MACH_MIN:
        raise _cfg("kernel image has a truncated ELF header")
    if head[4] != 2:
        raise _cfg("ELF kernel image must be 64-bit (EI_CLASS=2)")
    if head[5] != 1:
        raise _cfg("ELF kernel image must be little-endian (EI_DATA=1)")
    return int.from_bytes(head[ELF_MACH_OFF:ELF_MACH_MIN], "little")


def _list_tests(path: Path) -> dict[str, int]:
    """解析 kselftest-list.txt 并统计每个套件的用例数。"""
    counts: dict[str, int] = {}
    for lineno, raw in enumerate(_lines(path), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.count(":") != 1:
            raise _cfg(
                f"{path}:{lineno}: expected <suite>:<test>, got {raw!r}"
            )
        suite, test = line.split(":")
        if not suite or not test or any(ch.isspace() for ch in line):
            raise _cfg(f"{path}:{lineno}: invalid entry {raw!r}")
        counts[suite] = counts.get(suite, 0) + 1
    if not counts:
        raise _cfg(f"{path}: no tests listed")
    return counts


def _check_src(source: Path, tests: dict[str, int]) -> None:
    """交叉校验安装清单中的套件确实存在于内核源码树。"""
    root = _need_dir(source, "kernel source")
    base = _need_dir(root / "tools" / "testing" / "selftests", "selftests dir")
    _need_file(base / RUN_SCRIPT, f"source {RUN_SCRIPT}")
    for name in tests:
        _need_dir(base / name, f"source suite directory {name!r}")


def _read_head(path: Path) -> bytes:
    """读取镜像头部字节，IO 失败转为配置错误。"""
    try:
        with path.open("rb") as handle:
            return handle.read(HEAD_BYTES)
    except OSError as exc:
        raise _cfg(f"cannot read kernel image {path}: {exc}") from exc


def _lines(path: Path) -> list[str]:
    """按行读取 UTF-8 文本，IO 或解码失败转为配置错误。"""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise _cfg(f"cannot read {path}: {exc}") from exc


def _need_dir(value: object, name: str) -> Path:
    """校验路径存在且为目录。"""
    path = _as_path(value, name)
    if not path.is_dir():
        raise _cfg(f"{name} is not a directory: {path}")
    return path


def _need_file(value: object, name: str) -> Path:
    """校验路径存在且为普通文件。"""
    path = _as_path(value, name)
    if not path.is_file():
        raise _cfg(f"{name} is not a file: {path}")
    return path


def _as_path(value: object, name: str) -> Path:
    """把字符串或 Path 规范化为非空 Path。"""
    path = Path(value) if isinstance(value, str) else value
    if not isinstance(path, Path) or not path.name:
        raise _cfg(f"{name} must be a non-empty path, got {value!r}")
    return path


def _cfg(message: str) -> FarmConfigError:
    """构造统一错误码的配置异常。"""
    return FarmConfigError(message)
