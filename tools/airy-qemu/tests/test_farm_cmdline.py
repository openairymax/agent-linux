# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_cmdline 单元测试：virtme-ng argv 装配与配置错误路径。"""

from __future__ import annotations

from pathlib import Path

import pytest

import farm_cmdline as fc
import farm_manifest as fm
import farm_target as ft
import farm_types as ftp

KERNEL = "/tmp/bzImage"
KSFT = "/tmp/kselftest-install"
SUITES = ("seccomp", "pidfd")


def machine(
    arch: ftp.Arch = ftp.Arch.X86_64,
    name: str = "q35",
    cpu: str = "max",
    console: str = "ttyS0",
) -> ftp.Machine:
    """按架构构造清单风格的机器定义。"""
    return ftp.Machine(arch, name, cpu, console)


def target(
    arch: ftp.Arch = ftp.Arch.X86_64,
    accel: ftp.Accel = ftp.Accel.TCG,
) -> ftp.Target:
    """构造目标（绕过宿主探测，便于确定性断言）。"""
    return ftp.Target(machine(arch), accel)


def job(
    tgt: ftp.Target,
    names: tuple[str, ...] = SUITES,
    accel: ftp.Accel | None = None,
) -> ftp.FarmJob:
    """按目标构造作业。"""
    return ftp.FarmJob(
        "smoke",
        tgt.machine,
        ftp.KernelImage(Path(KERNEL), tgt.arch),
        tuple(ftp.Suite(name) for name in names),
        accel=tgt.accel if accel is None else accel,
    )


def env(**overrides: object) -> fc.RunEnv:
    """构造同架构宿主的默认运行环境。"""
    values: dict[str, object] = {"ksft_dir": Path(KSFT), "host": ftp.Arch.X86_64}
    values.update(overrides)
    return fc.RunEnv(**values)  # type: ignore[arg-type]


def build_error(
    tgt: object, current: object, run_env: object
) -> str:
    """执行构造并返回配置错误消息。"""
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        fc.build_run(tgt, current, run_env)  # type: ignore[arg-type]
    return excinfo.value.message


def test_vng_arch_covers_every_farm_arch() -> None:
    assert set(fc.VNG_ARCH) == set(ftp.Arch)


def test_ksft_cmd_keeps_suite_order() -> None:
    tgt = target()
    assert fc.ksft_cmd(job(tgt)) == (
        "./run_kselftest.sh --summary --collection seccomp --collection pidfd"
    )


def test_ksft_cmd_rejects_non_job() -> None:
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        fc.ksft_cmd("seccomp")  # type: ignore[arg-type]
    assert excinfo.value.message == "job must be FarmJob, got str"


def test_build_run_native_tcg() -> None:
    tgt = target()
    assert fc.build_run(tgt, job(tgt), env()) == (
        "vng",
        "--run",
        KERNEL,
        "--arch",
        "x86_64",
        "--memory",
        "2048M",
        "--cpus",
        "2",
        "--cwd",
        KSFT,
        "--append",
        "console=ttyS0",
        "--disable-microvm",
        "--qemu-opts=-M",
        "--qemu-opts=q35",
        "--qemu-opts=-cpu",
        "--qemu-opts=max",
        "--force-9p",
        "--disable-kvm",
        "--exec",
        "./run_kselftest.sh --summary --collection seccomp --collection pidfd",
        "--quiet",
    )


def test_build_run_native_kvm() -> None:
    tgt = target(accel=ftp.Accel.KVM)
    argv = fc.build_run(tgt, job(tgt), env())
    assert "--disable-kvm" not in argv
    assert "--force-9p" in argv


def test_build_run_foreign_requires_rootfs() -> None:
    tgt = target(ftp.Arch.ARM64)
    message = build_error(tgt, job(tgt), env())
    assert message == "arm64 requires rootfs when running on x86_64"


def test_build_run_unknown_host_requires_rootfs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fc, "host_arch", lambda: None)
    tgt = target()
    message = build_error(tgt, job(tgt), fc.RunEnv(Path(KSFT)))
    assert message == "x86_64 requires rootfs when running on unknown host"


def test_build_run_foreign_with_rootfs() -> None:
    tgt = target(ftp.Arch.ARM64)
    run_env = env(rootfs=Path("/tmp/arm64.img"), root_fstype="ext4")
    argv = fc.build_run(tgt, job(tgt), run_env)
    assert argv[argv.index("--arch") + 1] == "arm64"
    assert "--force-9p" not in argv
    assert argv[argv.index("--root-disk") + 1] == "/tmp/arm64.img"
    assert argv[argv.index("--root-dev") + 1] == fc.DEFAULT_ROOT_DEV
    assert argv[argv.index("--root-fstype") + 1] == "ext4"
    assert "--disable-kvm" in argv


def test_build_run_omits_root_fstype_when_empty() -> None:
    tgt = target(ftp.Arch.ARM64)
    argv = fc.build_run(tgt, job(tgt), env(rootfs=Path("/tmp/arm64.img")))
    assert "--root-fstype" not in argv


def test_build_run_accepts_busybox() -> None:
    tgt = target()
    argv = fc.build_run(tgt, job(tgt), env(busybox=Path("/bin/busybox")))
    assert argv[argv.index("--busybox") + 1] == "/bin/busybox"


def test_build_run_rejects_machine_mismatch() -> None:
    tgt = target()
    other = ftp.Target(machine(name="pc"), ftp.Accel.TCG)
    message = build_error(tgt, job(other), env())
    assert message == "job machine does not match resolved target"


def test_build_run_rejects_accel_mismatch() -> None:
    tgt = target()
    message = build_error(tgt, job(tgt, accel=ftp.Accel.KVM), env())
    assert message == "job accel kvm does not match target tcg"


def test_build_run_rejects_bad_target() -> None:
    tgt = target()
    message = build_error("x86_64", job(tgt), env())
    assert message == "target must be Target, got str"


def test_build_run_rejects_bad_env() -> None:
    tgt = target()
    message = build_error(tgt, job(tgt), KSFT)
    assert message == "env must be RunEnv, got str"


def test_run_env_normalizes_string_paths() -> None:
    run_env = fc.RunEnv(KSFT, rootfs="/tmp/rootfs.img", busybox="/bin/busybox")
    assert run_env.ksft_dir == Path(KSFT)
    assert run_env.rootfs == Path("/tmp/rootfs.img")
    assert run_env.busybox == Path("/bin/busybox")


def test_run_env_rejects_root_fstype_without_rootfs() -> None:
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        fc.RunEnv(Path(KSFT), root_fstype="ext4")
    assert excinfo.value.message == "root_fstype requires rootfs"


@pytest.mark.parametrize("value", ["vda", "/dev/", " /dev/vda", "/dev/vda\n"])
def test_run_env_rejects_bad_root_dev(value: str) -> None:
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        fc.RunEnv(Path(KSFT), root_dev=value)
    assert "root_dev must be a /dev node path" in excinfo.value.message


def test_run_env_rejects_bad_host() -> None:
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        fc.RunEnv(Path(KSFT), host="x86_64")  # type: ignore[arg-type]
    assert excinfo.value.message == "host must be Arch or None, got str"


@pytest.mark.parametrize("value", ["", "  ", " vng"])
def test_run_env_rejects_bad_vng(value: str) -> None:
    with pytest.raises(ftp.FarmConfigError):
        fc.RunEnv(Path(KSFT), vng=value)


def test_defaults_follow_job_budget() -> None:
    tgt = target()
    run_job = ftp.FarmJob(
        "smoke",
        tgt.machine,
        ftp.KernelImage(Path(KERNEL), tgt.arch),
        (ftp.Suite("rseq"),),
        mem_mb=4096,
        smp=4,
    )
    argv = fc.build_run(tgt, run_job, env())
    assert argv[argv.index("--memory") + 1] == "4096M"
    assert argv[argv.index("--cpus") + 1] == "4"


def test_resolved_target_builds(farm_dir: Path) -> None:
    manifest = fm.load_manifest(farm_dir / "manifest.yaml")
    resolved = ft.resolve_target(
        manifest, "x86_64", "tcg", host=ftp.Arch.X86_64
    )
    argv = fc.build_run(resolved, job(resolved), env())
    assert argv[argv.index("--qemu-opts=-M") + 1] == "--qemu-opts=q35"
    assert argv[argv.index("--qemu-opts=-cpu") + 1] == "--qemu-opts=max"
