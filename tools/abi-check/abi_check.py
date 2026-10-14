#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
abi_check.py — agent-linux syscall ABI stability checker

Validates syscall number ABI stability (OS-IRON-001: 用户空间 ABI 永不破坏)
by cross-checking four authoritative sources against a baseline snapshot:

  1. SSoT document  — docs/docs-linux/140-application-development/07-syscall-registry.md
  2. UAPI header     — kernel/include/uapi/asm-generic/unistd.h
  3. Kernel entry tbl — kernel/arch/x86/entry/syscalls/syscall_64.tbl
  4. [SC] contract hdr — kernel/include/uapi/linux/airymax/syscalls.h

Usage:
    python3 tools/abi-check/abi_check.py [options]

Options:
    --repo-root PATH    agent-linux repo root (default: auto-detect)
    --ssot-path PATH    SSoT document path (default: auto)
    --unistd-path PATH  unistd.h path (default: auto)
    --tbl-path PATH     syscall_64.tbl path (default: auto)
    --sc-header-path PATH  [SC] contract header path (default: auto)
    --baseline PATH     baseline JSON path (default: tools/abi-check/abi_baseline.json)
    --no-baseline       skip baseline comparison
    --check             CI mode: minimal output, exit code only (0=pass, 1=fail)

Exit codes:
    0 — all ABI checks passed
    1 — one or more ABI checks failed
    2 — input/parse error (file not found, invalid baseline JSON, etc.)
"""

import argparse
import json
import os
import re
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
DEFAULT_SC_HEADER_REL = os.path.join("kernel", "include", "uapi", "linux",
                                     "airymax", "syscalls.h")
DEFAULT_BASELINE_REL = os.path.join("tools", "abi-check", "abi_baseline.json")


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


def check_reserved_slots(unistd, tbl):
    """Check the 20 reserved slots (552-571) are registered consistently."""
    failures = []

    expected_nums = list(range(EXPECTED_RESERVED_START,
                              EXPECTED_RESERVED_END + 1))
    if len(expected_nums) != EXPECTED_RESERVED_COUNT:
        return CheckResult(
            "预留槽登记一致 (552-571)", False,
            f"  内部常量不一致: 区间槽位数 != {EXPECTED_RESERVED_COUNT}")

    unistd_res = {s["linux_number"]: s["name"] for s in unistd["reserved"]}
    tbl_res = {s["linux_number"]: s for s in tbl["reserved"]}

    # unistd.h reserved slot coverage + naming
    missing = [n for n in expected_nums if n not in unistd_res]
    extra = [n for n in sorted(unistd_res) if n not in expected_nums]
    if missing:
        failures.append(f"  unistd.h: 缺失预留槽 {missing}")
    if extra:
        failures.append(f"  unistd.h: 越界预留槽 {extra}")
    for num, name in sorted(unistd_res.items()):
        expected_name = f"{RESERVED_NAME_PREFIX}{num - EXPECTED_BASE}"
        if name != expected_name:
            failures.append(
                f"  unistd.h: {num} 命名 {name} (期望 {expected_name})")

    # syscall_64.tbl reserved slot coverage + naming + entry point
    missing = [n for n in expected_nums if n not in tbl_res]
    extra = [n for n in sorted(tbl_res) if n not in expected_nums]
    if missing:
        failures.append(f"  syscall_64.tbl: 缺失预留槽 {missing}")
    if extra:
        failures.append(f"  syscall_64.tbl: 越界预留槽 {extra}")
    for num, entry in sorted(tbl_res.items()):
        expected_name = f"{RESERVED_NAME_PREFIX}{num - EXPECTED_BASE}"
        if entry["name"] != expected_name:
            failures.append(
                f"  syscall_64.tbl: {num} 命名 {entry['name']} "
                f"(期望 {expected_name})")
        if entry["entry"] != RESERVED_TBL_ENTRY:
            failures.append(
                f"  syscall_64.tbl: {entry['name']} 入口点 "
                f"{entry['entry']} (期望 {RESERVED_TBL_ENTRY})")

    if failures:
        return CheckResult("预留槽登记一致 (552-571)", False,
                           "\n".join(failures))
    return CheckResult(
        "预留槽登记一致 (552-571)", True,
        f"unistd.h 与 syscall_64.tbl 各登记 {EXPECTED_RESERVED_COUNT} 个"
        f"预留槽 (552-571)，命名 airy_reserved_<num-548>，"
        f"入口 sys_ni_syscall")


def check_tbl_abi_and_entry(tbl):
    """Check syscall_64.tbl ABI column and entry naming for airy entries."""
    failures = []

    for s in tbl["syscalls"]:
        if s["abi"] != "common":
            failures.append(
                f"  {s['name']} ({s['linux_number']}): abi={s['abi']} "
                f"(期望 common)")
        expected_entry = f"sys_{s['name']}"
        if s["entry"] != expected_entry:
            failures.append(
                f"  {s['name']}: 入口点 {s['entry']} "
                f"(期望 {expected_entry})")

    for s in tbl["reserved"]:
        if s["abi"] != "common":
            failures.append(
                f"  {s['name']} ({s['linux_number']}): abi={s['abi']} "
                f"(期望 common)")

    if failures:
        return CheckResult("syscall_64.tbl ABI 列与入口点正确", False,
                           "\n".join(failures))
    total = len(tbl["syscalls"]) + len(tbl["reserved"])
    return CheckResult(
        "syscall_64.tbl ABI 列与入口点正确", True,
        f"{total} 个 airy 条目 abi 均为 common，核心入口 sys_airy_sys_*，"
        f"预留入口 sys_ni_syscall")


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


# ─── Report ────────────────────────────────────────────────────────────────

def print_report(results, ssot, unistd, tbl, baseline, sources, check_mode):
    """Print the ABI check report."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    if check_mode:
        # CI mode: minimal output
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
    print(f"SSoT version:     {ssot.get('version', '?')}")
    bl_ver = baseline.get("version", "N/A") if baseline else "N/A (无基线)"
    print(f"Baseline version: {bl_ver}")
    print()
    print("Syscalls found:")
    print(f"  SSoT:           {len(ssot['syscalls'])}")
    print(f"  unistd.h:       {len(unistd['syscalls'])}")
    print(f"  syscall_64.tbl: {len(tbl['syscalls'])}")
    print()
    print("Reserved slots (552-571):")
    print(f"  unistd.h:       {len(unistd.get('reserved', []))}")
    print(f"  syscall_64.tbl: {len(tbl.get('reserved', []))}")
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
        "--sc-header-path", default=None,
        help="[SC] contract header path (default: "
             "kernel/include/uapi/linux/airymax/syscalls.h)")
    parser.add_argument(
        "--baseline", default=None,
        help="baseline JSON path (default: "
             "tools/abi-check/abi_baseline.json)")
    parser.add_argument(
        "--no-baseline", action="store_true",
        help="skip baseline comparison")
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
    unistd_path = (Path(args.unistd_path) if args.unistd_path
                   else repo_root / DEFAULT_UNISTD_REL)
    tbl_path = (Path(args.tbl_path) if args.tbl_path
                else repo_root / DEFAULT_TBL_REL)
    sc_header_path = (Path(args.sc_header_path) if args.sc_header_path
                      else repo_root / DEFAULT_SC_HEADER_REL)
    baseline_path = (Path(args.baseline) if args.baseline
                     else repo_root / DEFAULT_BASELINE_REL)

    # Read and parse all sources
    ssot_text = read_file(ssot_path)
    unistd_text = read_file(unistd_path)
    tbl_text = read_file(tbl_path)
    sc_header_text = read_file(sc_header_path)

    ssot = parse_ssot(ssot_text)
    unistd = parse_unistd(unistd_text)
    tbl = parse_syscall_tbl(tbl_text)
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
    if baseline is not None:
        results.append(check_baseline(ssot, unistd, tbl, baseline))

    # Print report
    sources = [
        ("SSoT", str(ssot_path)),
        ("unistd.h", str(unistd_path)),
        ("syscall_64.tbl", str(tbl_path)),
        ("[SC] syscalls.h", str(sc_header_path)),
        ("baseline", str(baseline_path) if baseline else "(none)"),
    ]
    print_report(results, ssot, unistd, tbl, baseline, sources,
                 args.check)

    # Exit code: 0=pass, 1=fail
    failed = sum(1 for r in results if not r.passed)
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
