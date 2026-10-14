#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
os_chk.py — AirymaxOS 统一门禁编排器（OS-CHK）

读取 tools/os-chk/gates.yaml 门禁注册表，以子进程方式依次调用各既有检查
工具，汇总为统一报告（格式对齐 05-development-process.md §5.3），并按严重
级（P0/P1/P2）判定是否阻断 CI。

设计原则（与项目铁律一致）：
  * 本编排器不实现任何检查逻辑，只负责注册表解析、调度、汇总与门禁判定；
    检查本体由 tools/ 下的既有执行器承担，避免逻辑重复与长期漂移。
  * 注册表只登记「存在真实可执行器」的门禁（见 gates.yaml 头部说明），
    严禁登记占位项。

权威依据：
  docs/docs-linux/50-engineering-standards/05-development-process.md
    Part III（OS-CHK 编号体系 §6、检查报告格式 §5.3）

Usage:
    python3 tools/os-chk/os_chk.py [options]

Options:
    --repo-root PATH    仓库根（默认：自动探测）
    --registry PATH     门禁注册表（默认：tools/os-chk/gates.yaml）
    --gate ID           仅运行指定门禁（可重复指定）
    --fail-on LEVEL     阻断阈值：P0 / P1 / P2 / none（默认：P1）
    --timeout SECONDS   未显式声明超时的门禁所用默认超时（默认：600）
    --json PATH         输出 JSON 报告到指定路径
    --list              仅列出注册表门禁，不执行
    --check             CI 模式：精简输出，仅退出码
    --verbose           输出未通过门禁的完整 stdout/stderr

Exit codes:
    0 — 通过（无达到阻断阈值的未通过门禁）
    1 — 阻断（存在达到阈值的未通过门禁）
    2 — 用法/环境错误（注册表缺失、schema 非法等）
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺失时在解析阶段报错
    yaml = None

# ─── Constants ─────────────────────────────────────────────────────────────

DEFAULT_REGISTRY_REL = "tools/os-chk/gates.yaml"
DEFAULT_TIMEOUT_S = 600

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_ERROR = "ERROR"

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
FAIL_ON_CHOICES = ("P0", "P1", "P2", "none")

EXIT_PASS = 0
EXIT_BLOCKED = 1
EXIT_USAGE = 2

DETAIL_LIMIT = 200

GATE_KEYS = frozenset(
    {"id", "name", "severity", "category", "authority", "executor",
     "timeout_s"})
EXECUTOR_KEYS = frozenset({"argv"})

# ─── Data model ────────────────────────────────────────────────────────────


class RegistryError(Exception):
    """门禁注册表缺失 / schema 非法。"""


@dataclass(frozen=True)
class Gate:
    id: str
    name: str
    severity: str
    category: str
    authority: str
    argv: Tuple[str, ...]
    timeout_s: Optional[int]


@dataclass
class GateResult:
    gate: Gate
    status: str
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    detail: str


# ─── Registry parsing ──────────────────────────────────────────────────────


def _parse_gate(item, idx: int, seen: set) -> Gate:
    where = f"gates[{idx}]"
    if not isinstance(item, dict):
        raise RegistryError(f"{where}: 必须是映射（mapping）")

    unknown = sorted(set(item) - GATE_KEYS)
    if unknown:
        raise RegistryError(f"{where}: 未知字段：{', '.join(unknown)}")

    gid = item.get("id")
    if not isinstance(gid, str) or not gid.strip():
        raise RegistryError(f"{where}: 缺少合法 id")
    if gid in seen:
        raise RegistryError(f"{where}: 门禁 id 重复：{gid}")

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise RegistryError(f"{where} ({gid}): 缺少合法 name")

    severity = item.get("severity")
    if severity not in SEVERITY_ORDER:
        raise RegistryError(f"{where} ({gid}): severity 必须是 P0/P1/P2")

    executor = item.get("executor")
    if not isinstance(executor, dict):
        raise RegistryError(f"{where} ({gid}): 缺少 executor 映射")

    unknown = sorted(set(executor) - EXECUTOR_KEYS)
    if unknown:
        raise RegistryError(
            f"{where} ({gid}): executor 未知字段：{', '.join(unknown)}")

    argv = executor.get("argv")
    if (not isinstance(argv, list) or not argv
            or not all(isinstance(a, str) and a for a in argv)):
        raise RegistryError(
            f"{where} ({gid}): executor.argv 必须是非空字符串列表")

    timeout_s = item.get("timeout_s")
    if timeout_s is not None and (
            not isinstance(timeout_s, int) or isinstance(timeout_s, bool)
            or timeout_s <= 0):
        raise RegistryError(f"{where} ({gid}): timeout_s 必须是正整数")

    seen.add(gid)
    return Gate(
        id=gid,
        name=name,
        severity=severity,
        category=str(item.get("category", "")),
        authority=str(item.get("authority", "")),
        argv=tuple(argv),
        timeout_s=timeout_s,
    )


def parse_registry(text: str) -> Tuple[str, List[Gate]]:
    if yaml is None:
        raise RegistryError("PyYAML 未安装：pip install pyyaml")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RegistryError(f"注册表 YAML 解析失败：{exc}") from exc
    if not isinstance(doc, dict):
        raise RegistryError("注册表根节点必须是映射（mapping）")

    version = doc.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RegistryError("注册表缺少合法 version 字段")

    raw_gates = doc.get("gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise RegistryError("注册表 gates 必须是非空列表")

    seen: set = set()
    gates = [_parse_gate(item, idx, seen)
             for idx, item in enumerate(raw_gates)]
    return version, gates


# ─── Repository / gate execution ───────────────────────────────────────────


def find_repo_root(start: Optional[Path] = None) -> Path:
    base = Path(start).resolve() if start else Path(__file__).resolve().parent
    for candidate in [base, *base.parents]:
        if (candidate / ".git").exists():
            return candidate
    return Path(__file__).resolve().parents[2]


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _summarize(stdout: str, stderr: str) -> str:
    detail = _last_nonempty_line(stdout) or _last_nonempty_line(stderr)
    if not detail:
        return "(无输出)"
    if len(detail) > DETAIL_LIMIT:
        detail = detail[:DETAIL_LIMIT - 1] + "…"
    return detail


def run_gate(gate: Gate, repo_root: Path,
             default_timeout: Optional[int] = None) -> GateResult:
    timeout = gate.timeout_s or default_timeout or DEFAULT_TIMEOUT_S
    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(gate.argv),
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        detail = f"执行器未找到：{gate.argv[0]}"
        return GateResult(gate, STATUS_ERROR, 127, "", detail,
                          time.monotonic() - started, detail)
    except subprocess.TimeoutExpired:
        detail = f"执行超时（>{timeout}s）"
        return GateResult(gate, STATUS_ERROR, 124, "", detail,
                          time.monotonic() - started, detail)
    except OSError as exc:
        detail = f"无法启动执行器：{exc}"
        return GateResult(gate, STATUS_ERROR, 126, "", detail,
                          time.monotonic() - started, detail)

    duration = time.monotonic() - started
    status = STATUS_PASS if proc.returncode == 0 else STATUS_FAIL
    return GateResult(gate, status, proc.returncode, proc.stdout,
                      proc.stderr, duration, _summarize(proc.stdout,
                                                        proc.stderr))


def evaluate(results: Sequence[GateResult], fail_on: str) -> List[GateResult]:
    if fail_on == "none":
        return []
    threshold = SEVERITY_ORDER[fail_on]
    return [
        r for r in results
        if r.status != STATUS_PASS
        and SEVERITY_ORDER.get(r.gate.severity, len(SEVERITY_ORDER)) <= threshold
    ]


# ─── Report ────────────────────────────────────────────────────────────────


def _marker(status: str) -> str:
    return "[  OK  ]" if status == STATUS_PASS else "[ERROR]"


def format_line(result: GateResult, ts: str) -> str:
    return (f"[{ts}] {_marker(result.status)} "
            f"{result.gate.id} {result.gate.name}: {result.detail}")


def print_report(results: Sequence[GateResult], registry_version: str,
                 elapsed_s: float, check_mode: bool, verbose: bool,
                 ts: str) -> None:
    total = len(results)
    failed = [r for r in results if r.status != STATUS_PASS]
    passed = total - len(failed)

    if check_mode:
        if not failed:
            print(f"OS-CHK: PASS ({passed}/{total} gates, {elapsed_s:.1f}s)")
        else:
            print(f"OS-CHK: FAIL ({passed}/{total} gates, "
                  f"{len(failed)} failed, {elapsed_s:.1f}s)")
            for r in failed:
                print(f"  {_marker(r.status)} {r.gate.id} {r.gate.name}: "
                      f"{r.detail}")
        return

    print("=" * 72)
    print("AirymaxOS OS-CHK 门禁报告")
    print(f"注册表版本: {registry_version}")
    print("=" * 72)
    print()
    for r in results:
        print(format_line(r, ts))
        if verbose and r.status != STATUS_PASS:
            for line in (r.stdout + r.stderr).splitlines():
                print(f"    | {line}")
    print()
    print("-" * 72)
    if not failed:
        print(f"RESULT: PASS — 全部 {total} 条门禁通过（{elapsed_s:.1f}s）")
    else:
        print(f"RESULT: FAIL — {len(failed)}/{total} 条门禁未通过"
              f"（{elapsed_s:.1f}s）")
    print("-" * 72)


def write_json_report(path: Path, registry_version: str, timestamp: str,
                      fail_on: str, results: Sequence[GateResult]) -> None:
    payload = {
        "registry_version": registry_version,
        "timestamp": timestamp,
        "fail_on": fail_on,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.status == STATUS_PASS),
            "failed": sum(1 for r in results if r.status != STATUS_PASS),
        },
        "results": [
            {
                "id": r.gate.id,
                "name": r.gate.name,
                "severity": r.gate.severity,
                "category": r.gate.category,
                "status": r.status,
                "returncode": r.returncode,
                "duration_s": round(r.duration_s, 3),
                "detail": r.detail,
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# ─── Main ──────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AirymaxOS 统一门禁编排器（OS-CHK）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", default=None,
                        help="仓库根（默认：自动探测）")
    parser.add_argument("--registry", default=None,
                        help=f"门禁注册表（默认：{DEFAULT_REGISTRY_REL}）")
    parser.add_argument("--gate", action="append", dest="gate",
                        metavar="ID", default=None,
                        help="仅运行指定门禁（可重复指定）")
    parser.add_argument("--fail-on", choices=FAIL_ON_CHOICES, default="P1",
                        help="阻断阈值（默认：P1，即 P0+P1 阻断）")
    parser.add_argument("--timeout", type=int, default=None, metavar="SECONDS",
                        help=f"未显式声明超时的门禁所用默认超时"
                             f"（默认：{DEFAULT_TIMEOUT_S}）")
    parser.add_argument("--json", default=None, metavar="PATH",
                        help="输出 JSON 报告到指定路径")
    parser.add_argument("--list", action="store_true",
                        help="仅列出注册表门禁，不执行")
    parser.add_argument("--check", action="store_true",
                        help="CI 模式：精简输出，仅退出码")
    parser.add_argument("--verbose", action="store_true",
                        help="输出未通过门禁的完整 stdout/stderr")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.timeout is not None and args.timeout <= 0:
        print("ERROR: --timeout 必须是正整数", file=sys.stderr)
        return EXIT_USAGE

    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else find_repo_root())
    if not repo_root.is_dir():
        print(f"ERROR: 仓库根不存在：{repo_root}", file=sys.stderr)
        return EXIT_USAGE

    registry_path = (Path(args.registry) if args.registry
                     else repo_root / DEFAULT_REGISTRY_REL)
    if not registry_path.is_file():
        print(f"ERROR: 门禁注册表不存在：{registry_path}", file=sys.stderr)
        return EXIT_USAGE

    try:
        version, gates = parse_registry(
            registry_path.read_text(encoding="utf-8"))
    except RegistryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.gate:
        wanted = set(args.gate)
        known = {g.id for g in gates}
        unknown = sorted(wanted - known)
        if unknown:
            print(f"ERROR: 未知门禁 id：{', '.join(unknown)}", file=sys.stderr)
            return EXIT_USAGE
        gates = [g for g in gates if g.id in wanted]

    if args.list:
        for g in gates:
            print(f"{g.severity}  {g.id:22s} {g.name}")
        return EXIT_PASS

    started = time.monotonic()
    results = [run_gate(g, repo_root, args.timeout) for g in gates]
    elapsed = time.monotonic() - started
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print_report(results, version, elapsed, args.check, args.verbose, ts)

    if args.json:
        write_json_report(Path(args.json), version, ts, args.fail_on, results)

    blocking = evaluate(results, args.fail_on)
    return EXIT_BLOCKED if blocking else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
