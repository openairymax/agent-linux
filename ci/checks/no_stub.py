#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/checks/no_stub.py — 零桩扫描（QG-02 与 §3.4 判负口径的判定实现）
#
# 「禁桩」是本版的铁律，但此前只有文字：`todo!()` 与 `unimplemented!()` 编译
# 期合法，运行时才 panic；恒 `Ok(())` 的函数更像实现——调用方拿到成功、用户
# 看到绿灯，功能却从未存在。这类形态无法靠编译器和 clippy 拦住，必须扫描。
#
# 判据五条，全部在清理过注释与字符串字面量的源文本上匹配，避免把文档里的
# `todo!()` 当成缺陷：
#
#   S001 `todo!()`           一律判负，测试代码也不例外：桩用例不是用例。
#   S002 `unimplemented!()`  同上。
#   S003 `unreachable!()`    只允许出现在测试上下文（tests/ 目录或文件末尾的
#                            `#[cfg(test)]` 模块）。产品代码里的不可达断言等价
#                            于「这条分支我没实现」，正是禁桩要拦的形态。
#   S004 `unsafe`            一律判负。工作区置 `unsafe_code = "forbid"`，
#                            任何一处都同时是构建失败；这里提前给出位置，并
#                            指向豁免的唯一出口（`// SAFETY:` 注释 + 登记
#                            system/tui/UNSAFE.md），而不是让构建报错成为唯一
#                            提示。
#   S005 恒 `Ok(())`         函数体规范化后只剩 `Ok(())`（或 `return Ok(())`）
#                            即判负。判定范围限产品代码：测试脚手架里的空断言
#                            辅助函数不承载功能语义。
#
# 清理器只处理词法，不建语法树：注释（含嵌套块注释）、普通字符串、原始字符串
# （`r"…"`、`r#"…"#`、`b"…"` 等）与字符字面量都被替换为空格，长度与换行保持
# 不变，故行号与原文一致。`'a` 这类生命周期不按字符字面量处理。
#
# 遍历口径见 ci/lib/scan_src.py：本脚本必须看进 tests/，故用 DEEP 跳过集；
# vendor/ 与 target/ 一律不扫——前者是第三方快照，后者是构建产物。
#
# 用法: python3 ci/checks/no_stub.py [根...]（缺省 system/tui）
# 退出码: 0 通过；1 有命中或根不存在；门禁脚本对参数错误另退 5。
#
# Author: Liren Wang <wangliren@spharx.cn>
# Version: 0.0.5
# ===========================================================================

from __future__ import annotations

import re
import sys
from pathlib import Path

# 判定器位于 ci/checks/，把上级 ci/ 加入 sys.path：共用库 `lib` 与同层
# 的 `no_stub` 都按包名导入，夹具树也保持同一布局。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import rs_src, scan_src

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT = ("system/tui",)
TODO = re.compile(r"\btodo!")
UNIMPL = re.compile(r"\bunimplemented!")
UNREACH = re.compile(r"\bunreachable!")
UNSAFE = re.compile(r"\bunsafe\b")


def hide(out: list[str], start: int, end: int) -> None:
    for k in range(start, end):
        if out[k] != "\n":
            out[k] = " "


def block(text: str, i: int) -> int:
    """嵌套块注释的结束位置。"""
    depth = 1
    j = i + 2
    while j < len(text) and depth:
        if text.startswith("/*", j):
            depth += 1
            j += 2
        elif text.startswith("*/", j):
            depth -= 1
            j += 2
        else:
            j += 1
    return j


def raw_end(text: str, j: int) -> int:
    """j 处 `r` 起的原始字符串结束位置。"""
    k = 0
    j += 1
    while j < len(text) and text[j] == "#":
        k += 1
        j += 1
    if j >= len(text) or text[j] != '"':
        return -1
    hit = text.find('"' + "#" * k, j + 1)
    return len(text) if hit < 0 else hit + 1 + k


def quot_end(text: str, j: int) -> int:
    """j 处 `"` 起的普通字符串结束位置。"""
    j += 1
    while j < len(text):
        if text[j] == "\\":
            j += 2
        elif text[j] == '"':
            return j + 1
        else:
            j += 1
    return len(text)


def lit_end(text: str, i: int) -> int:
    """i 处若是字符串字面量返回结束位置，否则 -1。"""
    j = i + 1 if text[i] == "b" else i
    if j >= len(text):
        return -1
    if text[j] == "r":
        return raw_end(text, j)
    return quot_end(text, j) if text[j] == '"' else -1


def chr_end(text: str, i: int) -> int | None:
    """i 处的 `'` 若是字符字面量返回结束位置；生命周期返回 None。"""
    n = len(text)
    if i + 1 >= n:
        return None
    if text[i + 1] == "\\":
        j = i + 2
        while j < n and text[j] != "'":
            j += 1
        return min(j + 1, n)
    if i + 2 < n and text[i + 2] == "'":
        return i + 3
    return None


def step(text: str, i: int) -> int:
    """i 处若起一段注释或字面量返回其结束位置，否则返回 i。"""
    ch = text[i]
    if text.startswith("//", i):
        j = text.find("\n", i)
        return len(text) if j < 0 else j
    if text.startswith("/*", i):
        return block(text, i)
    if ch == '"' or ch in "rb":
        j = lit_end(text, i)
        return i if j < 0 else j
    if ch == "'":
        j = chr_end(text, i)
        return i if j is None else j
    return i


def blank(text: str) -> str:
    """注释与字面量替换为空格，长度与换行不变。"""
    out = list(text)
    i = 0
    while i < len(text):
        j = step(text, i)
        if j > i:
            hide(out, i, j)
        i = max(i + 1, j)
    return "".join(out)


def span(clean: str, begin: int) -> tuple[int, int] | None:
    """begin 之后第一个函数体的 (左花括号, 右花括号) 位置。

    先遇到分号说明是声明而非定义，返回 None。
    """
    idx = clean.find("{", begin)
    semi = clean.find(";", begin)
    if idx < 0 or 0 <= semi < idx:
        return None
    depth = 0
    for j in range(idx, len(clean)):
        if clean[j] == "{":
            depth += 1
        elif clean[j] == "}":
            depth -= 1
            if depth == 0:
                return idx, j
    return None


def okbody(clean: str, defs) -> list[int]:
    """函数体规范化后只剩 Ok(()) 的定义行号。"""
    starts = [0]
    for k, ch in enumerate(clean):
        if ch == "\n":
            starts.append(k + 1)
    held = []
    for no, _name in defs:
        found = span(clean, starts[no - 1])
        if found is None:
            continue
        body = "".join(clean[found[0] + 1:found[1]].split()).rstrip(";")
        if body in ("Ok(())", "returnOk(())"):
            held.append(no)
    return held


def macros(path: Path, clean: str, cut) -> list[str]:
    """S001–S004 的逐行判定。"""
    test = "tests" in path.parts
    # split_tests 截到 `#[cfg(test)]` 之前，len(cut) 即产品区的最后一行号；
    # 行号超过它就是测试模块内，不再受 S003 约束。没有测试模块时上界等于
    # 总行数，等价于整份文件都按产品代码判。
    tail = len(cut)
    held = []
    for no, text in enumerate(clean.splitlines(), 1):
        if TODO.search(text):
            held.append(f"{path}:{no}: S001 todo! 是桩，须实现或删除")
        if UNIMPL.search(text):
            held.append(f"{path}:{no}: S002 unimplemented! 是桩，须实现或删除")
        if UNREACH.search(text) and not test and no <= tail:
            held.append(f"{path}:{no}: S003 产品代码的 unreachable!，须实现该分支")
        if UNSAFE.search(text):
            held.append(f"{path}:{no}: S004 unsafe 需 // SAFETY: 注释并登记 "
                        "system/tui/UNSAFE.md")
    return held


def judge(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    clean = blank(raw)
    cut = rs_src.split_tests(lines)
    held = macros(path, clean, cut)
    if "tests" not in path.parts:
        held += [f"{path}:{no}: S005 函数体恒 Ok(())，是桩"
                 for no in okbody(clean, rs_src.defs(cut))]
    return held


def main(argv: list[str]) -> int:
    bases, bad = scan_src.bases(argv, ROOT, DEFAULT)
    files = 0
    issues = bad
    for base in bases:
        for path in scan_src.sources(base, (".rs",), scan_src.DEEP):
            files += 1
            try:
                found = judge(path)
            except OSError as exc:
                print(f"{path}: {exc}", file=sys.stderr)
                issues += 1
                continue
            for line in found:
                print(line)
            issues += len(found)
    print(f"no-stub: {files} file(s), {issues} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
