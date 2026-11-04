#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/doc_links.py — 文档树悬空链接检查（供 ci/gates/docs-links.sh 调用）
#
# 只判相对链接能否落到真实文件：绝对 URL（http/https/mailto/file）与页内锚点
# 不在此列——前者要联网才能判活，后者由渲染器自己处理，混进来只会让门禁
# 变成网络拨测。跨仓链接按实际检出布局解析，所以 docs-linux 里指向
# agent-linux 源码或 docs-workload 的写法必须与超级仓一致，不能靠
# 「在我机器上能点开」过关；脱离超级仓单独检出文档时，这类链接会如实判悬空。
#
# 冻结归档（默认 20-archive/）整目录跳过：那是历史快照，改写其中的链接等于
# 篡改留档，检查它们只会逼着人改历史。
#
# 围栏代码块与行内代码里的方括号不当链接——文档里写 C 数组下标、写
# reserved[72](__u8, offset 56) 这类排版，都能被朴素正则误判成链接。
#
# 退出码: 0 无悬空；1 有悬空；5 用法或环境错误。
# ===========================================================================

from __future__ import annotations

import argparse
import os
import re
import sys

FENCE = re.compile(r"^\s*(```|~~~)")
INLINE = re.compile(r"`[^`]*`")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
ABSOLUTE = ("http://", "https://", "mailto:", "file://", "//", "<")

EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_USAGE = 5


class Fail(Exception):
    """用法或环境错误（退出码 5）。"""


def _targets(path: str) -> list[tuple[int, str]]:
    """取一份 Markdown 里所有相对链接及其行号。"""
    found = []
    inside = False
    with open(path, encoding="utf-8", errors="replace") as stream:
        for lineno, raw in enumerate(stream, 1):
            if FENCE.match(raw):
                inside = not inside
                continue
            if inside:
                continue
            for match in LINK.finditer(INLINE.sub("", raw)):
                link = match.group(1).strip()
                if link.startswith(ABSOLUTE) or link.startswith("#"):
                    continue
                if not link.split("#", 1)[0]:
                    continue
                found.append((lineno, link))
    return found


def walk(source: str, exclude: str) -> tuple[int, int, list[str]]:
    pages = 0
    links = 0
    broken = []
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in (".git", exclude))
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            page = os.path.join(dirpath, name)
            pages += 1
            for lineno, link in _targets(page):
                links += 1
                bare = link.split("#", 1)[0]
                target = os.path.normpath(
                    os.path.join(os.path.dirname(page), bare))
                if not os.path.exists(target):
                    rel = os.path.relpath(page, source)
                    broken.append(f"link/dangling {rel}:{lineno} -> {link}")
    return pages, links, broken


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", required=True)
    parser.add_argument("--exclude", default="20-archive")
    args, extra = parser.parse_known_args(argv)
    if extra:
        raise Fail(f"未知参数: {' '.join(extra)}")
    source = os.path.abspath(args.source)
    if not os.path.isdir(source):
        raise Fail(f"文档树不存在: {args.source}")
    pages, links, broken = walk(source, args.exclude)
    print(f"link: 文件 {pages}，相对链接 {links}，悬空 {len(broken)}")
    for line in broken:
        print(line)
    return EXIT_BROKEN if broken else EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Fail as exc:
        print(f"docs-links: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
