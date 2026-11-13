#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
sc_tri_compile.py — [SC] header multi-path compile & layout checker

Validates that every [SC] shared contract header under
kernel/include/uapi/linux/airymax/ compiles in all type-bridge paths and
that the fixed-width types and struct layouts are identical across those
paths (OS-IRON-014, 50-engineering-standards/11-sc-header-type-bridging.md).
It also verifies that the syscall ABI is bidirectionally consistent between
the [SC] contract header, the R-01 SSoT and its generated projection, and
that the [DSL] degraded build keeps the two-branch contract.

Compile paths (design §5.1; [DSL] §2.2 for the dsl-* paths):
  1. kernel-space         gcc -std=gnu11 -D__KERNEL__ -D__linux__
  2. linux-user-space     gcc -std=gnu11 -D__linux__
  3. third-party          gcc -std=c99   -U__linux__
  4. dsl-kernel-space     #1 + -DAIRY_SC_FALLBACK -Wno-error=cpp
  5. dsl-linux-user-space #2 + -DAIRY_SC_FALLBACK -Wno-error=cpp

Checks (design §5.3):
  * five-way compile — every header compiles under all five paths
  * type-size consistency — __u8/__u16/__u32/__u64/__s8/... identical
  * field-offset consistency — struct sizes and field offsets identical
  * [DSL] contract — dsl-* paths bridge exactly two branches
  * syscall ABI consistency — syscalls.h ↔ syscall.xml ↔ syscall_gen.h
    core symbol names and numbering agree in both directions (L-04 / D-04)

The include search order mirrors Kbuild: -Iinclude precedes -Iinclude/uapi so
that <linux/stddef.h> resolves to the kernel-internal header (the only offsetof
provider under -nostdinc) rather than the UAPI stub.

Usage:
    python3 tools/sc-tri-compile/sc_tri_compile.py [options]

Options:
    --repo-root PATH    agent-linux repo root (default: auto-detect)
    --headers-dir PATH  [SC] header directory
                        (default: kernel/include/uapi/linux/airymax)
    --kernel-dir PATH   kernel source root for -I flags (default: kernel)
    --cc PATH           C compiler (default: $CC or gcc)
    --check             CI mode: minimal output, exit code only (0=pass, 1=fail)

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
    2 — input/environment error (missing dir, compiler not found, etc.)
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

# ─── Constants ─────────────────────────────────────────────────────────────

# Type-bridge compile paths: (label, compiler flags). Design §5.1.
# The dsl-* paths pin the [DSL] degraded contract (AIRY_SC_FALLBACK) to the
# two branches §2.2 guarantees: __KERNEL__ and __linux__. Because every [SC]
# header intentionally emits #warning in degraded mode ([DSL] §2.1), those
# paths demote only the cpp diagnostic to keep -Werror for real errors.
DSL_PREFIX = "dsl-"
DSL_WARN_FLAG = "-Wno-error=cpp"
DSL_BRANCHES = 2  # [DSL] §2.2: __KERNEL__ / __linux__ only
KERNEL_FLAGS = ["-std=gnu11", "-D__KERNEL__", "-D__linux__"]
LINUX_FLAGS = ["-std=gnu11", "-D__linux__"]
THIRD_FLAGS = ["-std=c99", "-U__linux__"]
DSL_FLAGS = ["-DAIRY_SC_FALLBACK", DSL_WARN_FLAG]
COMPILE_PATHS = [
    ("kernel-space", KERNEL_FLAGS),
    ("linux-user-space", LINUX_FLAGS),
    ("third-party", THIRD_FLAGS),
    ("dsl-kernel-space", [*KERNEL_FLAGS, *DSL_FLAGS]),
    ("dsl-linux-user-space", [*LINUX_FLAGS, *DSL_FLAGS]),
]

# Kbuild-faithful include search order. Mirrors the kernel Makefile order
# (design §5.1); -Iinclude must precede -Iinclude/uapi.
INCLUDE_SUBDIRS = [
    "arch/x86/include",
    "arch/x86/include/generated",
    "include",
    "arch/x86/include/uapi",
    "arch/x86/include/generated/uapi",
    "include/uapi",
    "include/generated/uapi",
]

DEFAULT_HEADERS_REL = os.path.join("kernel", "include", "uapi", "linux", "airymax")
DEFAULT_KERNEL_REL = "kernel"

# Fixed-width type sizes, identical in all three paths (design §3.3).
TYPE_SIZES = {
    "__u8": 1,
    "__u16": 2,
    "__u32": 4,
    "__u64": 8,
    "__s8": 1,
    "__s16": 2,
    "__s32": 4,
    "__s64": 8,
}

# Curated struct layouts pinned by the [SC] contract headers' own
# _Static_asserts. (struct, expected_size, [(field, expected_offset), ...])
LAYOUT = [
    (
        "airy_ipc_msg_hdr",
        128,
        [
            ("magic", 0),
            ("opcode", 4),
            ("capability_badge", 40),
            ("payload_len", 48),
            ("crc32", 52),
            ("reserved", 56),
        ],
    ),
    (
        "airy_log_record",
        128,
        [
            ("magic", 0),
            ("level", 4),
            ("timestamp_ns", 8),
            ("payload", 24),
            ("reserved", 120),
        ],
    ),
    (
        "airy_task_desc",
        64,
        [
            ("magic", 0),
            ("runtime_ns", 8),
            ("vtime", 32),
            ("agent_id", 36),
            ("state", 48),
            ("_reserved", 52),
        ],
    ),
    (
        "bpf_struct_ops_common_val",
        64,
        [
            ("state", 0),
            ("refcount", 4),
            ("registered_ns", 8),
            ("activated_ns", 16),
            ("_reserved", 24),
        ],
    ),
    (
        "airy_struct_ops_value",
        128,
        [
            ("common", 0),
            ("version", 64),
            ("flags", 68),
            ("name", 72),
            ("_reserved", 120),
        ],
    ),
]

# Headers pulled into the layout probe. Only canonical [SC] headers — the
# codegen-derived structures_{32,64}_gen.h redefine the same structs and must
# not be included alongside them.
PROBE_HEADERS = [
    "uapi_compat.h",
    "error.h",
    "ipc.h",
    "log_types.h",
    "sched.h",
    "bpf_struct_ops.h",
]

# Syscall ABI tri-source consistency (L-04 / D-04). The [SC] contract header
# (syscalls.h) must agree with the R-01 SSoT (syscall.xml) and its generated
# projection (syscall_gen.h) on both symbol names and numbering, in both
# directions — a name present in one source and absent in another, or a
# number that disagrees, is an ABI break.
SYSCALL_CONTRACT_FILE = "syscalls.h"
SYSCALL_XML_FILE = "syscall.xml"
SYSCALL_GENERATED_FILE = "syscall_gen.h"

# Names in the [SC] contract header under the AIRY_SYS_ prefix that describe
# the reserved span rather than a core syscall slot. They carry numbering
# metadata and are excluded when deriving the core-symbol suffix set.
CONTRACT_META_SUFFIXES = frozenset({"RESERVED_BASE", "RESERVED_END", "SLOTS_MAX"})

CONTRACT_SYMBOL_PREFIX = "AIRY_SYS_"
GENERATED_NR_PREFIX = "__NR_"
GENERATED_IDX_PREFIX = "AIRY_SYS_IDX_"

SYSCALL_ABI_CHECK_NAME = (
    "syscall ABI 三源一致 (syscalls.h ↔ syscall.xml ↔ syscall_gen.h)"
)

# [DSL] degraded error table (08-sc-error-contract.md §5, [DSL] §2.1). The
# seven core codes keep their [SC] magnitudes; the 38 POSIX codes absent from
# the core set collapse onto them. The mapping is normative: a drifted alias
# silently changes errno semantics in a degraded build, so both the alias set
# and every resolved value are pinned here.
DSL_ERROR_HEADER = "error.h"
DSL_ERROR_BLOCK_OPEN = "#ifdef AIRY_SC_FALLBACK"
DSL_ERROR_BLOCK_CLOSE = "#endif /* AIRY_SC_FALLBACK */"

DSL_ERR_CORE = {
    "AIRY_EEXIST": 2,
    "AIRY_EINVAL": 5,
    "AIRY_ENOMEM": 9,
    "AIRY_ENOTSUP": 11,
    "AIRY_EBUSY": 16,
    "AIRY_ECANCELED": 19,
    "AIRY_EAGAIN": 35,
}

DSL_ERR_ALIASES = {
    "AIRY_DSL_E2BIG": "AIRY_EINVAL",
    "AIRY_DSL_ECHILD": "AIRY_EINVAL",
    "AIRY_DSL_EDEADLK": "AIRY_EBUSY",
    "AIRY_DSL_EDOM": "AIRY_EINVAL",
    "AIRY_DSL_EEXIST_S": "AIRY_EEXIST",
    "AIRY_DSL_EFBIG": "AIRY_EINVAL",
    "AIRY_DSL_EILSEQ": "AIRY_EINVAL",
    "AIRY_DSL_EINPROGRESS": "AIRY_EAGAIN",
    "AIRY_DSL_EISCONN": "AIRY_EBUSY",
    "AIRY_DSL_ELOOP": "AIRY_EINVAL",
    "AIRY_DSL_EMFILE": "AIRY_ENOMEM",
    "AIRY_DSL_EMLINK": "AIRY_ENOMEM",
    "AIRY_DSL_ENAMETOOLONG": "AIRY_EINVAL",
    "AIRY_DSL_ENFILE": "AIRY_ENOMEM",
    "AIRY_DSL_ENODEV": "AIRY_EINVAL",
    "AIRY_DSL_ENOEXEC": "AIRY_EINVAL",
    "AIRY_DSL_ENOLCK": "AIRY_ENOMEM",
    "AIRY_DSL_ENOMSG": "AIRY_ECANCELED",
    "AIRY_DSL_ENOTBLK": "AIRY_EINVAL",
    "AIRY_DSL_ENOTCONN": "AIRY_ECANCELED",
    "AIRY_DSL_ENOTDIR": "AIRY_EINVAL",
    "AIRY_DSL_ENOTEMPTY": "AIRY_EBUSY",
    "AIRY_DSL_ENOTSOCK": "AIRY_EINVAL",
    "AIRY_DSL_ENXIO": "AIRY_EINVAL",
    "AIRY_DSL_EOPNOTSUPP": "AIRY_ENOTSUP",
    "AIRY_DSL_EOVERFLOW": "AIRY_EINVAL",
    "AIRY_DSL_EPIPE": "AIRY_ECANCELED",
    "AIRY_DSL_EPROTO": "AIRY_EINVAL",
    "AIRY_DSL_EROFS": "AIRY_EBUSY",
    "AIRY_DSL_ESPIPE": "AIRY_EINVAL",
    "AIRY_DSL_ESRCH": "AIRY_EINVAL",
    "AIRY_DSL_ETIMEDOUT": "AIRY_ECANCELED",
    "AIRY_DSL_ETXTBSY": "AIRY_EBUSY",
    "AIRY_DSL_EWOULDBLOCK": "AIRY_EAGAIN",
    "AIRY_DSL_EXDEV": "AIRY_EINVAL",
    "AIRY_DSL_ENODATA": "AIRY_ECANCELED",
    "AIRY_DSL_ENOSR": "AIRY_ENOMEM",
    "AIRY_DSL_ESTALE": "AIRY_ECANCELED",
}

DSL_ERR_CHECK_NAME = "[DSL] 降级错误码表 (38 POSIX 码 → 7 核心码)"

# Object-like alias inside the [DSL] error block: `#define AIRY_DSL_E* <core>`.
_DSL_ALIAS_RE = re.compile(
    r"^\s*#\s*define\s+(AIRY_DSL_E\w*)\s+(AIRY_E\w*)\s*$", re.MULTILINE
)

# Object-like numeric #define. Function-like macros and token aliases are
# intentionally not matched, e.g. AIRY_SYS_RESERVED_CLAIM(num) or
# AIRY_DSL_SYS_CALL AIRY_SYS_CALL.
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\s+(0[xX][0-9a-fA-F]+|[0-9]+)\b")


# ─── Helpers ───────────────────────────────────────────────────────────────


def find_repo_root():
    """Auto-detect agent-linux repo root from script location.

    Script lives at <repo_root>/tools/sc-tri-compile/sc_tri_compile.py.
    """
    return Path(__file__).resolve().parent.parent.parent


def inc_flags(kernel_dir):
    """Return -I flags in Kbuild order for the given kernel source root."""
    return [f"-I{kernel_dir / sub}" for sub in INCLUDE_SUBDIRS]


def run_capture(cmd, cwd=None):
    """Run cmd, returning (returncode, stdout, stderr).

    Exits with code 2 when the compiler cannot be executed at all.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError:
        print(f"ERROR: cannot execute command: {cmd[0]}", file=sys.stderr)
        sys.exit(2)
    except subprocess.TimeoutExpired:
        print(f"ERROR: command timed out: {' '.join(cmd)}", file=sys.stderr)
        sys.exit(2)
    return proc.returncode, proc.stdout, proc.stderr


def first_error(stderr):
    """Return the first compiler error line, or a trimmed stderr fallback."""
    for line in stderr.splitlines():
        if "error:" in line:
            return line.strip()
    stripped = stderr.strip()
    return stripped.splitlines()[0] if stripped else "unknown error"


# ─── Syscall ABI Sources ([SC] tri-source, L-04 / D-04) ────────────────────


@dataclass(frozen=True)
class SyscallXml:
    """Parsed syscall.xml (R-01 SSoT) — the numbering authority.

    Core syscalls are keyed by their uppercase suffix (the syscall name with
    the ``prefix_function`` stripped and folded to upper case), so CALL maps
    to airy_sys_call and its internal number.
    """

    version: int
    core_count: int
    reserved_count: int
    total_slots: int
    linux_base: int
    symbol_prefix: str
    function_prefix: str
    core_names: dict
    core_numbers: dict
    reserved_from: int
    reserved_to: int


@dataclass(frozen=True)
class SyscallSources:
    """The three syscall ABI sources loaded from the [SC] header directory.

    contract/generated map every object-like numeric #define name to its
    value; the checks filter them by prefix into their respective namespaces.
    """

    xml: SyscallXml
    contract: dict
    generated: dict


def parse_c_defines(text):
    """Return {macro name: int} for object-like numeric #defines in text."""
    defines = {}
    for line in text.splitlines():
        match = _DEFINE_RE.match(line)
        if match:
            defines[match.group(1)] = int(match.group(2), 0)
    return defines


def _xml_root(text):
    """整篇文档只解析一次；根元素不对，后面所有判据都不成立。"""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"syscall.xml XML 解析失败: {exc}") from exc
    if root.tag != "syscalls":
        raise ValueError(f"syscall.xml 根元素应为 <syscalls>，实际 <{root.tag}>")
    return root


def _attr_int(node, attr):
    """取整数属性；措辞随所在元素而定，根元素说「属性 x」，<reserved> 用 @。"""
    raw = node.get(attr)
    if raw is None:
        if node.tag == "syscalls":
            raise ValueError(f"syscall.xml 缺少属性 {attr}")
        raise ValueError(f"syscall.xml <{node.tag}> 缺少属性 {attr}")
    try:
        return int(raw, 0)
    except ValueError as exc:
        where = (f"属性 {attr}" if node.tag == "syscalls"
                 else f"<{node.tag}>@{attr}")
        raise ValueError(f"syscall.xml {where} 非整数: {raw}") from exc


def _xml_meta(root):
    """<meta> 的两个前缀：名称不带前缀，后缀去重就会串味。"""
    meta = root.find("meta")
    if meta is None:
        raise ValueError("syscall.xml 缺少 <meta>")
    symbol = (meta.findtext("prefix_symbol") or "").strip()
    func = (meta.findtext("prefix_function") or "").strip()
    if not symbol or not func:
        raise ValueError("syscall.xml <meta> 缺少 prefix_symbol/prefix_function")
    return symbol, func


def _xml_sys(entry, prefix):
    """单条 <syscall>：返回 (大写后缀, 原名, 编号)，不合规即 ValueError。"""
    name, raw_number = _xml_pair(entry)
    _need_prefix(name, prefix)
    return name[len(prefix):].upper(), name, _xml_num(name, raw_number)


def _xml_pair(entry):
    """<name>/<number> 都必须非空；两者一起报，免得逐个文件来回修。"""
    name = (entry.findtext("name") or "").strip()
    raw_number = (entry.findtext("number") or "").strip()
    if not name or not raw_number:
        raise ValueError("syscall.xml <syscall> 缺少 <name>/<number>")
    return name, raw_number


def _need_prefix(name, prefix):
    """名称必须带约定前缀，否则表与内核入口对不上。"""
    if not name.startswith(prefix):
        raise ValueError(f"syscall.xml 名称 {name} 未带前缀 {prefix}")


def _xml_num(name, raw_number):
    """编号按 C 字面量解析（0x 前缀可用），失败时带原名报错。"""
    try:
        return int(raw_number, 0)
    except ValueError as exc:
        raise ValueError(
            f"syscall.xml {name} 的 <number> 非整数: {raw_number}") from exc


def _xml_core(root, prefix):
    """逐条 <syscall>：名字带前缀且去重，编号可解析为整数。"""
    names = {}
    numbers = {}
    for entry in root.findall("syscall"):
        suffix, name, number = _xml_sys(entry, prefix)
        if suffix in names:
            raise ValueError(f"syscall.xml 重复的 syscall 名称: {name}")
        names[suffix] = name
        numbers[suffix] = number
    return names, numbers


def _xml_res(root):
    """保留槽位块：缺了它，插号与空洞的一致性检查就无对象可查。"""
    reserved = root.find("reserved")
    if reserved is None:
        raise ValueError("syscall.xml 缺少 <reserved>")
    return reserved


def parse_sc_xml(text):
    """Parse syscall.xml text into a SyscallXml.

    Raises ValueError when the document is not well-formed XML or is missing
    any structural element the consistency checks rely on.
    """
    root = _xml_root(text)
    symbol_prefix, function_prefix = _xml_meta(root)
    core_names, core_numbers = _xml_core(root, function_prefix)
    reserved = _xml_res(root)
    return SyscallXml(
        version=_attr_int(root, "version"),
        core_count=_attr_int(root, "core_count"),
        reserved_count=_attr_int(root, "reserved_count"),
        total_slots=_attr_int(root, "total_slots"),
        linux_base=_attr_int(root, "linux_base"),
        symbol_prefix=symbol_prefix,
        function_prefix=function_prefix,
        core_names=core_names,
        core_numbers=core_numbers,
        reserved_from=_attr_int(reserved, "from"),
        reserved_to=_attr_int(reserved, "to"),
    )


def load_sc_src(headers_dir):
    """Load the three syscall ABI sources.

    Returns (sources, failures): on any read or parse error sources is None
    and failures holds the human-readable reasons, so the caller reports a
    failed check instead of crashing.
    """
    failures = []
    texts = {}
    for name in (SYSCALL_CONTRACT_FILE, SYSCALL_XML_FILE, SYSCALL_GENERATED_FILE):
        try:
            texts[name] = (Path(headers_dir) / name).read_text(encoding="utf-8")
        except OSError as exc:
            failures.append(f"  无法读取 {name}: {exc}")
    if failures:
        return None, failures

    try:
        xml = parse_sc_xml(texts[SYSCALL_XML_FILE])
    except ValueError as exc:
        return None, [f"  {exc}"]

    return (
        SyscallSources(
            xml=xml,
            contract=parse_c_defines(texts[SYSCALL_CONTRACT_FILE]),
            generated=parse_c_defines(texts[SYSCALL_GENERATED_FILE]),
        ),
        failures,
    )


def _exp_suffix(sources):
    """Return the expected uppercase core-symbol suffix set from the SSoT."""
    return frozenset(sources.xml.core_names)


def _core_defs(sources):
    """Return {suffix: value} for the contract header's core syscall macros."""
    return {
        name[len(CONTRACT_SYMBOL_PREFIX):]: value
        for name, value in sources.contract.items()
        if name.startswith(CONTRACT_SYMBOL_PREFIX)
        and name[len(CONTRACT_SYMBOL_PREFIX):] not in CONTRACT_META_SUFFIXES
    }


def _nr_names(sources):
    """Return {__NR_* symbol: value} for syscall_gen.h registration numbers."""
    return {
        name: value
        for name, value in sources.generated.items()
        if name.startswith(GENERATED_NR_PREFIX)
    }


def _idx_defs(sources):
    """Return {suffix: value} for syscall_gen.h AIRY_SYS_IDX_* indices."""
    return {
        name[len(GENERATED_IDX_PREFIX):]: value
        for name, value in sources.generated.items()
        if name.startswith(GENERATED_IDX_PREFIX)
    }


def _set_delta(label, actual, expected):
    """Return failure lines for symbols present in only one of two sets."""
    failures = []
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        failures.append(f"  {label}: 缺失 {', '.join(missing)}")
    if extra:
        failures.append(f"  {label}: 多出 {', '.join(extra)}")
    return failures


# ─── Check Results ─────────────────────────────────────────────────────────


class CheckResult:
    """Result of a single [SC] tri-compile check."""

    def __init__(self, name, passed, details=""):
        self.name = name
        self.passed = passed
        self.details = details


# ─── Checks ────────────────────────────────────────────────────────────────


def tri_check(cc, include_flags, header_files):
    """Compile every header under every path; collect the first error each."""
    failures = []
    for label, flags in COMPILE_PATHS:
        for header in header_files:
            rc, _out, err = run_capture(
                [
                    cc,
                    *flags,
                    *include_flags,
                    "-fsyntax-only",
                    "-Werror",
                    str(header),
                ]
            )
            if rc != 0:
                failures.append(f"  [{label}] {header.name}: {first_error(err)}")

    total = len(header_files) * len(COMPILE_PATHS)
    name = "五路编译通过 (常规三路 + [DSL] 降级两路)"
    if failures:
        return CheckResult(name, False, "\n".join(failures))
    return CheckResult(
        name,
        True,
        f"{len(header_files)} 个头文件 × {len(COMPILE_PATHS)} 路径 "
        f"= {total} 次编译全部通过",
    )


def probe_src():
    """Generate a C program printing type sizes, layouts and DSL markers."""
    lines = ["#include <stdio.h>"]
    lines += [f"#include <linux/airymax/{h}>" for h in PROBE_HEADERS]
    lines += [
        "",
        "#ifdef AIRY_SC_FALLBACK",
        'AIRY_COMPILE_ASSERT(AIRY_DSL_UAPI_BRANCHES == 2, "two branches");',
        "#define AIRY_PROBE_BRANCHES AIRY_DSL_UAPI_BRANCHES",
        "#else",
        "#define AIRY_PROBE_BRANCHES (-1)",
        "#endif",
        "",
        "int main(void)",
        "{",
        '\tprintf("D branches %d\\n", AIRY_PROBE_BRANCHES);',
    ]
    lines += [
        "#ifdef AIRY_SC_FALLBACK",
        '\tprintf("D errs 1\\n");',
    ]
    for name in (*DSL_ERR_CORE, *DSL_ERR_ALIASES):
        lines.append(f'\tprintf("E {name} %d\\n", {name});')
    lines += [
        "#else",
        '\tprintf("D errs 0\\n");',
        "#endif",
    ]
    for tname in TYPE_SIZES:
        lines.append(f'\tprintf("T {tname} %zu\\n", sizeof({tname}));')
    for struct, _size, fields in LAYOUT:
        lines.append(f'\tprintf("S {struct} %zu\\n", sizeof(struct {struct}));')
        for field, _off in fields:
            lines.append(
                f'\tprintf("O {struct} {field} %zu\\n", ' f"offsetof(struct {struct}, {field}));"
            )
    lines += ["\treturn 0;", "}", ""]
    return "\n".join(lines)


def parse_probe(stdout):
    """Parse probe output into {(kind, struct, field): int}."""
    values = {}
    for line in stdout.splitlines():
        parsed = _probe_row(line.split())
        if parsed is not None:
            key, number = parsed
            values[key] = number
    return values


def _probe_row(parts):
    """一行探针输出 → (键, 数值)；空行与不认识的形态丢弃。"""
    if not parts:
        return None
    kind = parts[0]
    if kind in ("T", "S", "D", "E") and len(parts) == 3:
        return (kind, parts[1], ""), int(parts[2])
    if kind == "O" and len(parts) == 4:
        return (kind, parts[1], parts[2]), int(parts[3])
    return None


def collect_probe(cc, include_flags, workdir):
    """Compile and run the layout probe under every compile path.

    Returns (per_path, failures): per_path maps the path label to its parsed
    values; failures lists paths whose probe failed to compile or run.
    """
    src = Path(workdir) / "sc_probe.c"
    src.write_text(probe_src(), encoding="utf-8")
    per_path = {}
    failures = []
    for label, flags in COMPILE_PATHS:
        exe = Path(workdir) / f"sc_probe_{label}"
        # The probe intentionally avoids -Werror: the kernel path mixes kernel
        # headers with glibc <stdio.h>, yielding benign redefinition warnings
        # while still producing a correct program.
        rc, _out, err = run_capture([cc, *flags, *include_flags, str(src), "-o", str(exe)])
        if rc != 0:
            failures.append(f"  [{label}] probe compile failed: {first_error(err)}")
            continue
        rc, out, err = run_capture([str(exe)])
        if rc != 0:
            failures.append(f"  [{label}] probe run failed: {err.strip()}")
            continue
        per_path[label] = parse_probe(out)
    return per_path, failures


def check_consist(item, expected, per_path, key, failures):
    """Record a failure unless every path reports `expected` for `key`."""
    values = {label: vals.get(key) for label, vals in per_path.items()}
    missing = [label for label, value in values.items() if value is None]
    if missing:
        failures.append(f"  {item}: 探针未返回 ({', '.join(missing)})")
        return
    _need_same(item, expected, values, failures)


def _need_same(item, expected, values, failures):
    """所有路径取值一致且等于期望；否则一条带出实得表。"""
    if set(values.values()) == {expected}:
        return
    shown = ", ".join(f"{k}={v}" for k, v in values.items())
    failures.append(f"  {item}: 期望 {expected}, 实测 {shown}")


def type_size_check(per_path, probe_failures):
    """Check fixed-width type sizes are identical across all compile paths."""
    name = "类型大小五路一致 (__u8..__s64)"
    if probe_failures:
        return CheckResult(name, False, "\n".join(probe_failures))
    failures = []
    for tname, expected in TYPE_SIZES.items():
        check_consist(tname, expected, per_path, ("T", tname, ""), failures)
    if failures:
        return CheckResult(name, False, "\n".join(failures))
    return CheckResult(name, True, f"{len(TYPE_SIZES)} 个定宽类型各路径大小一致")


def layout_check(per_path, probe_failures):
    """Check struct sizes and field offsets are identical across paths."""
    name = "结构体字段偏移五路一致"
    if probe_failures:
        return CheckResult(name, False, "\n".join(probe_failures))
    failures = []
    field_count = 0
    for struct, size, fields in LAYOUT:
        check_consist(f"{struct} sizeof", size, per_path, ("S", struct, ""), failures)
        for field, off in fields:
            field_count += 1
            check_consist(
                f"{struct}.{field}",
                off,
                per_path,
                ("O", struct, field),
                failures,
            )
    if failures:
        return CheckResult(name, False, "\n".join(failures))
    return CheckResult(
        name,
        True,
        f"{len(LAYOUT)} 个结构体、{field_count} 个字段偏移各路径一致",
    )


def _dsl_fail(label, flags, probed):
    """单条 dsl-* 路径的降级契约：开关在位，且探测到两路分支。"""
    failures = []
    if "-DAIRY_SC_FALLBACK" not in flags:
        failures.append(f"  [{label}] 缺少 -DAIRY_SC_FALLBACK")
    branches = probed.get(("D", "branches", ""))
    if branches != DSL_BRANCHES:
        failures.append(
            f"  [{label}] AIRY_DSL_UAPI_BRANCHES={branches}, "
            f"期望 {DSL_BRANCHES}"
        )
    return failures


def _off_fail(label, probed):
    """常规路径不得激活降级层：探针须回报未定义分支数。"""
    branches = probed.get(("D", "branches", ""))
    if branches == -1:
        return []
    return [f"  [{label}] 降级层意外激活 (branches={branches})"]


def _dsl_labels():
    """COMPILE_PATHS 里的 dsl-* 路径标签，保持清单顺序。"""
    return [
        label for label, _flags in COMPILE_PATHS if label.startswith(DSL_PREFIX)
    ]


def _dsl_on_fail(per_path):
    """全部 dsl-* 路径的降级契约问题汇总。"""
    return [
        item
        for label, flags in COMPILE_PATHS
        if label.startswith(DSL_PREFIX)
        for item in _dsl_fail(label, flags, per_path.get(label, {}))
    ]


def _dsl_off_fail(per_path):
    """全部常规路径的「降级层未激活」问题汇总。"""
    return [
        item
        for label, _flags in COMPILE_PATHS
        if not label.startswith(DSL_PREFIX)
        for item in _off_fail(label, per_path.get(label, {}))
    ]


def fallback_check(per_path):
    """Verify the [DSL] degraded contract on the dsl-* compile paths.

    Each dsl-* path must define AIRY_SC_FALLBACK and have its probe report
    the two-branch count from [DSL] §2.2; every regular path must report
    that the degraded layer stayed inactive.
    """
    name = "[DSL] 降级路径契约 (__KERNEL__ / __linux__ 两路)"
    dsl_labels = _dsl_labels()
    if not dsl_labels:
        return CheckResult(name, False, "  COMPILE_PATHS 未包含 [DSL] 降级路径")

    failures = _dsl_on_fail(per_path) + _dsl_off_fail(per_path)
    if failures:
        return CheckResult(name, False, "\n".join(failures))
    return CheckResult(
        name,
        True,
        f"{len(dsl_labels)} 条降级路径均桥接到 {DSL_BRANCHES} 路",
    )


def scan_dsl_alias(header_text):
    """Return {alias: core} declared inside error.h's [DSL] error block."""
    start = header_text.find(DSL_ERROR_BLOCK_OPEN)
    end = header_text.rfind(DSL_ERROR_BLOCK_CLOSE)
    if start < 0 or end < start:
        return {}
    return dict(_DSL_ALIAS_RE.findall(header_text[start:end]))


def _dsl_delta(declared):
    """Return failures for aliases missing, extra, or mapped elsewhere."""
    failures = _set_delta(
        f"{DSL_ERROR_HEADER} [DSL] 别名", set(declared), set(DSL_ERR_ALIASES)
    )
    for alias, core in sorted(declared.items()):
        want = DSL_ERR_ALIASES.get(alias)
        if want is not None and core != want:
            failures.append(f"  {DSL_ERROR_HEADER}: {alias} 映射 {core}, 期望 {want}")
    return failures


def _dsl_drift(label, values):
    """Return failures where one path's probed magnitudes deviate."""
    failures = []
    if values.get(("D", "errs", "")) != 1:
        failures.append(f"  [{label}] [DSL] 错误码块未激活")
    for core, want in DSL_ERR_CORE.items():
        got = values.get(("E", core, ""))
        if got != want:
            failures.append(f"  [{label}] {core} 期望 {want}, 实测 {got}")
    for alias, core in DSL_ERR_ALIASES.items():
        got = values.get(("E", alias, ""))
        want = DSL_ERR_CORE[core]
        if got != want:
            failures.append(f"  [{label}] {alias} 期望 {want} ({core}), 实测 {got}")
    return failures


def _dsl_leak(per_path, dsl_labels):
    """Return failures when a regular path activates the [DSL] block."""
    return [
        f"  [{label}] 常规构建泄漏 [DSL] 错误码别名"
        for label, _flags in COMPILE_PATHS
        if label not in dsl_labels
        and per_path.get(label, {}).get(("D", "errs", "")) != 0
    ]


def dsl_err_check(per_path, header_text):
    """Verify the 38-code [DSL] error table against its declared contract.

    The alias set in error.h must match the normative table exactly, every
    alias must resolve to the pinned core magnitude on both dsl-* paths, and
    no alias may leak into a regular build.
    """
    dsl_labels = [
        label for label, _flags in COMPILE_PATHS if label.startswith(DSL_PREFIX)
    ]
    if not dsl_labels:
        return CheckResult(DSL_ERR_CHECK_NAME, False, "  COMPILE_PATHS 未包含 [DSL] 降级路径")

    failures = _dsl_delta(scan_dsl_alias(header_text))
    for label in dsl_labels:
        failures += _dsl_drift(label, per_path.get(label, {}))
    failures += _dsl_leak(per_path, dsl_labels)

    if failures:
        return CheckResult(DSL_ERR_CHECK_NAME, False, "\n".join(failures))
    return CheckResult(
        DSL_ERR_CHECK_NAME,
        True,
        f"{len(DSL_ERR_ALIASES)} 个别名映射到 {len(DSL_ERR_CORE)} 个核心码，"
        f"{len(dsl_labels)} 条降级路径数值一致",
    )


def check_sc_sym(sources):
    """Cross-check core syscall symbol-name sets across the three sources.

    Every core syscall in the SSoT must appear, and only it, as an
    AIRY_SYS_<SUFFIX> macro in syscalls.h, an AIRY_SYS_IDX_<SUFFIX> index and
    a __NR_<name> number in syscall_gen.h. Returns failure lines.
    """
    expected = _exp_suffix(sources)
    failures = _set_delta(
        "syscalls.h 核心宏",
        set(_core_defs(sources)),
        set(expected),
    )
    failures += _set_delta(
        "syscall_gen.h AIRY_SYS_IDX_ 符号",
        set(_idx_defs(sources)),
        set(expected),
    )
    expected_nr = {GENERATED_NR_PREFIX + name for name in sources.xml.core_names.values()}
    failures += _set_delta(
        "syscall_gen.h __NR_ 符号",
        set(_nr_names(sources)),
        expected_nr,
    )
    return failures


def _span_probs(xml):
    """SSoT 槽位算术、条目数与预留区间三项自洽。"""
    expected_span = (xml.core_count, xml.total_slots - 1)
    problems = []
    if xml.core_count + xml.reserved_count != xml.total_slots:
        problems.append(
            f"  syscall.xml: core_count({xml.core_count}) + "
            f"reserved_count({xml.reserved_count}) != total_slots({xml.total_slots})"
        )
    if len(xml.core_numbers) != xml.core_count:
        problems.append(
            f"  syscall.xml: 声明 core_count({xml.core_count}) 与 "
            f"<syscall> 条目数({len(xml.core_numbers)}) 不符"
        )
    if (xml.reserved_from, xml.reserved_to) != expected_span:
        problems.append(
            f"  syscall.xml: <reserved> 区间 ({xml.reserved_from}-{xml.reserved_to}) "
            f"!= 期望 ({expected_span[0]}-{expected_span[1]})"
        )
    return problems


def _contract_want(xml, base):
    """[SC] 契约头应声明的宏 → 编号。"""
    expect = {
        f"{xml.symbol_prefix}RESERVED_BASE": base + xml.core_count,
        f"{xml.symbol_prefix}RESERVED_END": base + xml.total_slots - 1,
        f"{xml.symbol_prefix}SLOTS_MAX": xml.total_slots,
    }
    for suffix, number in xml.core_numbers.items():
        expect[f"{xml.symbol_prefix}{suffix}"] = base + number
    return expect


def _generated_want(xml, base):
    """生成头应声明的宏 → 编号。"""
    expect = {
        "AIRY_SYS_BASE": base,
        "AIRY_SYS_NR_CORE": xml.core_count,
        "AIRY_SYS_NR_RESERVED": xml.reserved_count,
        "AIRY_SYS_NR_TOTAL": xml.total_slots,
    }
    for suffix, number in xml.core_numbers.items():
        expect[GENERATED_NR_PREFIX + xml.core_names[suffix]] = base + number
        expect[GENERATED_IDX_PREFIX + suffix] = number
    return expect


def check_sc_nr(sources):
    """Check syscall numbering consistency across the three sources.

    Verifies the SSoT's slot arithmetic and reserved span, the contract
    header's boundary macros and per-core Linux numbers, and the generated
    header's base/count macros, registration numbers and internal indices.
    Returns failure lines.
    """
    base = sources.xml.linux_base
    failures = _span_probs(sources.xml)
    for name, want in _contract_want(sources.xml, base).items():
        _check_macro(failures, "syscalls.h", name, want, sources.contract)
    for name, want in _generated_want(sources.xml, base).items():
        _check_macro(failures, "syscall_gen.h", name, want, sources.generated)

    return failures


def _check_macro(failures, source, name, want, defines):
    """Append a failure unless defines[name] equals want."""
    got = defines.get(name)
    if got is None:
        failures.append(f"  {source}: 缺失 {name}")
    elif got != want:
        failures.append(f"  {source}: {name} 期望 {want}, 实测 {got}")


def check_sc_abi(sources, load_failures):
    """Aggregate syscall ABI tri-source consistency into one check result."""
    if sources is None:
        return CheckResult(SYSCALL_ABI_CHECK_NAME, False, "\n".join(load_failures))
    failures = list(load_failures)
    failures += check_sc_sym(sources)
    failures += check_sc_nr(sources)
    if failures:
        return CheckResult(SYSCALL_ABI_CHECK_NAME, False, "\n".join(failures))
    xml = sources.xml
    span = f"{xml.linux_base}-{xml.linux_base + xml.total_slots - 1}"
    return CheckResult(
        SYSCALL_ABI_CHECK_NAME,
        True,
        f"{xml.core_count} 个核心 syscall + {xml.reserved_count} 个预留槽，"
        f"号段 {span} 符号名与编号三源一致",
    )


# ─── Report ────────────────────────────────────────────────────────────────


def print_report(results, header_files, cc, sources, check_mode):
    """Print the tri-compile check report."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    if check_mode:
        _print_ci(results, passed, total, failed)
        return
    _print_body(results, header_files, cc, sources, passed, total, failed)


def _print_ci(results, passed, total, failed):
    """CI 模式：一行判定加失败项明细，便于在流水线日志里定位。"""
    if failed == 0:
        print(f"SC TRI-COMPILE: PASS ({passed}/{total} checks)")
        return
    print(f"SC TRI-COMPILE: FAIL ({passed}/{total} checks, {failed} failed)")
    for r in results:
        if not r.passed:
            print(f"  FAIL: {r.name}")
            for line in r.details.splitlines():
                print(f"    {line}")


def _print_body(results, header_files, cc, sources, passed, total, failed):
    """详细模式：来源与编译器、逐项判定、结论横幅。"""
    _print_head(header_files, cc, sources, passed, total, failed)
    for r in results:
        _print_one(r)
    print("-" * 72)
    if failed == 0:
        print("RESULT: PASS — all [SC] tri-compile checks passed (OS-IRON-014)")
    else:
        print(f"RESULT: FAIL — {failed} check(s) failed (OS-IRON-014 violation)")
    print("-" * 72)


def _print_head(header_files, cc, sources, passed, total, failed):
    """报告抬头：契约来源、编译器与判定计数。"""
    print("=" * 72)
    print("agent-linux [SC] Tri-Compile & Layout Check Report")
    print("OS-IRON-014: [SC] 共享契约头多路编译与二进制布局一致")
    print("=" * 72)
    print()
    print("Sources:")
    for label, path in sources:
        print(f"  {label:14s}: {path}")
    print()
    print(f"Compiler:  {cc}")
    print(f"Headers:   {len(header_files)} 个 [SC] 头文件")
    print()
    print("-" * 72)
    print(f"Checks: {passed}/{total} passed, {failed} failed")
    print("-" * 72)
    print()


def _print_one(r):
    """单项判定：标记、状态、名称与明细。"""
    marker = "[+]" if r.passed else "[-]"
    status = "PASS" if r.passed else "FAIL"
    print(f"{marker} {status}: {r.name}")
    if r.details:
        for line in r.details.splitlines():
            print(f"      {line}")
    print()


# ─── Main ──────────────────────────────────────────────────────────────────


def _build_parser():
    """命令行定义：参数顺序即帮助里的顺序，改动会改变对外契约。"""
    parser = argparse.ArgumentParser(
        description="agent-linux [SC] tri-compile & layout checker " "(OS-IRON-014)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="agent-linux repo root (default: auto-detect)",
    )
    parser.add_argument(
        "--headers-dir",
        default=None,
        help="[SC] header directory " "(default: kernel/include/uapi/linux/airymax)",
    )
    parser.add_argument(
        "--kernel-dir",
        default=None,
        help="kernel source root for -I flags (default: kernel)",
    )
    parser.add_argument(
        "--cc",
        default=None,
        help="C compiler (default: $CC or gcc)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="CI mode: minimal output, exit code only (0=pass, 1=fail)",
    )
    return parser


def _fail(msg):
    """输入面错误统一走退出码 2（与环境不可用同义，区别于判定失败）。"""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _dirs(args):
    """定出契约头目录与内核源码根；两者缺失都无法给出判定，直接收场。"""
    repo_root = _repo_root(args)
    headers_dir = _pick(args.headers_dir, repo_root / DEFAULT_HEADERS_REL)
    kernel_dir = _pick(args.kernel_dir, repo_root / DEFAULT_KERNEL_REL)
    _need_dir(headers_dir, "headers dir")
    _need_dir(kernel_dir, "kernel dir")
    return headers_dir, kernel_dir


def _repo_root(args):
    """显式 --repo-root 优先，否则向上探测；找不到即收场。"""
    root = Path(args.repo_root) if args.repo_root else find_repo_root()
    _need_dir(root, "repo root")
    return root


def _pick(given, default):
    """显式目录优先于仓库内缺省相对路径。"""
    return Path(given) if given else default


def _need_dir(path, what):
    """目录不存在就无法给出判定，按既有失败通道收场。"""
    if not path.is_dir():
        _fail(f"{what} not found: {path}")


def _pick_cc(arg):
    """编译器按「显式参数 → 环境变量 → gcc」定，找到才算数。"""
    cc = arg or os.environ.get("CC") or "gcc"
    if shutil.which(cc) is None:
        _fail(f"compiler not found: {cc}")
    return cc


def _run_checks(headers_dir, kernel_dir, cc):
    """跑满五路判定：多路编译、类型尺寸、布局、回退面与 ABI。"""
    header_files = sorted(headers_dir.glob("*.h"))
    if not header_files:
        _fail(f"no [SC] headers in {headers_dir}")

    include_flags = inc_flags(kernel_dir)
    results = [tri_check(cc, include_flags, header_files)]
    with tempfile.TemporaryDirectory(prefix="sc-tri-compile-") as workdir:
        per_path, probe_failures = collect_probe(cc, include_flags, workdir)
    results.append(type_size_check(per_path, probe_failures))
    results.append(layout_check(per_path, probe_failures))
    results.append(fallback_check(per_path))
    err_header = (headers_dir / DSL_ERROR_HEADER).read_text(encoding="utf-8")
    results.append(dsl_err_check(per_path, err_header))

    syscall_sources, syscall_failures = load_sc_src(headers_dir)
    results.append(check_sc_abi(syscall_sources, syscall_failures))
    return results, header_files


def main(argv=None):
    args = _build_parser().parse_args(argv)
    headers_dir, kernel_dir = _dirs(args)
    cc = _pick_cc(args.cc)
    results, header_files = _run_checks(headers_dir, kernel_dir, cc)

    sources = [
        ("headers-dir", str(headers_dir)),
        ("kernel-dir", str(kernel_dir)),
    ]
    print_report(results, header_files, cc, sources, args.check)

    failed = sum(1 for r in results if not r.passed)
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
