# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""airy-qemu 端到端测试：从命令行到门禁报告的完整链路。

真实 QEMU 与 virtme-ng 由假执行器替代，测试聚焦于流水线的可观测契约：
作业顺序、报告 schema、门禁结论、证据落盘以及 CI/文档挂点。这样既能在
无 KVM、无 vng 的开发机上复现，也能在 CI 中稳定运行。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

import farm_cli as fc
import farm_cmdline as fm
import farm_runner as fr
import farm_types as ftp

IMAGE_SIZE = 0x206
BZ_FLAG_OFF = 0x1FE
BZ_SIG_OFF = 0x202
ARM64_MAGIC_OFF = 56
ROOTFS_SIZE = 512


def _bz_image(path: Path) -> Path:
    """写出可被预检识别的 x86 bzImage 头。"""
    data = bytearray(IMAGE_SIZE)
    data[BZ_FLAG_OFF:BZ_FLAG_OFF + 2] = b"\x55\xaa"
    data[BZ_SIG_OFF:BZ_SIG_OFF + 4] = b"HdrS"
    path.write_bytes(bytes(data))
    return path


def _arm_image(path: Path) -> Path:
    """写出可被预检识别的 arm64 Image 头。"""
    data = bytearray(IMAGE_SIZE)
    data[ARM64_MAGIC_OFF:ARM64_MAGIC_OFF + 4] = b"ARM\x64"
    path.write_bytes(bytes(data))
    return path


def _ksft(root: Path) -> Path:
    """按安装契约写出单套件 kselftest 目录骨架。"""
    root.mkdir()
    (root / "run_kselftest.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "kselftest-list.txt").write_text(
        "seccomp:seccomp_bpf\n", encoding="utf-8"
    )
    (root / "seccomp").mkdir()
    return root


def _tap(status: str = "ok", directive: str = "") -> str:
    """生成单套件、单用例的 TAP 文本。"""
    line = f"{status} 1 selftests: seccomp: seccomp_bpf"
    if directive:
        line = f"{line} # {directive}"
    return f"TAP version 13\n1..1\n{line}\n"


def _args(
    ksft: Path,
    out: Path,
    *images: tuple[str, Path],
    rootfs: Path | None = None,
) -> list[str]:
    """构造覆盖全部必需参数的命令行。"""
    args = ["--ksft-dir", str(ksft), "--out", str(out)]
    for arch, path in images:
        args.extend(("--kernel", f"{arch}={path}"))
    if rootfs is not None:
        args.extend(("--rootfs", str(rootfs)))
    args.extend(("--suites", "seccomp"))
    return args


def _fake(
    taps: dict[str, str],
    codes: dict[str, int] | None = None,
    delays: dict[str, float] | None = None,
    timed_out: tuple[str, ...] = (),
) -> Any:
    """构造按作业 ID 取证、可注入时序与退出码的执行器。"""

    def run(
        target: ftp.Target,
        job: ftp.FarmJob,
        env: fm.RunEnv,
        preflight: Any,
        out_dir: Path,
    ) -> fr.RawRun:
        delay = (delays or {}).get(job.job_id)
        if delay is not None:
            time.sleep(delay)
        out_dir.mkdir(parents=True, exist_ok=True)
        tap_path = out_dir / f"{job.job_id}.tap"
        if job.job_id in taps:
            tap_path.write_text(taps[job.job_id], encoding="utf-8")
        log_path = out_dir / f"{job.job_id}.log"
        log_path.write_text("console\n", encoding="utf-8")
        return fr.RawRun(
            job.job_id,
            ("vng", f"--run={job.kernel.path}"),
            (codes or {}).get(job.job_id, 0),
            0.05,
            job.job_id in timed_out,
            tap_path,
            log_path,
            preflight,
        )

    return run


def _both(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """准备双架构内核、kselftest 目录与 rootfs 镜像。"""
    x86 = _bz_image(tmp_path / "bzImage")
    arm = _arm_image(tmp_path / "Image")
    ksft = _ksft(tmp_path / "ksft")
    rootfs = tmp_path / "rootfs.img"
    rootfs.write_bytes(bytes(ROOTFS_SIZE))
    return x86, arm, ksft, rootfs


def _report(out: Path) -> dict[str, Any]:
    """读取默认报告落点。"""
    return json.loads((out / "farm.json").read_text(encoding="utf-8"))


def test_two_arch_gate_blocks_only_failing_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """双架构中 arm64 失败时，只有该作业阻断门禁。"""
    x86, arm, ksft, rootfs = _both(tmp_path)
    taps = {"x86_64": _tap(), "arm64": _tap("not ok")}
    monkeypatch.setattr(fm, "host_arch", lambda: ftp.Arch.X86_64)
    monkeypatch.setattr(fc, "run_job", _fake(taps, {"arm64": 1}))
    out = tmp_path / "out"

    code = fc.main(
        _args(ksft, out, ("x86_64", x86), ("arm64", arm), rootfs=rootfs)
    )

    payload = _report(out)
    assert code == fc.EXIT_GATE
    assert payload["gate"] == "failed"
    assert payload["blocked"] == ["arm64"]
    assert payload["counts"] == {"total": 2, "ok": 1, "blocking": 1}
    assert payload["states"] == {"failed": 1, "passed": 1}
    assert [job["job_id"] for job in payload["jobs"]] == ["x86_64", "arm64"]
    assert [job["state"] for job in payload["jobs"]] == ["passed", "failed"]
    for job_id in ("x86_64", "arm64"):
        assert (out / f"{job_id}.tap").is_file()
        assert (out / f"{job_id}.log").is_file()
    assert "gate: FAIL blocked: arm64" in capsys.readouterr().out


def test_failing_job_reports_tap_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败作业的报告应保留 TAP 计数与可定位原因。"""
    x86, arm, ksft, rootfs = _both(tmp_path)
    monkeypatch.setattr(fm, "host_arch", lambda: ftp.Arch.X86_64)
    monkeypatch.setattr(
        fc, "run_job", _fake({"x86_64": _tap(), "arm64": _tap("not ok")})
    )
    out = tmp_path / "out"

    fc.main(
        _args(ksft, out, ("x86_64", x86), ("arm64", arm), rootfs=rootfs)
    )

    failed = _report(out)["jobs"][1]
    assert failed["tap"]["not_ok_count"] == 1
    assert failed["tap"]["complete"] is True
    assert failed["tap"]["passed"] is False
    assert "not ok" in failed["reason"]


def test_all_skipped_passes_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全部用例 SKIP 时判 SKIPPED，不阻断门禁。"""
    x86, _, ksft, _ = _both(tmp_path)
    monkeypatch.setattr(
        fc, "run_job", _fake({"x86_64": _tap(directive="SKIP unsupported")})
    )
    out = tmp_path / "out"

    code = fc.main(_args(ksft, out, ("x86_64", x86)))

    payload = _report(out)
    assert code == fc.EXIT_OK
    assert payload["gate"] == "passed"
    assert payload["states"] == {"skipped": 1}
    assert payload["jobs"][0]["tap"]["skip_count"] == 1
    assert payload["jobs"][0]["tap"]["ok_count"] == 0


def test_missing_tap_evidence_is_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行器未留下 TAP 时应判 ERROR 而不是静默通过。"""
    x86, _, ksft, _ = _both(tmp_path)
    monkeypatch.setattr(fc, "run_job", _fake({}))
    out = tmp_path / "out"

    code = fc.main(_args(ksft, out, ("x86_64", x86)))

    payload = _report(out)
    assert code == fc.EXIT_GATE
    assert payload["states"] == {"error": 1}
    assert payload["jobs"][0]["tap"] is None
    assert payload["jobs"][0]["reason"]


def test_timeout_is_reported_as_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时作业进入 timeout 状态并阻断门禁。"""
    x86, _, ksft, _ = _both(tmp_path)
    monkeypatch.setattr(
        fc,
        "run_job",
        _fake({"x86_64": _tap()}, {"x86_64": -15}, timed_out=("x86_64",)),
    )
    out = tmp_path / "out"

    code = fc.main(_args(ksft, out, ("x86_64", x86)))

    payload = _report(out)
    assert code == fc.EXIT_GATE
    assert payload["states"] == {"timeout": 1}
    assert "timeout" in payload["jobs"][0]["reason"]


def test_report_keeps_input_order_under_reversed_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """后派发作业先完成时，报告仍按输入顺序排列。"""
    x86, arm, ksft, rootfs = _both(tmp_path)
    taps = {"x86_64": _tap(), "arm64": _tap()}
    monkeypatch.setattr(fm, "host_arch", lambda: ftp.Arch.X86_64)
    monkeypatch.setattr(
        fc, "run_job", _fake(taps, delays={"x86_64": 0.2})
    )
    out = tmp_path / "out"

    code = fc.main(
        _args(ksft, out, ("x86_64", x86), ("arm64", arm), rootfs=rootfs)
    )

    assert code == fc.EXIT_OK
    assert [job["job_id"] for job in _report(out)["jobs"]] == [
        "x86_64",
        "arm64",
    ]


def test_report_is_byte_identical_across_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一份证据重复运行应产出逐字节相同的报告。"""
    x86, _, ksft, _ = _both(tmp_path)
    monkeypatch.setattr(fc, "run_job", _fake({"x86_64": _tap()}))
    first = tmp_path / "first"
    second = tmp_path / "second"

    fc.main(_args(ksft, first, ("x86_64", x86)))
    fc.main(_args(ksft, second, ("x86_64", x86)))

    text = (first / "farm.json").read_text(encoding="utf-8")
    assert text == (second / "farm.json").read_text(encoding="utf-8")


def test_ci_hook_runs_farm_tests(repo_root: Path) -> None:
    """CI 必须真实挂载农场测试与依赖。

    os-chk-verify.yml 只保留一行 gate 调用（内联 run 块无法在本地重放），
    依赖清单与 pytest 目录因此落在 gate 里：workflow 断言接线，gate 断言
    载荷，两者缺一都算红。
    """
    workflow = repo_root / ".github" / "workflows" / "os-chk-verify.yml"
    assert "ci/ci.sh python-verify --kind oschk" in workflow.read_text(encoding="utf-8")

    gate = repo_root / "ci" / "gates" / "python-verify.sh"
    text = gate.read_text(encoding="utf-8")

    # 农场目录必须留在 oschk 的工具集合里，否则它的用例会静默退出 CI。
    assert "airy-qemu" in text


def test_readme_documents_contract(farm_dir: Path) -> None:
    """README 必须列出入口、报告落点、清单与退出码契约。"""
    text = (farm_dir / "README.md").read_text(encoding="utf-8")

    for token in ("farm_cli", "manifest.yaml", "farm.json", "退出码"):
        assert token in text
