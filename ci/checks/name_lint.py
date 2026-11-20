#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/checks/name_lint.py — 函数名长度门禁（N001，覆盖 shell、python 与 rust）
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
# rust 同样按定义行取值，但测试模块除外：`#[cfg(test)] mod tests` 里的用例名
# 由被测语义决定，写成 rejects_unknown_channel 才是好名字。判定口径取「紧邻
# 的 `#[cfg(test)]` + `mod …{` 起至文件末尾不再计入」，也就是把测试模块作为
# 文件的最后一项——这条不是权宜：括号配平要正确处理 raw string 与嵌套注释，
# 行级扫描做不到，与其在词法上猜，不如把结构约束写进编码规范并在此断言。
# 该判据与 quality_metrics.py 共用 ci/lib/rs_src.py 一份实现。
#
# 不扫 tests/ 与 fixture/：那里的函数名由被测语义决定（本门禁自己的回归就
# 要故意造长名），按产品代码标准要求只会逼着人为了过门禁改写断言。
# 不扫 vendor/ 与 target/：前者是第三方源码快照，规范只约束自有代码；后者是
# 构建产物，扫它等于让一次成功构建把门禁拖红。
# distro/ 由本门禁一并覆盖，其 run-lint.sh 不再放第二份实现：同一判据写两
# 遍，改阈值时必然漏改一处。
#
# 用法: python3 ci/checks/name_lint.py [根...]（缺省 ci tools distro system）
# 退出码: 0 通过；1 有超长名或文件读不出来（解析失败按缺陷计，不静默跳过）。
#
# Author: Liren Wang <wangliren@spharx.cn>
# Version: 0.0.5
# ===========================================================================

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# 定义行与测试模块边界的判据与 quality_metrics.py 共用一份（ci/lib/rs_src.py）：
# 两份实现必然在某个边界上分叉，而两道门禁都绿时没人会发现。
# 判定器位于 ci/checks/，把上级 ci/ 加入 sys.path：共用库 `lib` 与同层
# 的 `no_stub` 都按包名导入，夹具树也保持同一布局。
_CI = Path(__file__).resolve().parent.parent
if str(_CI) not in sys.path:
    sys.path.insert(0, str(_CI))

from lib import lint_drive, rs_src, scan_src

ROOT = Path(__file__).resolve().parent.parent.parent
LIMIT = 15
DEFAULT = ("ci", "tools", "distro", "system")

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


def _rs(path: Path) -> list[tuple[int, str]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return rs_src.defs(rs_src.split_tests(lines))


def judge(path: Path) -> list[tuple[int, str]]:
    if path.suffix == ".rs":
        return _rs(path)
    return _py(path) if path.suffix == ".py" else _sh(path)


def scan_base(base: Path) -> tuple[int, int]:
    files = 0
    total = 0
    for path in scan_src.sources(base):
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
    return lint_drive.run("name", argv or list(DEFAULT), scan_base, ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
