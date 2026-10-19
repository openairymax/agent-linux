# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_cli 单元测试：参数解析、流水线编排与退出码。

真实 QEMU 由假执行器替代：测试只验证 CLI 如何把清单、预检、调度、
判定与报告串起来，不依赖宿主虚拟化能力。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import farm_cli as fc
import farm_cmdline as fm
import farm_manifest as fman
import farm_runner as fr
import farm_types as ftp

IMAGE_SIZE = 0x206
BZ_FLAG_OFF = 0x1FE
BZ_SIG_OFF = 0x202


def _image(path: Path) -> Path:
    """写出可被预检识别的 x86 bzImage 头。"""
    data = bytearray(IMAGE_SIZE)
    data[BZ_FLAG_OFF:BZ_FLAG_OFF + 2] = b"\x55\xaa"
    data[BZ_SIG_OFF:BZ_SIG_OFF + 4] = b"HdrS"
    path.write_bytes(bytes(data))
    return path


def _ksft(root: Path, suites: tuple[str, ...]) -> Path:
    """按安装契约写出 kselftest 目录骨架。"""
    root.mkdir()
    (root / "run_kselftest.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    lines = [f"{name}:{name}_test" for name in suites]
    (root / "kselftest-list.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    for name in suites:
        (root / name).mkdir()
    return root


def _inputs(
    tmp_path: Path, suites: tuple[str, ...] = ("seccomp",)
) -> tuple[Path, Path, Path]:
    """准备内核镜像、kselftest 安装目录与输出目录。"""
    image = _image(tmp_path / "bzImage")
    ksft = _ksft(tmp_path / "ksft", suites)
    return image, ksft, tmp_path / "out"


def _argv(
    image: Path,
    ksft: Path,
    out: Path,
    *extra: str,
) -> list[str]:
    """构造最小可运行命令行。"""
    base = [
        "--kernel",
        f"x86_64={image}",
        "--ksft-dir",
        str(ksft),
        "--out",
        str(out),
        "--suites",
        "seccomp",
    ]
    return [*base, *extra]


def _tap(*suites: str) -> str:
    """为给定套件生成全绿 TAP 文本。"""
    lines: list[str] = []
    for name in suites:
        lines.extend(
            (
                "TAP version 13",
                "1..1",
                f"ok 1 selftests: {name}: {name}_test",
            )
        )
    return "\n".join(lines) + "\n"


def _fake(
    tap: str,
    code: int = 0,
    timed_out: bool = False,
    seen: list[ftp.FarmJob] | None = None,
) -> Any:
    """构造替代真实 QEMU 的执行器。"""

    def run(
        target: ftp.Target,
        job: ftp.FarmJob,
        env: fm.RunEnv,
        preflight: Any,
        out_dir: Path,
    ) -> fr.RawRun:
        if seen is not None:
            seen.append(job)
        out_dir.mkdir(parents=True, exist_ok=True)
        tap_path = out_dir / f"{job.job_id}.tap"
        tap_path.write_text(tap, encoding="utf-8")
        log_path = out_dir / f"{job.job_id}.log"
        log_path.write_text("console\n", encoding="utf-8")
        return fr.RawRun(
            job.job_id,
            ("vng",),
            code,
            0.1,
            timed_out,
            tap_path,
            log_path,
            preflight,
        )

    return run


def test_green_tap_passes_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """全绿 TAP 应通过门禁并写出默认报告。"""
    image, ksft, out = _inputs(tmp_path)
    monkeypatch.setattr(fc, "run_job", _fake(_tap("seccomp")))

    code = fc.main(_argv(image, ksft, out))

    assert code == fc.EXIT_OK
    assert "gate: PASS" in capsys.readouterr().out
    payload = json.loads((out / "farm.json").read_text(encoding="utf-8"))
    assert payload["gate"] == "passed"
    assert payload["jobs"][0]["job_id"] == "x86_64"


def test_not_ok_blocks_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """存在 not ok 时门禁失败且退出码为 1。"""
    image, ksft, out = _inputs(tmp_path)
    monkeypatch.setattr(
        fc,
        "run_job",
        _fake(
            "TAP version 13\n1..1\n"
            "not ok 1 selftests: seccomp: seccomp_bpf\n",
            code=1,
        ),
    )

    code = fc.main(_argv(image, ksft, out))

    assert code == fc.EXIT_GATE
    assert "gate: FAIL blocked: x86_64" in capsys.readouterr().out


def test_timeout_blocks_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时作业应记为 TIMEOUT 并阻断门禁。"""
    image, ksft, out = _inputs(tmp_path)
    monkeypatch.setattr(
        fc, "run_job", _fake(_tap("seccomp"), code=-15, timed_out=True)
    )

    code = fc.main(_argv(image, ksft, out))

    payload = json.loads((out / "farm.json").read_text(encoding="utf-8"))
    assert code == fc.EXIT_GATE
    assert payload["states"] == {"timeout": 1}


def test_suite_subset_follows_manifest_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--suites 子集按清单顺序生效，与命令行顺序无关。"""
    image, ksft, out = _inputs(tmp_path, ("seccomp", "pidfd"))
    seen: list[ftp.FarmJob] = []
    tap = _tap("seccomp", "pidfd")
    monkeypatch.setattr(fc, "run_job", _fake(tap, seen=seen))
    args = _argv(image, ksft, out, "--suites", "pidfd,seccomp")

    code = fc.main(args)

    assert code == fc.EXIT_OK
    assert seen[0].suite_names == ("seccomp", "pidfd")


def test_default_suites_are_manifest_suites() -> None:
    """未指定 --suites 时选择清单全集。"""
    manifest = fman.load_manifest(fc.DEFAULT_MANIFEST)

    assert fc._suites(manifest, None) == manifest.suites


def test_explicit_tcg_is_propagated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--accel tcg 应写进作业并传给运行器。"""
    image, ksft, out = _inputs(tmp_path)
    seen: list[ftp.FarmJob] = []
    monkeypatch.setattr(fc, "run_job", _fake(_tap("seccomp"), seen=seen))

    code = fc.main(_argv(image, ksft, out, "--accel", "tcg"))

    assert code == fc.EXIT_OK
    assert seen[0].accel is ftp.Accel.TCG


def test_report_option_overrides_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--report 应覆盖 OUT/farm.json。"""
    image, ksft, out = _inputs(tmp_path)
    target = tmp_path / "reports" / "gate.json"
    monkeypatch.setattr(fc, "run_job", _fake(_tap("seccomp")))

    code = fc.main(_argv(image, ksft, out, "--report", str(target)))

    assert code == fc.EXIT_OK
    assert json.loads(target.read_text(encoding="utf-8"))["gate"] == "passed"
    assert not (out / "farm.json").exists()


def test_usage_error_on_unknown_suite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """未知套件属于用法错误，不应启动任何作业。"""
    image, ksft, out = _inputs(tmp_path)
    called: list[object] = []
    monkeypatch.setattr(fc, "run_job", lambda *args: called.append(args))

    code = fc.main(_argv(image, ksft, out, "--suites", "nope"))

    assert code == fc.EXIT_USAGE
    assert called == []
    assert "ERROR: unknown suites" in capsys.readouterr().err


def test_usage_error_on_duplicate_suite(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """重复套件名应被拒绝而不是静默去重。"""
    image, ksft, out = _inputs(tmp_path)

    code = fc.main(_argv(image, ksft, out, "--suites", "seccomp,seccomp"))

    assert code == fc.EXIT_USAGE
    assert "duplicate suites" in capsys.readouterr().err


def test_usage_error_on_missing_kernel(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """内核镜像缺失应在预检阶段报用法错误。"""
    _, ksft, out = _inputs(tmp_path)
    missing = tmp_path / "missing.bzImage"

    code = fc.main(_argv(missing, ksft, out))

    assert code == fc.EXIT_USAGE
    assert "ERROR:" in capsys.readouterr().err


def test_cross_arch_requires_rootfs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """跨架构缺少 --rootfs 时在派发前失败。"""
    image, ksft, out = _inputs(tmp_path)
    called: list[object] = []
    monkeypatch.setattr(fc, "run_job", lambda *args: called.append(args))
    monkeypatch.setattr(fm, "host_arch", lambda: ftp.Arch.X86_64)
    args = [
        "--kernel",
        f"arm64={image}",
        "--ksft-dir",
        str(ksft),
        "--out",
        str(out),
        "--suites",
        "seccomp",
    ]

    code = fc.main(args)

    assert code == fc.EXIT_USAGE
    assert called == []
    assert "requires rootfs" in capsys.readouterr().err


def test_parser_requires_kernel() -> None:
    """缺少 --kernel 时 argparse 以退出码 2 终止。"""
    with pytest.raises(SystemExit) as exc:
        fc.main([])

    assert exc.value.code == fc.EXIT_USAGE


def test_kernel_spec_parsing() -> None:
    """ARCH=PATH 解析保持命令行顺序。"""
    parsed = fc._kernels(["x86_64=a", "arm64=b"])

    assert parsed == ((ftp.Arch.X86_64, Path("a")), (ftp.Arch.ARM64, Path("b")))


def test_kernel_spec_rejects_missing_equals() -> None:
    """缺少等号应被拒绝。"""
    with pytest.raises(ftp.FarmConfigError, match="ARCH=PATH"):
        fc._kernels(["x86_64"])


def test_kernel_spec_rejects_empty_path() -> None:
    """空镜像路径应被拒绝。"""
    with pytest.raises(ftp.FarmConfigError, match="ARCH=PATH"):
        fc._kernels(["x86_64="])


def test_kernel_spec_rejects_unknown_arch() -> None:
    """未知架构应被拒绝。"""
    with pytest.raises(ftp.FarmConfigError, match="unknown arch"):
        fc._kernels(["mips=image"])


def test_kernel_spec_rejects_duplicate_arch() -> None:
    """同一架构重复声明应被拒绝。"""
    with pytest.raises(ftp.FarmConfigError, match="duplicate --kernel"):
        fc._kernels(["x86_64=a", "x86_64=b"])
