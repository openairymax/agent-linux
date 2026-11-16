#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/timing.sh — 门禁分段计时
#
# 用法:
#   . ci/lib/timing.sh
#   timing_start <段名>
#   timing_end <段名>
#   timing_sum
#
# 每段一对 start/end；同名多次 end 会累加到同一段。timing_sum 在末行打印
# 可解析的汇总:
#   timing: N segments, total X.XXXs, top <段名> Y.YYYs
#
# AIRY_TIMING_OUT 指定落盘文件时，每段追加一行 TSV:
#   段名 <TAB> 秒（三位小数） <TAB> 出处
# 前两列与 ci/baseline/build-segments.tsv 同名（该表第三列是基线出处，
# 本表写入 CI_NS，语义可映射）。本地缺省不落盘，避免污染只读仓库；路径
# 落在仓库内时跳过并告警。GitHub Actions 中未显式指定时写入
# ${RUNNER_TEMP}/airy-timing-<GITHUB_JOB>.tsv，供 artifact 步骤取用。
#
# 并发：状态只在当前进程内，门禁由 ci/ci.sh 顺序调用，不共享计时槽。
# 多进程追加同一文件时每行一次 write（远小于 PIPE_BUF），行不会互相截断；
# 跨进程顺序不保证，也不需要——每行自带段名与耗时。
# ===========================================================================

declare -gA TIMING_T0=()
declare -gA TIMING_MS=()
TIMING_SEGS=()

_timing_now()
{
    date +%s%N
}

# 毫秒转秒，保留三位小数，与基线表的「秒」列同单位。
_timing_sec()
{
    printf '%d.%03d' "$(( $1 / 1000 ))" "$(( $1 % 1000 ))"
}

_timing_root()
{
    (cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
}

_timing_path()
{
    local out="${AIRY_TIMING_OUT:-}"
    if [ -n "${out}" ]; then
        printf '%s' "${out}"
        return 0
    fi
    if [ "${GITHUB_ACTIONS:-}" = "true" ] && [ -n "${RUNNER_TEMP:-}" ]; then
        printf '%s/airy-timing-%s.tsv' "${RUNNER_TEMP}" "${GITHUB_JOB:-ci}"
    fi
}

_timing_inside()
{
    local out root
    out="$(realpath -m -- "$1" 2>/dev/null)" || return 1
    root="$(_timing_root)"
    case "${out}" in
        "${root}"/*) return 0 ;;
    esac
    return 1
}

_timing_add()
{
    local name="$1" ms="$2" out dir
    out="$(_timing_path)"
    [ -n "${out}" ] || return 0
    if _timing_inside "${out}"; then
        printf 'warning: AIRY_TIMING_OUT 指向仓库内，跳过落盘: %s\n' "${out}" >&2
        return 0
    fi
    dir="$(dirname -- "${out}")"
    if ! mkdir -p -- "${dir}" 2>/dev/null; then
        printf 'warning: 无法创建计时目录: %s\n' "${dir}" >&2
        return 0
    fi
    if ! printf '%s\t%s\t%s\n' "${name}" "$(_timing_sec "${ms}")" \
        "${CI_NS:-ci}" >>"${out}"; then
        printf 'warning: 计时落盘失败: %s\n' "${out}" >&2
    fi
    return 0
}

timing_start()
{
    local name="${1:-}"
    if [ -z "${name}" ]; then
        printf 'error: timing_start 需要段名\n' >&2
        return 1
    fi
    TIMING_T0["${name}"]="$(_timing_now)"
    case " ${TIMING_SEGS[*]:-} " in
        *" ${name} "*) ;;
        *) TIMING_SEGS+=("${name}") ;;
    esac
    return 0
}

timing_end()
{
    local name="${1:-}" t0 now ms
    if [ -z "${name}" ]; then
        printf 'error: timing_end 需要段名\n' >&2
        return 1
    fi
    t0="${TIMING_T0[${name}]:-}"
    if [ -z "${t0}" ]; then
        printf 'error: 未开始计时: %s\n' "${name}" >&2
        return 1
    fi
    now="$(_timing_now)"
    ms=$(( (now - t0) / 1000000 ))
    unset "TIMING_T0[${name}]"
    TIMING_MS["${name}"]=$(( ${TIMING_MS[${name}]:-0} + ms ))
    _timing_add "${name}" "${ms}"
    return 0
}

timing_sum()
{
    local n="${#TIMING_SEGS[@]}" name ms total=0 top="" best=-1
    if [ "${n}" -eq 0 ]; then
        printf 'timing: 0 segments\n'
        return 0
    fi
    for name in "${TIMING_SEGS[@]}"; do
        ms="${TIMING_MS[${name}]:-0}"
        total=$(( total + ms ))
        if [ "${ms}" -gt "${best}" ]; then
            best="${ms}"
            top="${name}"
        fi
    done
    printf 'timing: %d segments, total %ss, top %s %ss\n' \
        "${n}" "$(_timing_sec "${total}")" "${top}" "$(_timing_sec "${best}")"
}
