#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/profile.sh — 验收剖面归一化（单一事实源）
#
# 剖面只有两种：0 产品 ISO、1 验收 ISO。调用方以环境变量 ACCEPTANCE 声明
# （1|true|yes 为验收，其余为产品），命令行的 --acceptance 由各 gate 自行
# 置 1 后归一，避免一处声明、另一处又当字符串比较。
#
# 这个库存在的原因：同一段 case 曾在 assemble/accept/tap-extract 各写一份，
# 其中两份在归一之前先 `ACCEPTANCE=0` 赋初值，把 workflow 传进来的 true
# 覆盖成 0——于是验收轮装的是产品 ISO、断言的也是产品标记，会话内冒烟
# 从未执行，TAP 为空才在末端暴露。归一函数只读不写：默认值只在调用方
# 未声明时兜底，绝不覆盖既有值。
# ===========================================================================

airy_profile()
{
    case "${ACCEPTANCE:-0}" in
        1|true|yes) printf '1\n' ;;
        *)          printf '0\n' ;;
    esac
}
