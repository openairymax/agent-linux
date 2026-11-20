#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/cli.py — 检查器共用的命令行解析
#
# 门禁协议里 2 是「未知门禁」，参数问题一律是 5；argparse 默认把参数错误
# 当 2 抛给调用方，直接用会把两种情形混在一起。两个检查器各写一遍子类
# 只会多一份重复窗口，收到这里一处。
#
# 用法行的中文前缀也要统一：帮助输出里必须能搜到「用法:」，否则回归脚本
# 只能按英文 usage 断言，换个解析器就全红。
#
# Author: Liren Wang <wangliren@spharx.cn>
# Version: 0.0.5
# ===========================================================================
from __future__ import annotations

import argparse
import sys


class Parser(argparse.ArgumentParser):
    """参数错误按 CI 约定退 5，而不是 argparse 默认的 2。"""

    def error(self, message: str) -> None:
        print(f"未知参数: {message}", file=sys.stderr)
        raise SystemExit(5)


def parser(prog: str, usage: str, text: str) -> Parser:
    """带统一退出码与中文用法行的解析器。"""
    return Parser(prog=prog, usage=usage, description=text,
                  formatter_class=argparse.RawDescriptionHelpFormatter)
