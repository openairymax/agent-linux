# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_preflight 单元测试：镜像格式、安装布局与超时预算校验。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import farm_preflight as fp
import farm_types as ftp

_MACH: dict[ftp.Arch, tuple[str, str, str]] = {
    ftp.Arch.X86_64: ("q35", "max", "ttyS0"),
    ftp.Arch.ARM64: ("virt", "cortex-a57", "ttyAMA0"),
    ftp.Arch.RISCV: ("virt", "rv64", "ttyS0"),
    ftp.Arch.LOONGARCH: ("virt", "la464", "ttyS0"),
}
ELF_MACH: dict[ftp.Arch, int] = {
    ftp.Arch.X86_64: 62,
    ftp.Arch.ARM64: 183,
    ftp.Arch.RISCV: 243,
    ftp.Arch.LOONGARCH: 258,
}


def write(path: Path, data: bytes) -> Path:
    """写入二进制夹具并返回路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def bzimage() -> bytes:
    """构造隐含 HdrS 与 0xAA55 的最小 bzImage 头。"""
    data = bytearray(0x300)
    data[0x1FE:0x200] = b"\x55\xaa"
    data[0x202:0x206] = b"HdrS"
    return bytes(data)


def arm64_img() -> bytes:
    """构造 arm64 Image 头。"""
    data = bytearray(64)
    data[56:60] = b"ARM\x64"
    return bytes(data)


def riscv_img() -> bytes:
    """构造 riscv Image 头。"""
    data = bytearray(64)
    data[48:56] = b"RISCV\x00\x00\x00"
    data[56:60] = b"RSC\x05"
    return bytes(data)


def elf_img(mach: int, klass: int = 2, data: int = 1) -> bytes:
    """构造最小 ELF 头。"""
    raw = bytearray(64)
    raw[0:4] = b"\x7fELF"
    raw[4] = klass
    raw[5] = data
    raw[18:20] = mach.to_bytes(2, "little")
    return bytes(raw)


def make_job(
    kernel: Path,
    arch: ftp.Arch = ftp.Arch.X86_64,
    names: tuple[str, ...] = ("seccomp", "pidfd"),
    timeout: int = 60,
) -> ftp.FarmJob:
    """按架构构造作业（机器定义与清单一致）。"""
    machine, cpu, console = _MACH[arch]
    return ftp.FarmJob(
        "smoke",
        ftp.Machine(arch, machine, cpu, console),
        ftp.KernelImage(kernel, arch),
        tuple(ftp.Suite(name, timeout) for name in names),
    )


def make_ksft(
    root: Path,
    names: tuple[str, ...] = ("seccomp", "pidfd"),
    settings: dict[str, str] | None = None,
) -> Path:
    """构造最小但完整的 kselftest 安装目录。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / fp.RUN_SCRIPT).write_text("#!/bin/sh\n", encoding="utf-8")
    lines = []
    for name in names:
        (root / name).mkdir(exist_ok=True)
        lines.append(f"{name}:{name}_test")
    text = "\n".join(lines) + "\n"
    (root / fp.LIST_NAME).write_text(text, encoding="utf-8")
    for name, content in (settings or {}).items():
        (root / name / fp.SETTINGS_NAME).write_text(content, encoding="utf-8")
    return root


def error_of(call: Callable[..., Any], *args: object) -> str:
    """执行调用并返回配置错误消息。"""
    with pytest.raises(ftp.FarmConfigError) as excinfo:
        call(*args)
    return excinfo.value.message


@pytest.mark.parametrize(
    ("data", "fmt"),
    [
        (elf_img(62), fp.KernelFormat.ELF),
        (bzimage(), fp.KernelFormat.BZIMAGE),
        (arm64_img(), fp.KernelFormat.ARM64),
        (riscv_img(), fp.KernelFormat.RISCV),
        (bytes(64), fp.KernelFormat.UNKNOWN),
    ],
)
def test_detect_fmt_recognizes_images(
    data: bytes, fmt: fp.KernelFormat
) -> None:
    assert fp.detect_fmt(data) is fmt


def test_detect_fmt_rejects_non_bytes() -> None:
    assert error_of(fp.detect_fmt, "x") == "head must be bytes, got str"


def test_check_job_accepts_bzimage(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft")
    report = fp.check_job(make_job(kernel), fp.PreflightEnv(ksft))
    assert report.fmt is fp.KernelFormat.BZIMAGE
    assert report.kernel_size == len(bzimage())
    assert report.elf_mach is None
    assert report.suite_tests == (("seccomp", 1), ("pidfd", 1))
    assert report.suites == ("seccomp", "pidfd")


@pytest.mark.parametrize("arch", list(ftp.Arch))
def test_check_job_accepts_elf_per_arch(
    tmp_path: Path, arch: ftp.Arch
) -> None:
    kernel = write(tmp_path / "vmlinux", elf_img(ELF_MACH[arch]))
    ksft = make_ksft(tmp_path / "ksft")
    report = fp.check_job(make_job(kernel, arch), fp.PreflightEnv(ksft))
    assert report.fmt is fp.KernelFormat.ELF
    assert report.elf_mach == ELF_MACH[arch]


def test_check_job_accepts_arm64_image(tmp_path: Path) -> None:
    kernel = write(tmp_path / "Image", arm64_img())
    ksft = make_ksft(tmp_path / "ksft")
    report = fp.check_job(
        make_job(kernel, ftp.Arch.ARM64), fp.PreflightEnv(ksft)
    )
    assert report.fmt is fp.KernelFormat.ARM64


def test_check_job_accepts_riscv_image(tmp_path: Path) -> None:
    kernel = write(tmp_path / "Image", riscv_img())
    ksft = make_ksft(tmp_path / "ksft")
    report = fp.check_job(
        make_job(kernel, ftp.Arch.RISCV), fp.PreflightEnv(ksft)
    )
    assert report.fmt is fp.KernelFormat.RISCV


def test_check_job_accepts_loongarch_unknown(tmp_path: Path) -> None:
    kernel = write(tmp_path / "vmlinuz.efi", bytes(256))
    ksft = make_ksft(tmp_path / "ksft")
    report = fp.check_job(
        make_job(kernel, ftp.Arch.LOONGARCH), fp.PreflightEnv(ksft)
    )
    assert report.fmt is fp.KernelFormat.UNKNOWN


def test_check_job_rejects_missing_kernel(tmp_path: Path) -> None:
    ksft = make_ksft(tmp_path / "ksft")
    message = error_of(
        fp.check_job, make_job(tmp_path / "bzImage"), fp.PreflightEnv(ksft)
    )
    assert message == "kernel image is not a file: " + str(
        tmp_path / "bzImage"
    )


def test_check_job_rejects_directory_kernel(tmp_path: Path) -> None:
    kernel = tmp_path / "bzImage"
    kernel.mkdir()
    ksft = make_ksft(tmp_path / "ksft")
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message.startswith("kernel image is not a file:")


def test_check_job_rejects_empty_kernel(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", b"")
    ksft = make_ksft(tmp_path / "ksft")
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message == f"kernel image is empty: {kernel}"


def test_check_job_rejects_truncated_elf(tmp_path: Path) -> None:
    kernel = write(tmp_path / "vmlinux", b"\x7fELF")
    ksft = make_ksft(tmp_path / "ksft")
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message == "kernel image has a truncated ELF header"


def test_check_job_rejects_elf32(tmp_path: Path) -> None:
    kernel = write(tmp_path / "vmlinux", elf_img(62, klass=1))
    ksft = make_ksft(tmp_path / "ksft")
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message == "ELF kernel image must be 64-bit (EI_CLASS=2)"


def test_check_job_rejects_elf_be(tmp_path: Path) -> None:
    kernel = write(tmp_path / "vmlinux", elf_img(62, data=2))
    ksft = make_ksft(tmp_path / "ksft")
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message == "ELF kernel image must be little-endian (EI_DATA=1)"


def test_check_job_rejects_elf_machine_mismatch(tmp_path: Path) -> None:
    kernel = write(tmp_path / "vmlinux", elf_img(183))
    ksft = make_ksft(tmp_path / "ksft")
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message == "ELF machine 183 does not match x86_64 (expected 62)"


@pytest.mark.parametrize(
    ("arch", "data", "expected"),
    [
        (ftp.Arch.X86_64, arm64_img(), "arm64"),
        (ftp.Arch.X86_64, bytes(64), "unknown"),
        (ftp.Arch.ARM64, bzimage(), "bzimage"),
        (ftp.Arch.RISCV, arm64_img(), "arm64"),
        (ftp.Arch.LOONGARCH, bzimage(), "bzimage"),
    ],
)
def test_check_job_rejects_format_mismatch(
    tmp_path: Path, arch: ftp.Arch, data: bytes, expected: str
) -> None:
    kernel = write(tmp_path / "kernel", data)
    ksft = make_ksft(tmp_path / "ksft")
    message = error_of(
        fp.check_job, make_job(kernel, arch), fp.PreflightEnv(ksft)
    )
    assert "unexpected format " + expected in message


def test_check_job_rejects_missing_ksft(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    env = fp.PreflightEnv(tmp_path / "ksft")
    message = error_of(fp.check_job, make_job(kernel), env)
    assert message.startswith("kselftest dir is not a directory:")


def test_check_job_rejects_missing_run_script(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft")
    (ksft / fp.RUN_SCRIPT).unlink()
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message == f"{fp.RUN_SCRIPT} is not a file: {ksft / fp.RUN_SCRIPT}"


def test_check_job_rejects_missing_list(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft")
    (ksft / fp.LIST_NAME).unlink()
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message.startswith(f"{fp.LIST_NAME} is not a file:")


@pytest.mark.parametrize(
    "line",
    ["seccomp", "seccomp:test:extra", "seccomp:", ":test", "seccomp a:test"],
)
def test_check_job_rejects_bad_list_line(
    tmp_path: Path, line: str
) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft")
    (ksft / fp.LIST_NAME).write_text(line + "\n", encoding="utf-8")
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert fp.LIST_NAME in message


@pytest.mark.parametrize("text", ["", "# only a comment\n"])
def test_check_job_rejects_empty_list(tmp_path: Path, text: str) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft")
    (ksft / fp.LIST_NAME).write_text(text, encoding="utf-8")
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message.endswith("no tests listed")


def test_check_job_accepts_commented_list(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft")
    text = "# generated\nseccomp:seccomp_bpf\n\npidfd:pidfd_test\n"
    (ksft / fp.LIST_NAME).write_text(text, encoding="utf-8")
    report = fp.check_job(make_job(kernel), fp.PreflightEnv(ksft))
    assert dict(report.suite_tests) == {"seccomp": 1, "pidfd": 1}


def test_check_job_rejects_uninstalled_suite(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft", names=("seccomp",))
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message == f"suite 'pidfd' is not installed under {ksft}"


def test_check_job_rejects_missing_suite_dir(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft")
    (ksft / "pidfd").rmdir()
    message = error_of(
        fp.check_job, make_job(kernel), fp.PreflightEnv(ksft)
    )
    assert message.startswith("suite directory 'pidfd' is not a directory:")


def test_check_job_rejects_over_budget_timeout(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(
        tmp_path / "ksft",
        names=("seccomp",),
        settings={"seccomp": "timeout=90\n"},
    )
    message = error_of(
        fp.check_job,
        make_job(kernel, names=("seccomp",)),
        fp.PreflightEnv(ksft),
    )
    assert message == (
        "suite seccomp: guest timeout 90s exceeds farm budget 60s"
    )


def test_check_job_rejects_default_over_budget(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft", names=("seccomp",))
    message = error_of(
        fp.check_job,
        make_job(kernel, names=("seccomp",), timeout=30),
        fp.PreflightEnv(ksft),
    )
    assert message == (
        "suite seccomp: guest timeout 45s exceeds farm budget 30s"
    )


def test_check_job_allows_unlimited_timeout(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(
        tmp_path / "ksft", names=("rseq",), settings={"rseq": "timeout=0\n"}
    )
    report = fp.check_job(
        make_job(kernel, names=("rseq",), timeout=10), fp.PreflightEnv(ksft)
    )
    assert report.suites == ("rseq",)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("# comment\ntimeout=30\n", 30),
        ("migrate=1\ntimeout=20\n", 20),
        ("", fp.DEFAULT_GUEST_TIMEOUT_S),
        (None, fp.DEFAULT_GUEST_TIMEOUT_S),
    ],
)
def test_suite_timeout_parses(
    tmp_path: Path, content: str | None, expected: int
) -> None:
    path = tmp_path / fp.SETTINGS_NAME
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert fp.suite_timeout(path) == expected


@pytest.mark.parametrize(
    "content", ["timeout=abc\n", "timeout=\n", "timeout\n"]
)
def test_suite_timeout_rejects_bad_value(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / fp.SETTINGS_NAME
    path.write_text(content, encoding="utf-8")
    message = error_of(fp.suite_timeout, path)
    assert str(path) in message


def test_suite_timeout_rejects_directory(tmp_path: Path) -> None:
    path = tmp_path / fp.SETTINGS_NAME
    path.mkdir()
    assert error_of(fp.suite_timeout, path) == (
        f"settings is not a file: {path}"
    )


def test_check_job_with_source_verifies_provenance(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft")
    base = tmp_path / "kernel" / "tools" / "testing" / "selftests"
    (base / "seccomp").mkdir(parents=True)
    (base / "pidfd").mkdir()
    (base / fp.RUN_SCRIPT).write_text("#!/bin/sh\n", encoding="utf-8")
    env = fp.PreflightEnv(ksft, source=tmp_path / "kernel")
    report = fp.check_job(make_job(kernel), env)
    assert report.source == tmp_path / "kernel"


def test_check_job_rejects_missing_source_suite(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft")
    base = tmp_path / "kernel" / "tools" / "testing" / "selftests"
    (base / "seccomp").mkdir(parents=True)
    (base / fp.RUN_SCRIPT).write_text("#!/bin/sh\n", encoding="utf-8")
    env = fp.PreflightEnv(ksft, source=tmp_path / "kernel")
    message = error_of(fp.check_job, make_job(kernel), env)
    assert message.startswith("source suite directory 'pidfd'")


def test_check_job_rejects_bad_source(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    ksft = make_ksft(tmp_path / "ksft")
    env = fp.PreflightEnv(ksft, source=tmp_path / "missing")
    message = error_of(fp.check_job, make_job(kernel), env)
    assert message.startswith("kernel source is not a directory:")


def test_check_job_rejects_bad_types(tmp_path: Path) -> None:
    kernel = write(tmp_path / "bzImage", bzimage())
    env = fp.PreflightEnv(make_ksft(tmp_path / "ksft"))
    assert error_of(fp.check_job, "job", env) == "job must be FarmJob, got str"
    message = error_of(fp.check_job, make_job(kernel), str(env.ksft_dir))
    assert message == "env must be PreflightEnv, got str"


def test_check_kernel_rejects_bad_type() -> None:
    message = error_of(fp.check_kernel, "bzImage")
    assert message == "kernel must be KernelImage, got str"


def test_check_suite_rejects_bad_type(tmp_path: Path) -> None:
    message = error_of(
        fp.check_suite, "seccomp", fp.PreflightEnv(tmp_path), {}
    )
    assert message == "suite must be Suite, got str"


def test_preflight_env_normalizes_strings(tmp_path: Path) -> None:
    env = fp.PreflightEnv(str(tmp_path / "ksft"), source=str(tmp_path / "k"))
    assert env.ksft_dir == tmp_path / "ksft"
    assert env.source == tmp_path / "k"


def test_preflight_env_rejects_empty_path() -> None:
    message = error_of(fp.PreflightEnv, "")
    assert message == "ksft_dir must be a non-empty path, got ''"
