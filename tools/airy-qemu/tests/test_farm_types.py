# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_types 单元测试：枚举派生、数据模型校验与作业结果证据链。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

from pathlib import Path

import pytest

import farm_types as ft


def make_machine(**overrides) -> ft.Machine:
    """构造默认 x86_64 机器（可按字段覆盖）。"""
    values = {
        "arch": ft.Arch.X86_64,
        "machine": "q35",
        "cpu": "max",
        "console": "ttyS0",
    }
    values.update(overrides)
    return ft.Machine(**values)


def make_kernel(**overrides) -> ft.KernelImage:
    """构造默认 x86_64 内核镜像。"""
    values = {"path": Path("/tmp/bzImage"), "arch": ft.Arch.X86_64}
    values.update(overrides)
    return ft.KernelImage(**values)


def make_job(**overrides) -> ft.FarmJob:
    """构造默认单套件作业。"""
    values = {
        "job_id": "smoke-x86",
        "machine": make_machine(),
        "kernel": make_kernel(),
        "suites": (ft.Suite("seccomp"),),
    }
    values.update(overrides)
    return ft.FarmJob(**values)


def make_tap(plan: int, ok: int, not_ok: int) -> ft.TapSummary:
    """构造 TAP 汇总。"""
    return ft.TapSummary(plan_total=plan, ok_count=ok, not_ok_count=not_ok)


@pytest.mark.parametrize(
    ("arch", "binary"),
    [
        (ft.Arch.X86_64, "qemu-system-x86_64"),
        (ft.Arch.ARM64, "qemu-system-aarch64"),
        (ft.Arch.RISCV, "qemu-system-riscv64"),
        (ft.Arch.LOONGARCH, "qemu-system-loongarch64"),
    ],
)
def test_arch_qemu_bin(arch: ft.Arch, binary: str) -> None:
    assert arch.qemu_bin == binary


def test_arch_parse_from_value() -> None:
    assert ft.Arch("arm64") is ft.Arch.ARM64


def test_accel_auto_prefers_kvm() -> None:
    assert ft.Accel.auto(True) is ft.Accel.KVM
    assert ft.Accel.auto(False) is ft.Accel.TCG


def test_accel_qemu_arg() -> None:
    assert ft.Accel.KVM.qemu_arg == "kvm"
    assert ft.Accel.TCG.qemu_arg == "tcg"


def test_job_state_predicates() -> None:
    assert not ft.JobState.PENDING.terminal
    assert not ft.JobState.RUNNING.terminal
    assert ft.JobState.PASSED.success
    assert ft.JobState.SKIPPED.success
    assert ft.JobState.FAILED.failure
    assert ft.JobState.TIMEOUT.failure
    assert ft.JobState.ERROR.failure
    assert not ft.JobState.PASSED.failure


def test_tap_summary_complete_pass() -> None:
    tap = make_tap(5, 5, 0)
    assert tap.executed == 5
    assert tap.missing == 0
    assert tap.complete
    assert tap.passed


def test_tap_summary_incomplete() -> None:
    tap = make_tap(5, 4, 0)
    assert tap.missing == 1
    assert not tap.complete
    assert not tap.passed


def test_tap_summary_empty_plan_is_not_pass() -> None:
    tap = make_tap(0, 0, 0)
    assert tap.missing == 0
    assert not tap.complete
    assert not tap.passed


def test_tap_summary_not_ok_fails() -> None:
    tap = make_tap(5, 4, 1)
    assert tap.complete
    assert not tap.passed


def test_tap_summary_rejects_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        make_tap(-1, 0, 0)


def test_tap_summary_rejects_overflow() -> None:
    with pytest.raises(ValueError, match="exceeds plan_total"):
        make_tap(2, 2, 1)


def test_machine_console_arg_and_default_bin() -> None:
    machine = make_machine()
    assert machine.qemu_bin == "qemu-system-x86_64"
    assert machine.console_arg == "console=ttyS0"


def test_machine_binary_override() -> None:
    machine = make_machine(binary="/opt/qemu/bin/qemu-system-x86_64")
    assert machine.qemu_bin == "/opt/qemu/bin/qemu-system-x86_64"


def test_machine_console_with_baud() -> None:
    assert make_machine(console="ttyAMA0,115200").console_arg == (
        "console=ttyAMA0,115200"
    )


@pytest.mark.parametrize("console", ["", "tty S0", "0ttyS0", "ttyS0;rm"])
def test_machine_rejects_bad_console(console: str) -> None:
    with pytest.raises(ValueError):
        make_machine(console=console)


def test_machine_rejects_empty_fields() -> None:
    with pytest.raises(ValueError, match="machine"):
        make_machine(machine="  ")


def test_machine_rejects_non_arch() -> None:
    with pytest.raises(ValueError, match="arch must be Arch"):
        make_machine(arch="x86_64")


def test_kernel_coerces_str_path() -> None:
    kernel = make_kernel(path="/boot/vmlinuz")
    assert kernel.path == Path("/boot/vmlinuz")


def test_kernel_rejects_empty_path() -> None:
    with pytest.raises(ValueError, match="image file"):
        make_kernel(path=Path("/"))


def test_kernel_rejects_non_arch() -> None:
    with pytest.raises(ValueError, match="arch must be Arch"):
        make_kernel(arch="x86_64")


def test_suite_default_timeout() -> None:
    assert ft.Suite("pidfd").timeout_s == ft.DEFAULT_SUITE_TIMEOUT_S


@pytest.mark.parametrize("name", ["", "Seccomp", "sec comp", "sec/comp", "-sec"])
def test_suite_rejects_bad_name(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        ft.Suite(name)


def test_suite_rejects_zero_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        ft.Suite("seccomp", timeout_s=0)


def test_farm_job_derived_fields() -> None:
    job = make_job(
        suites=(ft.Suite("seccomp", 60), ft.Suite("pidfd", 30)),
    )
    assert job.suite_names == ("seccomp", "pidfd")
    assert job.suite_budget_s == 90
    assert job.accel is ft.Accel.TCG


def test_farm_job_rejects_arch_mismatch() -> None:
    with pytest.raises(ValueError, match="does not match"):
        make_job(kernel=make_kernel(arch=ft.Arch.ARM64))


def test_farm_job_rejects_empty_suites() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        make_job(suites=())


def test_farm_job_rejects_non_tuple_suites() -> None:
    with pytest.raises(ValueError, match="must be a tuple"):
        make_job(suites=[ft.Suite("seccomp")])


def test_farm_job_rejects_duplicate_suites() -> None:
    with pytest.raises(ValueError, match="duplicate suite"):
        make_job(suites=(ft.Suite("seccomp"), ft.Suite("seccomp")))


@pytest.mark.parametrize("job_id", ["", "bad id", "../escape", "-lead"])
def test_farm_job_rejects_bad_id(job_id: str) -> None:
    with pytest.raises(ValueError, match="job_id"):
        make_job(job_id=job_id)


@pytest.mark.parametrize(
    ("field", "value"),
    [("timeout_s", 0), ("mem_mb", 0), ("smp", -1)],
)
def test_farm_job_rejects_bad_resources(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        make_job(**{field: value})


def test_farm_job_coerces_log_path() -> None:
    job = make_job(log_path="artifacts/smoke.log")
    assert job.log_path == Path("artifacts/smoke.log")


def test_result_passed() -> None:
    result = ft.JobResult(
        job=make_job(),
        state=ft.JobState.PASSED,
        duration_s=12.5,
        exit_code=0,
        tap=make_tap(4, 4, 0),
        qemu_argv=("qemu-system-x86_64", "-M", "q35"),
    )
    assert result.ok
    assert result.qemu_argv[0] == "qemu-system-x86_64"


def test_result_passed_requires_zero_exit() -> None:
    with pytest.raises(ValueError, match="exit_code 0"):
        ft.JobResult(
            job=make_job(),
            state=ft.JobState.PASSED,
            duration_s=1.0,
            exit_code=1,
            tap=make_tap(1, 1, 0),
        )


def test_result_passed_requires_tap() -> None:
    with pytest.raises(ValueError, match="passing TAP"):
        ft.JobResult(
            job=make_job(),
            state=ft.JobState.PASSED,
            duration_s=1.0,
            exit_code=0,
        )


def test_result_passed_requires_complete_tap() -> None:
    with pytest.raises(ValueError, match="passing TAP"):
        ft.JobResult(
            job=make_job(),
            state=ft.JobState.PASSED,
            duration_s=1.0,
            exit_code=0,
            tap=make_tap(4, 3, 0),
        )


def test_result_failed_with_exit_code() -> None:
    result = ft.JobResult(
        job=make_job(),
        state=ft.JobState.FAILED,
        duration_s=3.0,
        exit_code=2,
    )
    assert not result.ok


def test_result_failed_with_failing_tap() -> None:
    result = ft.JobResult(
        job=make_job(),
        state=ft.JobState.FAILED,
        duration_s=3.0,
        exit_code=0,
        tap=make_tap(3, 2, 1),
    )
    assert not result.ok


def test_result_failed_requires_evidence() -> None:
    with pytest.raises(ValueError, match="failing TAP"):
        ft.JobResult(
            job=make_job(),
            state=ft.JobState.FAILED,
            duration_s=3.0,
            exit_code=0,
            tap=make_tap(3, 3, 0),
        )


@pytest.mark.parametrize(
    "state", [ft.JobState.TIMEOUT, ft.JobState.SKIPPED, ft.JobState.ERROR]
)
def test_result_reason_required_states(state: ft.JobState) -> None:
    with pytest.raises(ValueError, match="requires a reason"):
        ft.JobResult(job=make_job(), state=state, duration_s=1.0)


def test_result_skipped_is_ok() -> None:
    result = ft.JobResult(
        job=make_job(),
        state=ft.JobState.SKIPPED,
        duration_s=0.0,
        reason="qemu binary not installed",
    )
    assert result.ok


def test_result_rejects_non_terminal_state() -> None:
    with pytest.raises(ValueError, match="must be terminal"):
        ft.JobResult(job=make_job(), state=ft.JobState.RUNNING, duration_s=1.0)


def test_result_rejects_negative_duration() -> None:
    with pytest.raises(ValueError, match="duration_s"):
        ft.JobResult(
            job=make_job(),
            state=ft.JobState.SKIPPED,
            duration_s=-0.1,
            reason="not run",
        )


def test_result_rejects_list_argv() -> None:
    with pytest.raises(ValueError, match="qemu_argv"):
        ft.JobResult(
            job=make_job(),
            state=ft.JobState.SKIPPED,
            duration_s=0.0,
            reason="not run",
            qemu_argv=["qemu-system-x86_64"],
        )


def test_limits_can_host() -> None:
    limits = ft.FarmLimits(max_jobs=2, mem_mb=4096, cpus=4)
    assert limits.can_host(make_job(mem_mb=2048, smp=2))
    assert not limits.can_host(make_job(mem_mb=8192, smp=2))
    assert not limits.can_host(make_job(mem_mb=2048, smp=8))


def test_limits_reject_zero() -> None:
    with pytest.raises(ValueError, match="max_jobs"):
        ft.FarmLimits(max_jobs=0)


def make_manifest(**overrides) -> ft.FarmManifest:
    """构造默认双架构清单。"""
    values = {
        "version": "1.0.0",
        "machines": (
            make_machine(),
            make_machine(
                arch=ft.Arch.ARM64,
                machine="virt",
                cpu="cortex-a57",
                console="ttyAMA0",
            ),
        ),
        "suites": (ft.Suite("seccomp"), ft.Suite("pidfd")),
    }
    values.update(overrides)
    return ft.FarmManifest(**values)


def test_manifest_machine_for() -> None:
    manifest = make_manifest()
    assert manifest.machine_for(ft.Arch.ARM64).console == "ttyAMA0"
    assert manifest.limits.max_jobs == 1


def test_manifest_machine_for_missing_arch() -> None:
    manifest = make_manifest()
    with pytest.raises(ft.FarmConfigError) as excinfo:
        manifest.machine_for(ft.Arch.LOONGARCH)
    assert excinfo.value.code == ft.AIRY_QEMU_CONFIG_ERROR


def test_manifest_rejects_duplicate_arch() -> None:
    with pytest.raises(ValueError, match="duplicate machine arches"):
        make_manifest(machines=(make_machine(), make_machine()))


def test_manifest_rejects_duplicate_suite() -> None:
    with pytest.raises(ValueError, match="duplicate suite"):
        make_manifest(suites=(ft.Suite("seccomp"), ft.Suite("seccomp")))


@pytest.mark.parametrize("version", ["", "1.0", "v1.0.0", "1.0.0-rc1"])
def test_manifest_rejects_bad_version(version: str) -> None:
    with pytest.raises(ValueError, match="version"):
        make_manifest(version=version)


def test_manifest_rejects_empty_machines() -> None:
    with pytest.raises(ValueError, match="non-empty tuple"):
        make_manifest(machines=())


def test_farm_error_default_code() -> None:
    error = ft.FarmError("boom")
    assert error.code == ft.AIRY_QEMU_ERROR
    assert str(error) == "boom"
