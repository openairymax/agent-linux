#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/checks/abi_snapshot.py — [SC] UAPI 头文件字节级冻结（K-03 判定本体）
#
# 调用方是 ci/gates/kernel-uapi.sh；本文件只做判定，参数校验与退出码透传
# 由门禁承担，操作员入口统一为 `ci/ci.sh kernel-uapi`。
#
# 用法:
#   python3 ci/checks/abi_snapshot.py [--root DIR] [--check]
#   python3 ci/checks/abi_snapshot.py [--root DIR] --update
#
# 判据有两层，缺一不可：
#   · 集合面——kernel/include/uapi/linux/airymax/ 下的 .h 集合必须与快照
#     登记的集合逐条相同，新增、删除、改名都算漂移；
#   · 字节面——每个文件的 sha256 必须与登记摘要相同，任何字节改动都算漂移。
#
# 为什么不复用 tools/abi-check: 那个工具查 syscall 号与派生契约快照，回答
# 「结构性兼容」；本门禁回答「用户态看到的头文件变没变」。一条注释的改动
# 不会破坏结构兼容，但它改变了发布物料，必须走一次显式刷新与评审。结构
# 布局另由 tools/sc-tri-compile 断言，三处读点不同、互不替代。
#
# 刷新不是自动的：--update 只重写快照，是否接受这次改动由评审回答。CI 用
# --check（默认）判负，不回写文件。
#
# 退出码: 0 一致；1 漂移或快照缺失；5 参数或环境不合法。
# ===========================================================================

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_INPUT = 5

UAPI_DIR = "kernel/include/uapi/linux/airymax"
SNAP_FILE = "tools/abi-check/contract/uapi_headers.sha256"
ROW = re.compile(r"([0-9a-f]{64})  ([^ ]+)")

BANNER = """\
# AirymaxOS [SC] UAPI 头文件字节级快照（K-03）
#
# 受检目录: kernel/include/uapi/linux/airymax
# 刷新命令: bash ci/ci.sh kernel-uapi --update
# 每行格式: <sha256>  <相对仓库根的路径>
#
# 受检目录下的 .h 全部登记：新增、删除、改名与任何字节改动都会让
# bash ci/ci.sh kernel-uapi --check 判负。改动 UAPI 头文件是允许的，
# 但必须显式重跑 --update 并把快照一并提交，让改动进入评审视野。
"""


class BadInput(Exception):
    """输入不合法：调用方给错参数，或仓库状态不足以做出判定。"""


def _sha(path: Path) -> str:
    """返回文件的 sha256 小写十六进制摘要。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan(root: Path) -> dict[str, str]:
    """扫描受检目录，返回 {相对路径: sha256}，按路径升序。"""
    base = root / UAPI_DIR
    if not base.is_dir():
        raise BadInput(f"受检目录不存在: {UAPI_DIR}")

    files = sorted(base.glob("*.h"))
    if not files:
        raise BadInput(f"受检目录没有 .h: {UAPI_DIR}")

    return {f.relative_to(root).as_posix(): _sha(f) for f in files}


def _split(line: str, no: int) -> tuple[str, str]:
    """把一条登记行拆成 (digest, path)，格式不对即判输入错误。"""
    hit = ROW.fullmatch(line)
    if hit is None:
        raise BadInput(f"快照第 {no} 行格式错误")
    return hit.group(1), hit.group(2)


def load(snap: Path) -> dict[str, str]:
    """读取快照；空行与 # 注释跳过，重复路径判输入错误。"""
    if not snap.is_file():
        raise BadInput(f"快照缺失: {SNAP_FILE}")

    out: dict[str, str] = {}
    for no, raw in enumerate(snap.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        digest, path = _split(line, no)
        if path in out:
            raise BadInput(f"快照第 {no} 行重复登记: {path}")
        out[path] = digest

    if not out:
        raise BadInput("快照没有任何登记项")
    return out


def render(files: dict[str, str]) -> str:
    """把登记项渲染成确定性文本：不写时间戳，同输入逐字节同输出。"""
    rows = [f"{digest}  {name}" for name, digest in sorted(files.items())]
    return BANNER + "\n" + "\n".join(rows) + "\n"


def drift(want: dict[str, str], got: dict[str, str]) -> list[str]:
    """列出集合面与字节面的全部差异，按类别固定顺序返回。"""
    msgs = [f"缺失 {name}（快照有登记，磁盘不存在）"
            for name in sorted(want.keys() - got.keys())]
    msgs += [f"新增 {name}（磁盘存在，未登记到快照）"
             for name in sorted(got.keys() - want.keys())]
    msgs += [f"变更 {name}（{want[name][:12]} -> {got[name][:12]}）"
             for name in sorted(want.keys() & got.keys())
             if want[name] != got[name]]
    return msgs


def check(root: Path) -> int:
    """按快照判定当前树；有差异时逐条打印并返回判负。"""
    files = scan(root)
    snap = root / SNAP_FILE
    if not snap.is_file():
        # 快照缺失是判负而非参数错误：CI 上它意味着冻结记录没进仓库。
        print(f"uapi-snapshot: 快照缺失: {SNAP_FILE}", file=sys.stderr)
        print("uapi-snapshot: 首次登记请跑 --update 并把快照一并提交",
              file=sys.stderr)
        return EXIT_DRIFT

    msgs = drift(load(snap), files)
    for msg in msgs:
        print(f"uapi-snapshot: {msg}", file=sys.stderr)
    if msgs:
        print("uapi-snapshot: 刷新请跑 --update，并把快照一并提交评审",
              file=sys.stderr)
        return EXIT_DRIFT

    print(f"uapi-snapshot: UAPI 快照 {len(files)} 份一致")
    return EXIT_OK


def update(root: Path) -> int:
    """重写快照并打印本次登记的变化；旧快照缺失时按全新增处理。"""
    files = scan(root)
    snap = root / SNAP_FILE
    old = load(snap) if snap.is_file() else {}

    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(render(files), encoding="utf-8")

    for msg in drift(old, files) if old else []:
        print(f"uapi-snapshot: {msg}")
    print(f"uapi-snapshot: 已登记 {len(files)} 份 -> {SNAP_FILE}")
    return EXIT_OK


def run(argv: list[str]) -> int:
    """解析参数并分派；所有输入类错误统一折算为退出码 5。"""
    root_default = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description="[SC] UAPI 头文件字节级冻结")
    ap.add_argument("--root", default=str(root_default), metavar="DIR")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只判定，不写盘")
    mode.add_argument("--update", action="store_true", help="重写快照")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        # argparse 以 2 报参数错、以 0 收 --help；本门禁统一按 5 报参数错。
        return EXIT_INPUT if exc.code else EXIT_OK

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"uapi-snapshot: 根目录不存在: {root}", file=sys.stderr)
        return EXIT_INPUT

    try:
        return update(root) if args.update else check(root)
    except BadInput as exc:
        print(f"uapi-snapshot: {exc}", file=sys.stderr)
        return EXIT_INPUT


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
