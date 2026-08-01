#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""
check-copyright.py — 检查文档文件的版权头

被 mgmt-orchestrator.yml 引用。遍历 ``--root`` 目录下的 .md 文件，
检查第 1 行是否包含 "Copyright" 或 "SPDX-License-Identifier"。

退出码：
    0 — 全部 .md 文件均含版权头
    1 — 存在缺失版权头的 .md 文件
    2 — 参数错误或根目录不可访问
"""

import argparse
import sys
from pathlib import Path


def has_copyright_header(path):
    """检查文件第 1 行是否含 Copyright 或 SPDX-License-Identifier。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return False
    line = first.lower()
    return "copyright" in line or "spdx-license-identifier" in line


def iter_markdown_files(root):
    """遍历 root 下所有 .md 文件，跳过 .git 等隐藏目录。"""
    for p in sorted(Path(root).rglob("*.md")):
        # 跳过任何路径段以 '.' 开头的目录（如 .git、.github）
        if any(part.startswith(".") for part in p.parts):
            continue
        yield p


def main():
    parser = argparse.ArgumentParser(
        description="检查文档文件的版权头（第 1 行）"
    )
    parser.add_argument(
        "--root", default="docs",
        help="待检查目录（默认 docs）",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: --root 目录不存在：{args.root}", file=sys.stderr)
        return 2

    missing = []
    total = 0
    for path in iter_markdown_files(root):
        total += 1
        if not has_copyright_header(path):
            missing.append(path)

    print(f"扫描 {total} 个 .md 文件，{len(missing)} 个缺失版权头")
    if missing:
        print("缺失版权头的文件：")
        for p in missing:
            try:
                print(f"  {p.relative_to(root)}")
            except ValueError:
                print(f"  {p}")
        print(
            "提示：在第 1 行添加形如 "
            "'Copyright (c) 2025-2026 SPHARX Ltd.' "
            "或 'SPDX-License-Identifier: ...'",
            file=sys.stderr,
        )
        return 1

    print("OK: 所有 .md 文件均含版权头")
    return 0


if __name__ == "__main__":
    sys.exit(main())
