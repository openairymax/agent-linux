#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/scan_src.py — 源码遍历与扫描根的共用实现
#
# 遍历口径原先在 ci/checks/name_lint.py、ci/checks/quality_metrics.py 里各写了一份，V-06 的
# 零桩与死代码扫描如果照抄就是第三、第四份：四处都跳过 vendor/ 与 target/，
# 四处都要处理相对根，改一处必然漏改三处。这里收成一份。
#
# 两套跳过集不是冗余，口径本就不同：
#   SKIP 度量口径：tests/ 与 fixture(s)/ 里的函数名、复杂度由被测语义决定，
#        按产品标准要求只会逼人改写断言；
#   DEEP 扫描口径：零桩与死代码必须看进 tests/——`unreachable!` 的放行边界
#        正是 tests/ 目录与 `#[cfg(test)]` 模块，引用计数也要算上测试里的使用。
#
# 用法: import scan_src; scan_src.sources(base, suffix, skip)
#
# Author: Liren Wang <wangliren@spharx.cn>
# Version: 0.0.5
# ===========================================================================

from __future__ import annotations

import sys
from pathlib import Path

SKIP = frozenset(("tests", "fixture", "fixtures", "vendor", "target",
                  "__pycache__", ".git", "node_modules"))
DEEP = frozenset(("vendor", "target", "__pycache__", ".git", "node_modules"))
SUFFIX = (".sh", ".py", ".rs")


def sources(base: Path, suffix: tuple[str, ...] = SUFFIX,
            skip: frozenset[str] = SKIP):
    """先剪枝再收集：子仓的 tests 整棵子树不必走进去再过滤。"""
    stack = [Path(base)]
    while stack:
        for ent in sorted(stack.pop().iterdir()):
            if ent.is_dir():
                if ent.name not in skip:
                    stack.append(ent)
            elif ent.suffix in suffix and ent.is_file():
                yield ent


def bases(argv: list[str], root: Path,
          default: tuple[str, ...]) -> tuple[list[Path], int]:
    """解析扫描根并返回 (有效根, 错误数)；相对路径按仓库根解析。

    根不存在按错误计而不是跳过：参数写错却拿到绿灯，比多一行报错更坏。
    """
    got: list[Path] = []
    bad = 0
    for name in argv or list(default):
        base = Path(name)
        if not base.is_absolute():
            base = root / name
        if not base.exists():
            print(f"error: 根不存在: {name}", file=sys.stderr)
            bad += 1
            continue
        got.append(base)
    return got, bad
