#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""syscall_gen — AirymaxOS syscall 契约代码生成器。

解析 syscall.xml 契约源（R-01 SSoT），生成：
  - C UAPI 头 kernel/include/uapi/linux/airymax/syscall_gen.h
  - syscall_64.tbl 的 Airymax 段片段（供内核入口表校验）
  - Rust/Python/Go/TypeScript SDK syscall 绑定（内部索引常量 + Linux 注册号
    常量 + 元数据表；Rust Python Go 另附可调用封装）

syscall_64.tbl 是含 450+ 行 Linux 既有表项的上游共享文件，无法整体生成，
故本生成器只生成 Airymax 段片段，并校验其在 tbl 中逐字节存在
（--check-tbl，§9.3 零漂移）。

契约源 signature 的参数类型取自固定词汇表（见 _TYPE_TABLE）；遇到词汇表外的
类型直接报错终止，绝不静默降级为桩。

契约源引用的 capability 符号由 cap_gen 的 §5.4 跨契约校验覆盖——管道依赖序
（cap_gen → bitfield_gen → ipc_gen → syscall_gen）保证其先于本生成器运行。

设计依据: docs-linux/30-interfaces/06-codegen-pipeline.md §2 / §8。

Author:
    SPHARX Ltd. - Airymax Team

Version:
    1.0.1
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import codegen_io

from codegen_types import (
    GENERATOR_VERSION,
    INDEX_PREFIX,
    SCHEMA_VERSION,
    SYMBOL_PREFIX,
    CodegenError,
    ContractSourceError,
    ContractValidationError,
    ExitCode,
    ReservedRange,
    SyscallContract,
    SyscallSpec,
)

GENERATOR = "syscall_gen.py"

# ── 契约源与生成目标（相对 agent-linux/ 仓库根） ──
_UAPI_DIR = Path("kernel") / "include" / "uapi" / "linux" / "airymax"
SYSCALL_XML_REL = _UAPI_DIR / "syscall.xml"
HEADER_REL = _UAPI_DIR / "syscall_gen.h"
TBL_REL = Path("kernel") / "arch" / "x86" / "entry" / "syscalls" / "syscall_64.tbl"

# ── 多语言 SDK 绑定目标（相对 agent-linux/ 仓库根） ──
SDK_RUST_REL = Path("sdk") / "rust" / "airyx" / "syscalls.rs"
SDK_PY_REL = Path("sdk") / "python" / "airyx" / "syscalls.py"
SDK_GO_REL = Path("sdk") / "go" / "airyx" / "syscalls.go"
SDK_TS_REL = Path("sdk") / "ts" / "airyx" / "syscalls.ts"

HEADER_TEMPLATE = "syscall_gen.h.j2"
TBL_TEMPLATE = "syscall_64.tbl.j2"
RUST_TEMPLATE = "syscall.rs.j2"
PY_TEMPLATE = "syscall.py.j2"
GO_TEMPLATE = "syscall.go.j2"
TS_TEMPLATE = "syscall.ts.j2"

GUARD = "AIRY_SYSCALL_GEN_H"
SOURCE_NAME = SYSCALL_XML_REL.name
# 产物注释中的契约源路径为内核树内相对路径（kernel/ 子模块视角）
SOURCE_PATH = SYSCALL_XML_REL.relative_to("kernel").as_posix()

# ── 头文件宏名（与 [SC] syscalls.h 的实际 syscall 号命名空间分离） ──
BASE_MACRO = "AIRY_SYS_BASE"
CORE_MACRO = "AIRY_SYS_NR_CORE"
RESERVED_COUNT_MACRO = "AIRY_SYS_NR_RESERVED"
TOTAL_MACRO = "AIRY_SYS_NR_TOTAL"
SLOT_MACRO = "AIRY_SYS_RESERVED"

# ── syscall_64.tbl Airymax 段布局常量 ──
TBL_ABI = "common"
TBL_RESERVED_NAME = "airy_reserved_"
TBL_RESERVED_ENTRY = "sys_ni_syscall"
# 段注释续行缩进（对齐 "# 552-571: ..." 的正文列）
TBL_CONT_INDENT = " " * 10

# ── 注释与原型换行宽度（Linux C 代码 80 列惯例） ──
DOC_WIDTH = 80
PROTO_WIDTH = 80
PROTO_CONT_INDENT = "\t\t\t\t"
RETURN_DOC = " * Return: 0 on success, negative errno on failure."

DESCRIPTION = (
    "syscall.xml → C UAPI syscall_gen.h + syscall_64.tbl Airymax section"
    " + Rust/Python/Go/TypeScript SDK bindings"
)

# ── 参数类型词汇表：C 契约类型 → (Rust, Python 注解, Python 实参构造, Go) ──
# Python 实参构造为 None 表示原样透传（指针类，由调用者提供 ctypes 指针对象）；
# 否则为 ctypes 构造表达式前缀，渲染时拼接为 "<ctor>(<param>)"。
_TYPE_TABLE: dict[str, tuple[str, str, str | None, str]] = {
    "airy_cap_t": ("u32", "int", "ctypes.c_uint32", "uint32"),
    "__u32": ("u32", "int", "ctypes.c_uint32", "uint32"),
    "__u64": ("u64", "int", "ctypes.c_uint64", "uint64"),
    "int": ("i32", "int", "ctypes.c_int", "int32"),
    "const char *": (
        "*const core::ffi::c_char",
        "bytes",
        None,
        "unsafe.Pointer",
    ),
    "const struct airy_ipc_msg_hdr *": (
        "*const core::ffi::c_void",
        "ctypes.c_void_p | None",
        None,
        "unsafe.Pointer",
    ),
}

# Go syscall.Syscall 的 3 寄存器实参 ABI：参数多于 3 个需手写 Syscall6 包装。
_GO_SYSCALL_ARG_REGS = 3
# 实参补齐用的空参数（占位为寄存器 0）。
_GO_EMPTY_ARG: dict[str, str] = {"go_arg": "0"}

# 跨语言名称常量（各语言惯例：Go 导出标识符 PascalCase）。
_NR_MACRO_PREFIX = "__NR_"
_GO_IDX_PREFIX = "AirySysIdx"
_GO_NR_PREFIX = "NrAirySys"
_GO_FUNC_PREFIX = "AirySys"


def _child_text(elem, tag: str) -> str:
    """取子元素文本（缺失或全空白归一为空串）。"""
    return (elem.findtext(tag) or "").strip()


def _child_int(elem, tag: str, default: int = 0) -> int:
    raw = _child_text(elem, tag)
    return int(raw) if raw else default


def _parse_syscall(elem) -> SyscallSpec:
    return SyscallSpec(
        name=_child_text(elem, "name"),
        number=_child_int(elem, "number"),
        signature=_child_text(elem, "signature"),
        flags=_child_text(elem, "flags"),
        category=_child_text(elem, "category"),
        since=_child_text(elem, "since"),
        stability=_child_text(elem, "stability"),
        doc=_child_text(elem, "doc"),
        capability_required=_child_text(elem, "capability_required"),
    )


def _parse_reserved(elem) -> ReservedRange:
    return ReservedRange(
        start=int(elem.get("from", "0")),
        end=int(elem.get("to", "0")),
        reason=elem.get("reason", "future extension"),
    )


def _parse_meta(root) -> dict[str, str]:
    meta_elem = root.find("meta")
    if meta_elem is None:
        return {}
    return {child.tag: (child.text or "").strip() for child in meta_elem}


def _contract_root(xml_path: Path):
    codegen_io.require_file(xml_path, "syscall contract source")
    try:
        return ET.parse(str(xml_path)).getroot()
    except ET.ParseError as exc:
        raise ContractSourceError(f"{xml_path}: XML parse error: {exc}") from exc


def _contract_from(root, xml_path: Path) -> SyscallContract:
    meta = _parse_meta(root)
    try:
        return SyscallContract(
            version=root.get("version", "0x0100"),
            linux_base=int(root.get("linux_base", "548")),
            core_count=int(root.get("core_count", "0")),
            reserved_count=int(root.get("reserved_count", "0")),
            total_slots=int(root.get("total_slots", "0")),
            syscalls=tuple(_parse_syscall(e) for e in root.findall("syscall")),
            reserved=tuple(_parse_reserved(e) for e in root.findall("reserved")),
            generator=meta.get("generator", GENERATOR),
            generator_version=meta.get("generator_version", GENERATOR_VERSION),
            schema_version=meta.get("schema_version", SCHEMA_VERSION),
        )
    except (TypeError, ValueError) as exc:
        raise ContractSourceError(f"{xml_path}: malformed syscall contract: {exc}") from exc


def parse_contract(xml_path: Path) -> SyscallContract:
    """解析 syscall.xml 契约源，返回类型化契约模型（含槽位自洽校验）。"""
    return _contract_from(_contract_root(xml_path), xml_path)


def _doc_split(doc_full: str, prefix: str) -> int | None:
    """在 80 列内寻找最靠后的空格作为 Doxygen 描述折行点。"""
    split_pos = DOC_WIDTH
    while split_pos > len(prefix) and doc_full[split_pos] != " ":
        split_pos -= 1
    return None if split_pos <= len(prefix) else split_pos


def _doc_lines(spec: SyscallSpec) -> list[str]:
    """Doxygen 描述行（超 80 列在末个可用空格处折行，续行前缀 " * "）。"""
    prefix = f" * {spec.name} - "
    doc_full = prefix + spec.doc
    if len(doc_full) <= DOC_WIDTH:
        return [doc_full]
    split_pos = _doc_split(doc_full, prefix)
    if split_pos is None:
        return [doc_full]
    return [doc_full[:split_pos], " * " + doc_full[split_pos + 1:]]


def _proto_lines(spec: SyscallSpec) -> list[str]:
    """函数原型行（超 80 列在末个参数逗号后折行，续行对齐 4 个制表位）。"""
    proto_line = f"AIRY_API int {spec.name}({spec.signature});"
    if len(proto_line) <= PROTO_WIDTH:
        return [proto_line]
    last_comma = spec.signature.rfind(",")
    if last_comma == -1:
        return [proto_line]
    head = f"AIRY_API int {spec.name}({spec.signature[:last_comma + 1]}"
    tail = f"{PROTO_CONT_INDENT}{spec.signature[last_comma + 2:]});"
    return [head, tail]


def _prototype_block(spec: SyscallSpec) -> list[str]:
    block = ["/**", *_doc_lines(spec), " *", RETURN_DOC, " *"]
    block.append(f" * @since {spec.since}")
    block.append(f" * @stability {spec.stability}")
    if spec.capability_required:
        block.append(f" * @capability {spec.capability_required}")
    block.append(" */")
    block.extend(_proto_lines(spec))
    block.append("")
    return block


def _prototype_lines(contract: SyscallContract) -> list[str]:
    return [line for spec in contract.syscalls for line in _prototype_block(spec)]


def _slot_macro_lines(contract: SyscallContract) -> list[str]:
    """基址与槽位计数宏（等宽列对齐，与已提交 syscall_gen.h 一致）。"""
    linux_end = contract.linux_base + contract.total_slots - 1
    return [
        f"#define {BASE_MACRO}\t\t{contract.linux_base}  /* agent-linux 专用编号起始 */",
        f"#define {CORE_MACRO}\t{contract.core_count}    /* 核心 syscall 数 */",
        f"#define {RESERVED_COUNT_MACRO}\t{contract.reserved_count}   /* 预留槽位数 */",
        f"#define {TOTAL_MACRO}\t{contract.total_slots}   "
        f"/* 总槽位数（{contract.linux_base}-{linux_end}） */",
    ]


def _index_lines(contract: SyscallContract) -> list[str]:
    """内部索引宏（AIRY_SYS_IDX_* = 内部编号，仅 codegen 内部使用）。"""
    return [
        f"#define {spec.index_macro}\t{spec.number}  /* {spec.name} */"
        for spec in contract.syscalls
    ]


def _nr_lines(contract: SyscallContract) -> list[str]:
    """Linux 注册号宏（__NR_airy_sys_* = 内部编号 + linux_base）。"""
    return [
        f"#define {spec.linux_nr_macro()}\t{spec.linux_nr(contract.linux_base)}"
        for spec in contract.syscalls
    ]


def _reserved_slot_lines(contract: SyscallContract) -> list[str]:
    """预留槽位编号宏（值为内部编号，注释给出 Linux 注册号）。"""
    return [
        f"#define {SLOT_MACRO}_{slot}\t{slot}  /* Linux {slot + contract.linux_base} */"
        for slot in _reserved_slots(contract)
    ]


def _tbl_syscall_lines(contract: SyscallContract) -> list[str]:
    """syscall_64.tbl 核心 syscall 行（nr / abi / name / entry）。"""
    return [
        f"{spec.linux_nr(contract.linux_base)}\t{TBL_ABI}\t{spec.name}\t\tsys_{spec.name}"
        for spec in contract.syscalls
    ]


def _tbl_reserved_lines(contract: SyscallContract) -> list[str]:
    """syscall_64.tbl 预留槽行（逐行登记为 ENOSYS stub，防止编号被误用）。"""
    return [
        f"{slot + contract.linux_base}\t{TBL_ABI}\t{TBL_RESERVED_NAME}{slot}"
        f"\t\t{TBL_RESERVED_ENTRY}"
        for slot in _reserved_slots(contract)
    ]


def _reserved_slots(contract: SyscallContract) -> range:
    """预留槽位的内部编号序列（跨越多个 <reserved> 区间）。"""
    start = _first_reserved_start(contract)
    end = _last_reserved_end(contract)
    return range(start, end + 1)


def _first_reserved_start(contract: SyscallContract) -> int:
    return contract.reserved[0].start if contract.reserved else contract.core_count


def _last_reserved_end(contract: SyscallContract) -> int:
    return contract.reserved[-1].end if contract.reserved else contract.core_count - 1


# 空参数签名（"void"/"" 视为无参，不参与绑定构造）。
_NO_PARAM = frozenset({"", "void"})


def _parse_params(signature: str) -> list[str]:
    """把 C 参数列表拆为原始参数串列表（"void"/空签名返回空列表）。"""
    return [tok.strip() for tok in signature.split(",") if tok.strip() not in _NO_PARAM]


def _normalize_type(before: str, pointer: bool) -> str:
    """归一化参数类型串（把前置星号还原到类型上并规整空格）。"""
    return " ".join((before + (" *" if pointer else "")).replace("*", " *").split())


def _lookup_types(c_type: str, owner: str) -> tuple[str, str, str | None, str]:
    """查参数类型词汇表；类型不在表中直接报错（绝不静默降级为桩）。"""
    try:
        return _TYPE_TABLE[c_type]
    except KeyError:
        raise ContractValidationError(
            f"{owner}: unsupported parameter type {c_type!r}; "
            f"allowed: {', '.join(sorted(_TYPE_TABLE))}"
        ) from None


def _split_param(token: str, owner: str) -> tuple[str, str]:
    """把 "const char *name" 拆为 (归一化 C 类型, 参数名)。"""
    type_part, sep, name_part = token.rpartition(" ")
    if not sep:
        raise ContractValidationError(f"{owner}: malformed parameter {token!r}")
    return _normalize_type(type_part, name_part.startswith("*")), name_part.lstrip("*")


def _camel(name: str) -> str:
    """把蛇形名（cgroup_path）转为 camelCase（Go/TS 标识符惯例）。"""
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _param_binding(token: str, owner: str) -> dict[str, object]:
    """把单个 C 参数（"const char *name"）映射为各语言类型与实参表达式。"""
    c_type, name = _split_param(token, owner)
    rust_type, py_type, py_ctor, go_type = _lookup_types(c_type, owner)
    go_name = _camel(name)
    return {
        "name": name,
        "c_type": c_type,
        "rust_type": rust_type,
        "py_type": py_type,
        "py_call": name if py_ctor is None else f"{py_ctor}({name})",
        "go_name": go_name,
        "go_type": go_type,
        "go_arg": f"uintptr({go_name})",
    }


def _pascal(suffix: str) -> str:
    """把大写后缀（ROVOL_CTL）转为 PascalCase（RovolCtl）。"""
    return "".join(part.capitalize() for part in suffix.split("_"))


def _format_params(params: list[dict[str, object]], fmt: str) -> str:
    """按模板格式化参数列表（{field} 占位映射到参数字典的键）。"""
    return ", ".join(fmt.format(**param) for param in params)


def _go_arg_list(params: list[dict[str, object]], owner: str) -> str:
    """Go syscall.Syscall 的 3 寄存器实参列表（不足补 0，超出即报错）。"""
    if len(params) > _GO_SYSCALL_ARG_REGS:
        raise ContractValidationError(
            f"{owner}: {len(params)} parameters exceed the "
            f"{_GO_SYSCALL_ARG_REGS}-register syscall.Syscall ABI"
        )
    padded = [*params, *([_GO_EMPTY_ARG] * (_GO_SYSCALL_ARG_REGS - len(params)))]
    return _format_params(padded, "{go_arg}")


def _any_needs_unsafe(params: list[dict[str, object]]) -> bool:
    """参数列表是否含需要 import "unsafe" 的指针类型。"""
    return any(param["go_type"] == "unsafe.Pointer" for param in params)


def _binding(spec: SyscallSpec, linux_base: int) -> dict[str, object]:
    """为单个 syscall 构造四语言绑定的渲染上下文片段。"""
    params = [_param_binding(token, spec.name) for token in _parse_params(spec.signature)]
    pascal = _pascal(spec.suffix)
    return {
        "name": spec.name,
        "suffix": spec.suffix,
        "pascal": pascal,
        "idx": spec.number,
        "nr": spec.linux_nr(linux_base),
        "index_macro": spec.index_macro,
        "nr_macro": _NR_MACRO_PREFIX + spec.symbol_macro,
        "go_index_const": _GO_IDX_PREFIX + pascal,
        "go_nr_const": _GO_NR_PREFIX + pascal,
        "go_func": _GO_FUNC_PREFIX + pascal,
        "flags": spec.flags,
        "category": spec.category,
        "since": spec.since,
        "stability": spec.stability,
        "doc": spec.doc,
        "capability_required": spec.capability_required,
        "rust_params": _format_params(params, "{name}: {rust_type}"),
        "rust_args": _format_params(params, "{name}"),
        "py_params": _format_params(params, "{name}: {py_type}"),
        "py_args": _format_params(params, "{py_call}"),
        "go_params": _format_params(params, "{go_name} {go_type}"),
        "go_args": _go_arg_list(params, spec.name),
        "go_needs_unsafe": _any_needs_unsafe(params),
    }


def build_context(contract: SyscallContract) -> dict[str, object]:
    """构造 header / tbl / SDK 模板统一的渲染上下文。"""
    linux_end = contract.linux_base + contract.total_slots - 1
    bindings = [_binding(spec, contract.linux_base) for spec in contract.syscalls]
    return {
        "generator": contract.generator,
        "generator_version": contract.generator_version,
        "source_name": SOURCE_NAME,
        "source_path": SOURCE_PATH,
        "guard": GUARD,
        "symbol_prefix": SYMBOL_PREFIX,
        "index_prefix": INDEX_PREFIX,
        "linux_base": contract.linux_base,
        "linux_end": linux_end,
        "x32_end": contract.linux_base - 1,
        "core_count": contract.core_count,
        "core_end": contract.linux_base + contract.core_count - 1,
        "control_count": contract.core_count - 1,
        "reserved_count": contract.reserved_count,
        "reserved_linux_start": contract.linux_base + contract.core_count,
        "reserved_internal_start": _first_reserved_start(contract),
        "reserved_internal_end": _last_reserved_end(contract),
        "total_slots": contract.total_slots,
        "index_max": contract.core_count - 1,
        "reserved_stub": TBL_RESERVED_ENTRY,
        "reserved_name_prefix": TBL_RESERVED_NAME,
        "tbl_cont_indent": TBL_CONT_INDENT,
        "slot_macro_lines": _slot_macro_lines(contract),
        "index_lines": _index_lines(contract),
        "nr_lines": _nr_lines(contract),
        "reserved_slot_lines": _reserved_slot_lines(contract),
        "prototype_lines": _prototype_lines(contract),
        "syscall_lines": _tbl_syscall_lines(contract),
        "reserved_stub_lines": _tbl_reserved_lines(contract),
        "bindings": bindings,
        "go_needs_unsafe": any(b["go_needs_unsafe"] for b in bindings),
    }


def render_outputs(repo_root: Path) -> dict[Path, str]:
    """解析契约源并渲染 C UAPI 头 + 四语言 SDK 绑定，返回 {目标路径: 内容}。"""
    contract = parse_contract(repo_root / SYSCALL_XML_REL)
    env = codegen_io.make_env()
    context = build_context(contract)
    targets = (
        (HEADER_REL, HEADER_TEMPLATE),
        (SDK_RUST_REL, RUST_TEMPLATE),
        (SDK_PY_REL, PY_TEMPLATE),
        (SDK_GO_REL, GO_TEMPLATE),
        (SDK_TS_REL, TS_TEMPLATE),
    )
    return {repo_root / rel: codegen_io.render(env, tpl, context) for rel, tpl in targets}


def render_tbl_section(repo_root: Path) -> str:
    """渲染 syscall_64.tbl Airymax 段片段（tbl 为上游共享文件，仅校验用）。"""
    contract = parse_contract(repo_root / SYSCALL_XML_REL)
    env = codegen_io.make_env()
    return codegen_io.render(env, TBL_TEMPLATE, build_context(contract))


def generate(repo_root: Path) -> int:
    """生成 syscall_gen.h（run_codegen 统一入口）。"""
    return codegen_io.generate(render_outputs, repo_root)


def check(repo_root: Path) -> int:
    """校验 syscall_gen.h 与已提交文件一致（CI 零 diff，§9.3）。"""
    return codegen_io.check(render_outputs, repo_root, generator="syscall_gen", make_target="syscall")


def check_tbl(repo_root: Path) -> int:
    """校验 syscall_64.tbl 的 Airymax 段与生成片段逐字节一致（§9.3）。"""
    tbl_path = repo_root / TBL_REL
    try:
        section = render_tbl_section(repo_root)
        content = codegen_io.read_text(tbl_path, "kernel syscall table")
    except CodegenError as exc:
        return codegen_io.fail(exc.message)
    if section in content:
        print(f"OK: syscall_64.tbl Airymax section matches generated section ({tbl_path})")
        return int(ExitCode.SUCCESS)
    print(f"ERROR: Airymax section differs from generated section: {tbl_path}", file=sys.stderr)
    print("       Expected Airymax section:", file=sys.stderr)
    print(section, file=sys.stderr)
    return int(ExitCode.FAILURE)


def main(argv: list[str] | None = None) -> int:
    """命令行入口：默认生成，--check 校验产物，--check-tbl 校验内核入口表。"""
    parser = codegen_io.build_parser(DESCRIPTION)
    parser.add_argument(
        "--check-tbl",
        action="store_true",
        help="校验 syscall_64.tbl 的 Airymax 段与生成片段逐字节一致",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    if args.check_tbl:
        return check_tbl(repo_root)
    if args.check:
        return check(repo_root)
    return generate(repo_root)


if __name__ == "__main__":
    sys.exit(main())