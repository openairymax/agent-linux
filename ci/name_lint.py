#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/name_lint.py — 函数名长度门禁（N001，覆盖 shell 与 python）
#
# 0.0.4 收尾时全仓有 139 个函数名超过 15 字节，改完之后没有任何机制拦住
# 回潮：下一个 helper 只要叫 airy_assets_wanted，规范就被重新拉开一档，而
# 所有门禁照样全绿。本条把「函数名一般不超过 15 字节」变成提交期判据。
#
# 判据只有一条：函数名（不含括号与参数）超过 LIMIT 字节即 N001。字节数而
# 非字符数——过长的名字几乎都是英文词堆叠，用 utf-8 字节数与规范原文的
# 「15 字节」口径一致，也避免多字节标识符上出现两套度量。
#
# shell 按定义行取值：`name()` 与 `function name()` 两种写法都要认，`{` 可
# 与本行同行也可另起一行，故只判到右括号为止。python 走 AST：注释、字符串
# 与文档里的 `foo()` 不是定义，逐行正则会误判；ast 同时覆盖类方法、嵌套
# 函数与装饰器定义，判定口径与解释器的名字表一致。
#
# python 文件解析不了按缺陷计（E001），不静默跳过：语法坏的 .py 只会出现在
# 未提交完成的改动里，跳过等于给「门禁绿」开一个后门。
#
# 不扫 tests/ 与 fixture/：那里的函数名由被测语义决定（本门禁自己的回归就
# 要故意造长名），按产品代码标准要求只会逼着人为了过门禁改写断言。
# distro/ 由本门禁一并覆盖，其 run-lint.sh 不再放第二份实现：同一判据写两
# 遍，改阈值时必然漏改一处。
#
# 用法: python3 ci/name_lint.py [根...]（缺省 ci tools distro system）
# 退出码: 0 通过；1 有超长名或文件读不出来（解析失败按缺陷计，不静默跳过）。
#
# Author: Liren Wang <wangliren@spharx.cn>
# Version: 0.0.4
# ===========================================================================

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 15
SUFFIX = (".sh", ".py")
DEFAULT = ("ci", "tools", "distro", "system")
SKIP = frozenset(("tests", "fixture", "__pycache__", ".git", "node_modules"))

SH_DEF = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)")
# `function name {` 不带括号也是合法定名，两种写法都要认：漏掉这一种，门禁
# 只需换成另一种写法就能绕过。
SH_KW = re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*\)|\{|$)")
PY_DEF = (ast.FunctionDef, ast.AsyncFunctionDef)


def _sh(path: Path) -> list[tuple[int, str]]:
    found = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for lineno, raw in enumerate(text.splitlines(), 1):
        match = SH_DEF.match(raw) or SH_KW.match(raw)
        if match:
            found.append((lineno, match.group(1)))
    return found


def _py(path: Path) -> list[tuple[int, str]]:
    # read_text 与 ast.parse 的两次读盘合并：AST 只认 str，错误信息里要行号，
    # 交给 SyntaxError 自己带出，调用方按缺陷计。
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    return [(node.lineno, node.name) for node in ast.walk(tree)
            if isinstance(node, PY_DEF)]


def judge(path: Path) -> list[tuple[int, str]]:
    return _py(path) if path.suffix == ".py" else _sh(path)


def sources(base: Path) -> list[Path]:
    # 先剪枝再收集：distro 与 system 子仓里各有一条 tests 目录，先 rglob 再
    # 过滤会白走一遍它们的整棵子树。
    files: list[Path] = []
    stack = [base]
    while stack:
        for child in sorted(stack.pop().iterdir()):
            if child.is_dir():
                if child.name not in SKIP:
                    stack.append(child)
            elif child.suffix in SUFFIX:
                files.append(child)
    return files


def scan_base(base: Path) -> tuple[int, int]:
    files = 0
    total = 0
    for path in sources(base):
        files += 1
        try:
            found = judge(path)
        except OSError as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            total += 1
            continue
        except SyntaxError as exc:
            print(f"{path}:{exc.lineno}: E001 cannot parse ({exc.msg})",
                  file=sys.stderr)
            total += 1
            continue
        for lineno, name in found:
            size = len(name.encode("utf-8"))
            if size > LIMIT:
                print(f"{path}:{lineno}: N001 {name} ({size} bytes > {LIMIT})")
                total += 1
    return files, total


def main(argv: list[str]) -> int:
    files = 0
    total = 0
    for name in argv or list(DEFAULT):
        base = ROOT / name
        if not base.is_dir():
            print(f"skip: {name} 不存在", file=sys.stderr)
            continue
        got_files, got_total = scan_base(base)
        files += got_files
        total += got_total
    print(f"name: {files} file(s), {total} issue(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
