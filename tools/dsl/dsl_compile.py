#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""dsl_compile — AirymaxOS 策略 DSL（.adl）编译编排与零漂移 CLI。

把 .adl 契约源编译为纯 C 决策表头：读源 → dsl_parser 解析 → dsl_semantics
语义校验降 IR → dsl_codegen 渲染。全部产物登记在 CONTRACTS，由 codegen_io
的统一入口生成或做 --check 零漂移校验（docs-linux/30-interfaces/
06-codegen-pipeline.md §9.3）。

用法:
    python tools/dsl/dsl_compile.py            # 生成全部产物
    python tools/dsl/dsl_compile.py --check    # CI：生成内容与已提交文件零 diff
    python tools/dsl/dsl_compile.py --repo-root DIR

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

# 1. 标准库导入
from __future__ import annotations

import sys
from pathlib import Path

_DSL_DIR = Path(__file__).resolve().parent
_CODEGEN_DIR = _DSL_DIR.parent / "codegen"
for _path in (_DSL_DIR, _CODEGEN_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import codegen_io  # noqa: E402  (bootstrap 后导入)
from codegen_types import ContractSourceError  # noqa: E402

from dsl_codegen import TEMPLATE_DIR, render_program  # noqa: E402
from dsl_parser import parse  # noqa: E402
from dsl_semantics import to_ir  # noqa: E402
from dsl_types import DslSourceError, ProgramIR  # noqa: E402

# 2. 公开导出
__all__ = ["CONTRACTS", "compile_text", "render_outputs", "generate", "check", "main"]

# ── 契约源 → 生成目标（相对 agent-linux/ 仓库根） ──
_EXAMPLE_DIR = Path("tools") / "dsl" / "examples"
CONTRACTS: tuple[tuple[Path, Path], ...] = (
    (_EXAMPLE_DIR / "security_policy.adl", _EXAMPLE_DIR / "security_policy_tables.h"),
)


def load_program_ir(source_path: Path, source_name: str) -> ProgramIR:
    """读取并编译单个 .adl 契约源为语义合法的 ProgramIR。"""
    try:
        source = codegen_io.read_text(source_path, "policy DSL contract source")
    except ContractSourceError as exc:
        raise DslSourceError(exc.message) from exc
    return to_ir(parse(source, source_name), source_name)


def compile_text(source: str, source_name: str = "<adl>") -> str:
    """把 .adl 源文本编译为 C 决策表头文本（供测试与工具复用）。"""
    return render_program(to_ir(parse(source, source_name), source_name))


def render_outputs(repo_root: Path) -> dict[Path, str]:
    """编译全部登记契约源，返回 {生成目标绝对路径: 内容}。"""
    env = codegen_io.make_env(TEMPLATE_DIR)
    rendered: dict[Path, str] = {}
    for source_rel, output_rel in CONTRACTS:
        source_name = source_rel.as_posix()
        program_ir = load_program_ir(repo_root / source_rel, source_name)
        rendered[repo_root / output_rel] = render_program(program_ir, env)
    return rendered


def generate(repo_root: Path) -> int:
    """生成全部策略决策表产物（run_codegen 统一入口）。"""
    return codegen_io.generate(render_outputs, repo_root)


def check(repo_root: Path) -> int:
    """校验生成内容与已提交文件一致（CI 零 diff，§9.3）。"""
    return codegen_io.check(render_outputs, repo_root, generator="dsl_compile", make_target="dsl")


def main(argv: list[str] | None = None) -> int:
    return codegen_io.main(
        render_outputs,
        description="Airymax Decision Language (.adl) → pure C policy decision tables",
        generator="dsl_compile",
        make_target="dsl",
        argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
