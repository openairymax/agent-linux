#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/lint_drive.py — 逐根扫描并汇总的公共驱动
#
# name_lint 与 header_lint 曾各自持有一份逐字相同的 main：同一段「根 → 目录
# 存在性 → 逐文件 → 汇总 → 退出码」在两位门禁里各写一遍，连跳过提示都一样。
# 判据本身不同，驱动完全相同，复制出来的驱动只会在某一处被改对：改了退出码
# 或改成绝对路径，另一份仍按旧约定走，CI 于是看到同一类失败两种口径。
#
# 调用方只提供判据（scan_base）与标签：扫描根、缺目录的跳过提示、汇总行与
# 退出码都由这里决定。相对根按调用方给出的 ROOT 解析，绝对根原样使用——两种
# 写法在本地复跑时都会用到。
#
# 用法: 由 name_lint.py / header_lint.py 调用，不单独执行。
# ===========================================================================

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

Scan = Callable[[Path], tuple[int, int]]


def run(label: str, roots: list[str], scan: Scan, root: Path) -> int:
    """逐根调用 scan 并汇总 (文件数, 问题数)；有问题即返回 1。"""
    files = 0
    total = 0
    for name in roots:
        base = Path(name)
        if not base.is_absolute():
            base = root / name
        if not base.is_dir():
            print(f"skip: {name} 不存在", file=sys.stderr)
            continue
        got_files, got_total = scan(base)
        files += got_files
        total += got_total
    print(f"{label}: {files} file(s), {total} issue(s)")
    return 1 if total else 0
