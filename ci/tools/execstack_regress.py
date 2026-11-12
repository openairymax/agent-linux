#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tools/execstack_regress.py — W^X 例外取证回归器（供 ci/gates/wx-policy.sh）
#
# 用法:
#   python3 ci/tools/execstack_regress.py [--scan DIR]... [--keep DIR] [--quiet]
#   python3 ci/tools/execstack_regress.py --scan DIR --no-probe
#
# --no-probe 只做 ELF 扫描，给不接触内核的门禁声明面用。AIRY_WX_FS 可把
# 「本机是否 AirymaxOS 内核」指到夹具目录，除此之外没有行为开关。
#
# 背景：ALK 强制 W^X。已写映射再申请执行（W→X mprotect）返回 EACCES，而声明
# PT_GNU_STACK=RWE 的共享库在 dlopen 时由 glibc 的 dl_make_stack_executable()
# 走到同一条路径，于是闭源库在加载阶段就被拒。0.0.3 在验证机上用「清掉库的
# PT_GNU_STACK 执行位」取证，那只是取证手段：写进发行版等于要求用户改厂商库，
# 且下次升级即失效，属于把缺陷转嫁给用户。
#
# 本工具只做三件可证伪的事，不模拟、不假设：
#   ① 现场合成一对共享库（同一份源码，只差 -z execstack），用 ctypes 真实
#      dlopen。普通库必须能加载——否则拒绝来自环境而不是 W^X，结论无意义。
#      这条阴性对照是判据①可用的前提：没有它的「被拒」不值得相信。
#   ② 未授权的 W→X mprotect 必须仍被拒；一次性 mmap(W|X) 必须仍放行。后者是
#      JIT 与图形栈的既存依赖，误伤它和放过前者一样是缺陷。
#   ③ --scan 下每个 ELF 读出 PT_GNU_STACK 的 X 位，逐条列出装机后会撞墙的库。
#      这是**例外清单**，不是白名单：白名单要求内核能受理规则并据此放行，而
#      0.0.4 的 airy LSM 没有 file_mprotect 钩子、policy 接口只读，故白名单
#      顺延（见 0.0.4 方案 §2.3）；清单仍要出，它决定顺延期间有多少软件会
#      在加载阶段失败。
#
# 结论三态，末行是固定标记，便于门禁解析：
#   AIRY-WX-PASS     判据全部成立
#   AIRY-WX-FAIL     有判据不成立，原因逐条打印
#   AIRY-WX-WAIVED   本机没有 airy LSM，判据无从成立（非 AirymaxOS 内核）
#
# 退出码: 0 通过；1 失败；2 用法错误；4 不适用。
# ===========================================================================

from __future__ import annotations

import argparse
import ctypes
import os
import struct
import subprocess
import sys
import tempfile

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_NA = 4

MAGIC = b"\x7fELF"
PT_GNU_STACK = 0x6474E551
PF_X = 0x1
AIRY_FS = "/sys/kernel/security/airy"
# glibc 把内核的 EACCES/EPERM 原样带进 dlerror 文本。其他 errno（如 EINVAL）
# 说明拒绝来自别处，不能算作 W^X 生效的证据。
ENFORCED = ("Permission denied", "Operation not permitted")

PROT_READ = 0x1
PROT_WRITE = 0x2
PROT_EXEC = 0x4
MAP_PRIVATE = 0x2
MAP_ANON = 0x20
PAGE = 4096

SOURCE = "int airy_wx_probe(void) { return 0; }\n"
FAILS: list[str] = []
QUIET = False


def say(msg: str) -> None:
    """按 --quiet 抑制进度行，结论行不受影响。"""
    if not QUIET:
        print(msg)


def note(msg: str) -> None:
    print(msg)


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"FAIL {msg}")


def _elf_phdr(head: bytes) -> tuple[tuple[str, int, int, int] | None, str]:
    """从 ELF 头取 (字节序前缀, 程序头偏移, 表项大小, 表项数)。

    首个元素为 None 时第二项给出拒因：头长不足、魔数不符、编码未知都说明
    这不是可解析的 ELF，后续程序头遍历没有意义。
    """
    if len(head) < 52 or head[:4] != MAGIC:
        return None, "非 ELF"
    cls, end = head[4], head[5]
    fmt = {1: "<", 2: ">"}.get(end)
    if fmt is None or cls not in (1, 2):
        return None, "未知 ELF 编码"
    if cls == 2:
        phoff = struct.unpack_from(fmt + "Q", head, 32)[0]
        phes, phnum = struct.unpack_from(fmt + "HH", head, 54)
    else:
        phoff = struct.unpack_from(fmt + "I", head, 28)[0]
        phes, phnum = struct.unpack_from(fmt + "HH", head, 42)
    return (fmt, phoff, phes, phnum), ""


def _gnu_stack(
    fh, fmt: str, phoff: int, phes: int, phnum: int
) -> tuple[bool | None, str]:
    """按程序头表逐项找 PT_GNU_STACK；找不到即该对象不在判定范围。"""
    for i in range(phnum):
        fh.seek(phoff + i * phes)
        ph = fh.read(phes)
        if len(ph) < 8:
            break
        p_type, p_flags = struct.unpack_from(fmt + "II", ph, 0)
        if p_type == PT_GNU_STACK:
            return bool(p_flags & PF_X), "RWE" if p_flags & PF_X else "RW"
    return None, "无 PT_GNU_STACK"


def elf_stack(path: str) -> tuple[bool | None, str]:
    """读 ELF 的 PT_GNU_STACK，返回 (是否可执行栈, 备注)。

    不是 ELF 或没有该程序头时返回 (None, 原因)：缺该头的对象由加载器按
    ABI 默认处理，不在本工具判定范围，不能当成「安全」计数。
    """
    try:
        with open(path, "rb") as fh:
            fields, reason = _elf_phdr(fh.read(64))
            if fields is None:
                return None, reason
            return _gnu_stack(fh, *fields)
    except OSError as exc:
        return None, str(exc)


def build_pair(tmp: str) -> tuple[str, str]:
    """同一份源码编出普通库与请求可执行栈的库。"""
    src = os.path.join(tmp, "probe.c")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(SOURCE)
    plain = os.path.join(tmp, "plain.so")
    xstack = os.path.join(tmp, "xstack.so")
    base = ["gcc", "-shared", "-fPIC", "-o"]
    subprocess.run(base + [plain, src], check=True, capture_output=True)
    subprocess.run(base + [xstack, src, "-Wl,-z,execstack"], check=True,
                   capture_output=True)
    return plain, xstack


def airy_dir() -> str:
    """airy 的 securityfs 目录；AIRY_WX_FS 只供夹具指向假目录。"""
    return os.environ.get("AIRY_WX_FS", AIRY_FS)


def dlopen_lib(path: str) -> tuple[bool, str]:
    """真实 dlopen；返回 (是否加载成功, 错误摘要)。"""
    try:
        ctypes.CDLL(path)
    except OSError as exc:
        return False, str(exc)
    return True, ""


def libc_call() -> ctypes.CDLL:
    """取 libc 并把 mmap 的返回值固定成有符号长整型。"""
    lib = ctypes.CDLL("libc.so.6", use_errno=True)
    lib.mmap.restype = ctypes.c_long
    lib.mprotect.restype = ctypes.c_int
    return lib


def mmap_wx() -> tuple[bool, str]:
    """一次性申请 W+X：必须放行，否则 JIT 与图形栈全部失效。"""
    lib = libc_call()
    addr = lib.mmap(None, PAGE, PROT_READ | PROT_WRITE | PROT_EXEC,
                    MAP_PRIVATE | MAP_ANON, -1, 0)
    if addr == -1:
        return False, f"mmap(W|X) 失败: errno={ctypes.get_errno()}"
    lib.munmap(ctypes.c_void_p(addr), PAGE)
    return True, ""


def mprot_wx() -> tuple[bool, str]:
    """W→X 提权：必须被拒。放行说明 W^X 已被关闭（判据②失守）。"""
    lib = libc_call()
    addr = lib.mmap(None, PAGE, PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_ANON, -1, 0)
    if addr == -1:
        return False, f"mmap(RW) 失败: errno={ctypes.get_errno()}"
    rc = lib.mprotect(ctypes.c_void_p(addr), PAGE, PROT_READ | PROT_EXEC)
    err = ctypes.get_errno()
    lib.munmap(ctypes.c_void_p(addr), PAGE)
    if rc == 0:
        return False, "mprotect(W→X) 被放行"
    if err in (13, 1):  # EACCES / EPERM
        return True, f"拒绝 errno={err}"
    return False, f"mprotect(W→X) 以 errno={err} 失败，不是拒绝语义"


def scan_tree(root: str) -> int:
    """遍历目录，逐条列出 RWE 的 ELF；返回命中数。"""
    hits = 0
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(base, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            rwe, why = elf_stack(path)
            if rwe is None:
                continue
            if rwe:
                hits += 1
                note(f"RWE  {path}  {why}")
    return hits


def prepare(tmp: str) -> str | None:
    """合成取证对并验证阴性对照；返回失败原因，None 表示可以继续。"""
    try:
        plain, xstack = build_pair(tmp)
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"合成共享库失败: {exc}"
    whyp = elf_stack(plain)[1]
    okx, whyx = elf_stack(xstack)
    if okx is not True:
        return f"合成库未带上可执行栈请求（{whyx}），本轮判据无意义"
    say(f"取证对: plain={whyp} xstack={whyx}")
    loaded, why = dlopen_lib(plain)
    if not loaded:
        return f"阴性对照未通过，普通库也加载失败: {why}"
    return None


def judge(why: str, loaded: bool) -> None:
    """本机是 AirymaxOS 内核时的判据①②。"""
    if loaded:
        fail("带可执行栈的库被放行，W^X 已失效")
    elif not any(s in why for s in ENFORCED):
        fail(f"库被拒但语义不是权限拒绝，判据不可信: {why}")
    else:
        say(f"可执行栈库被拒: {why.splitlines()[0]}")
    ok, why = mprot_wx()
    if ok:
        say(f"mprotect(W→X) {why}")
    else:
        fail(why)
    ok, why = mmap_wx()
    if ok:
        say("mmap(W|X) 放行")
    else:
        fail(why)


def probe(tmp: str) -> int:
    """判据①②：先立阴性对照，再判拒绝与放行。"""
    bad = prepare(tmp)
    if bad:
        fail(bad)
        return EXIT_FAIL
    loaded, why = dlopen_lib(os.path.join(tmp, "xstack.so"))
    if not os.path.isdir(airy_dir()):
        note("WAIVE 本机无 airy LSM，以下为观测值、不作判据")
        note(f"OBS xstack={'loaded' if loaded else why.splitlines()[0]}")
        note(f"OBS mprotect={mprot_wx()[1]}")
        note(f"OBS mmap={mmap_wx()[1] or '放行'}")
        return EXIT_NA
    judge(why, loaded)
    return EXIT_FAIL if FAILS else EXIT_OK


def main(argv: list[str]) -> int:
    global QUIET

    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--scan", action="append", default=[],
                    metavar="DIR", help="列出 DIR 下 PT_GNU_STACK 可执行的 ELF")
    ap.add_argument("--keep", metavar="DIR", help="保留合成库到 DIR 以便复查")
    ap.add_argument("--no-probe", action="store_true",
                    help="只做 --scan 的 ELF 扫描，不触碰内核判据")
    ap.add_argument("--quiet", action="store_true", help="只输出结论行")
    args = ap.parse_args(argv)
    QUIET = args.quiet

    for root in args.scan:
        if not os.path.isdir(root):
            print(f"error: 目录不存在: {root}", file=sys.stderr)
            return EXIT_USAGE
        hits = scan_tree(root)
        note(f"SCAN {root} rwe={hits}")

    tmp = args.keep or tempfile.mkdtemp(prefix="airy-wx-")
    os.makedirs(tmp, exist_ok=True)
    rc = EXIT_OK if args.no_probe else probe(tmp)

    tag = {EXIT_OK: "AIRY-WX-PASS", EXIT_FAIL: "AIRY-WX-FAIL",
           EXIT_NA: "AIRY-WX-WAIVED"}[rc]
    note(tag)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
