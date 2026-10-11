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

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import codegen_io

from codegen_types import (
    CAP_ID_COUNT,
    CAP_ID_MAX_SYMBOL,
    CAP_ID_PREFIX,
    CapContract,
    CapIdSpec,
    ContractSourceError,
    ContractValidationError,
)

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


def _read_cap_def_lines(def_path: Path) -> list[str]:
    return codegen_io.read_text(def_path, "capability contract source").splitlines()


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
    codegen_io.require_file(path, "syscall contract source")
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


def render_outputs(repo_root: Path) -> dict[Path, str]:
    """解析契约源并渲染全部产物，返回 {目标路径: 内容}。"""
    contract = parse_cap_ids_def(repo_root / CAP_DEF_REL)
    validate_capability_references(contract, repo_root / SYSCALL_XML_REL)
    env = codegen_io.make_env()
    context = build_context(contract)
    return {repo_root / rel: codegen_io.render(env, name, context) for name, rel in OUTPUTS}


def generate(repo_root: Path) -> int:
    """生成全部 capability 产物（run_codegen 统一入口）。"""
    return codegen_io.generate(render_outputs, repo_root)


def check(repo_root: Path) -> int:
    """校验生成内容与已提交文件一致（CI 零 diff，§9.3）。"""
    return codegen_io.check(render_outputs, repo_root, generator="cap_gen", make_target="cap")


def main(argv: list[str] | None = None) -> int:
    return codegen_io.main(
        render_outputs,
        description="airy_cap_ids.def → C security_types.h + Rust/Python bindings",
        generator="cap_gen",
        make_target="cap",
        argv=argv,
    )


if __name__ == "__main__":
    sys.exit(main())
