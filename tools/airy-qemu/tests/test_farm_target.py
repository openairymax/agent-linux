# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_target 单元测试：宿主探测、KVM 判定与目标解析。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import farm_manifest as fm
import farm_target as ft
import farm_types as ftp


def kvm_node(tmp_path: Path, writable: bool = True) -> Path:
    """构造可控的 KVM 设备替身（仅权限位参与判定）。"""
    node = tmp_path / "kvm"
    node.write_bytes(b"")
    node.chmod(0o600 if writable else 0o400)
    return node


def manifest(farm_dir: Path) -> ftp.FarmManifest:
    """加载真实农场清单。"""
    return fm.load_manifest(farm_dir / "manifest.yaml")


def raw_manifest() -> dict[str, Any]:
    """仅含 x86_64 的最小清单，用于缺失架构路径。"""
    return {
        "version": "1.0.0",
        "machines": [
            {
                "arch": "x86_64",
                "machine": "q35",
                "cpu": "max",
                "console": "ttyS0",
            }
        ],
        "suites": [{"name": "rseq"}],
    }


def resolve_error(*args: object, **kwargs: object) -> str:
    """解析并返回错误消息。"""
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        ft.resolve_target(*args, **kwargs)  # type: ignore[arg-type]
    return excinfo.value.message


@pytest.mark.parametrize(
    ("name", "arch"),
    [
        ("x86_64", ftp.Arch.X86_64),
        ("AMD64", ftp.Arch.X86_64),
        ("aarch64", ftp.Arch.ARM64),
        ("arm64", ftp.Arch.ARM64),
        ("riscv64", ftp.Arch.RISCV),
        ("loongarch64", ftp.Arch.LOONGARCH),
        (" x86_64 ", ftp.Arch.X86_64),
    ],
)
def test_host_arch_known(name: str, arch: ftp.Arch) -> None:
    assert ft.host_arch(name) is arch


def test_host_arch_unknown() -> None:
    assert ft.host_arch("s390x") is None


def test_host_arch_detects_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ft.platform, "machine", lambda: "aarch64")
    assert ft.host_arch() is ftp.Arch.ARM64


def test_kvm_missing_device(tmp_path: Path) -> None:
    reason = ft.kvm_reason(ftp.Arch.X86_64, tmp_path / "kvm", ftp.Arch.X86_64)
    assert reason is not None
    assert "does not exist" in reason


def test_kvm_device_not_writable(tmp_path: Path) -> None:
    reason = ft.kvm_reason(
        ftp.Arch.X86_64, kvm_node(tmp_path, writable=False), ftp.Arch.X86_64
    )
    assert reason == f"{tmp_path / 'kvm'} is not readable and writable"


def test_kvm_usable(tmp_path: Path) -> None:
    assert ft.can_kvm(ftp.Arch.X86_64, kvm_node(tmp_path), ftp.Arch.X86_64)


def test_kvm_cross_arch(tmp_path: Path) -> None:
    reason = ft.kvm_reason(ftp.Arch.ARM64, kvm_node(tmp_path), ftp.Arch.X86_64)
    assert reason is not None
    assert "cannot accelerate arm64" in reason


def test_kvm_unknown_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ft, "host_arch", lambda: None)
    reason = ft.kvm_reason(ftp.Arch.X86_64, kvm_node(tmp_path))
    assert reason == "host architecture is not supported"


def test_kvm_reason_rejects_non_arch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="arch must be Arch"):
        ft.kvm_reason("x86_64", tmp_path)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("choice", "name"),
    [("auto", "auto"), ("kvm", "kvm"), ("tcg", "tcg")],
)
def test_accel_name_strings(choice: str, name: str) -> None:
    assert ft.accel_name(choice) == name


@pytest.mark.parametrize(
    ("choice", "name"),
    [(ftp.Accel.KVM, "kvm"), (ftp.Accel.TCG, "tcg")],
)
def test_accel_name_enum(choice: ftp.Accel, name: str) -> None:
    assert ft.accel_name(choice) == name


@pytest.mark.parametrize("choice", ["KVM", "", "host", None, 1])
def test_accel_name_rejects(choice: object) -> None:
    with pytest.raises(ftp.FarmConfigError, match="accel must be one of"):
        ft.accel_name(choice)  # type: ignore[arg-type]


def test_target_arch_and_binary(farm_dir: Path) -> None:
    target = ft.resolve_target(
        manifest(farm_dir), "arm64", "tcg", dev=farm_dir / "kvm"
    )
    assert target.arch is ftp.Arch.ARM64
    assert target.accel is ftp.Accel.TCG
    assert target.qemu_bin == "qemu-system-aarch64"


def test_auto_falls_back_to_tcg(farm_dir: Path) -> None:
    target = ft.resolve_target(
        manifest(farm_dir), ftp.Arch.X86_64, dev=farm_dir / "kvm"
    )
    assert target.accel is ftp.Accel.TCG


def test_auto_selects_kvm(farm_dir: Path, tmp_path: Path) -> None:
    target = ft.resolve_target(
        manifest(farm_dir),
        "x86_64",
        "auto",
        dev=kvm_node(tmp_path),
        host=ftp.Arch.X86_64,
    )
    assert target.accel is ftp.Accel.KVM


def test_explicit_kvm_fails(farm_dir: Path) -> None:
    message = resolve_error(
        manifest(farm_dir), "x86_64", "kvm", dev=farm_dir / "kvm"
    )
    assert "kvm unavailable for x86_64" in message


def test_explicit_tcg_ignores_kvm(farm_dir: Path, tmp_path: Path) -> None:
    target = ft.resolve_target(
        manifest(farm_dir),
        "x86_64",
        "tcg",
        dev=kvm_node(tmp_path),
        host=ftp.Arch.X86_64,
    )
    assert target.accel is ftp.Accel.TCG


def test_unknown_arch(farm_dir: Path) -> None:
    message = resolve_error(manifest(farm_dir), "mips")
    assert "unsupported arch 'mips'" in message


def test_missing_machine() -> None:
    message = resolve_error(fm.parse_manifest(raw_manifest()), "arm64")
    assert "no machine for arch: 'arm64'" in message


def test_manifest_type_rejected() -> None:
    message = resolve_error("manifest", "x86_64")  # type: ignore[arg-type]
    assert "manifest must be FarmManifest" in message


def test_binary_override(farm_dir: Path) -> None:
    raw = raw_manifest()
    raw["machines"][0]["binary"] = "/opt/qemu-system-x86_64"
    parsed = fm.parse_manifest(raw)
    target = ft.resolve_target(parsed, "x86_64", "tcg")
    assert target.qemu_bin == "/opt/qemu-system-x86_64"


def test_resolve_all_in_manifest_order(farm_dir: Path) -> None:
    targets = ft.resolve_targets(manifest(farm_dir), dev=farm_dir / "kvm")
    assert [target.arch for target in targets] == list(ftp.Arch)
    assert all(target.accel is ftp.Accel.TCG for target in targets)


def test_resolve_subset_keeps_request_order(farm_dir: Path) -> None:
    targets = ft.resolve_targets(
        manifest(farm_dir), ["arm64", "x86_64"], "tcg"
    )
    assert [target.arch for target in targets] == [
        ftp.Arch.ARM64,
        ftp.Arch.X86_64,
    ]


def test_resolve_empty_means_all(farm_dir: Path) -> None:
    targets = ft.resolve_targets(manifest(farm_dir), [], dev=farm_dir / "kvm")
    assert len(targets) == len(ftp.Arch)


def test_resolve_duplicates_rejected(farm_dir: Path) -> None:
    with pytest.raises(ftp.FarmConfigError, match="duplicate arches"):
        ft.resolve_targets(manifest(farm_dir), ["x86_64", "x86_64"])


def test_resolve_string_rejected(farm_dir: Path) -> None:
    with pytest.raises(ftp.FarmConfigError, match="not a string"):
        ft.resolve_targets(manifest(farm_dir), "x86_64")  # type: ignore[arg-type]


def test_resolve_unknown_rejected(farm_dir: Path) -> None:
    with pytest.raises(ftp.FarmConfigError, match="unsupported arch"):
        ft.resolve_targets(manifest(farm_dir), ["x86_64", "ppc64"])


def test_target_requires_machine() -> None:
    with pytest.raises(ValueError, match="machine must be Machine"):
        ftp.Target("machine", ftp.Accel.TCG)  # type: ignore[arg-type]


def test_target_requires_accel(farm_dir: Path) -> None:
    machine = manifest(farm_dir).machine_for(ftp.Arch.X86_64)
    with pytest.raises(ValueError, match="accel must be Accel"):
        ftp.Target(machine, "tcg")  # type: ignore[arg-type]
