#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/checks/header_lint.py — 文件头许可证与版权判据（H001/H002）
#
# 编码规范要求每个源文件头部带 SPDX 许可证标识与版权行，此前没有任何执行
# 者：720 个自有源文件全部只有许可证行、没有版权行，tools/governance 下还有一个
# 文件连许可证行都没有。没有判据的规范等于没有规范，下一个新文件照样能只
# 写许可证，而所有门禁保持全绿。
#
# 判据只有两条：
#   H001 前 HEAD 行内没有非空的 `SPDX-License-Identifier:`；
#   H002 前 HEAD 行内既没有 `SPDX-FileCopyrightText:`，也没有
#        `Copyright (c) <年份>` / `Copyright © <年份>`。
# 两行都必须落在注释里：许可证文本作为字符串常量出现不构成文件头声明。
#
# 只认 `.sh`、`.py`、`.rs` 与 name_lint.py 同一口径，扫描根也一致（多一个
# sdk/：生成产物已带许可证与版权，纳入判据才能保证生成器不倒退）。kernel/
# 等子模块是上游源码，版权不属于本项目，不扫；vendor/ 与 target/ 由
# scan_src 的 DEEP 口径排除。
#
# 用法: python3 ci/checks/header_lint.py [根...]（缺省 ci tools distro sdk system）
# 退出码: 0 通过；1 有缺失或文件读不出来。
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

from lib import lint_drive, scan_src

ROOT = Path(__file__).resolve().parent.parent.parent
HEAD = 10
DEFAULT = ("ci", "tools", "distro", "sdk", "system")
COMMENT = ("#", "//", "/*", "*")
LIC = re.compile(r"\bSPDX-License-Identifier:\s*(\S.*)$")
CPY = re.compile(r"\bSPDX-FileCopyrightText:\s*(\S.*)$")
COPY = re.compile(r"\bCopyright\s*(?:\(c\)|©)\s*\d{4}", re.IGNORECASE)


def _mark(line: str, pat: re.Pattern[str]) -> bool:
    """注释行里命中才算声明；字符串常量里的同名文本不构成文件头。"""
    return line.strip().startswith(COMMENT) and pat.search(line) is not None


def judge(path: Path) -> list[str]:
    """返回该文件缺失的判据编号，空列表表示两行都在。"""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:HEAD]
    codes = []
    if not any(_mark(line, LIC) for line in lines):
        codes.append("H001")
    if not any(_mark(line, CPY) or _mark(line, COPY) for line in lines):
        codes.append("H002")
    return codes


def scan_base(base: Path) -> tuple[int, int]:
    files = 0
    total = 0
    for path in scan_src.sources(base, skip=scan_src.DEEP):
        files += 1
        try:
            codes = judge(path)
        except OSError as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            total += 1
            continue
        for code in codes:
            print(f"{path}:1: {code} 文件头缺{'许可证' if code == 'H001' else '版权'}行")
            total += 1
    return files, total


def main(argv: list[str]) -> int:
    return lint_drive.run("header", argv or list(DEFAULT), scan_base, ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
