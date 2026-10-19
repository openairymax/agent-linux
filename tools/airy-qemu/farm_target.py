# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_target — QEMU 农场目标解析与加速器选择。

把命令行给出的架构集合解析为农场清单中的真实机器定义，并按宿主能力
决定实际生效的加速器：

  - KVM 只在 /dev/kvm 可读写且宿主 ISA 与目标 ISA 一致时可用；
  - accel=auto（默认）在 KVM 不可用时静默回退 TCG，符合 OS-TEST-014
    对 CI 无嵌套虚拟化的要求；
  - accel=kvm 为显式约束，不可用时立即以 FarmConfigError 失败，避免
    作业在运行期才暴露配置错误；
  - accel=tcg 强制软件模拟，便于复现与对照。

本模块只做解析与能力探测，不启动任何进程；QEMU 命令行由 farm_cmdline
构造，二者通过 farm_types.Target 交接。

权威依据：
  docs-linux/70-build-system/03-ci-cd-pipeline.md §2.3
    OS-TEST-013~022（QEMU 运行 kselftest，KVM 不可用时回退 TCG）
  docs-linux/80-testing/02-kselftest.md
    OS-STD-009（测试超时默认 60 秒）

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

import os
import platform
from collections.abc import Iterable
from pathlib import Path
from typing import Final

# 2. 本工具模块导入
from farm_types import (
    Accel,
    Arch,
    FarmConfigError,
    FarmManifest,
    Target,
)

# 3. 公开导出
__all__ = [
    "ACCEL_CHOICES",
    "KVM_DEVICE",
    "accel_name",
    "can_kvm",
    "host_arch",
    "kvm_reason",
    "resolve_target",
    "resolve_targets",
]

# 4. 常量
KVM_DEVICE: Final[Path] = Path("/dev/kvm")
ACCEL_CHOICES: Final[tuple[str, ...]] = ("auto", "kvm", "tcg")
_HOST_ARCH: Final[dict[str, Arch]] = {
    "x86_64": Arch.X86_64,
    "amd64": Arch.X86_64,
    "aarch64": Arch.ARM64,
    "arm64": Arch.ARM64,
    "riscv64": Arch.RISCV,
    "loongarch64": Arch.LOONGARCH,
}


def host_arch(machine: str | None = None) -> Arch | None:
    """把宿主机器标识映射为农场架构。

    Args:
        machine: platform.machine() 风格的标识；缺省时现场读取。

    Returns:
        匹配的 Arch；宿主架构不在农场矩阵内时返回 None。
    """
    name = platform.machine() if machine is None else machine
    return _HOST_ARCH.get(name.strip().lower())


def kvm_reason(
    arch: Arch,
    dev: Path = KVM_DEVICE,
    host: Arch | None = None,
) -> str | None:
    """检查目标架构能否使用 KVM 加速。

    Args:
        arch: 目标架构。
        dev: KVM 字符设备路径。
        host: 宿主架构；None 表示现场探测。

    Returns:
        None 表示可用；否则返回人类可读的不可用原因。

    Raises:
        ValueError: arch 不是 Arch 实例。
    """
    if not isinstance(arch, Arch):
        raise ValueError(f"arch must be Arch, got {type(arch).__name__}")
    device = Path(dev)
    if not device.exists():
        return f"{device} does not exist"
    if not os.access(device, os.R_OK | os.W_OK):
        return f"{device} is not readable and writable"
    host_value = host_arch() if host is None else host
    if host_value is None:
        return "host architecture is not supported"
    if host_value is not arch:
        return f"host {host_value.value} cannot accelerate {arch.value}"
    return None


def can_kvm(arch: Arch, dev: Path = KVM_DEVICE, host: Arch | None = None) -> bool:
    """目标架构是否具备 KVM 加速能力。"""
    return kvm_reason(arch, dev, host) is None


def accel_name(choice: str | Accel) -> str:
    """把加速器取值规范化为 auto/kvm/tcg。

    Args:
        choice: 命令行字符串或已有 Accel 实例。

    Returns:
        规范化后的取值字符串。

    Raises:
        FarmConfigError: 取值不在 ACCEL_CHOICES 内。
    """
    name = choice.value if isinstance(choice, Accel) else choice
    if isinstance(name, str) and name in ACCEL_CHOICES:
        return name
    raise FarmConfigError(
        f"accel must be one of {list(ACCEL_CHOICES)}, got {choice!r}"
    )


def resolve_target(
    manifest: FarmManifest,
    arch: str | Arch,
    accel: str | Accel = "auto",
    *,
    dev: Path = KVM_DEVICE,
    host: Arch | None = None,
) -> Target:
    """解析单个目标架构的机器定义与实际加速器。

    Args:
        manifest: 已加载的农场清单。
        arch: 目标架构名称或 Arch。
        accel: auto/kvm/tcg 或 Accel 实例。
        dev: KVM 字符设备路径。
        host: 宿主架构；None 表示现场探测。

    Returns:
        machine 与 accel 均已确定的 Target。

    Raises:
        FarmConfigError: 架构未知、清单缺失该架构，或显式 kvm 不可用。
    """
    _need_manifest(manifest)
    machine = manifest.machine_for(_as_arch(arch))
    return Target(machine, _pick(machine.arch, accel, dev, host))


def resolve_targets(
    manifest: FarmManifest,
    arches: Iterable[str | Arch] | None = None,
    accel: str | Accel = "auto",
    *,
    dev: Path = KVM_DEVICE,
    host: Arch | None = None,
) -> tuple[Target, ...]:
    """解析一组目标架构，保持调用方给出的顺序。

    Args:
        manifest: 已加载的农场清单。
        arches: 架构名称或 Arch 的可迭代对象；None 或空表示清单全部架构。
        accel: auto/kvm/tcg 或 Accel 实例。
        dev: KVM 字符设备路径。
        host: 宿主架构；None 表示现场探测。

    Returns:
        按请求顺序排列的 Target 元组。

    Raises:
        FarmConfigError: 架构未知、重复或清单缺失。
    """
    _need_manifest(manifest)
    if isinstance(arches, str):
        raise FarmConfigError(
            "arches must be an iterable of arch names, not a string"
        )
    if arches is None:
        requested: tuple[str | Arch, ...] = tuple(
            machine.arch for machine in manifest.machines
        )
    else:
        requested = tuple(arches)
        if not requested:
            requested = tuple(machine.arch for machine in manifest.machines)
    parsed = _unique(tuple(_as_arch(value) for value in requested))
    return tuple(
        resolve_target(manifest, arch, accel, dev=dev, host=host)
        for arch in parsed
    )


def _need_manifest(manifest: FarmManifest) -> None:
    """要求清单为 FarmManifest。"""
    if not isinstance(manifest, FarmManifest):
        raise FarmConfigError(
            f"manifest must be FarmManifest, got {type(manifest).__name__}"
        )


def _as_arch(value: str | Arch) -> Arch:
    """把架构取值转换为 Arch。"""
    if isinstance(value, Arch):
        return value
    if isinstance(value, str):
        try:
            return Arch(value)
        except ValueError:
            pass
    names = [arch.value for arch in Arch]
    raise FarmConfigError(f"unsupported arch {value!r}; expected one of {names}")


def _unique(arches: tuple[Arch, ...]) -> tuple[Arch, ...]:
    """拒绝重复架构，保持首次出现顺序。"""
    seen: set[Arch] = set()
    duplicates: list[str] = []
    for arch in arches:
        if arch in seen and arch.value not in duplicates:
            duplicates.append(arch.value)
        seen.add(arch)
    if duplicates:
        raise FarmConfigError(f"duplicate arches: {duplicates}")
    return arches


def _pick(
    arch: Arch,
    accel: str | Accel,
    dev: Path,
    host: Arch | None,
) -> Accel:
    """按请求模式与宿主能力选择加速器。"""
    name = accel_name(accel)
    if name == Accel.TCG.value:
        return Accel.TCG
    reason = kvm_reason(arch, dev, host)
    if reason is None:
        return Accel.KVM
    if name == Accel.KVM.value:
        raise FarmConfigError(f"kvm unavailable for {arch.value}: {reason}")
    return Accel.TCG
