# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""farm_cli — QEMU 测试农场的命令行入口。

把清单加载、目标解析、预检、并发调度、执行取证、TAP 判定与报告输出
串成一条完整流水线，供 CI 与开发者在本地以单条命令运行 kselftest：

  * ``--kernel ARCH=PATH`` 为每个待测架构声明内核镜像，可重复；作业 ID
    取架构名，天然唯一，报告因此与架构一一对应；
  * ``--suites`` 只选择清单中已登记的套件，未知或重复名称直接拒绝，
    不静默裁剪；
  * 跨架构运行必须提供 ``--rootfs``，该约束在派发前由命令行构造器校验，
    避免部分作业已执行后才失败；
  * 调度、执行与判定全部复用 farm_sched / farm_runner / farm_verdict，
    CLI 自身不复制任何门禁或 TAP 语义。

退出码沿用 tools/parse-tap.py 的门禁约定：

  0 — 门禁通过（允许 SKIPPED，不允许 FAILED/TIMEOUT/ERROR）
  1 — 门禁失败
  2 — 用法或配置错误（清单、内核、套件、路径非法）

权威依据：
  docs-linux/70-build-system/03-ci-cd-pipeline.md §5.3
    OS-TEST-010/013~022（QEMU 运行 kselftest 与门禁）
  docs-linux/80-testing/02-kselftest.md（OS-TEST-021）
  tools/parse-tap.py（退出码契约）

Example:
    >>> main(["--kernel", "x86_64=arch/x86/boot/bzImage",
    ...       "--ksft-dir", "kselftest-install", "--out", "out"])
    0

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

# 1. 标准库导入
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# 2. 本工具模块导入
from farm_cmdline import RunEnv, build_run
from farm_manifest import load_manifest
from farm_preflight import Preflight, PreflightEnv, check_job
from farm_report import summary_lines, write_json
from farm_runner import run_job
from farm_sched import run_batch
from farm_target import ACCEL_CHOICES, resolve_target
from farm_types import (
    Arch,
    FarmConfigError,
    FarmJob,
    FarmLimits,
    FarmManifest,
    JobResult,
    KernelImage,
    Suite,
    Target,
)
from farm_verdict import GateReport, judge_run

# 3. 公开导出
__all__ = ["build_parser", "main"]

# 4. 契约常量
DEFAULT_MANIFEST: Final[Path] = Path(__file__).with_name("manifest.yaml")
REPORT_NAME: Final[str] = "farm.json"
EXIT_OK: Final[int] = 0
EXIT_GATE: Final[int] = 1
EXIT_USAGE: Final[int] = 2


@dataclass(frozen=True)
class _Plan:
    """命令行解析后、派发前的完整执行计划。"""

    jobs: tuple[FarmJob, ...]
    targets: dict[str, Target]
    checks: dict[str, Preflight]
    env: RunEnv
    limits: FarmLimits
    out_dir: Path
    report: Path


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。

    Returns:
        已注册全部参数与校验规则的 ArgumentParser。
    """
    parser = argparse.ArgumentParser(
        prog="airy-qemu",
        description="Run kselftest in QEMU and gate on TAP evidence.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        metavar="PATH",
        help="farm manifest (default: %(default)s)",
    )
    parser.add_argument(
        "--kernel",
        action="append",
        required=True,
        metavar="ARCH=PATH",
        help="kernel image per arch; repeat for each arch",
    )
    parser.add_argument(
        "--ksft-dir",
        type=Path,
        required=True,
        metavar="PATH",
        help="installed kselftest directory",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        metavar="DIR",
        help="directory for TAP and console logs",
    )
    parser.add_argument(
        "--accel",
        choices=ACCEL_CHOICES,
        default="auto",
        help="KVM/TCG selection (default: %(default)s)",
    )
    parser.add_argument(
        "--suites",
        default=None,
        metavar="LIST",
        help="comma separated suite subset (default: manifest order)",
    )
    parser.add_argument(
        "--rootfs",
        type=Path,
        default=None,
        metavar="PATH",
        help="rootfs image required for cross-arch runs",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        metavar="PATH",
        help="JSON report path (default: OUT/farm.json)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行完整农场流水线。

    Args:
        argv: 命令行参数；None 表示读取 sys.argv。

    Returns:
        0 门禁通过、1 门禁失败、2 用法或配置错误。
    """
    args = build_parser().parse_args(argv)
    try:
        gate = _run_all(_plan(args))
    except FarmConfigError as exc:
        return _fail(exc.message)
    except OSError as exc:
        return _fail(str(exc))
    sys.stdout.write("\n".join(summary_lines(gate)) + "\n")
    return EXIT_OK if gate.ok else EXIT_GATE


def _plan(args: argparse.Namespace) -> _Plan:
    """把命令行参数解析为可执行计划。

    Args:
        args: argparse 解析结果。

    Returns:
        作业、目标、预检证据与运行环境齐备的计划。

    Raises:
        FarmConfigError: 清单、内核、套件或路径非法。
    """
    manifest = load_manifest(args.manifest)
    out_dir = _out_dir(args.out)
    suites = _suites(manifest, args.suites)
    env = RunEnv(args.ksft_dir, rootfs=args.rootfs)
    jobs: list[FarmJob] = []
    targets: dict[str, Target] = {}
    checks: dict[str, Preflight] = {}
    for arch, path in _kernels(args.kernel):
        target = resolve_target(manifest, arch, args.accel)
        job = FarmJob(
            arch.value,
            target.machine,
            KernelImage(path, arch),
            suites,
            target.accel,
        )
        build_run(target, job, env)
        jobs.append(job)
        targets[job.job_id] = target
        checks[job.job_id] = check_job(job, PreflightEnv(args.ksft_dir))
    return _Plan(
        tuple(jobs),
        targets,
        checks,
        env,
        manifest.limits,
        out_dir,
        _report(args.report, out_dir),
    )


def _run_all(plan: _Plan) -> GateReport:
    """在资源预算内执行全部作业并写出报告。

    Args:
        plan: 已校验的执行计划。

    Returns:
        汇总后的门禁结果。

    Raises:
        FarmConfigError: 调度或报告落点非法。
        OSError: 报告无法写入。
    """
    def execute(job: FarmJob) -> JobResult:
        raw = run_job(
            plan.targets[job.job_id],
            job,
            plan.env,
            plan.checks[job.job_id],
            plan.out_dir,
        )
        return judge_run(raw)

    gate = GateReport(run_batch(plan.jobs, plan.limits, execute))
    write_json(gate, plan.report)
    return gate


def _kernels(specs: Sequence[str]) -> tuple[tuple[Arch, Path], ...]:
    """解析重复出现的 --kernel ARCH=PATH。

    Args:
        specs: 形如 ``x86_64=bzImage`` 的取值序列。

    Returns:
        按命令行顺序排列的 (架构, 镜像路径) 元组。

    Raises:
        FarmConfigError: 缺少等号、架构未知、镜像路径为空或架构重复。
    """
    parsed: list[tuple[Arch, Path]] = []
    seen: set[Arch] = set()
    for spec in specs:
        name, sep, raw = spec.partition("=")
        if not sep or not raw.strip():
            raise FarmConfigError(f"--kernel expects ARCH=PATH, got {spec!r}")
        try:
            arch = Arch(name.strip())
        except ValueError as exc:
            names = [item.value for item in Arch]
            raise FarmConfigError(
                f"unknown arch {name!r}; expected one of {names}"
            ) from exc
        if arch in seen:
            raise FarmConfigError(f"duplicate --kernel for {arch.value}")
        seen.add(arch)
        parsed.append((arch, Path(raw.strip())))
    return tuple(parsed)


def _suites(manifest: FarmManifest, spec: str | None) -> tuple[Suite, ...]:
    """选择清单中已登记的套件子集。

    Args:
        manifest: 已加载的农场清单。
        spec: 逗号分隔的套件名；None 表示清单全集。

    Returns:
        按清单顺序排列的套件元组。

    Raises:
        FarmConfigError: 子集为空、含未知名称或重复名称。
    """
    if spec is None:
        return manifest.suites
    names = [part.strip() for part in spec.split(",") if part.strip()]
    if not names:
        raise FarmConfigError("--suites must name at least one suite")
    known = {suite.name for suite in manifest.suites}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise FarmConfigError(
            f"unknown suites {unknown}; expected {sorted(known)}"
        )
    if len(names) != len(set(names)):
        raise FarmConfigError(f"duplicate suites: {names}")
    selected = set(names)
    return tuple(
        suite for suite in manifest.suites if suite.name in selected
    )


def _out_dir(value: Path) -> Path:
    """校验并创建输出目录。

    Args:
        value: --out 指定的目录。

    Returns:
        已存在的目录路径。

    Raises:
        FarmConfigError: 路径不是目录或无法创建。
    """
    if value.exists() and not value.is_dir():
        raise FarmConfigError(f"--out is not a directory: {value}")
    try:
        value.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FarmConfigError(f"cannot create --out {value}: {exc}") from exc
    return value


def _report(value: Path | None, out_dir: Path) -> Path:
    """确定 JSON 报告落点。

    Args:
        value: --report 指定的路径；None 表示 OUT/farm.json。
        out_dir: 输出目录。

    Returns:
        报告文件路径。

    Raises:
        FarmConfigError: 落点未指向文件。
    """
    path = out_dir / REPORT_NAME if value is None else value
    if not path.name or path.is_dir():
        raise FarmConfigError(f"report path must name a file: {path}")
    return path


def _fail(message: str) -> int:
    """向 stderr 输出错误并返回用法错误码。"""
    sys.stderr.write(f"ERROR: {message}\n")
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
