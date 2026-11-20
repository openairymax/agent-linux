#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/lib/three-state.sh — 门禁三态协议的唯一声明
#
# 三态是判定结论的全集：0 通过、1 判负、3 不适用。第四类结果——参数不
# 合法、前置产物缺失、校验器不在——不是判定结论，沿用 ci/lib/log.sh 的
# CI_EINVAL（5）。把它并进「不适用」，一个拼错的参数就会以跳过的名义
# 静默放行；并进「判负」，编排层又分不清是产物坏了还是调用写错了。
#
# 「不适用」只给「这份检出里确实无事可判」用，例如方案册不在检出范围。
# 判不了是 5，不该判才是 3：两者的区分是跳过能否被信任的前提。
#
# 调用方：ci/gates 下声明了 3 的门禁，以及 ci/gates/tri-state.sh。
# ===========================================================================

# shellcheck source=../../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/log.sh"

# 三个结论码。数字与调用侧约定一致，不允许门禁自行改写。
readonly TS_PASS=0
readonly TS_FAIL=1
readonly TS_NA=3

# ts_name RC —— 退出码的读数，用于汇总行与日志；协议外的码按 error 读。
ts_name()
{
    case "$1" in
    "${TS_PASS}") printf 'pass' ;;
    "${TS_FAIL}") printf 'fail' ;;
    "${TS_NA}") printf 'na' ;;
    *) printf 'error' ;;
    esac
}

# ts_na REASON... —— 声明本次检定不适用并退 3。原因必写：跳过没有原因，
# 下一个读日志的人只能重新判一遍这个门禁到底该不该跑。
ts_na()
{
    say "不适用: $*"
    exit "${TS_NA}"
}
