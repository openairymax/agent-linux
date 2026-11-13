# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""manifest_io — 契约检查工具的清单读取与 CLI 前置。

六个契约检查工具在 ``main()`` 开头做同一段前置：探测仓库根、确认
PyYAML 可用、确认仓库根存在（内核类工具还要确认内核源码目录存在）、
按 ``--manifest`` 或默认相对路径定位清单，再把「读 YAML → 顶层必须
是映射」的失败转成各自的清单异常并打印 ``ERROR: ...``。这段前置决定
了同一个坏清单在不同工具下的提示与退出码，和报告格式一样属于对外
契约；各写一份时，新增一个参数或调整一条提示只会改到其中一个工具。

本模块只收敛前置与清单读取，不含任何工具的业务判定。

权威依据：
  docs/docs-linux/50-engineering-standards/10-coding-style.md
    命名、模块头与 docstring 规范
  tools/README.md
    工具聚合清单；tools/lib 为跨工具共享层

Example:
    工具在登记完自身参数后交接给 ``open_gate``；失败原因已经打印过，
    调用方只需按自己的退出约定处理 ``None``：
        gate = open_gate(parser, argv, DEFAULT_MANIFEST_REL,
                         load_yaml, NightlyError)
        if gate is None:
            return EXIT_USAGE

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.0
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NoReturn, Sequence

# tools/lib 是契约检查工具的共享层：清单异常与报告函数复用同一份。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_report import CheckResult, ManifestError, print_report, yaml

__all__ = [
    "Gate",
    "find_repo_root",
    "load_manifest",
    "open_gate",
    "run_gate",
]

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

_KERNEL_DIR = "kernel"


@dataclass(frozen=True)
class Gate:
    """CLI 前置通过后的上下文。

    Attributes:
        args: 解析后的命令行参数。
        root: agent-linux 仓库根。
        manifest: 已加载的清单映射。
        kernel_dir: 内核源码目录；未要求校验内核树时为 ``None``。
    """

    args: argparse.Namespace
    root: Path
    manifest: dict[str, Any]
    kernel_dir: Path | None = None


def find_repo_root() -> Path:
    """从共享层位置自动探测 agent-linux 仓库根。

    本模块位于 ``<repo_root>/tools/lib/manifest_io.py``，与各工具的
    ``<repo_root>/tools/<tool>/<tool>.py`` 处在同一深度。

    Returns:
        仓库根目录的绝对路径。
    """
    return Path(__file__).resolve().parents[2]


def load_manifest(path: str | Path) -> dict[str, Any]:
    """读取并解析清单。

    Args:
        path: 清单文件路径。

    Returns:
        清单映射。

    Raises:
        ManifestError: 文件缺失、不可读、YAML 非法或顶层非映射。
    """
    if yaml is None:
        raise ManifestError(
            "PyYAML 不可用：请安装 pyyaml（见 requirements-dev.txt）")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ManifestError(f"清单文件不存在：{path}") from exc
    except OSError as exc:
        raise ManifestError(f"清单文件不可读：{path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"清单 YAML 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("清单顶层必须是映射（mapping）")
    return data


def _env_bad(root: Path, kernel: bool) -> str | None:
    """运行环境校验：仓库根与（可选）内核源码目录都必须存在。"""
    if not root.is_dir():
        return f"仓库根不存在: {root}"
    if kernel and not (root / _KERNEL_DIR).is_dir():
        return f"内核源码目录不存在: {root / _KERNEL_DIR}"
    return None


def open_gate(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None,
    default_manifest: str,
    loader: Callable[[Path], dict[str, Any]] = load_manifest,
    error: type[Exception] = ManifestError,
    *,
    kernel: bool = False,
) -> Gate | None:
    """解析 CLI、校验运行环境并加载清单。

    失败时按工具既有的 ``ERROR:`` 格式打印到 stderr 并返回 ``None``；
    是否把 ``None`` 转成 ``sys.exit`` 由调用方决定。

    Args:
        parser: 已登记完工具自身参数的解析器。
        argv: 命令行参数；``None`` 表示使用 ``sys.argv[1:]``。
        default_manifest: 清单默认路径（相对仓库根）。
        loader: 清单读取函数，失败时抛 ``error``。
        error: ``loader`` 抛出的清单异常类型。
        kernel: 为真时额外校验仓库根下的内核源码目录存在。

    Returns:
        ``Gate``；环境或清单不合法时为 ``None``。
    """
    args = parser.parse_args(argv)
    if yaml is None:
        print("ERROR: PyYAML 不可用，请安装 pyyaml", file=sys.stderr)
        return None
    root = Path(args.repo_root) if args.repo_root else find_repo_root()
    bad = _env_bad(root, kernel)
    if bad is not None:
        print(f"ERROR: {bad}", file=sys.stderr)
        return None
    kernel_dir = root / _KERNEL_DIR
    path = Path(args.manifest) if args.manifest else root / default_manifest
    try:
        manifest = loader(path)
    except error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return None
    return Gate(args, root, manifest, kernel_dir if kernel else None)


def run_gate(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None,
    default_manifest: str,
    build: Callable[[Gate], Sequence[CheckResult]],
    tag: str,
    title: str,
    *,
    kernel: bool = False,
    tail: Callable[[Gate], str] | None = None,
) -> NoReturn:
    """执行一次完整检查并以对应退出码结束进程。

    Args:
        parser: 已登记完工具自身参数的解析器。
        argv: 命令行参数；``None`` 表示使用 ``sys.argv[1:]``。
        default_manifest: 清单默认路径（相对仓库根）。
        build: 由 ``Gate`` 生成检查结果序列。
        tag: 汇总行的工具标识。
        title: 完整报告标题。
        kernel: 为真时额外校验内核源码目录存在。
        tail: 追加在版本行末尾的补充信息。

    Raises:
        SystemExit: 以 ``EXIT_PASS`` / ``EXIT_FAIL`` / ``EXIT_USAGE`` 退出。
    """
    gate = open_gate(parser, argv, default_manifest, kernel=kernel)
    if gate is None:
        sys.exit(EXIT_USAGE)
    results = list(build(gate))
    print_report(results, tag, gate.args.check, title,
                 str(gate.manifest.get("version", "?")),
                 tail(gate) if tail else "")
    failed = any(not item.passed for item in results)
    sys.exit(EXIT_FAIL if failed else EXIT_PASS)
