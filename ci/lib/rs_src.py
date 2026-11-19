#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/rs_src.py — rust 源文件的定义行与测试模块边界
#
# 目的：name_lint 要按定义行取函数名，quality_metrics 要按定义行起算函数体。
# 两者对「什么算函数定义」「测试模块到哪为止」必须是同一口径，否则同一个
# 函数在一道门禁里被数进去、在另一道里被漏掉，而两道门禁都是绿的。
#
# 测试模块作为文件的最后一项，`#[cfg(test)]` 起至文件末尾不参与产品代码
# 度量。这条不是权宜：括号配平要正确处理 raw string 与嵌套注释，行级扫描
# 做不到；与其在词法上猜，不如把结构约束写进编码规范并在此断言。
#
# 调用方：ci/name_lint.py 与 ci/quality_metrics.py（同目录，均为独立脚本，
# 故由调用方把 ci/lib 加进 sys.path）。
# ===========================================================================
"""Rust source helpers shared by the lint and metrics gates."""

import re

# `pub(crate) async unsafe extern "C" fn name` 的修饰词顺序有几种合法排列，
# 逐个允许但整体锚在行首；缩进后的 `fn` 也可能是函数指针类型或 trait 方法
# 声明，trait 方法同样算定义，一并计入。第一个分组是缩进，供函数体分段。
RS_DEF = re.compile(
    r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:default\s+)?(?:const\s+)?"
    r"(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?"
    r"fn\s+([A-Za-z_][A-Za-z0-9_]*)")
RS_TEST = re.compile(r"^\s*#\[cfg\(test\)\]")
RS_MOD = re.compile(r"^\s*(?:pub\s+)?mod\s+[A-Za-z_][A-Za-z0-9_]*\s*\{")
RS_GAP = 3


def split_tests(lines):
    """截到末尾测试模块之前；没有测试模块时原样返回。

    起点取 `#[cfg(test)]` 与其后 RS_GAP 行内出现的 `mod …{`；只找起点不
    配平，文件末尾才放测试模块是编码规范的一部分，这里断言它。
    """
    for idx, raw in enumerate(lines):
        hit = RS_TEST.match(raw)
        if not hit:
            continue
        near = [raw[hit.end():], *lines[idx + 1: idx + RS_GAP]]
        if any(RS_MOD.match(line) for line in near):
            return lines[:idx]
    return lines


def defs(lines):
    """(行号, 函数名)，行号从 1 起；调用方先按 split_tests 截断。"""
    return [(no, hit.group(2)) for no, raw in enumerate(lines, 1)
            if (hit := RS_DEF.match(raw))]
