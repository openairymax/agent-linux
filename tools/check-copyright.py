#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
"""
check-copyright.py — 检查管理仓自有 Markdown 文档的版权头

被 mgmt-orchestrator.yml 引用。仅枚举 ``--root`` 所在 git 仓库中
**被跟踪**的 .md 文件（``git ls-files``），因此天然排除子模块内部内容与
未跟踪文件；再跳过路径段以 ``.`` 开头的文件（如 ``.github/`` 下的
front-matter 模板），最后检查每个文件的**第 1 行**是否包含
"Copyright" 或 "SPDX-License-Identifier"。

约定：管理仓（agent-linux）自有 .md 的第 1 行必须是版权头或 SPDX 标识。
权威依据：docs/docs-linux/50-engineering-standards/05-development-process.md
的 OS-CHK-DOC-01（Copyright 头部规则）。

退出码：
    0 — 全部受检 .md 文件均含版权头
    1 — 存在缺失版权头的 .md 文件
    2 — 参数错误、root 不可访问或非 git 仓库
"""

import argparse
import subprocess
import sys
from pathlib import Path


def list_tracked_markdown(root):
    """返回 root 所在 git 仓库中被跟踪的 .md 相对路径列表。

    非 git 仓库或 git 不可用时返回 (None, 错误信息)。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", "*.md"],
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, f"git 不可用或超时：{exc}"
    if proc.returncode != 0:
        return None, proc.stderr.decode("utf-8", errors="replace").strip()
    raw = proc.stdout.decode("utf-8", errors="replace")
    return [p for p in raw.split("\0") if p], None


def is_hidden_path(rel_path):
    """路径任一段以 '.' 开头即视为隐藏（如 .github/、.git/）。"""
    return any(part.startswith(".") for part in Path(rel_path).parts)


def has_copyright_header(path):
    """检查文件第 1 行是否含 Copyright 或 SPDX-License-Identifier。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return False
    line = first.lower()
    return "copyright" in line or "spdx-license-identifier" in line


def main():
    parser = argparse.ArgumentParser(
        description="检查管理仓自有 .md 文件的版权头（第 1 行）"
    )
    parser.add_argument(
        "--root", default=".",
        help="git 仓库根目录（默认当前目录）",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: --root 目录不存在：{args.root}", file=sys.stderr)
        return 2

    tracked, err = list_tracked_markdown(root)
    if err is not None:
        print(f"ERROR: 无法枚举被跟踪的 .md 文件：{err}", file=sys.stderr)
        return 2

    missing = []
    total = 0
    for rel in tracked:
        if is_hidden_path(rel):
            continue
        total += 1
        if not has_copyright_header(root / rel):
            missing.append(rel)

    print(f"扫描 {total} 个被跟踪的 .md 文件，{len(missing)} 个缺失版权头")
    if missing:
        print("缺失版权头的文件：")
        for rel in missing:
            print(f"  {rel}")
        print(
            "提示：在第 1 行添加形如 "
            "'Copyright (c) 2025-2026 SPHARX Ltd.' "
            "或 'SPDX-License-Identifier: ...'",
            file=sys.stderr,
        )
        return 1

    print("OK: 所有受检 .md 文件的第 1 行均含版权头")
    return 0


if __name__ == "__main__":
    sys.exit(main())
