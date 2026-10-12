#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
sc_tri_compile.py — [SC] header three-way compile & layout checker

Validates that every [SC] shared contract header under
kernel/include/uapi/linux/airymax/ compiles in all three type-bridge paths
and that the fixed-width types and struct layouts are identical across those
paths (OS-IRON-014, 50-engineering-standards/11-sc-header-type-bridging.md).

Compile paths (design §5.1):
  1. kernel-space      gcc -std=gnu11 -D__KERNEL__ -D__linux__
  2. linux-user-space  gcc -std=gnu11 -D__linux__
  3. third-party       gcc -std=c99   -U__linux__

Checks (design §5.3):
  * three-way compile — every header compiles under all three paths
  * type-size consistency — __u8/__u16/__u32/__u64/__s8/... identical
  * field-offset consistency — struct sizes and field offsets identical

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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ─── Constants ─────────────────────────────────────────────────────────────

# Type-bridge compile paths: (label, compiler flags). Design §5.1.
COMPILE_PATHS = [
    ("kernel-space", ["-std=gnu11", "-D__KERNEL__", "-D__linux__"]),
    ("linux-user-space", ["-std=gnu11", "-D__linux__"]),
    ("third-party", ["-std=c99", "-U__linux__"]),
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
    "ipc.h",
    "log_types.h",
    "sched.h",
    "bpf_struct_ops.h",
]


# ─── Helpers ───────────────────────────────────────────────────────────────


def find_repo_root():
    """Auto-detect agent-linux repo root from script location.

    Script lives at <repo_root>/tools/sc-tri-compile/sc_tri_compile.py.
    """
    return Path(__file__).resolve().parent.parent.parent


def build_include_flags(kernel_dir):
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


# ─── Check Results ─────────────────────────────────────────────────────────


class CheckResult:
    """Result of a single [SC] tri-compile check."""

    def __init__(self, name, passed, details=""):
        self.name = name
        self.passed = passed
        self.details = details


# ─── Checks ────────────────────────────────────────────────────────────────


def tri_compile_check(cc, include_flags, header_files):
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
    if failures:
        return CheckResult(
            "三路编译通过 (内核态 / Linux 用户态 / 第三方)",
            False,
            "\n".join(failures),
        )
    return CheckResult(
        "三路编译通过 (内核态 / Linux 用户态 / 第三方)",
        True,
        f"{len(header_files)} 个头文件 × {len(COMPILE_PATHS)} 路径 " f"= {total} 次编译全部通过",
    )


def build_probe_source():
    """Generate a C program printing type sizes and struct layouts."""
    lines = ["#include <stdio.h>"]
    lines += [f"#include <linux/airymax/{h}>" for h in PROBE_HEADERS]
    lines += ["", "int main(void)", "{"]
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
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]
        if kind in ("T", "S") and len(parts) == 3:
            values[(kind, parts[1], "")] = int(parts[2])
        elif kind == "O" and len(parts) == 4:
            values[(kind, parts[1], parts[2])] = int(parts[3])
    return values


def collect_probe(cc, include_flags, workdir):
    """Compile and run the layout probe under all three paths.

    Returns (per_path, failures): per_path maps the path label to its parsed
    values; failures lists paths whose probe failed to compile or run.
    """
    src = Path(workdir) / "sc_probe.c"
    src.write_text(build_probe_source(), encoding="utf-8")
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


def check_consistency(item, expected, per_path, key, failures):
    """Record a failure unless every path reports `expected` for `key`."""
    values = {label: vals.get(key) for label, vals in per_path.items()}
    missing = [label for label, value in values.items() if value is None]
    if missing:
        failures.append(f"  {item}: 探针未返回 ({', '.join(missing)})")
        return
    if set(values.values()) != {expected}:
        shown = ", ".join(f"{k}={v}" for k, v in values.items())
        failures.append(f"  {item}: 期望 {expected}, 实测 {shown}")


def type_size_check(per_path, probe_failures):
    """Check fixed-width type sizes are identical across the three paths."""
    name = "类型大小三路一致 (__u8..__s64)"
    if probe_failures:
        return CheckResult(name, False, "\n".join(probe_failures))
    failures = []
    for tname, expected in TYPE_SIZES.items():
        check_consistency(tname, expected, per_path, ("T", tname, ""), failures)
    if failures:
        return CheckResult(name, False, "\n".join(failures))
    return CheckResult(name, True, f"{len(TYPE_SIZES)} 个定宽类型三路大小一致")


def layout_check(per_path, probe_failures):
    """Check struct sizes and field offsets are identical across paths."""
    name = "结构体字段偏移三路一致"
    if probe_failures:
        return CheckResult(name, False, "\n".join(probe_failures))
    failures = []
    field_count = 0
    for struct, size, fields in LAYOUT:
        check_consistency(f"{struct} sizeof", size, per_path, ("S", struct, ""), failures)
        for field, off in fields:
            field_count += 1
            check_consistency(
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
        f"{len(LAYOUT)} 个结构体、{field_count} 个字段偏移三路一致",
    )


# ─── Report ────────────────────────────────────────────────────────────────


def print_report(results, header_files, cc, sources, check_mode):
    """Print the tri-compile check report."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    if check_mode:
        if failed == 0:
            print(f"SC TRI-COMPILE: PASS ({passed}/{total} checks)")
        else:
            print(f"SC TRI-COMPILE: FAIL ({passed}/{total} checks, " f"{failed} failed)")
            for r in results:
                if not r.passed:
                    print(f"  FAIL: {r.name}")
                    for line in r.details.splitlines():
                        print(f"    {line}")
        return

    print("=" * 72)
    print("agent-linux [SC] Tri-Compile & Layout Check Report")
    print("OS-IRON-014: [SC] 共享契约头三路编译与二进制布局一致")
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

    for r in results:
        marker = "[+]" if r.passed else "[-]"
        status = "PASS" if r.passed else "FAIL"
        print(f"{marker} {status}: {r.name}")
        if r.details:
            for line in r.details.splitlines():
                print(f"      {line}")
        print()

    print("-" * 72)
    if failed == 0:
        print("RESULT: PASS — all [SC] tri-compile checks passed (OS-IRON-014)")
    else:
        print(f"RESULT: FAIL — {failed} check(s) failed (OS-IRON-014 violation)")
    print("-" * 72)


# ─── Main ──────────────────────────────────────────────────────────────────


def main():
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
    args = parser.parse_args()

    repo_root = Path(args.repo_root) if args.repo_root else find_repo_root()
    if not repo_root.is_dir():
        print(f"ERROR: repo root not found: {repo_root}", file=sys.stderr)
        sys.exit(2)

    headers_dir = Path(args.headers_dir) if args.headers_dir else repo_root / DEFAULT_HEADERS_REL
    kernel_dir = Path(args.kernel_dir) if args.kernel_dir else repo_root / DEFAULT_KERNEL_REL
    if not headers_dir.is_dir():
        print(f"ERROR: headers dir not found: {headers_dir}", file=sys.stderr)
        sys.exit(2)
    if not kernel_dir.is_dir():
        print(f"ERROR: kernel dir not found: {kernel_dir}", file=sys.stderr)
        sys.exit(2)

    cc = args.cc or os.environ.get("CC") or "gcc"
    if shutil.which(cc) is None:
        print(f"ERROR: compiler not found: {cc}", file=sys.stderr)
        sys.exit(2)

    header_files = sorted(headers_dir.glob("*.h"))
    if not header_files:
        print(f"ERROR: no [SC] headers in {headers_dir}", file=sys.stderr)
        sys.exit(2)

    include_flags = build_include_flags(kernel_dir)

    results = [tri_compile_check(cc, include_flags, header_files)]
    with tempfile.TemporaryDirectory(prefix="sc-tri-compile-") as workdir:
        per_path, probe_failures = collect_probe(cc, include_flags, workdir)
    results.append(type_size_check(per_path, probe_failures))
    results.append(layout_check(per_path, probe_failures))

    sources = [
        ("headers-dir", str(headers_dir)),
        ("kernel-dir", str(kernel_dir)),
    ]
    print_report(results, header_files, cc, sources, args.check)

    failed = sum(1 for r in results if not r.passed)
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
