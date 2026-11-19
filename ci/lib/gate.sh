#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/gate.sh — 判负型门禁的共用骨架
#
# 检查器落在 python 里的门禁只有两处不同：检查器路径与通过语。参数解析、
# 帮助分支、检查器存在性与转调逐行相同；同一段逻辑写两遍，改一处必然漏改
# 另一处，而且会把质量棘轮的重复片段基数撑起来。
#
# 用法：调用方先 source log.sh（`say` 的日志前缀 CI_NS 必须取门禁自己的名字，
# 不能由本文件代为确定），再 source 本文件，定义 ROOT（本文件的 BASH_SOURCE
# 指向 ci/lib，算不出仓库根），随后 `gate_py <检查器相对路径> "$@"`，通过时
# 自己 say 一句。退出码由 gate_py 直接给出：0 通过、1 有命中、5 参数不满足。
# ===========================================================================

readonly EINVAL=5
# shellcheck source=dirs.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/dirs.sh"

usage()
{
    printf '用法: ci/gates/%s [DIR...]（缺省 system/tui）\n' "$(basename "$0")"
}

gate_py()
{
    local check="$1"
    shift
    dir_args "$@" || exit "${EINVAL}"
    if [ "${DIR_HELP}" -eq 1 ]; then
        usage
        exit 0
    fi
    need_file "${ROOT}/${check}" 检查器
    need_cmd python3
    python3 "${ROOT}/${check}" "${DIR_ARGS[@]}" || exit 1
}
