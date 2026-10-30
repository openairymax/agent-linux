#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/progress.sh — 长阻塞阶段的活性心跳
#
# 为什么需要它：容器内的 ISO 组装与 QEMU 内的装机各要 40-120 分钟，期间
# CI 控制台只剩「仍在运行」。GitHub 在 job 运行期间不提供日志下载
# （jobs/<id>/logs 返回 BlobNotFound），事后翻网页也只有「某一步花了 N 小时」
# 一行——「慢」与「挂死」在里面完全同形，归因只能靠猜。
#
# 本库不侵入被观察进程：按固定周期扫描产物目录里最新的 *.log，打印文件名、
# 当前大小、末行与停滞时长。子进程在写，末行就是它当下在做的事；写不动时，
# 停滞时长直接指出卡点。
#
# 用法:
#   . "${LIB}/progress.sh"
#   progress_start "${OUT}" 60
#   trap progress_stop EXIT
#   ... 长阻塞命令 ...
#   progress_stop
#
# 判据只有文件时间戳，不解析内容，因此对被观察进程零假设。
# ===========================================================================

PROGRESS_PERIOD=60
PROGRESS_PID=""

progress_line()
{
    printf 'hb: %s\n' "$*"
}

progress_span()
{
    local secs="$1"

    if [ "${secs}" -lt 60 ]; then
        printf '%ds' "${secs}"
    elif [ "${secs}" -lt 3600 ]; then
        printf '%dm%02ds' "$((secs / 60))" "$((secs % 60))"
    else
        printf '%dh%02dm' "$((secs / 3600))" "$(((secs % 3600) / 60))"
    fi
}

# 最新日志：按 mtime 取。同一秒内多文件并写时再按路径定序，保证同一个周期
# 的输出在重复运行间可比——心跳的价值全在「相邻两行能不能看出变化」。
progress_newest()
{
    find "$1" -type f -name '*.log' -printf '%T@ %p\n' 2>/dev/null |
        sort -k1,1n -k2,2 | tail -n 1 | cut -d' ' -f2-
}

progress_beat()
{
    local dir="$1" period="$2" started="${SECONDS}" now name size idle line

    while sleep "${period}"; do
        now="$(date +%s)"
        name="$(progress_newest "${dir}")"
        if [ -z "${name}" ]; then
            progress_line "已运行 $(progress_span "$((SECONDS - started))")，尚无日志文件"
            continue
        fi
        size="$(stat -c %s "${name}" 2>/dev/null || printf 0)"
        idle="$((now - $(stat -c %Y "${name}" 2>/dev/null || printf "${now}")))"
        line="$(tail -n 1 "${name}" 2>/dev/null | tr -d '\r' | cut -c1-100)"
        printf 'hb: 已运行 %s %s %s 停滞 %s 末行: %s\n' \
            "$(progress_span "$((SECONDS - started))")" \
            "${name#"${dir}"/}" "$(progress_size "${size}")" \
            "$(progress_span "${idle}")" "${line:-<空>}"
    done
}

progress_size()
{
    local bytes="$1"

    if [ "${bytes}" -lt 1024 ]; then
        printf '%dB' "${bytes}"
    elif [ "${bytes}" -lt 1048576 ]; then
        printf '%dKiB' "$((bytes / 1024))"
    else
        printf '%dMiB' "$((bytes / 1048576))"
    fi
}

progress_start()
{
    local dir="$1" period="${2:-${PROGRESS_PERIOD}}"

    [ -d "${dir}" ] || return 0
    progress_stop
    progress_beat "${dir}" "${period}" &
    PROGRESS_PID=$!
}

progress_stop()
{
    [ -n "${PROGRESS_PID}" ] || return 0
    kill "${PROGRESS_PID}" 2>/dev/null
    wait "${PROGRESS_PID}" 2>/dev/null
    PROGRESS_PID=""
    return 0
}
