#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/opts.sh — 「静态面 + 已安装树」门禁的通用参数解析
#
# 这是 0.0.4 门禁最常见的形状：--source 判仓库里的配方与台账，--root 判装好
# 的树，两者可同时给。session-closure 与 wallpaper-set 的这段解析原本逐字
# 相同（含「至少给出一个」的兜底），两份各自演化时必然只改一边——一边加了
# 新选项，另一边的用法文本就与实现对不上。
#
# 解析与兜底集中在此；usage() 仍留在各门禁，因为用法文本属于门禁自己。
# 调用约定：先 source lib/log.sh（依赖 need_dir / err / CI_EINVAL），并已声明
# SOURCE / ROOT 变量与 usage()；opts_tree 直接给这两个变量赋值。
#
# opts_src 是内核源码类门禁的形状：只有一个 --src，缺省 <repo>/kernel。
# kernel-obs 与 kernel-debugfs 的用法行与解析循环逐行相同，两份各自演化时
# 必然只改一边；用法文本也一并由本函数生成，两点因此不会再分叉。
# ===========================================================================

# opts_tree ARGS... —— 解析 --source/--root；两者至少给出一个。
#
# 缺参、目录不存在、未知选项、一个目录都没给，一律退 CI_EINVAL 并打印用法，
# 与「判定失败」（退 1）区分开：编排层据此判断是该修命令还是该修系统。
opts_tree()
{
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --source) need_dir "$1" "${2:-}"; SOURCE="$2"; shift 2 ;;
            --root)   need_dir "$1" "${2:-}"; ROOT="$2"; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) err "未知参数: $1"; usage >&2; exit "${CI_EINVAL}" ;;
        esac
    done
    [ -n "${SOURCE}" ] || [ -n "${ROOT}" ] || {
        err "至少给出 --source 或 --root"
        usage >&2
        exit "${CI_EINVAL}"
    }
}

# opts_src LABEL ARGS... —— 内核源码类门禁的 --src 解析、用法与目录校验。
#
# --src 指向内核源码树，缺省由调用方预置为 <repo>/kernel；目录必须存在。
# LABEL 是门禁自身的文件名，用来生成用法行，因此每个门禁的帮助输出仍指向
# 自己。帮助退 0、参数不合法退 CI_EINVAL，返回即表示 SRC 可用。
opts_src()
{
    local label="$1"

    shift
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -h|--help)
                printf '用法: ci/gates/%s [--src DIR]\n' "${label}"
                exit 0
                ;;
            --src) need_dir "$1" "${2:-}"; SRC="$2"; shift 2 ;;
            *)
                err "未知参数: $1（可用: --src）"
                printf '用法: ci/gates/%s [--src DIR]\n' "${label}" >&2
                exit "${CI_EINVAL}"
                ;;
        esac
    done
    [ -d "${SRC}" ] || {
        err "内核源码目录不存在: ${SRC}"
        exit "${CI_EINVAL}"
    }
}
