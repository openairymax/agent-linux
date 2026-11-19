#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/dirs.sh — 扫描型门禁的位置参数解析
#
# no-stub 与 dead-code 的参数面完全一致（零个或多个 DIR，-h 帮助），两份解析
# 循环逐行相同，正好撞上质量度量的重复窗口判据。解析收到这里一处；usage 文本
# 与退出码仍由调用方自决：
#
#   dir_args "$@"   DIR_ARGS 收集目录（相对路径由调用方的扫描器按仓库根解析），
#                   -h/--help 置 DIR_HELP=1，未知选项返回 1 且不自行退出。
#
# 调用方必须先 source ci/lib/log.sh：err 与 need_dir 来自那里。返回 1 时由
# 调用方决定退出码——参数错误的码是各门禁自己的约定，不由公共库统一。
# ===========================================================================

# shellcheck disable=SC2034  # DIR_ARGS 与 DIR_HELP 由调用方在 source 后读取
DIR_ARGS=()
# shellcheck disable=SC2034  # 同上
DIR_HELP=0


dir_args()
{
    DIR_ARGS=()
    # shellcheck disable=SC2034  # 同上
    DIR_HELP=0
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -h|--help) DIR_HELP=1; return 0 ;;
            -*) err "未知参数: $1"; return 1 ;;
            *) need_dir "DIR" "$1" || return 1; DIR_ARGS+=("$1"); shift ;;
        esac
    done
}
