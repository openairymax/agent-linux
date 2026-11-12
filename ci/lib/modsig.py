#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# modsig.py — 模块签名尾部与内核污染位的离线校验
#
# 模块签名不是 ELF 段。scripts/sign-file 把 PKCS#7 数据、struct
# module_signature 与魔数 "~Module signature appended~\n" 依次追加在文件
# 尾部，kernel/module/signing.c 的 module_sig_check() 按同一顺序读取；用
# readelf -S 找 .module_sig 段永远找不到，据此判签会对全部模块都为假。
#
#   verify --kind struct|taint|all [--root DIR] [--kernel REL] [--taint FILE]
#
# 判据（与内核一致，取证见 10-references/module-signature.md）：
#   1. 每个 .ko 末尾 28 字节是签名魔数；
#   2. 魔数前 12 字节的 module_signature 自洽：PKCS#7 构型的 signer_len
#      与 key_id_len 必须为 0，sig_len 非零且签名段不吞掉模块正文；
#   3. /proc/sys/kernel/tainted 的 8192 位（TAINT_UNSIGNED_MODULE）为 0。
#
# 压缩模块（.ko.xz/.ko.gz/.ko.zst）不可离线判定：Kbuild 先签名后压缩，
# 魔数被封在压缩流里，直接读文件尾部只会得到压缩帧。这类树按证据不可用
# 处理并退 5，提示先解压，而不是把「读不到魔数」误报成「未签名」。
#
# 退出码：0 通过；1 判定不通过；5 证据不可用（缺根、无模块、压缩模块、
# taint 不可读）。
# ===========================================================================

import argparse
import os
import sys
from pathlib import Path

MARKER = b"~Module signature appended~\n"
STRUCT = 12
MIN = STRUCT + len(MARKER)
SUFFIX = (".ko", ".ko.xz", ".ko.gz", ".ko.zst")
PACKED = (".ko.xz", ".ko.gz", ".ko.zst")
PKCS7 = 2
NOTE = 1
UNSIGNED = 8192
EINVAL = 5


def say(msg):
    print(msg)


def unavail(msg):
    sys.stderr.write("证据不可用: %s\n" % msg)


def sig_bad(path):
    """返回签名块不符判据的稳定标识；合格返回空串。"""
    try:
        size = os.path.getsize(path)
        if size < MIN + 1:
            return "short"
        with open(path, "rb") as fh:
            fh.seek(size - MIN)
            blob = fh.read(MIN)
    except OSError:
        return "unreadable"
    if len(blob) != MIN:
        return "short"
    if blob[STRUCT:] != MARKER:
        return "no-marker"
    return tail_bad(blob, size)


def tail_bad(blob, size):
    """构型与长度判读：签名段不得吞掉模块正文，长度不得为 0。"""
    ident, signer, key_id = blob[2], blob[3], blob[4]
    why = id_bad(ident, signer, key_id)
    if why:
        return why
    sig_len = int.from_bytes(blob[8:12], "big")
    if not sig_len:
        return "zero-sig"
    if sig_len + MIN + signer + key_id >= size:
        return "sig-overrun"
    return ""


def id_bad(ident, signer, key_id):
    """签名构型：PKCS#7 不带名字，NOTE 构型必须两者俱全。

    这两个字段的判据来自 kernel/module/signing.c：ident 为 PKCS#7 时
    signer/key_id 保留未用，必须为 0；为 NOTE 时是构型标记，必须非 0。
    构型之外的取值只可能来自损坏或伪造的尾部，单独成码以免混进上面两类。
    """
    if ident == PKCS7:
        return "bad-pkcs7" if signer or key_id else ""
    if ident == NOTE:
        return "" if signer and key_id else "bad-note"
    return "bad-id-type"


def rel_dirs(root, kernel):
    """返回待扫描的版本目录；usr/lib/modules 缺失时返回 None。"""
    base = root / "usr/lib/modules"
    if not base.is_dir():
        return None
    if kernel:
        dirs = [base / kernel]
    else:
        dirs = [p for p in base.iterdir()
                if p.is_dir() and not p.is_symlink()]
    return [p for p in dirs if p.is_dir()]


def modules(root, kernel):
    """枚举模块文件；根不可用时返回 None，空树返回空列表。"""
    dirs = rel_dirs(root, kernel)
    if dirs is None:
        return None
    found = []
    for top in dirs:
        for cur, _sub, files in os.walk(top):
            found += [Path(cur) / n for n in files if n.endswith(SUFFIX)]
    return sorted(found)


def check_struct(root, kernel):
    """全量校验模块签名尾部，返回退出码。"""
    mods = modules(root, kernel)
    if mods is None:
        unavail("%s 不存在" % (root / "usr/lib/modules"))
        return EINVAL
    if not mods:
        unavail("%s 下没有模块" % (root / "usr/lib/modules"))
        return EINVAL
    packed = [p for p in mods if p.name.endswith(PACKED)]
    if packed:
        unavail("%s 为压缩模块，须解压后判定" % packed[0])
        return EINVAL
    bad = 0
    for path in mods:
        why = sig_bad(path)
        if why:
            bad += 1
            sys.stderr.write("unsigned module: %s (%s)\n" % (path, why))
    if bad:
        sys.stderr.write("module-signature: %d/%d 个模块签名不合格\n"
                         % (bad, len(mods)))
        return 1
    say("模块签名 %d/%d 通过" % (len(mods), len(mods)))
    return 0


def check_taint(path):
    """校验运行内核污染位的 8192 位，返回退出码。"""
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        unavail("%s 不可读" % path)
        return EINVAL
    try:
        val = int(text, 10)
    except ValueError:
        unavail("%s 不是十进制整数: %s" % (path, text))
        return EINVAL
    if val & UNSIGNED:
        sys.stderr.write("kernel tainted by unsigned module (tainted=%d)\n"
                         % val)
        return 1
    say("内核污染位 %d，未标记未签名模块" % val)
    return 0


def main():
    ap = argparse.ArgumentParser(description="模块签名尾部与 taint 校验")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ver = sub.add_parser("verify")
    ver.add_argument("--kind", choices=("struct", "taint", "all"),
                     default="all")
    ver.add_argument("--root")
    ver.add_argument("--kernel", default="")
    ver.add_argument("--taint")
    args = ap.parse_args()

    root = Path(args.root) if args.root else None
    if args.kind in ("struct", "all") and root is None:
        unavail("--kind %s 需要 --root" % args.kind)
        return EINVAL
    if args.kind in ("struct", "all"):
        rc = check_struct(root, args.kernel)
        if rc:
            return rc
    if args.kind in ("taint", "all"):
        src = args.taint
        if not src and root is not None:
            src = root / "proc/sys/kernel/tainted"
        return check_taint(src or "/proc/sys/kernel/tainted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
