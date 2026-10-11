#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""bitfield_gen — AirymaxOS 位域契约代码生成器。

解析 .bf 位域契约源（R-02 SSoT），生成 [SC] 位域访问层头文件：
  - C: kernel/include/uapi/linux/airymax/structures_64_gen.h
  - C: kernel/include/uapi/linux/airymax/structures_32_gen.h

每个 block 生成 layout 宏（_BITS/_OFFSET/_MASK）、getter/setter 访问器，
以及 struct 容器的 sizeof/offsetof 编译期断言（word 容器生成字宽断言）。
生成头不重复定义 struct —— 容器实体来自 [SC] 头（ipc.h / sched.h）或
原生整型字，见 06-codegen-pipeline.md §3。

生成前强制 structures_32.bf 与 structures_64.bf 的 layout_signature 一致。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateError

from codegen_types import (
    BitfieldBlockSpec,
    BitfieldContainer,
    BitfieldContract,
    BitfieldFieldKind,
    BitfieldFieldSpec,
    CodegenError,
    ContractSourceError,
    ContractValidationError,
    ExitCode,
    TemplateRenderError,
)

GENERATOR = "bitfield_gen.py"
SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR / "templates"

# ── 契约源与生成目标（相对 agent-linux/ 仓库根） ──
_BF_DIR = Path("kernel") / "include" / "uapi" / "linux" / "airymax"
BF64_REL = _BF_DIR / "structures_64.bf"
BF32_REL = _BF_DIR / "structures_32.bf"
HDR64_REL = _BF_DIR / "structures_64_gen.h"
HDR32_REL = _BF_DIR / "structures_32_gen.h"
TEMPLATE_NAME = "structures_gen.h.j2"

# .bf DSL 语法（06-codegen-pipeline.md §3.2）
_BLOCK_OPEN_RE = re.compile(r"^block\s+(\w+)\s*\(\s*(\d+)\s*\)\s*\{\s*$")
_BIND_STRUCT_RE = re.compile(r"^bind\s+struct\s+(\w+)\s+from\s+(\S+)$")
_BIND_WORD_RE = re.compile(r"^bind\s+word\s+(\w+)$")
_FIELD_RE = re.compile(r"^(field_high|field|mask)\s+(\w+)\s+(\d+)\s*\.\.\s*(\d+)$")
_PADDING_RE = re.compile(r"^padding(?:\s+(\w+))?\s+(\d+)\s*\.\.\s*(\d+)$")
_PIVOT_RE = re.compile(r"^pivot\s+(\w+)$")

_KIND_BY_KEYWORD: dict[str, BitfieldFieldKind] = {
    "field": BitfieldFieldKind.FIELD,
    "field_high": BitfieldFieldKind.FIELD_HIGH,
    "mask": BitfieldFieldKind.MASK,
}


def default_repo_root() -> Path:
    """agent-linux/ 仓库根（tools/codegen/ 上溯两级）。"""
    return Path(__file__).resolve().parents[2]


def make_env() -> Environment:
    """构造确定性 Jinja2 渲染环境（§8）。"""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )


def _require_file(path: Path, what: str) -> None:
    if not path.is_file():
        raise ContractSourceError(f"{what} not found: {path}")


def _read_text(path: Path, what: str) -> str:
    _require_file(path, what)
    return path.read_text(encoding="utf-8")


def _strip_comment(raw: str) -> str:
    """剥离 `--` 至行尾的注释并去除首尾空白。"""
    return raw.split("--", 1)[0].strip()


def _meaningful_lines(text: str) -> list[tuple[int, str]]:
    """返回 (行号, 有效行) 序列，跳过空行与纯注释行。"""
    lines: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if line:
            lines.append((lineno, line))
    return lines


def _parse_block_header(line: str) -> tuple[str, int]:
    match = _BLOCK_OPEN_RE.match(line)
    assert match is not None  # 由调用方 _consume 先行判定
    return match.group(1), int(match.group(2))


def _make_field(
    name: str, high: str, low: str, kind: BitfieldFieldKind, path: Path, lineno: int
) -> BitfieldFieldSpec:
    try:
        return BitfieldFieldSpec(name=name, high=int(high), low=int(low), kind=kind)
    except ContractValidationError as exc:
        raise ContractValidationError(f"{path}:{lineno}: {exc.message}") from exc


class _BlockAssembler:
    """累积单个 block 的解析状态，闭合时构造 BitfieldBlockSpec。"""

    def __init__(self, name: str, width: int) -> None:
        self.name = name
        self.width = width
        self.container: BitfieldContainer | None = None
        self.ctype = ""
        self.header = ""
        self.pivot = ""
        self.fields: list[BitfieldFieldSpec] = []

    def set_struct(self, ctype: str, header: str) -> None:
        self.container = BitfieldContainer.STRUCT
        self.ctype = ctype
        self.header = header

    def set_word(self, ctype: str) -> None:
        self.container = BitfieldContainer.WORD
        self.ctype = ctype

    def add_field(self, spec: BitfieldFieldSpec) -> None:
        self.fields.append(spec)

    def set_pivot(self, name: str) -> None:
        self.pivot = name

    def build(self, path: Path, lineno: int) -> BitfieldBlockSpec:
        if self.container is None:
            raise ContractValidationError(f"{path}:{lineno}: block {self.name} has no bind")
        try:
            return BitfieldBlockSpec(
                name=self.name,
                width=self.width,
                container=self.container,
                ctype=self.ctype,
                fields=tuple(self.fields),
                header=self.header,
                pivot=self.pivot,
            )
        except ContractValidationError as exc:
            raise ContractValidationError(f"{path}:{lineno}: {exc.message}") from exc


def _apply_bind(asm: _BlockAssembler, line: str) -> bool:
    struct_match = _BIND_STRUCT_RE.match(line)
    if struct_match:
        asm.set_struct(struct_match.group(1), struct_match.group(2))
        return True
    word_match = _BIND_WORD_RE.match(line)
    if word_match:
        asm.set_word(word_match.group(1))
        return True
    return False


def _apply_field(asm: _BlockAssembler, line: str, path: Path, lineno: int) -> bool:
    field_match = _FIELD_RE.match(line)
    if field_match:
        kind = _KIND_BY_KEYWORD[field_match.group(1)]
        asm.add_field(
            _make_field(
                field_match.group(2),
                field_match.group(3),
                field_match.group(4),
                kind,
                path,
                lineno,
            )
        )
        return True
    pad_match = _PADDING_RE.match(line)
    if pad_match:
        asm.add_field(
            _make_field(
                pad_match.group(1) or "",
                pad_match.group(2),
                pad_match.group(3),
                BitfieldFieldKind.PADDING,
                path,
                lineno,
            )
        )
        return True
    pivot_match = _PIVOT_RE.match(line)
    if pivot_match:
        asm.set_pivot(pivot_match.group(1))
        return True
    return False


def _apply_directive(asm: _BlockAssembler, line: str, path: Path, lineno: int) -> None:
    if _apply_bind(asm, line):
        return
    if _apply_field(asm, line, path, lineno):
        return
    raise ContractValidationError(f"{path}:{lineno}: unrecognized directive: {line}")


class _BlockParser:
    """逐块状态机：从有效行序列构造 BitfieldBlockSpec 序列。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._blocks: list[BitfieldBlockSpec] = []
        self._asm: _BlockAssembler | None = None

    def run(self, lines: list[tuple[int, str]]) -> tuple[BitfieldBlockSpec, ...]:
        for lineno, line in lines:
            self._consume(line, lineno)
        self._require_no_open()
        return tuple(self._blocks)

    def _consume(self, line: str, lineno: int) -> None:
        if line == "}":
            self._close(lineno)
        elif _BLOCK_OPEN_RE.match(line):
            self._open(line, lineno)
        else:
            _apply_directive(self._require_asm(lineno), line, self._path, lineno)

    def _open(self, line: str, lineno: int) -> None:
        if self._asm is not None:
            raise ContractValidationError(f"{self._path}:{lineno}: nested block not allowed")
        name, width = _parse_block_header(line)
        self._asm = _BlockAssembler(name, width)

    def _close(self, lineno: int) -> None:
        asm = self._require_asm(lineno)
        self._blocks.append(asm.build(self._path, lineno))
        self._asm = None

    def _require_asm(self, lineno: int) -> _BlockAssembler:
        if self._asm is None:
            raise ContractValidationError(f"{self._path}:{lineno}: directive outside a block")
        return self._asm

    def _require_no_open(self) -> None:
        if self._asm is not None:
            raise ContractValidationError(f"{self._path}: unexpected EOF inside block {self._asm.name}")


def parse_bf_contract(bf_path: Path) -> BitfieldContract:
    """解析 .bf 契约源为 BitfieldContract（含逐块语义校验）。"""
    text = _read_text(bf_path, "bitfield contract source")
    blocks = _BlockParser(bf_path).run(_meaningful_lines(text))
    if not blocks:
        raise ContractSourceError(f"no blocks parsed from {bf_path}")
    return BitfieldContract(blocks=blocks)


def _assert_layout_consistent(contract32: BitfieldContract, contract64: BitfieldContract) -> None:
    if contract32.layout_signature() != contract64.layout_signature():
        raise ContractValidationError(
            "structures_32.bf and structures_64.bf layouts are not bit-for-bit identical"
        )


def load_contracts(repo_root: Path) -> tuple[BitfieldContract, BitfieldContract]:
    """解析 32/64 位契约源并强制跨架构布局一致。"""
    contract64 = parse_bf_contract(repo_root / BF64_REL)
    contract32 = parse_bf_contract(repo_root / BF32_REL)
    _assert_layout_consistent(contract32, contract64)
    return contract32, contract64


def _guard_for(header_rel: Path) -> str:
    return "_UAPI_AIRYMAX_" + header_rel.stem.upper() + "_H"


def _build_context(contract: BitfieldContract, bf_rel: Path, header_rel: Path) -> dict[str, object]:
    return {
        "generator": contract.generator,
        "generator_version": contract.generator_version,
        "source_name": bf_rel.name,
        "source_path": bf_rel.as_posix(),
        "guard": _guard_for(header_rel),
        "includes": contract.includes,
        "blocks": contract.blocks,
    }


def _render(env: Environment, context: dict[str, object]) -> str:
    try:
        return env.get_template(TEMPLATE_NAME).render(**context)
    except TemplateError as exc:
        raise TemplateRenderError(f"{TEMPLATE_NAME}: {exc}") from exc


def render_outputs(repo_root: Path) -> dict[Path, str]:
    """解析契约源并渲染全部产物，返回 {目标路径: 内容}。"""
    contract32, contract64 = load_contracts(repo_root)
    env = make_env()
    plan = (
        (contract64, BF64_REL, HDR64_REL),
        (contract32, BF32_REL, HDR32_REL),
    )
    return {
        repo_root / header_rel: _render(env, _build_context(contract, bf_rel, header_rel))
        for contract, bf_rel, header_rel in plan
    }


def _unchanged(out_path: Path, content: str) -> bool:
    return out_path.is_file() and out_path.read_text(encoding="utf-8") == content


def _write_if_changed(out_path: Path, content: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _unchanged(out_path, content):
        return
    out_path.write_text(content, encoding="utf-8")


def write_outputs(rendered: dict[Path, str]) -> None:
    """将渲染结果写入磁盘（仅在内容变化时重写，保持幂等）。"""
    for out_path, content in rendered.items():
        _write_if_changed(out_path, content)


def _fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return int(ExitCode.FAILURE)


def generate(repo_root: Path) -> int:
    """生成全部位域访问层产物（run_codegen 统一入口）。

    Returns:
        ExitCode.SUCCESS / ExitCode.FAILURE。
    """
    try:
        rendered = render_outputs(repo_root)
        write_outputs(rendered)
    except CodegenError as exc:
        return _fail(exc.message)

    for out_path in rendered:
        print(f"Generated: {out_path}")
    return int(ExitCode.SUCCESS)


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


def _has_drift(rendered: dict[Path, str]) -> bool:
    return any(not _check_one(out_path, content) for out_path, content in rendered.items())


def check(repo_root: Path) -> int:
    """校验生成内容与已提交文件一致（CI 零 diff，§9.3）。

    Returns:
        ExitCode.SUCCESS / ExitCode.FAILURE。
    """
    try:
        rendered = render_outputs(repo_root)
    except CodegenError as exc:
        return _fail(exc.message)
    if _has_drift(rendered):
        print("       Run 'make bitfield' to regenerate and commit the result.", file=sys.stderr)
        return int(ExitCode.FAILURE)
    print(f"OK: bitfield_gen outputs match committed files ({len(rendered)} files)")
    return int(ExitCode.SUCCESS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="structures_{32,64}.bf → C structures_{32,64}_gen.h access layer",
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
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if args.check:
        return check(repo_root)
    return generate(repo_root)


if __name__ == "__main__":
    sys.exit(main())
