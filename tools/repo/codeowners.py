#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# codeowners.py — 由 MAINTAINERS 投影出 GitHub CODEOWNERS
#
# MAINTAINERS 是所有者信息的唯一事实来源（OS-STD-GOV-004），CODEOWNERS 只是
# 它在 GitHub 上的投影：路径取自 F:，所有者取自 O:。M:/R: 是邮件地址，GitHub
# 不接受这种 owner 形式，因此投影只读 O:；某个块有 F: 却没有 O: 时直接报错，
# 不再生成一份语法合法却无人认领的文件。
#
# 用法:
#     python3 tools/repo/codeowners.py           打印投影到标准输出
#     python3 tools/repo/codeowners.py --check   与仓库内 CODEOWNERS 比对
#     python3 tools/repo/codeowners.py --maintainers X --codeowners Y
#
# 退出码:
#     0 — 已打印投影，或 --check 判定无漂移
#     1 — --check 判定 CODEOWNERS 与投影不一致
#     2 — MAINTAINERS 解析失败、路径非法，或文件缺失
# ===========================================================================

import argparse
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

HEADER = """\
# ===========================================================================
# agent-linux (AirymaxOS) CODEOWNERS
# ===========================================================================
# GENERATED FILE — 由 MAINTAINERS 投影而来，请勿手工编辑。
# 权威来源是 MAINTAINERS（OS-STD-GOV-004）：改那里，再执行下面两条命令。
#
#   make codeowners-sync    重新生成
#   make codeowners-check   校验是否漂移（CI 由 ci/gates/mgmt-files.sh 执行）
# ===========================================================================

"""

SEPARATOR = re.compile(r"^-{10,}\s*$")
FIELD = re.compile(r"^([A-Z]):[ \t]*(.*)$")
COMMENT = re.compile(r"^[ \t]*#")
HANDLE = re.compile(r"^@[A-Za-z0-9][A-Za-z0-9-]*$")
PATH = re.compile(r"^(?:[^\s]+)$")


class FeedError(Exception):
    """MAINTAINERS 内容不满足投影前提。"""


def read_lines(path):
    """读取 MAINTAINERS 并返回行列表，文件缺失时抛 FeedError。"""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise FeedError(f"无法读取 {path}: {exc}") from exc


def after_preamble(lines):
    """跳过 MAINTAINERS 的字段图例，返回块列表首行之后的行。"""
    for index, line in enumerate(lines):
        if SEPARATOR.match(line):
            return lines[index + 1:]
    return lines


def parse_blocks(lines):
    """按块解析 MAINTAINERS，返回 (块名, [(路径, [所有者])]) 列表。"""
    blocks = []
    name = ""
    paths = []
    owners = []

    def flush():
        if not paths:
            return
        if not owners:
            raise FeedError(f"块「{name or '未命名'}」有 F: 却没有 O:，"
                            "无法投影出合法 owner")
        blocks.append((name, [(p, list(owners)) for p in paths]))

    for line in lines:
        if not line.strip() or COMMENT.match(line):
            continue
        if line[0] in " \t":
            continue
        match = FIELD.match(line)
        if not match:
            flush()
            name = line.strip()
            paths = []
            owners = []
            continue
        key, value = match.group(1), match.group(2).strip()
        if key == "F":
            if not value or not PATH.match(value):
                raise FeedError(f"块「{name}」的 F: 不是单一路径: {value!r}")
            paths.append(value)
        elif key == "O":
            for handle in value.split():
                if not HANDLE.match(handle):
                    raise FeedError(
                        f"块「{name}」的 O: 不是 GitHub 句柄: {handle!r}")
                if handle not in owners:
                    owners.append(handle)
    flush()
    return blocks


def render(blocks):
    """把解析结果渲染成 CODEOWNERS 文本，路径列按最长项对齐。"""
    rows = [(path, owners) for _, group in blocks for path, owners in group]
    if not rows:
        raise FeedError("MAINTAINERS 没有可投影的 F:/O: 组合")
    width = max(len(path) for path, _ in rows) + 4
    body = "".join(
        f"{path.ljust(width)}{' '.join(owners)}\n" for path, owners in rows)
    return HEADER + body


def main(argv):
    """入口：打印投影或校验漂移。"""
    parser = argparse.ArgumentParser(
        description="由 MAINTAINERS 投影 GitHub CODEOWNERS")
    parser.add_argument("--maintainers", default=str(ROOT / "MAINTAINERS"))
    parser.add_argument("--codeowners", default=str(ROOT / "CODEOWNERS"))
    parser.add_argument("--check", action="store_true",
                        help="与 CODEOWNERS 比对，漂移时退出 1")
    args = parser.parse_args(argv)

    try:
        want = render(parse_blocks(after_preamble(
            read_lines(Path(args.maintainers)))))
    except FeedError as exc:
        print(f"codeowners: {exc}", file=sys.stderr)
        return 2

    if not args.check:
        sys.stdout.write(want)
        return 0

    target = Path(args.codeowners)
    try:
        have = target.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"codeowners: 无法读取 {target}: {exc}", file=sys.stderr)
        return 2
    if have == want:
        return 0

    diff = difflib.unified_diff(
        have.splitlines(keepends=True), want.splitlines(keepends=True),
        fromfile=str(target), tofile="MAINTAINERS projection")
    sys.stderr.writelines(diff)
    print("codeowners: CODEOWNERS 与 MAINTAINERS 不一致，"
          "请执行 make codeowners-sync", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
