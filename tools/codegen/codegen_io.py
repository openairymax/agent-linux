#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""codegen_io — AirymaxOS 契约代码生成器共享 IO/CLI 脚手架。

为 tools/codegen 下的契约生成器（cap_gen / bitfield_gen / ipc_gen /
syscall_gen）提供统一的确定性渲染环境、产物写入、零漂移校验与命令行入口，
使各生成器只聚焦「解析契约源 → 构造上下文」，避免重复实现同一套脚手架
（FOUND-08 静态分析基线：重复率 < 15%）。

生成器以 render_outputs(repo_root) -> {目标路径: 内容} 作为唯一契约：
  - generate(render_outputs, repo_root)   生成并写盘（幂等）
  - check(render_outputs, repo_root, ...) 零漂移校验（§9.3）
  - main(render_outputs, ...)             argparse 统一入口

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import argparse
import difflib
import sys
from collections.abc import Callable
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateError

from codegen_types import CodegenError, ContractSourceError, ExitCode, TemplateRenderError

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

RenderOutputs = Callable[[Path], "dict[Path, str]"]
Rendered = "dict[Path, str]"


def default_repo_root() -> Path:
    """agent-linux/ 仓库根（tools/codegen/ 上溯两级）。"""
    return Path(__file__).resolve().parents[2]


def make_env(template_dir: Path | None = None) -> Environment:
    """构造确定性 Jinja2 渲染环境（06-codegen-pipeline.md §8）。

    template_dir 缺省为 tools/codegen/templates；同级的其它工具目录（如
    tools/dsl）可传入自己的模板目录，复用同一套确定性渲染设置。
    """
    loader_root = TEMPLATE_DIR if template_dir is None else template_dir
    return Environment(
        loader=FileSystemLoader(str(loader_root)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )


def require_file(path: Path, what: str) -> None:
    """断言契约源存在，缺失时抛 ContractSourceError。"""
    if not path.is_file():
        raise ContractSourceError(f"{what} not found: {path}")


def read_text(path: Path, what: str) -> str:
    """读取契约源文本（先做存在性断言）。"""
    require_file(path, what)
    return path.read_text(encoding="utf-8")


def render(env: Environment, template_name: str, context: dict[str, object]) -> str:
    """渲染单个模板，失败时包装为 TemplateRenderError。"""
    try:
        return env.get_template(template_name).render(**context)
    except TemplateError as exc:
        raise TemplateRenderError(f"{template_name}: {exc}") from exc


def _write_if_changed(out_path: Path, content: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.is_file() and out_path.read_text(encoding="utf-8") == content:
        return
    out_path.write_text(content, encoding="utf-8")


def write_outputs(rendered: dict[Path, str]) -> None:
    """将渲染结果写入磁盘（仅在内容变化时重写，保持幂等）。"""
    for out_path, content in rendered.items():
        _write_if_changed(out_path, content)


def fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return int(ExitCode.FAILURE)


def _diff_text(committed: str, content: str, out_path: Path):
    return difflib.unified_diff(
        committed.splitlines(keepends=True),
        content.splitlines(keepends=True),
        fromfile=f"{out_path} (committed)",
        tofile=f"{out_path} (generated)",
    )


def _check_one(out_path: Path, content: str) -> bool:
    if not out_path.is_file():
        print(f"ERROR: generated file missing: {out_path}", file=sys.stderr)
        return False
    committed = out_path.read_text(encoding="utf-8")
    if committed == content:
        return True
    print(f"ERROR: generated content differs from committed file: {out_path}", file=sys.stderr)
    sys.stderr.writelines(_diff_text(committed, content, out_path))
    return False


def has_drift(rendered: dict[Path, str]) -> bool:
    return any(not _check_one(out_path, content) for out_path, content in rendered.items())


def generate(render_outputs: RenderOutputs, repo_root: Path) -> int:
    """渲染并写盘全部产物（run_codegen 统一入口）。

    Returns:
        ExitCode.SUCCESS / ExitCode.FAILURE。
    """
    try:
        rendered = render_outputs(repo_root)
    except CodegenError as exc:
        return fail(exc.message)
    write_outputs(rendered)
    for out_path in rendered:
        print(f"Generated: {out_path}")
    return int(ExitCode.SUCCESS)


def check(
    render_outputs: RenderOutputs,
    repo_root: Path,
    *,
    generator: str,
    make_target: str,
) -> int:
    """校验生成内容与已提交文件一致（CI 零 diff，§9.3）。

    Returns:
        ExitCode.SUCCESS / ExitCode.FAILURE。
    """
    try:
        rendered = render_outputs(repo_root)
    except CodegenError as exc:
        return fail(exc.message)
    if has_drift(rendered):
        print(
            f"       Run 'make {make_target}' to regenerate and commit the result.",
            file=sys.stderr,
        )
        return int(ExitCode.FAILURE)
    print(f"OK: {generator} outputs match committed files ({len(rendered)} files)")
    return int(ExitCode.SUCCESS)


def build_parser(description: str) -> argparse.ArgumentParser:
    """构造统一 CLI 解析器（--repo-root / --check）。"""
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  %(prog)s                 # 生成全部产物（默认仓库根）
  %(prog)s --check         # CI 校验：生成内容与已提交文件零 diff
  %(prog)s --repo-root DIR # 指定 agent-linux 仓库根
""",
    )
    parser.add_argument(
        "--repo-root",
        default=str(default_repo_root()),
        help="agent-linux 仓库根目录（默认: %(default)s）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="校验模式：对比生成内容与已提交文件，不一致返回非零退出码",
    )
    return parser


def main(
    render_outputs: RenderOutputs,
    *,
    description: str,
    generator: str,
    make_target: str,
    argv: list[str] | None = None,
) -> int:
    """统一命令行入口：默认生成，--check 走零漂移校验。"""
    args = build_parser(description).parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    if args.check:
        return check(render_outputs, repo_root, generator=generator, make_target=make_target)
    return generate(render_outputs, repo_root)
