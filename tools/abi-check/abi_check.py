#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
abi_check.py — agent-linux syscall ABI stability checker

Validates syscall number ABI stability (OS-IRON-001: 用户空间 ABI 永不破坏)
by cross-checking sources against a baseline snapshot:

  1. SSoT document  — docs/docs-linux/140-application-development/07-syscall-registry.md
     Derived fallback: tools/abi-check/contract/syscall_registry.snapshot.json
  2. UAPI header     — kernel/include/uapi/asm-generic/unistd.h
  3. Kernel entry tbl — kernel/arch/x86/entry/syscalls/syscall_64.tbl
  4. [SC] contract hdr — kernel/include/uapi/linux/airymax/syscalls.h
  5. sw_64 entry tbl  — kernel/arch/sw_64/kernel/syscalls/syscall.tbl
  6. asm-generic wire — arch/{riscv,loongarch} UAPI include + table form
                        (+ include/linux/syscalls.h bare prototypes)

The SSoT document is the single authoritative registry, but it lives in the
docs-linux submodule which is not mirrored publicly (the GitHub mirror only
carries agent-linux). When the document is present it is used verbatim and a
drift check verifies the derived contract snapshot still matches it; when it
is absent the snapshot becomes the SSoT source for this run and the report
states that origin explicitly (the snapshot never becomes a second authority
— it is a derived artifact refreshed with --update-snapshot).

Usage:
    python3 tools/abi-check/abi_check.py [options]

Options:
    --repo-root PATH    agent-linux repo root (default: auto-detect)
    --ssot-path PATH    SSoT document path (default: auto)
    --snapshot-path PATH  contract snapshot path (default: auto)
    --unistd-path PATH  unistd.h path (default: auto)
    --tbl-path PATH     syscall_64.tbl path (default: auto)
    --sw64-tbl-path PATH  sw_64 syscall.tbl path (default: auto)
    --sc-header-path PATH  [SC] contract header path (default: auto)
    --baseline PATH     baseline JSON path (default: tools/abi-check/abi_baseline.json)
    --no-baseline       skip baseline comparison
    --update-snapshot   regenerate the contract snapshot from the SSoT and exit
    --require-ssot      fail (exit 2) when the SSoT document is unavailable
    --check             CI mode: minimal output, exit code only (0=pass, 1=fail)

Exit codes:
    0 — all ABI checks passed
    1 — one or more ABI checks failed
    2 — input/parse error (file not found, invalid baseline JSON, etc.)
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ─── Constants ─────────────────────────────────────────────────────────────

EXPECTED_BASE = 548
EXPECTED_NR_SYSCALLS = 572
AIRY_RANGE_START = 548
AIRY_RANGE_END = 571
X32_FORBIDDEN_START = 512
X32_FORBIDDEN_END = 547

# Reserved slot segment (552-571): 20 slots, SLOTS_MAX = 4 core + 20 = 24
EXPECTED_RESERVED_START = 552
EXPECTED_RESERVED_END = 571
EXPECTED_RESERVED_COUNT = 20
EXPECTED_SLOTS_MAX = 24

# Reserved slot naming: unistd.h / syscall_64.tbl use airy_reserved_<internal>,
# where <internal> is the slot index counting from AIRY_SYS_CALL (548).
RESERVED_NAME_PREFIX = "airy_reserved_"
RESERVED_TBL_ENTRY = "sys_ni_syscall"

CORE_SYSCALLS = [
    ("airy_sys_call", "AIRY_SYS_CALL", 548),
    ("airy_sys_rovol_ctl", "AIRY_SYS_ROVOL_CTL", 549),
    ("airy_sys_sched_ctl", "AIRY_SYS_SCHED_CTL", 550),
    ("airy_sys_clt_notify", "AIRY_SYS_CLT_NOTIFY", 551),
]

# [SC] contract header macros: core (macro -> C name) and reserved markers.
SC_CORE_MACRO_TO_NAME = {macro: name for name, macro, _num in CORE_SYSCALLS}
SC_RESERVED_MACROS = ("AIRY_SYS_RESERVED_BASE",
                      "AIRY_SYS_RESERVED_END",
                      "AIRY_SYS_SLOTS_MAX")

# Default paths relative to agent-linux repo root
DEFAULT_SSOT_REL = os.path.join("..", "docs", "docs-linux",
                                "140-application-development",
                                "07-syscall-registry.md")
DEFAULT_UNISTD_REL = os.path.join("kernel", "include", "uapi",
                                  "asm-generic", "unistd.h")
DEFAULT_TBL_REL = os.path.join("kernel", "arch", "x86", "entry",
                               "syscalls", "syscall_64.tbl")
DEFAULT_SW64_TBL_REL = os.path.join("kernel", "arch", "sw_64", "kernel",
                                    "syscalls", "syscall.tbl")
DEFAULT_SC_HEADER_REL = os.path.join("kernel", "include", "uapi", "linux",
                                     "airymax", "syscalls.h")
DEFAULT_BASELINE_REL = os.path.join("tools", "abi-check", "abi_baseline.json")
DEFAULT_SNAPSHOT_REL = os.path.join("tools", "abi-check", "contract",
                                    "syscall_registry.snapshot.json")

# asm-generic architectures share the UAPI numbering, so their tables pick up
# slots 548-571 without naming Airymax. Pin the include chain and expansion
# forms: a silent change binds those numbers to the wrong handler.
DEFAULT_RISCV_UAPI_REL = os.path.join("kernel", "arch", "riscv", "include",
                                     "uapi", "asm", "unistd.h")
DEFAULT_RISCV_TBL_REL = os.path.join("kernel", "arch", "riscv", "kernel",
                                    "syscall_table.c")
DEFAULT_LOONG_UAPI_REL = os.path.join("kernel", "arch", "loongarch", "include",
                                      "uapi", "asm", "unistd.h")
DEFAULT_LOONG_TBL_REL = os.path.join("kernel", "arch", "loongarch", "kernel",
                                     "syscall.c")
DEFAULT_SYS_H_REL = os.path.join("kernel", "include", "linux", "syscalls.h")

GENERIC_UAPI_INCLUDE = "#include <asm-generic/unistd.h>"
RISCV_TBL_INCLUDE = "#include <asm/unistd.h>"
RISCV_EXPAND_RE = re.compile(
    r"#define __SYSCALL\(nr, call\)\s+\[nr\] = __riscv_##call,")
RISCV_TABLE_DECL = "void * const sys_call_table[__NR_syscalls]"
LOONG_EXPAND_RE = re.compile(
    r"#define __SYSCALL\(nr, call\)\s+\[nr\] = \(call\),")
LOONG_TABLE_DECL = "void *sys_call_table[__NR_syscalls]"

# Derived contract snapshot schema (bump only on incompatible shape changes)
SNAPSHOT_SCHEMA_VERSION = "1.0.0"
SNAPSHOT_GENERATED_BY = "tools/abi-check/abi_check.py --update-snapshot"


# ─── Helpers ───────────────────────────────────────────────────────────────


def find_repo_root():
    """Auto-detect agent-linux repo root from script location.

    Script lives at <repo_root>/tools/abi-check/abi_check.py.
    """
    return Path(__file__).resolve().parent.parent.parent


def read_file(path):
    """Read file content. Exit with code 2 on error."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(2)
    except OSError as exc:
        print(f"ERROR: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def read_if_exists(path):
    """Read an optional source file; return None when it is unavailable."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ─── Parsers ───────────────────────────────────────────────────────────────

def parse_ssot(text):
    """Parse SSoT markdown to extract authoritative syscall definitions.

    Extracts from:
      - Section 3.1 table: | <num> | `MACRO` | `name` | ... |
      - Section 3.2 table: | <start>-<end> | `AIRY_SYS_RESERVED_*` ~ ... |
      - Section 6.2 UAPI template: #define AIRY_SYS_BASE <num>

    Returns dict: {version, linux_base, syscalls, reserved_range}
      syscalls: list of {name, macro, linux_number}
    """
    result = {
        "version": None,
        "linux_base": None,
        "syscalls": [],
        "reserved_range": None,
    }

    # Extract document version
    m = re.search(r"\*\*文档版本\*\*[：:]\s*(v[\d.]+)", text)
    if m:
        result["version"] = m.group(1)

    # Extract AIRY_SYS_BASE from UAPI template (section 6.2)
    m = re.search(r"#define\s+AIRY_SYS_BASE\s+(\d+)", text)
    if m:
        result["linux_base"] = int(m.group(1))

    # Extract core syscalls from section 3.1 table:
    #   | 548 | `AIRY_SYS_CALL` | `airy_sys_call` | ... |
    seen = set()
    for m in re.finditer(
        r"\|\s*(\d+)\s*\|\s*`([A-Z_]+)`\s*\|\s*`(airy_sys_\w+)`\s*\|",
        text,
    ):
        num = int(m.group(1))
        macro = m.group(2)
        name = m.group(3)
        if name in seen:
            continue
        seen.add(name)
        result["syscalls"].append({
            "name": name,
            "macro": macro,
            "linux_number": num,
        })

    # Fallback: if AIRY_SYS_BASE not found via define, use min syscall number
    if result["linux_base"] is None and result["syscalls"]:
        result["linux_base"] = min(s["linux_number"] for s in result["syscalls"])

    # Extract reserved range from section 3.2:
    #   | 552-571 | `AIRY_SYS_RESERVED_BASE` ~ `AIRY_SYS_RESERVED_END` | ...
    m = re.search(
        r"\|\s*(\d+)-(\d+)\s*\|\s*`AIRY_SYS_RESERVED_\w+`"
        r"\s*~\s*`AIRY_SYS_RESERVED_\w+`",
        text,
    )
    if m:
        result["reserved_range"] = [int(m.group(1)), int(m.group(2))]

    return result


def parse_unistd(text):
    """Parse unistd.h to extract actual syscall number definitions.

    Returns dict: {syscalls, reserved, nr_syscalls}
      syscalls: list of {name, linux_number}  (airy_sys_* core entries)
      reserved: list of {name, linux_number}  (airy_reserved_* slot entries)
      nr_syscalls: int or None
    """
    result = {"syscalls": [], "reserved": [], "nr_syscalls": None}

    # Match: #define __NR_airy_sys_call 548
    for m in re.finditer(
        r"#define\s+__NR_(airy_sys_\w+)\s+(\d+)", text,
    ):
        result["syscalls"].append({
            "name": m.group(1),
            "linux_number": int(m.group(2)),
        })

    # Match: #define __NR_airy_reserved_4 552
    for m in re.finditer(
        r"#define\s+__NR_(airy_reserved_\d+)\s+(\d+)", text,
    ):
        result["reserved"].append({
            "name": m.group(1),
            "linux_number": int(m.group(2)),
        })

    # Match: #define __NR_syscalls 572
    m = re.search(r"#define\s+__NR_syscalls\s+(\d+)", text)
    if m:
        result["nr_syscalls"] = int(m.group(1))

    return result


def parse_sc_header(text):
    """Parse the [SC] shared contract header (kernel/.../airymax/syscalls.h).

    Returns dict: {core_macros, reserved}
      core_macros: {macro: number}  (4 core AIRY_SYS_* macros)
      reserved: {AIRY_SYS_RESERVED_BASE / _END / AIRY_SYS_SLOTS_MAX: number}
    """
    result = {"core_macros": {}, "reserved": {}}

    for m in re.finditer(r"#define\s+(AIRY_SYS_[A-Z_]+)\s+(\d+)\b", text):
        macro = m.group(1)
        num = int(m.group(2))
        if macro in SC_RESERVED_MACROS:
            result["reserved"][macro] = num
        else:
            result["core_macros"][macro] = num

    return result


def parse_syscall_tbl(text):
    """Parse syscall_64.tbl to extract registered syscall entries.

    Returns dict: {syscalls, reserved}
      syscalls: list of {name, linux_number, abi, entry}  (airy_sys_* core)
      reserved: list of {name, linux_number, abi, entry}  (airy_reserved_* slots)
    """
    result = {"syscalls": [], "reserved": []}

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Format: <number> <abi> <name> <entry_point>
        m = re.match(r"(\d+)\s+(\S+)\s+(\S+)\s+(\S+)", stripped)
        if not m:
            continue
        name = m.group(3)
        entry = {
            "name": name,
            "linux_number": int(m.group(1)),
            "abi": m.group(2),
            "entry": m.group(4),
        }
        if name.startswith("airy_sys_"):
            result["syscalls"].append(entry)
        elif name.startswith(RESERVED_NAME_PREFIX):
            result["reserved"].append(entry)

    return result


def load_baseline(path):
    """Load baseline JSON. Returns None if file does not exist."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in baseline {path}: {exc}",
              file=sys.stderr)
        sys.exit(2)


# ─── Derived contract snapshot ─────────────────────────────────────────────

def compute_sha256(path):
    """Return the SHA-256 hex digest of a file (streamed, UTF-8 agnostic)."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_source_commit(path):
    """Best-effort detection of the git commit owning `path`.

    Returns the commit hash, or None when the path is not inside a git work
    tree (e.g. the docs submodule is not checked out).
    """
    target = Path(path).resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(target.parent), "log", "-1", "--format=%H",
             "--", target.name],
            capture_output=True, text=True, check=False)
    except OSError:
        return None
    commit = proc.stdout.strip()
    return commit if proc.returncode == 0 and commit else None


def relative_source_path(ssot_path, repo_root):
    """Render the SSoT path relative to the repo root, POSIX-separated."""
    try:
        rel = os.path.relpath(os.path.abspath(str(ssot_path)),
                              os.path.abspath(str(repo_root)))
    except ValueError:
        rel = str(ssot_path)
    return rel.replace(os.sep, "/")


def build_snapshot(ssot, ssot_path, repo_root, commit=None):
    """Derive a machine-readable contract snapshot from the authoritative SSoT."""
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source": {
            "repo": "docs-linux",
            "path": relative_source_path(ssot_path, repo_root),
            "commit": commit or "unknown",
            "sha256": compute_sha256(ssot_path),
            "doc_version": ssot.get("version"),
        },
        "generated_by": SNAPSHOT_GENERATED_BY,
        "linux_base": ssot.get("linux_base"),
        "syscalls": [dict(entry) for entry in ssot.get("syscalls", [])],
        "reserved_range": (list(ssot["reserved_range"])
                           if ssot.get("reserved_range") else None),
    }


def write_snapshot(path, snapshot):
    """Write the snapshot deterministically (stable order, LF, trailing NL)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_snapshot(path):
    """Load the derived contract snapshot. Returns None if absent."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in snapshot {path}: {exc}",
              file=sys.stderr)
        sys.exit(2)


def snapshot_to_ssot(snapshot):
    """Adapt a contract snapshot to the in-memory SSoT shape.

    Used only when the authoritative SSoT document is unavailable; the
    resulting dict is structurally identical to parse_ssot() output so the
    downstream checks stay source-agnostic.
    """
    source = snapshot.get("source", {})
    return {
        "version": source.get("doc_version"),
        "linux_base": snapshot.get("linux_base"),
        "syscalls": [dict(entry) for entry in snapshot.get("syscalls", [])],
        "reserved_range": (list(snapshot["reserved_range"])
                           if snapshot.get("reserved_range") else None),
    }


def describe_snapshot(snapshot):
    """Render a one-line provenance summary of a contract snapshot."""
    source = snapshot.get("source", {})
    commit = str(source.get("commit", "unknown"))
    digest = str(source.get("sha256", "unknown"))
    return (f"源 {source.get('repo', '?')}@{commit[:12]}, "
            f"sha256={digest[:12]}…, "
            f"doc_version={source.get('doc_version', '?')}")


# ─── Check Results ─────────────────────────────────────────────────────────

class CheckResult:
    """Result of a single ABI check."""

    def __init__(self, name, passed, details=""):
        self.name = name
        self.passed = passed
        self.details = details


# ─── Checks ────────────────────────────────────────────────────────────────

def check_three_way_consistency(ssot, unistd, tbl):
    """Check 4 core syscalls are consistent across SSoT / unistd.h / tbl."""
    failures = []

    ssot_map = {s["name"]: s["linux_number"] for s in ssot["syscalls"]}
    unistd_map = {s["name"]: s["linux_number"] for s in unistd["syscalls"]}
    tbl_map = {s["name"]: s["linux_number"] for s in tbl["syscalls"]}

    for name, _macro, _expected in CORE_SYSCALLS:
        ssot_num = ssot_map.get(name)
        unistd_num = unistd_map.get(name)
        tbl_num = tbl_map.get(name)

        if ssot_num is None:
            failures.append(f"  {name}: 在 SSoT 中缺失")
        if unistd_num is None:
            failures.append(f"  {name}: 在 unistd.h 中缺失")
        if tbl_num is None:
            failures.append(f"  {name}: 在 syscall_64.tbl 中缺失")

        nums = [n for n in (ssot_num, unistd_num, tbl_num) if n is not None]
        if len(set(nums)) > 1:
            failures.append(
                f"  {name}: SSoT={ssot_num}, unistd={unistd_num}, "
                f"tbl={tbl_num} (不一致)"
            )

    if failures:
        return CheckResult(
            "三方一致性 (SSoT / unistd.h / syscall_64.tbl)",
            False, "\n".join(failures))

    summary = ", ".join(
        f"{n}={ssot_map[n]}" for n, _, _ in CORE_SYSCALLS)
    return CheckResult(
        "三方一致性 (SSoT / unistd.h / syscall_64.tbl)",
        True, f"4 核心 syscall 编号三方一致: {summary}")


def check_base_number(ssot, unistd, tbl):
    """Check syscall base number is 548."""
    failures = []

    if ssot["linux_base"] != EXPECTED_BASE:
        failures.append(
            f"  SSoT AIRY_SYS_BASE = {ssot['linux_base']} "
            f"(期望 {EXPECTED_BASE})")

    # Verify no airy syscall has a number below 548
    for source_name, syscalls in [
        ("SSoT", ssot["syscalls"]),
        ("unistd.h", unistd["syscalls"]),
        ("syscall_64.tbl", tbl["syscalls"]),
    ]:
        for s in syscalls:
            if s["linux_number"] < EXPECTED_BASE:
                failures.append(
                    f"  {source_name}: {s['name']} = {s['linux_number']} "
                    f"< {EXPECTED_BASE}")

    if failures:
        return CheckResult("编号起点为 548", False, "\n".join(failures))
    return CheckResult(
        "编号起点为 548", True,
        f"AIRY_SYS_BASE = {ssot['linux_base']}, "
        f"所有 airy syscall >= {EXPECTED_BASE}")


def check_range_no_conflict(ssot, unistd, tbl):
    """Check no duplicate numbers and all within 548-571."""
    failures = []

    for source_name, syscalls in [
        ("SSoT", ssot["syscalls"]),
        ("unistd.h", unistd["syscalls"]),
        ("syscall_64.tbl", tbl["syscalls"]),
    ]:
        # Duplicate number check
        num_to_names = {}
        for s in syscalls:
            num_to_names.setdefault(s["linux_number"], []).append(s["name"])
        for num, names in sorted(num_to_names.items()):
            if len(names) > 1:
                failures.append(
                    f"  {source_name}: 编号 {num} 被重复使用: "
                    f"{', '.join(names)}")

        # Range check
        for s in syscalls:
            if not (AIRY_RANGE_START <= s["linux_number"] <= AIRY_RANGE_END):
                failures.append(
                    f"  {source_name}: {s['name']} = {s['linux_number']} "
                    f"超出范围 [{AIRY_RANGE_START}, {AIRY_RANGE_END}]")

    if failures:
        return CheckResult("编号范围 548-571 内无冲突", False,
                           "\n".join(failures))
    return CheckResult(
        "编号范围 548-571 内无冲突", True,
        f"所有 airy syscall 编号唯一且在 "
        f"[{AIRY_RANGE_START}, {AIRY_RANGE_END}] 范围内")


def check_nr_syscalls(unistd):
    """Check __NR_syscalls value is 572 (highest number 571 + 1)."""
    nr = unistd["nr_syscalls"]
    if nr is None:
        return CheckResult(
            "__NR_syscalls 值正确", False,
            "unistd.h 中未找到 __NR_syscalls 定义")
    if nr != EXPECTED_NR_SYSCALLS:
        return CheckResult(
            "__NR_syscalls 值正确", False,
            f"__NR_syscalls = {nr} (期望 {EXPECTED_NR_SYSCALLS})")
    return CheckResult(
        "__NR_syscalls 值正确", True,
        f"__NR_syscalls = {nr} (== {EXPECTED_NR_SYSCALLS})")


def check_x32_forbidden(unistd, tbl):
    """Check no airy syscall in 512-547 (x32 forbidden zone)."""
    failures = []

    for source_name, syscalls in [
        ("unistd.h", unistd["syscalls"]),
        ("syscall_64.tbl", tbl["syscalls"]),
    ]:
        for s in syscalls:
            if X32_FORBIDDEN_START <= s["linux_number"] <= X32_FORBIDDEN_END:
                failures.append(
                    f"  {source_name}: {s['name']} = {s['linux_number']} "
                    f"在 x32 禁止区域 "
                    f"[{X32_FORBIDDEN_START}, {X32_FORBIDDEN_END}]")

    if failures:
        return CheckResult(
            "无 512-547 区域的 syscall (x32 禁止区域)", False,
            "\n".join(failures))
    return CheckResult(
        "无 512-547 区域的 syscall (x32 禁止区域)", True,
        f"无 airy syscall 落入 x32 禁止区域 "
        f"[{X32_FORBIDDEN_START}, {X32_FORBIDDEN_END}]")


def check_sc_header_consistency(sc_header, ssot, unistd, tbl):
    """Check the [SC] contract header agrees with SSoT / unistd.h / tbl."""
    failures = []

    # 4 core macros present, equal to expected numbering and to SSoT
    ssot_map = {s["name"]: s["linux_number"] for s in ssot["syscalls"]}
    for name, macro, expected in CORE_SYSCALLS:
        num = sc_header["core_macros"].get(macro)
        if num is None:
            failures.append(f"  {macro}: [SC] 契约头中缺失")
            continue
        if num != expected:
            failures.append(f"  {macro} = {num} (期望 {expected})")
        ssot_num = ssot_map.get(name)
        if ssot_num is not None and ssot_num != num:
            failures.append(
                f"  {macro}: [SC]={num}, SSoT={ssot_num} (不一致)")

    # Reserved marker macros
    res = sc_header["reserved"]
    base = res.get("AIRY_SYS_RESERVED_BASE")
    end = res.get("AIRY_SYS_RESERVED_END")
    slots = res.get("AIRY_SYS_SLOTS_MAX")

    if base is None:
        failures.append("  AIRY_SYS_RESERVED_BASE: [SC] 契约头中缺失")
    elif base != EXPECTED_RESERVED_START:
        failures.append(
            f"  AIRY_SYS_RESERVED_BASE = {base} "
            f"(期望 {EXPECTED_RESERVED_START})")
    if end is None:
        failures.append("  AIRY_SYS_RESERVED_END: [SC] 契约头中缺失")
    elif end != EXPECTED_RESERVED_END:
        failures.append(
            f"  AIRY_SYS_RESERVED_END = {end} "
            f"(期望 {EXPECTED_RESERVED_END})")
    if slots is None:
        failures.append("  AIRY_SYS_SLOTS_MAX: [SC] 契约头中缺失")
    elif slots != EXPECTED_SLOTS_MAX:
        failures.append(
            f"  AIRY_SYS_SLOTS_MAX = {slots} "
            f"(期望 {EXPECTED_SLOTS_MAX})")

    if base is not None and end is not None and slots is not None:
        derived = end - EXPECTED_BASE + 1
        if slots != derived:
            failures.append(
                f"  AIRY_SYS_SLOTS_MAX={slots} != "
                f"RESERVED_END-RESERVED_BASE+1 (={derived})")

    # Cross-check reserved markers against SSoT reserved_range
    ssot_range = ssot["reserved_range"]
    if base is not None and end is not None:
        if ssot_range is None:
            failures.append(
                "  SSoT 预留段缺失，无法与 [SC] 契约头交叉校验")
        elif [base, end] != ssot_range:
            failures.append(
                f"  预留段: [SC]=[{base}, {end}], SSoT={ssot_range} "
                f"(不一致)")

    # Cross-check core numbering against unistd.h / tbl
    unistd_map = {s["name"]: s["linux_number"] for s in unistd["syscalls"]}
    tbl_map = {s["name"]: s["linux_number"] for s in tbl["syscalls"]}
    for name, macro, _expected in CORE_SYSCALLS:
        num = sc_header["core_macros"].get(macro)
        if num is None:
            continue
        for src, src_map in (("unistd.h", unistd_map),
                             ("syscall_64.tbl", tbl_map)):
            other = src_map.get(name)
            if other is not None and other != num:
                failures.append(
                    f"  {macro}: [SC]={num}, {src}={other} (不一致)")

    if failures:
        return CheckResult("[SC] 契约头一致 (syscalls.h)", False,
                           "\n".join(failures))
    return CheckResult(
        "[SC] 契约头一致 (syscalls.h)", True,
        f"4 核心宏 (548-551) + 预留标记 (BASE={base}, END={end}, "
        f"SLOTS_MAX={slots}) 与 SSoT/unistd.h/tbl 一致")


def reserved_slot_issues(reserved, label):
    """Diagnose reserved slot coverage, naming and entry point."""
    issues = []
    seen = {}
    for s in reserved:
        num = s["linux_number"]
        if num in seen:
            issues.append(
                f"  {label}: {num} 重复登记 "
                f"({seen[num]['name']} / {s['name']})")
            continue
        seen[num] = s

    expected_nums = list(range(EXPECTED_RESERVED_START,
                              EXPECTED_RESERVED_END + 1))
    missing = [n for n in expected_nums if n not in seen]
    extra = [n for n in sorted(seen) if n not in expected_nums]
    if missing:
        issues.append(f"  {label}: 缺失预留槽 {missing}")
    if extra:
        issues.append(f"  {label}: 越界预留槽 {extra}")

    for num in sorted(seen):
        s = seen[num]
        expected_name = f"{RESERVED_NAME_PREFIX}{num - EXPECTED_BASE}"
        if s["name"] != expected_name:
            issues.append(
                f"  {label}: {num} 命名 {s['name']} "
                f"(期望 {expected_name})")
        entry = s.get("entry")
        if entry is not None and entry != RESERVED_TBL_ENTRY:
            issues.append(
                f"  {label}: {num} 入口点 {entry} "
                f"(期望 {RESERVED_TBL_ENTRY})")
    return issues


def check_reserved_slots(unistd, tbl):
    """Check the 20 reserved slots (552-571) are registered consistently."""
    failures = reserved_slot_issues(unistd["reserved"], "unistd.h")
    failures += reserved_slot_issues(tbl["reserved"], "syscall_64.tbl")

    if failures:
        return CheckResult("预留槽登记一致 (552-571)", False,
                           "\n".join(failures))
    return CheckResult(
        "预留槽登记一致 (552-571)", True,
        f"unistd.h 与 syscall_64.tbl 各登记 {EXPECTED_RESERVED_COUNT} 个"
        f"预留槽 (552-571)，命名 airy_reserved_<num-548>，"
        f"入口 sys_ni_syscall")


def tbl_entry_issues(syscalls, reserved, label):
    """Diagnose ABI column and entry point naming of airy tbl entries."""
    issues = []

    for s in syscalls:
        if s["abi"] != "common":
            issues.append(
                f"  {label}: {s['name']} ({s['linux_number']}) "
                f"abi={s['abi']} (期望 common)")
        expected_entry = f"sys_{s['name']}"
        if s["entry"] != expected_entry:
            issues.append(
                f"  {label}: {s['name']} 入口点 {s['entry']} "
                f"(期望 {expected_entry})")

    for s in reserved:
        if s["abi"] != "common":
            issues.append(
                f"  {label}: {s['name']} ({s['linux_number']}) "
                f"abi={s['abi']} (期望 common)")
    return issues


def check_tbl_abi_and_entry(tbl):
    """Check syscall_64.tbl ABI column and entry naming for airy entries."""
    failures = tbl_entry_issues(tbl["syscalls"], tbl["reserved"],
                                "syscall_64.tbl")

    if failures:
        return CheckResult("syscall_64.tbl ABI 列与入口点正确", False,
                           "\n".join(failures))
    total = len(tbl["syscalls"]) + len(tbl["reserved"])
    return CheckResult(
        "syscall_64.tbl ABI 列与入口点正确", True,
        f"{total} 个 airy 条目 abi 均为 common，核心入口 sys_airy_sys_*，"
        f"预留入口 sys_ni_syscall")


def check_sw64_tbl(ssot, sw_tbl):
    """Check sw_64's positional table carries the same 548-571 block.

    sw_64 expands systbls.S straight from syscall.tbl, so a missing or
    misnumbered entry shifts every following slot; static checking is the
    only automated guard on hosts without a sw_64 cross toolchain.
    """
    failures = []
    sw_map = {s["name"]: s["linux_number"] for s in sw_tbl["syscalls"]}
    ssot_map = {s["name"]: s["linux_number"] for s in ssot["syscalls"]}

    for name, _macro, expected in CORE_SYSCALLS:
        num = sw_map.get(name)
        if num is None:
            failures.append(f"  {name}: 在 sw_64 syscall.tbl 中缺失")
        elif num != expected:
            failures.append(f"  {name}: sw_64={num} (期望 {expected})")
        elif ssot_map.get(name) != num:
            failures.append(
                f"  {name}: sw_64={num}, SSoT={ssot_map.get(name)} "
                f"(不一致)")

    failures += reserved_slot_issues(sw_tbl["reserved"],
                                     "sw_64 syscall.tbl")
    failures += tbl_entry_issues(sw_tbl["syscalls"], sw_tbl["reserved"],
                                 "sw_64 syscall.tbl")

    if failures:
        return CheckResult("sw_64 号段 548-571 落位一致", False,
                           "\n".join(failures))
    total = len(sw_tbl["syscalls"]) + len(sw_tbl["reserved"])
    return CheckResult(
        "sw_64 号段 548-571 落位一致", True,
        f"{total} 个条目与 SSoT 同号，核心入口 sys_airy_sys_*，"
        f"预留入口 sys_ni_syscall，abi 均为 common")


def check_arch_wire(repo_root):
    """Verify asm-generic architectures keep the Airymax block wired.

    riscv and loongarch inherit slots 548-571 from the shared UAPI header, so
    nothing in their tables names Airymax explicitly. That makes the wiring
    easy to break without any arch build noticing: dropping the asm-generic
    include, reshaping the __SYSCALL expansion, or losing the riscv wrapper
    prefix would bind an Airymax number to the wrong handler. Pin the exact
    include chain and expansion forms here.
    """
    name = "riscv/loongarch 通用表接线"
    sources = (
        ("riscv uapi unistd.h", DEFAULT_RISCV_UAPI_REL),
        ("riscv syscall_table.c", DEFAULT_RISCV_TBL_REL),
        ("loongarch uapi unistd.h", DEFAULT_LOONG_UAPI_REL),
        ("loongarch syscall.c", DEFAULT_LOONG_TBL_REL),
        ("include/linux/syscalls.h", DEFAULT_SYS_H_REL),
    )

    texts = {}
    failures = []
    for label, rel in sources:
        text = read_if_exists(repo_root / rel)
        if text is None:
            failures.append(f"  缺失: {label} ({rel})")
        texts[label] = text
    if failures:
        return CheckResult(name, False, "\n".join(failures))

    riscv_uapi = texts["riscv uapi unistd.h"]
    riscv_tbl = texts["riscv syscall_table.c"]
    loong_uapi = texts["loongarch uapi unistd.h"]
    loong_tbl = texts["loongarch syscall.c"]
    sys_h = texts["include/linux/syscalls.h"]

    if GENERIC_UAPI_INCLUDE not in riscv_uapi:
        failures.append("  riscv uapi unistd.h 未包含 "
                        "<asm-generic/unistd.h>")
    if GENERIC_UAPI_INCLUDE not in loong_uapi:
        failures.append("  loongarch uapi unistd.h 未包含 "
                        "<asm-generic/unistd.h>")

    if not RISCV_EXPAND_RE.search(riscv_tbl):
        failures.append("  riscv syscall_table.c 未以 "
                        "[nr] = __riscv_##call, 展开")
    includes = riscv_tbl.count(RISCV_TBL_INCLUDE)
    if includes != 2:
        failures.append(f"  riscv syscall_table.c 包含 <asm/unistd.h> "
                        f"{includes} 次 (期望 2: 原型 + 表项)")
    if RISCV_TABLE_DECL not in riscv_tbl:
        failures.append("  riscv syscall_table.c 缺 "
                        "void * const sys_call_table[__NR_syscalls]")

    if not LOONG_EXPAND_RE.search(loong_tbl):
        failures.append("  loongarch syscall.c 未以 [nr] = (call), 展开")
    if LOONG_TABLE_DECL not in loong_tbl:
        failures.append("  loongarch syscall.c 缺 "
                        "void *sys_call_table[__NR_syscalls]")

    for cname, _macro, _num in CORE_SYSCALLS:
        if f"asmlinkage long sys_{cname}(" not in sys_h:
            failures.append(f"  include/linux/syscalls.h 缺 sys_{cname} "
                            f"裸原型")

    if failures:
        return CheckResult(name, False, "\n".join(failures))
    return CheckResult(
        name, True,
        "riscv/loongarch 均包含 <asm-generic/unistd.h>；riscv 以 "
        "__riscv_##call 展开 (两次 include, __NR_syscalls 定界)；"
        "loongarch 以裸符号展开；4 个 sys_airy_sys_* 裸原型齐备")


def check_baseline(ssot, unistd, tbl, baseline):
    """Check current ABI matches baseline snapshot (immutability)."""
    failures = []

    # linux_base
    bl_base = baseline.get("linux_base")
    if bl_base is not None and bl_base != ssot["linux_base"]:
        failures.append(
            f"  linux_base: 当前={ssot['linux_base']}, "
            f"基线={bl_base}")

    # nr_syscalls
    bl_nr = baseline.get("nr_syscalls")
    if bl_nr is not None and bl_nr != unistd["nr_syscalls"]:
        failures.append(
            f"  nr_syscalls: 当前={unistd['nr_syscalls']}, "
            f"基线={bl_nr}")

    # reserved_range
    bl_range = baseline.get("reserved_range")
    if bl_range is not None and bl_range != ssot["reserved_range"]:
        failures.append(
            f"  reserved_range: 当前={ssot['reserved_range']}, "
            f"基线={bl_range}")

    # syscalls — compare name -> linux_number mapping
    bl_map = {s["name"]: s["linux_number"]
              for s in baseline.get("syscalls", [])}
    cur_map = {s["name"]: s["linux_number"] for s in ssot["syscalls"]}

    for name, bl_num in sorted(bl_map.items()):
        cur_num = cur_map.get(name)
        if cur_num is None:
            failures.append(
                f"  syscall '{name}' 在当前 SSoT 中缺失 "
                f"(基线编号={bl_num})")
        elif cur_num != bl_num:
            failures.append(
                f"  syscall '{name}': 当前={cur_num}, "
                f"基线={bl_num} (编号变更!)")

    for name, cur_num in sorted(cur_map.items()):
        if name not in bl_map:
            failures.append(
                f"  syscall '{name}' 在基线中缺失 "
                f"(当前编号={cur_num}, 新增?)")

    if failures:
        return CheckResult("编号与基线快照一致", False,
                           "\n".join(failures))

    bl_ver = baseline.get("version", "?")
    return CheckResult(
        "编号与基线快照一致", True,
        f"当前 ABI 与基线 {bl_ver} 完全一致 "
        f"({len(bl_map)} syscalls, base={bl_base}, nr={bl_nr})")


def check_snapshot_drift(ssot, snapshot, authoritative):
    """Verify the derived contract snapshot still matches the authoritative SSoT.

    `authoritative` is True when the SSoT document was read from ../docs. If
    the document is unavailable (mirror environment) the drift comparison
    cannot be performed; the check then passes but records the snapshot
    provenance so the report never hides which source was used.
    """
    name = "SSoT 契约快照一致"
    if snapshot is None:
        return CheckResult(
            name, False,
            f"  契约快照缺失: {DEFAULT_SNAPSHOT_REL}\n"
            f"  运行 --update-snapshot 从权威 SSoT 重新导出")

    provenance = describe_snapshot(snapshot)
    if not authoritative:
        return CheckResult(
            name, True,
            f"权威 SSoT 文档不可得（镜像未检出 ../docs），"
            f"漂移比对不可用；本次以派生契约快照为 SSoT 来源。{provenance}")

    snap = snapshot_to_ssot(snapshot)
    failures = []

    if snap["linux_base"] != ssot["linux_base"]:
        failures.append(f"  linux_base: 文档={ssot['linux_base']}, "
                        f"快照={snap['linux_base']}")
    if snap["reserved_range"] != ssot["reserved_range"]:
        failures.append(f"  reserved_range: 文档={ssot['reserved_range']}, "
                        f"快照={snap['reserved_range']}")
    if snap["version"] != ssot["version"]:
        failures.append(f"  doc_version: 文档={ssot['version']}, "
                        f"快照={snap['version']}")

    doc_map = {s["name"]: (s["macro"], s["linux_number"])
               for s in ssot["syscalls"]}
    snap_map = {s["name"]: (s["macro"], s["linux_number"])
                for s in snap["syscalls"]}
    for entry in sorted(set(doc_map) | set(snap_map)):
        if entry not in snap_map:
            failures.append(f"  syscall '{entry}' 在快照中缺失 "
                            f"(文档={doc_map[entry]})")
        elif entry not in doc_map:
            failures.append(f"  syscall '{entry}' 在文档中缺失 "
                            f"(快照={snap_map[entry]})")
        elif doc_map[entry] != snap_map[entry]:
            failures.append(f"  syscall '{entry}': 文档={doc_map[entry]}, "
                            f"快照={snap_map[entry]}")

    if failures:
        return CheckResult(
            name, False,
            "  契约快照已漂移，运行 --update-snapshot 刷新:\n"
            + "\n".join(failures))
    return CheckResult(
        name, True,
        f"契约快照与权威 SSoT 完全一致（{len(doc_map)} syscalls）。"
        f"{provenance}")


# ─── Report ────────────────────────────────────────────────────────────────

def print_report(results, ssot, unistd, tbl, sw_tbl, baseline, sources,
                 check_mode, ssot_note=None):
    """Print the ABI check report."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    if check_mode:
        # CI mode: minimal output
        if ssot_note:
            print(ssot_note)
        if failed == 0:
            print(f"ABI CHECK: PASS ({passed}/{total} checks)")
        else:
            print(f"ABI CHECK: FAIL ({passed}/{total} checks, "
                  f"{failed} failed)")
            for r in results:
                if not r.passed:
                    print(f"  FAIL: {r.name}")
                    for line in r.details.splitlines():
                        print(f"    {line}")
        return

    # Verbose mode: full report
    print("=" * 72)
    print("agent-linux ABI Stability Check Report")
    print("OS-IRON-001: 用户空间 ABI 永不破坏")
    print("=" * 72)
    print()
    print("Sources:")
    for label, path in sources:
        print(f"  {label:16s}: {path}")
    print()
    if ssot_note:
        print(ssot_note)
        print()
    print(f"SSoT version:     {ssot.get('version', '?')}")
    bl_ver = baseline.get("version", "N/A") if baseline else "N/A (无基线)"
    print(f"Baseline version: {bl_ver}")
    print()
    print("Syscalls found:")
    print(f"  SSoT:           {len(ssot['syscalls'])}")
    print(f"  unistd.h:       {len(unistd['syscalls'])}")
    print(f"  syscall_64.tbl: {len(tbl['syscalls'])}")
    print(f"  sw_64 tbl:      {len(sw_tbl['syscalls'])}")
    print()
    print("Reserved slots (552-571):")
    print(f"  unistd.h:       {len(unistd.get('reserved', []))}")
    print(f"  syscall_64.tbl: {len(tbl.get('reserved', []))}")
    print(f"  sw_64 tbl:      {len(sw_tbl.get('reserved', []))}")
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
        print("RESULT: PASS — all ABI checks passed (OS-IRON-001)")
    else:
        print(f"RESULT: FAIL — {failed} ABI check(s) failed "
              f"(OS-IRON-001 violation)")
    print("-" * 72)


# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="agent-linux ABI stability checker (OS-IRON-001)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-root", default=None,
        help="agent-linux repo root (default: auto-detect)")
    parser.add_argument(
        "--ssot-path", default=None,
        help="SSoT document path (default: "
             "../docs/docs-linux/140-application-development/"
             "07-syscall-registry.md)")
    parser.add_argument(
        "--unistd-path", default=None,
        help="unistd.h path (default: "
             "kernel/include/uapi/asm-generic/unistd.h)")
    parser.add_argument(
        "--tbl-path", default=None,
        help="syscall_64.tbl path (default: "
             "kernel/arch/x86/entry/syscalls/syscall_64.tbl)")
    parser.add_argument(
        "--sw64-tbl-path", default=None,
        help="sw_64 syscall.tbl path (default: "
             "kernel/arch/sw_64/kernel/syscalls/syscall.tbl)")
    parser.add_argument(
        "--sc-header-path", default=None,
        help="[SC] contract header path (default: "
             "kernel/include/uapi/linux/airymax/syscalls.h)")
    parser.add_argument(
        "--baseline", default=None,
        help="baseline JSON path (default: "
             "tools/abi-check/abi_baseline.json)")
    parser.add_argument(
        "--snapshot-path", default=None,
        help="contract snapshot path (default: "
             "tools/abi-check/contract/syscall_registry.snapshot.json)")
    parser.add_argument(
        "--no-baseline", action="store_true",
        help="skip baseline comparison")
    parser.add_argument(
        "--update-snapshot", action="store_true",
        help="regenerate the contract snapshot from the SSoT and exit")
    parser.add_argument(
        "--require-ssot", action="store_true",
        help="fail (exit 2) when the SSoT document is unavailable")
    parser.add_argument(
        "--check", action="store_true",
        help="CI mode: minimal output, exit code only (0=pass, 1=fail)")
    args = parser.parse_args()

    # Determine repo root
    repo_root = Path(args.repo_root) if args.repo_root else find_repo_root()
    if not repo_root.is_dir():
        print(f"ERROR: repo root not found: {repo_root}", file=sys.stderr)
        sys.exit(2)

    # Resolve all paths relative to repo root
    ssot_path = (Path(args.ssot_path) if args.ssot_path
                 else repo_root / DEFAULT_SSOT_REL)
    snapshot_path = (Path(args.snapshot_path) if args.snapshot_path
                     else repo_root / DEFAULT_SNAPSHOT_REL)
    unistd_path = (Path(args.unistd_path) if args.unistd_path
                   else repo_root / DEFAULT_UNISTD_REL)
    tbl_path = (Path(args.tbl_path) if args.tbl_path
                else repo_root / DEFAULT_TBL_REL)
    sw64_tbl_path = (Path(args.sw64_tbl_path) if args.sw64_tbl_path
                     else repo_root / DEFAULT_SW64_TBL_REL)
    sc_header_path = (Path(args.sc_header_path) if args.sc_header_path
                      else repo_root / DEFAULT_SC_HEADER_REL)
    baseline_path = (Path(args.baseline) if args.baseline
                     else repo_root / DEFAULT_BASELINE_REL)

    # The authoritative SSoT lives in the docs submodule; an explicit
    # --ssot-path is a hard contract and must exist, while the default path
    # may legitimately be missing (mirror checkout) and falls back to the
    # derived contract snapshot.
    ssot_available = ssot_path.is_file()
    if args.ssot_path and not ssot_available:
        print(f"ERROR: file not found: {ssot_path}", file=sys.stderr)
        sys.exit(2)
    if not ssot_available and (args.require_ssot or args.update_snapshot):
        reason = ("--require-ssot" if args.require_ssot
                  else "--update-snapshot")
        print(f"ERROR: authoritative SSoT not found: {ssot_path} "
              f"({reason})", file=sys.stderr)
        sys.exit(2)

    if args.update_snapshot:
        ssot = parse_ssot(read_file(ssot_path))
        snapshot = build_snapshot(ssot, ssot_path, repo_root,
                                  detect_source_commit(ssot_path))
        write_snapshot(snapshot_path, snapshot)
        print(f"snapshot written: {snapshot_path}")
        print(f"  {describe_snapshot(snapshot)}")
        print(f"  linux_base={snapshot['linux_base']} "
              f"syscalls={len(snapshot['syscalls'])} "
              f"reserved_range={snapshot['reserved_range']}")
        sys.exit(0)

    snapshot = load_snapshot(snapshot_path)
    if ssot_available:
        ssot = parse_ssot(read_file(ssot_path))
        ssot_note = None
    elif snapshot is not None:
        ssot = snapshot_to_ssot(snapshot)
        ssot_note = ("[INFO] SSoT source: contract snapshot "
                     "(../docs unavailable) — "
                     + describe_snapshot(snapshot))
    else:
        print(f"ERROR: neither SSoT ({ssot_path}) nor snapshot "
              f"({snapshot_path}) is available", file=sys.stderr)
        sys.exit(2)

    # Read and parse the kernel-side sources
    unistd_text = read_file(unistd_path)
    tbl_text = read_file(tbl_path)
    sw64_text = read_file(sw64_tbl_path)
    sc_header_text = read_file(sc_header_path)

    unistd = parse_unistd(unistd_text)
    tbl = parse_syscall_tbl(tbl_text)
    sw_tbl = parse_syscall_tbl(sw64_text)
    sc_header = parse_sc_header(sc_header_text)

    baseline = None if args.no_baseline else load_baseline(baseline_path)

    # Run all checks
    results = []
    results.append(check_three_way_consistency(ssot, unistd, tbl))
    results.append(check_base_number(ssot, unistd, tbl))
    results.append(check_range_no_conflict(ssot, unistd, tbl))
    results.append(check_nr_syscalls(unistd))
    results.append(check_x32_forbidden(unistd, tbl))
    results.append(check_sc_header_consistency(sc_header, ssot, unistd, tbl))
    results.append(check_reserved_slots(unistd, tbl))
    results.append(check_tbl_abi_and_entry(tbl))
    results.append(check_sw64_tbl(ssot, sw_tbl))
    results.append(check_arch_wire(repo_root))
    if baseline is not None:
        results.append(check_baseline(ssot, unistd, tbl, baseline))
    results.append(check_snapshot_drift(ssot, snapshot, ssot_available))

    # Print report
    ssot_label = str(ssot_path) if ssot_available else (
        f"{snapshot_path} (../docs unavailable)")
    sources = [
        ("SSoT", ssot_label),
        ("snapshot", str(snapshot_path) if snapshot else "(none)"),
        ("unistd.h", str(unistd_path)),
        ("syscall_64.tbl", str(tbl_path)),
        ("sw_64 syscall.tbl", str(sw64_tbl_path)),
        ("[SC] syscalls.h", str(sc_header_path)),
        ("riscv table", str(repo_root / DEFAULT_RISCV_TBL_REL)),
        ("loongarch table", str(repo_root / DEFAULT_LOONG_TBL_REL)),
        ("baseline", str(baseline_path) if baseline else "(none)"),
    ]
    print_report(results, ssot, unistd, tbl, sw_tbl, baseline, sources,
                 args.check, ssot_note)

    # Exit code: 0=pass, 1=fail
    failed = sum(1 for r in results if not r.passed)
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
