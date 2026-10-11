#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""cap_gen — AirymaxOS capability ID 契约代码生成器。

解析 security/include/airy_cap_ids.def 契约源（Capability ID SSoT），生成：
  - C:      kernel/include/uapi/linux/airymax/security_types.h
  - Rust:   sdk/rust/airyx/cap_ids.rs
  - Python: sdk/python/airyx/cap_ids.py

设计依据: docs-linux/30-interfaces/06-codegen-pipeline.md §5。
生成前执行 §5.4 跨契约引用校验：syscall.xml 的 <capability_required> 引用的
capability 符号必须存在于本契约源。

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
import xml.etree.ElementTree as ET
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateError

from codegen_types import (
    CAP_ID_COUNT,
    CAP_ID_MAX_SYMBOL,
    CAP_ID_PREFIX,
    CapContract,
    CapIdSpec,
    CodegenError,
    ContractSourceError,
    ContractValidationError,
    ExitCode,
    TemplateRenderError,
)

GENERATOR = "cap_gen.py"
SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR / "templates"

# ── 契约源与生成目标（相对 agent-linux/ 仓库根） ──
CAP_DEF_REL = Path("security") / "include" / "airy_cap_ids.def"
SYSCALL_XML_REL = Path("kernel") / "include" / "uapi" / "linux" / "airymax" / "syscall.xml"
C_HEADER_REL = Path("kernel") / "include" / "uapi" / "linux" / "airymax" / "security_types.h"
RUST_REL = Path("sdk") / "rust" / "airyx" / "cap_ids.rs"
PYTHON_REL = Path("sdk") / "python" / "airyx" / "cap_ids.py"

# (模板文件, 生成目标相对路径)
OUTPUTS: tuple[tuple[str, Path], ...] = (
    ("security_types.h.j2", C_HEADER_REL),
    ("cap_ids.rs.j2", RUST_REL),
    ("cap_ids.py.j2", PYTHON_REL),
)

# X-macro 行: AIRY_CAP_ID(symbol, value, "description")
_CAP_ID_RE = re.compile(
    r'^\s*AIRY_CAP_ID\(\s*(\w+)\s*,\s*(\d+)\s*,\s*"([^"]*)"\s*\)\s*$'
)


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


def _read_cap_def_lines(def_path: Path) -> list[str]:
    _require_file(def_path, "capability contract source")
    return def_path.read_text(encoding="utf-8").splitlines()


def _parse_cap_line(raw: str) -> tuple[str, int, str] | None:
    match = _CAP_ID_RE.match(raw)
    if match is None:
        return None
    return match.group(1), int(match.group(2)), match.group(3)


def _prefix_symbol(raw_symbol: str) -> str:
    if raw_symbol.startswith(CAP_ID_PREFIX):
        return raw_symbol
    return CAP_ID_PREFIX + raw_symbol


def _register_symbol(symbol: str, lineno: int, seen: dict[str, int], def_path: Path) -> None:
    if symbol in seen:
        raise ContractValidationError(
            f"{def_path}:{lineno}: duplicate capability symbol {symbol} "
            f"(first seen at line {seen[symbol]})"
        )
    seen[symbol] = lineno


def _make_cap_spec(
    parsed: tuple[str, int, str], def_path: Path, lineno: int, seen: dict[str, int]
) -> CapIdSpec:
    raw_symbol, value, description = parsed
    symbol = _prefix_symbol(raw_symbol)
    _register_symbol(symbol, lineno, seen, def_path)
    try:
        return CapIdSpec(
            symbol=symbol, value=value, description=description, raw_symbol=raw_symbol
        )
    except ContractValidationError as exc:
        raise ContractValidationError(f"{def_path}:{lineno}: {exc.message}") from exc


def parse_cap_ids_def(def_path: Path) -> CapContract:
    """解析 airy_cap_ids.def 契约源（D-9 前缀转换 + 语义校验）。

    POSIX 裸名（如 CAP_CHOWN）补 AIRY_ 前缀；已带 AIRY_ 前缀者原样保留。
    """
    specs: list[CapIdSpec] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(_read_cap_def_lines(def_path), 1):
        parsed = _parse_cap_line(raw)
        if parsed is None:
            continue
        specs.append(_make_cap_spec(parsed, def_path, lineno, seen))
    if not specs:
        raise ContractSourceError(f"no AIRY_CAP_ID entries parsed from {def_path}")
    return CapContract(cap_ids=tuple(specs))


def _load_xml_root(path: Path):
    _require_file(path, "syscall contract source")
    try:
        return ET.parse(str(path)).getroot()
    except ET.ParseError as exc:
        raise ContractSourceError(f"{path}: XML parse error: {exc}") from exc


def _known_symbols(contract: CapContract) -> set[str]:
    names = set(contract.symbol_names)
    names.update(c.raw_symbol for c in contract.cap_ids)
    return names


def _capability_required(sc_elem) -> str:
    return (sc_elem.findtext("capability_required") or "").strip()


def _is_unconstrained(cap_req: str) -> bool:
    return not cap_req or cap_req == "0"


def _undefined_cap_message(sc_elem, cap_req: str, syscall_xml: Path) -> str:
    sc_name = (sc_elem.findtext("name") or "<unnamed>").strip()
    return f"{syscall_xml}: syscall {sc_name} references undefined capability {cap_req}"


def _validate_syscall_cap(sc_elem, known: set[str], syscall_xml: Path) -> None:
    cap_req = _capability_required(sc_elem)
    if _is_unconstrained(cap_req):
        return
    if cap_req not in known:
        raise ContractValidationError(_undefined_cap_message(sc_elem, cap_req, syscall_xml))


def validate_capability_references(contract: CapContract, syscall_xml: Path) -> None:
    """§5.4 跨契约校验：syscall.xml 引用符号必须存在于能力契约源。"""
    root = _load_xml_root(syscall_xml)
    known = _known_symbols(contract)
    for sc_elem in root.findall("syscall"):
        _validate_syscall_cap(sc_elem, known, syscall_xml)


def build_context(contract: CapContract) -> dict[str, object]:
    """构造模板渲染上下文。"""
    return {
        "generator": contract.generator,
        "generator_version": contract.generator_version,
        "source_name": CAP_DEF_REL.name,
        "source_path": CAP_DEF_REL.as_posix(),
        "cap_id_count": CAP_ID_COUNT,
        "cap_id_max_symbol": CAP_ID_MAX_SYMBOL,
        "symbol_width": contract.symbol_width,
        "cap_ids": contract.cap_ids,
        "posix_caps": contract.posix_caps,
        "extension_caps": contract.extension_caps,
    }


def _render(env: Environment, template_name: str, context: dict[str, object]) -> str:
    try:
        return env.get_template(template_name).render(**context)
    except TemplateError as exc:
        raise TemplateRenderError(f"{template_name}: {exc}") from exc


def render_outputs(repo_root: Path) -> dict[Path, str]:
    """解析契约源并渲染全部产物，返回 {目标路径: 内容}。"""
    contract = parse_cap_ids_def(repo_root / CAP_DEF_REL)
    validate_capability_references(contract, repo_root / SYSCALL_XML_REL)
    env = make_env()
    context = build_context(contract)
    return {repo_root / rel: _render(env, name, context) for name, rel in OUTPUTS}


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
    """生成全部 capability 产物（run_codegen 统一入口）。

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
        print("       Run 'make cap' to regenerate and commit the result.", file=sys.stderr)
        return int(ExitCode.FAILURE)

    print(f"OK: cap_gen outputs match committed files ({len(rendered)} files)")
    return int(ExitCode.SUCCESS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="airy_cap_ids.def → C security_types.h + Rust/Python bindings",
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
