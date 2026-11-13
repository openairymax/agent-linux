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


def _gate_id(item: dict, where: str, seen: set) -> str:
    """id 是门禁的身份：既不能缺，也不能与已登记项重复。"""
    gid = item.get("id")
    if not isinstance(gid, str) or not gid.strip():
        raise RegistryError(f"{where}: 缺少合法 id")
    if gid in seen:
        raise RegistryError(f"{where}: 门禁 id 重复：{gid}")
    return gid


def _gate_name(item: dict, where: str, gid: str) -> str:
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise RegistryError(f"{where} ({gid}): 缺少合法 name")
    return name


def _gate_sev(item: dict, where: str, gid: str) -> str:
    """严重级只允许注册表已定义的档位，拼写错误不能退化为「不阻断」。"""
    severity = item.get("severity")
    if severity not in SEVERITY_ORDER:
        raise RegistryError(f"{where} ({gid}): severity 必须是 P0/P1/P2")
    return severity


def _str_args(argv: list) -> bool:
    """参数元素均为非空字符串。"""
    return all(isinstance(a, str) and bool(a) for a in argv)


def _argv_ok(argv: object) -> bool:
    """必须是非空列表，且元素均为非空字符串。"""
    return isinstance(argv, list) and bool(argv) and _str_args(argv)


def _gate_argv(item: dict, where: str, gid: str) -> Tuple[str, ...]:
    executor = _gate_exec(item, where, gid)
    argv = executor.get("argv")
    if not _argv_ok(argv):
        raise RegistryError(
            f"{where} ({gid}): executor.argv 必须是非空字符串列表")
    return tuple(argv)


def _gate_exec(item: dict, where: str, gid: str) -> dict:
    """executor 必须是映射，且只含注册表已知字段。"""
    executor = item.get("executor")
    if not isinstance(executor, dict):
        raise RegistryError(f"{where} ({gid}): 缺少 executor 映射")
    unknown = sorted(set(executor) - EXECUTOR_KEYS)
    if unknown:
        raise RegistryError(
            f"{where} ({gid}): executor 未知字段：{', '.join(unknown)}")
    return executor


def _gate_timeout(item: dict, where: str, gid: str) -> Optional[int]:
    """缺省时限由调用方补齐；显式声明则必须是正整数秒。"""
    timeout_s = item.get("timeout_s")
    if timeout_s is not None and not _secs_ok(timeout_s):
        raise RegistryError(f"{where} ({gid}): timeout_s 必须是正整数")
    return timeout_s


def _secs_ok(value: object) -> bool:
    """正整数秒；bool 是 int 子类故单独排除。"""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) and value > 0


def _parse_gate(item, idx: int, seen: set) -> Gate:
    """逐段校验一条门禁；抛错顺序即报告顺序，调用方据此定位到具体字段。"""
    where = f"gates[{idx}]"
    if not isinstance(item, dict):
        raise RegistryError(f"{where}: 必须是映射（mapping）")
    unknown = sorted(set(item) - GATE_KEYS)
    if unknown:
        raise RegistryError(f"{where}: 未知字段：{', '.join(unknown)}")

    gid = _gate_id(item, where, seen)
    gate = Gate(
        id=gid,
        name=_gate_name(item, where, gid),
        severity=_gate_sev(item, where, gid),
        category=str(item.get("category", "")),
        authority=str(item.get("authority", "")),
        argv=_gate_argv(item, where, gid),
        timeout_s=_gate_timeout(item, where, gid),
    )
    seen.add(gid)
    return gate


def _need_version(doc: dict) -> str:
    """version 必须是非空字符串。"""
    version = doc.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RegistryError("注册表缺少合法 version 字段")
    return version


def _need_gates(doc: dict) -> list:
    """gates 必须是非空列表。"""
    raw_gates = doc.get("gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise RegistryError("注册表 gates 必须是非空列表")
    return raw_gates


def parse_registry(text: str) -> Tuple[str, List[Gate]]:
    doc = _load_reg(text)
    version = _need_version(doc)
    raw_gates = _need_gates(doc)
    seen: set = set()
    gates = [_parse_gate(item, idx, seen)
             for idx, item in enumerate(raw_gates)]
    return version, gates


def _load_reg(text: str) -> dict:
    """解析 YAML 文本并要求根节点是映射。"""
    if yaml is None:
        raise RegistryError("PyYAML 未安装：pip install pyyaml")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RegistryError(f"注册表 YAML 解析失败：{exc}") from exc
    if not isinstance(doc, dict):
        raise RegistryError("注册表根节点必须是映射（mapping）")
    return doc


# ─── Repository / gate execution ───────────────────────────────────────────


def find_repo_root(start: Optional[Path] = None) -> Path:
    base = Path(start).resolve() if start else Path(__file__).resolve().parent
    for candidate in [base, *base.parents]:
        if (candidate / ".git").exists():
            return candidate
    return Path(__file__).resolve().parents[2]


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _summarize(stdout: str, stderr: str) -> str:
    detail = _last_line(stdout) or _last_line(stderr)
    if not detail:
        return "(无输出)"
    if len(detail) > DETAIL_LIMIT:
        detail = detail[:DETAIL_LIMIT - 1] + "…"
    return detail


def run_gate(gate: Gate, repo_root: Path,
             default_timeout: Optional[int] = None) -> GateResult:
    timeout = _timeout_of(gate, default_timeout)
    started = time.monotonic()
    try:
        proc = _exec_gate(gate, repo_root, timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _broken(gate, started, exc, timeout)
    return _done(gate, proc, started)


def _timeout_of(gate: Gate, default: Optional[int]) -> int:
    """门禁自带时限优先，其次命令行缺省，最后内置缺省。"""
    return gate.timeout_s or default or DEFAULT_TIMEOUT_S


def _exec_gate(gate: Gate, repo_root: Path, timeout: int):
    """按门禁 argv 在仓库根下执行，超时交由调用方解释。"""
    return subprocess.run(
        list(gate.argv),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _broken(gate: Gate, started: float, exc: BaseException,
            timeout: int) -> GateResult:
    """把启动失败与超时映射成固定退出码的 ERROR 结果。"""
    if isinstance(exc, subprocess.TimeoutExpired):
        code, detail = 124, f"执行超时（>{timeout}s）"
    elif isinstance(exc, FileNotFoundError):
        code, detail = 127, f"执行器未找到：{gate.argv[0]}"
    else:
        code, detail = 126, f"无法启动执行器：{exc}"
    return GateResult(gate, STATUS_ERROR, code, "", detail,
                      time.monotonic() - started, detail)


def _done(gate: Gate, proc, started: float) -> GateResult:
    """把完成的子进程转成 PASS/FAIL 结果。"""
    duration = time.monotonic() - started
    status = STATUS_PASS if proc.returncode == 0 else STATUS_FAIL
    return GateResult(gate, status, proc.returncode, proc.stdout,
                      proc.stderr, duration, _summarize(proc.stdout,
                                                        proc.stderr))


def _rank(result: GateResult) -> int:
    """严重级排序；未知级别排在末尾。"""
    return SEVERITY_ORDER.get(result.gate.severity, len(SEVERITY_ORDER))


def _blocks(result: GateResult, threshold: int) -> bool:
    """未通过且严重级达到阻断阈值。"""
    return result.status != STATUS_PASS and _rank(result) <= threshold


def evaluate(results: Sequence[GateResult], fail_on: str) -> List[GateResult]:
    if fail_on == "none":
        return []
    threshold = SEVERITY_ORDER[fail_on]
    return [r for r in results if _blocks(r, threshold)]


# ─── Report ────────────────────────────────────────────────────────────────


def _marker(status: str) -> str:
    return "[  OK  ]" if status == STATUS_PASS else "[ERROR]"


def format_line(result: GateResult, ts: str) -> str:
    return (f"[{ts}] {_marker(result.status)} "
            f"{result.gate.id} {result.gate.name}: {result.detail}")


def print_report(results: Sequence[GateResult], registry_version: str,
                 elapsed_s: float, check_mode: bool, verbose: bool,
                 ts: str, fail_on: str = "P1") -> None:
    total = len(results)
    failed = [r for r in results if r.status != STATUS_PASS]
    passed = total - len(failed)
    blocking = evaluate(results, fail_on)

    if check_mode:
        _rep_ci(results, failed, blocking, passed, total, elapsed_s)
        return
    _rep_full(results, registry_version, failed, verbose, passed, total,
              elapsed_s, ts)


def _dump_rows(results) -> None:
    """逐条打印未通过门禁的一行摘要。"""
    for r in results:
        print(f"  {_marker(r.status)} {r.gate.id} {r.gate.name}: {r.detail}")


def _rep_ci(results, failed, blocking, passed, total, elapsed_s):
    """CI 形态：一行判定，未通过项逐条列出；阻塞项另有计数。"""
    if not blocking:
        extra = "" if not failed else f", {len(failed)} non-blocking"
        print(f"OS-CHK: PASS ({passed}/{total} gates{extra}, "
              f"{elapsed_s:.1f}s)")
        _dump_rows(failed)
        return
    print(f"OS-CHK: FAIL ({passed}/{total} gates, "
          f"{len(blocking)} blocking, {elapsed_s:.1f}s)")
    _dump_rows(blocking)


def _dump_out(result) -> None:
    """附上未通过门禁的 stdout/stderr 全文。"""
    for line in (result.stdout + result.stderr).splitlines():
        print(f"    | {line}")


def _dump_full(results, verbose, ts) -> None:
    """逐条打印判定；verbose 时附未通过项的输出。"""
    for r in results:
        print(format_line(r, ts))
        if verbose and r.status != STATUS_PASS:
            _dump_out(r)


def _rep_full(results, registry_version, failed, verbose, passed, total,
              elapsed_s, ts):
    """详细形态：抬头、逐条判定（可按需附输出）与结论。"""
    print("=" * 72)
    print("AirymaxOS OS-CHK 门禁报告")
    print(f"注册表版本: {registry_version}")
    print("=" * 72)
    print()
    _dump_full(results, verbose, ts)
    print()
    _rep_tail(failed, total, elapsed_s)


def _rep_tail(failed, total, elapsed_s):
    """结论横幅：全通过与非全通过两种措辞。"""
    print("-" * 72)
    if not failed:
        print(f"RESULT: PASS — 全部 {total} 条门禁通过（{elapsed_s:.1f}s）")
    else:
        print(f"RESULT: FAIL — {len(failed)}/{total} 条门禁未通过"
              f"（{elapsed_s:.1f}s）")
    print("-" * 72)


def _passed_n(results: Sequence[GateResult]) -> int:
    """通过的门禁数。"""
    return sum(r.status == STATUS_PASS for r in results)


def _gate_row(r: GateResult) -> dict:
    """单个门禁结果的 JSON 行。"""
    return {
        "id": r.gate.id,
        "name": r.gate.name,
        "severity": r.gate.severity,
        "category": r.gate.category,
        "status": r.status,
        "returncode": r.returncode,
        "duration_s": round(r.duration_s, 3),
        "detail": r.detail,
    }


def write_json(path: Path, registry_version: str, timestamp: str,
                      fail_on: str, results: Sequence[GateResult]) -> None:
    passed = _passed_n(results)
    payload = {
        "registry_version": registry_version,
        "timestamp": timestamp,
        "fail_on": fail_on,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
        "results": [_gate_row(r) for r in results],
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


def _parse_args(argv: Optional[Sequence[str]]):
    """Usage 级校验在这里一次做完：非正数超时不会留到执行阶段。"""
    args = _build_parser().parse_args(argv)
    if args.timeout is not None and args.timeout <= 0:
        print("ERROR: --timeout 必须是正整数", file=sys.stderr)
        return None
    return args


def _repo_root(args) -> Optional[Path]:
    root = (Path(args.repo_root).resolve() if args.repo_root
            else find_repo_root())
    if not root.is_dir():
        print(f"ERROR: 仓库根不存在：{root}", file=sys.stderr)
        return None
    return root


def _load_registry(root: Path, args) -> Optional[Tuple[str, List[Gate]]]:
    """注册表缺失与 schema 非法都归为用法错误，让 CI 一眼看出配置面问题。"""
    path = (Path(args.registry) if args.registry
            else root / DEFAULT_REGISTRY_REL)
    if not path.is_file():
        print(f"ERROR: 门禁注册表不存在：{path}", file=sys.stderr)
        return None
    try:
        return parse_registry(path.read_text(encoding="utf-8"))
    except RegistryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return None


def _unknown_ids(gates: List[Gate], chosen: set) -> List[str]:
    """请求里未登记的门禁 id，按字典序。"""
    return sorted(chosen - {g.id for g in gates})


def _chosen(gates: List[Gate], chosen: set) -> List[Gate]:
    """按注册顺序保留被选中的门禁。"""
    return [g for g in gates if g.id in chosen]


def _pick(gates: List[Gate],
          wanted: Optional[List[str]]) -> Optional[List[Gate]]:
    """--gate 只做减法：未登记的 id 是用法错误，不静默跳过后当作已跑。"""
    if not wanted:
        return gates
    chosen = set(wanted)
    unknown = _unknown_ids(gates, chosen)
    if unknown:
        print(f"ERROR: 未知门禁 id：{', '.join(unknown)}", file=sys.stderr)
        return None
    return _chosen(gates, chosen)


def _list_gates(gates: List[Gate]) -> int:
    for gate in gates:
        print(f"{gate.severity}  {gate.id:22s} {gate.name}")
    return EXIT_PASS


def _dump_json(args, version: str, ts: str,
               results: Sequence[GateResult]) -> int:
    """JSON 报告是产线上的机读凭证：写失败必须让整轮门禁判为用法错误。"""
    if not args.json:
        return EXIT_PASS
    try:
        write_json(Path(args.json), version, ts, args.fail_on, results)
    except OSError as exc:
        print(f"ERROR: 无法写入 JSON 报告：{exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_PASS


def _setup(args) -> Optional[tuple]:
    """解析仓库根、注册表与 gate 选择；任一环节失败返回 None。"""
    root = _repo_root(args)
    if root is None:
        return None
    loaded = _load_registry(root, args)
    if loaded is None:
        return None
    version, gates = loaded
    gates = _pick(gates, args.gate)
    if gates is None:
        return None
    return root, version, gates


def _exec(gates: List[Gate], root: Path, args, version: str) -> int:
    """跑完全部 gate、打印报告并给出退出码。"""
    started = time.monotonic()
    results = [run_gate(gate, root, args.timeout) for gate in gates]
    elapsed = time.monotonic() - started
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print_report(results, version, elapsed, args.check, args.verbose, ts,
                 args.fail_on)
    code = _dump_json(args, version, ts, results)
    if code != EXIT_PASS:
        return code
    return EXIT_BLOCKED if evaluate(results, args.fail_on) else EXIT_PASS


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口：见文件头的两种用法说明。"""
    args = _parse_args(argv)
    if args is None:
        return EXIT_USAGE

    setup = _setup(args)
    if setup is None:
        return EXIT_USAGE
    root, version, gates = setup
    if args.list:
        return _list_gates(gates)
    return _exec(gates, root, args, version)


if __name__ == "__main__":
    sys.exit(main())
