#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/checks/dead_code.py — 死代码扫描（§3.4 判负口径「死代码」一行）
#
# 死代码是禁桩的另一面：桩让没实现的代码看起来能用，死代码让已实现的代码
# 看起来还在用。两者都不报错，只能靠扫描发现。
#
# 判据分两类，类别不同、处置也不同：
#
#   D001 判负   `#[allow(dead_code)]` 没有给出理由。这个属性一旦写上，编译器
#               的未使用告警就永久失效，此后该模块里新增的死代码再无提示。
#               豁免形态：同行或上一行注释含 `理由:`（或 `reason:`）且有正文。
#   D100 复核   `pub`（不含 `pub(crate)`）项在整个扫描根里只出现一次，即只有
#               定义。rustc 的 dead_code 对 `pub` 项一律沉默——外部 crate 可能
#               在用，编译器无从判断；`pub(crate)` 不在此列，它由 rustc 判。
#               这一条只输出清单供人工复核，不判负：跨仓消费方不在本仓可见，
#               判负会把合法的对外接口全部拖红。
#
# 词法清理复用 ci/checks/no_stub.py 的 blank()：注释与字符串里的名字不算引用，否则
# 一句「参见 Foo」就能让死代码复活。测试代码（tests/ 与 #[cfg(test)] 模块）
# 参与引用计数（被测语义里用到就是用到），但不从中收集待复核项。
#
# 与 clippy 的分工：`cargo clippy -- -D warnings` 负责 `pub(crate)` 与私有项、
# 不可达分支；本脚本只补它看不见的 `pub` 面与 `allow(dead_code)` 的理由。
# `cargo udeps` 需要 nightly，未纳入 vendor（R-02），故本版不依赖它。
#
# 用法: python3 ci/checks/dead_code.py [根...]（缺省 system/tui）
# 退出码: 0 通过（复核清单不影响退出码）；1 有 D001 或根不存在。
#
# Author: Liren Wang <wangliren@spharx.cn>
# Version: 0.0.5
# ===========================================================================

from __future__ import annotations

import re
import sys
from pathlib import Path

# 判定器位于 ci/checks/：同层的 `no_stub` 随 sys.path[0] 取到，共用库 `lib`
# 在上一级，故显式补一次路径；夹具树保持同一布局。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import no_stub
from lib import rs_src, scan_src

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEFAULT = ("system/tui",)
ALLOW = re.compile(r"#!?\[\s*allow\s*\(([^)]*)\)")
PUB_FN = re.compile(
    r"^\s*pub\s+(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+([A-Za-z_]\w*)")
PUB_ITEM = re.compile(
    r"^\s*pub\s+(struct|enum|trait|type|static|const)\s+([A-Za-z_]\w*)")
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MARKS = ("理由:", "理由：", "reason:")


def mark(text: str) -> bool:
    for name in MARKS:
        pos = text.find(name)
        if pos >= 0 and text[pos + len(name):].strip():
            return True
    return False


def reason(prev: str, line: str) -> bool:
    if mark(line.partition("//")[2]):
        return True
    head = prev.strip()
    return head.startswith("//") and mark(head[2:])


def allow_hits(path: Path, lines: list[str]) -> list[str]:
    held = []
    for no, text in enumerate(lines, 1):
        hit = ALLOW.search(text)
        if not hit or "dead_code" not in hit.group(1):
            continue
        prev = lines[no - 2] if no >= 2 else ""
        if not reason(prev, text):
            held.append(f"{path}:{no}: D001 allow(dead_code) 缺理由"
                        "（同行或上一行注释写 `理由:`）")
    return held


def pub_items(path: Path, lines: list[str], cut) -> list[tuple[int, str]]:
    """产品代码里的 pub 项；tests/ 与 #[cfg(test)] 模块不收集。"""
    if "tests" in path.parts:
        return []
    found = []
    for no, text in enumerate(lines[:len(cut)], 1):
        for hit in (PUB_FN.search(text), PUB_ITEM.search(text)):
            if hit:
                found.append((no, hit.group(hit.lastindex)))
    return found


def scan(path: Path, counts: dict[str, int]):
    """一次读盘：更新引用计数，返回 (D001 命中, 待复核项)。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    for token in TOKEN.findall(no_stub.blank(raw)):
        counts[token] = counts.get(token, 0) + 1
    lines = raw.splitlines()
    cut = rs_src.split_tests(lines)
    return allow_hits(path, lines), pub_items(path, lines, cut)


def main(argv: list[str]) -> int:
    bases, bad = scan_src.bases(argv, ROOT, DEFAULT)
    files = 0
    issues = bad
    counts: dict[str, int] = {}
    pending: list[tuple[Path, int, str]] = []
    for base in bases:
        for path in scan_src.sources(base, (".rs",), scan_src.DEEP):
            files += 1
            try:
                held, named = scan(path, counts)
            except OSError as exc:
                print(f"{path}: {exc}", file=sys.stderr)
                issues += 1
                continue
            pending += [(path, no, name) for no, name in named]
            for line in held:
                print(line)
            issues += len(held)
    review = [item for item in pending if counts.get(item[2], 0) <= 1]
    for path, no, name in review:
        print(f"{path}:{no}: D100 pub {name} 无外部引用（人工复核）")
    print(f"dead-code: {files} file(s), {issues} issue(s), "
          f"{len(review)} review item(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
