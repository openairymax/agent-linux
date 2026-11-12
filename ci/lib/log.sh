#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/log.sh — 统一日志、注解与前置校验原语
#
# 目的：让同一条门禁在本地终端与 GitHub Actions 里输出同形。CI 专属的
# ::group:: 与 ::error:: 语法在本地退化为普通文本，不改变语义，也不引入
# 两套实现——这是「本地能跑 = CI 能跑」的前提。
#
# 调用方：ci/ci.sh 与 ci/gates/*.sh。
# ===========================================================================

# 命名空间默认取调用脚本的文件名（去掉 .sh）。此前每个 gate 各自写一行
# CI_NS=<自己>，26 份赋值既重复又容易漏写；而且 shellcheck 逐个文件分析，
# 看不到本文件里的使用，26 处一律报 SC2034。改由这里派生后，新增门禁不必
# 再声明，日志前缀与实际脚本永远一致。
: "${CI_NS:=$(basename "${BASH_SOURCE[1]:-ci}" .sh)}"

say()
{
    printf '%s: %s\n' "${CI_NS}" "$*"
}

note()
{
    printf 'note: %s\n' "$*" >&2
}

group()
{
    if [ "${GITHUB_ACTIONS:-}" = true ]; then
        printf '::group::%s\n' "$*"
    else
        printf '=== %s ===\n' "$*"
    fi
}

endgroup()
{
    if [ "${GITHUB_ACTIONS:-}" = true ]; then
        printf '::endgroup::\n'
    fi
    return 0
}

warn()
{
    if [ "${GITHUB_ACTIONS:-}" = true ]; then
        printf '::warning::%s\n' "$*" >&2
    else
        printf 'warning: %s\n' "$*" >&2
    fi
}

err()
{
    if [ "${GITHUB_ACTIONS:-}" = true ]; then
        printf '::error::%s\n' "$*" >&2
    else
        printf 'error: %s\n' "$*" >&2
    fi
}

die()
{
    err "$*"
    exit 1
}

# need_cmd CMD... —— 前置工具校验，缺一个就立即失败。
#
# 放在这里而不是 host.sh：每个 gate 都会 source 本文件，而 need_cmd 只读
# 不写宿主状态，属于「任何门禁都可用的守卫」。此前它在 host.sh，四个未
# source host.sh 的 gate 调用时只得到 "command not found"，校验静默失效
# （run 40478538815 的 media-fetch/assemble/release-rpms/container-load）。
need_cmd()
{
    local c missing=()
    for c in "$@"; do
        command -v "${c}" >/dev/null 2>&1 || missing+=("${c}")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        die "缺少命令: ${missing[*]}"
    fi
}

# 参数不满足时门禁统一的退出码。与各 gate 自己的 EINVAL 同值，集中在此
# 是因为 need_dir 在这里定义：判定与它的退出码不该分居两个文件。调用方
# 另用自己的 EINVAL 表示同一约定时值必须相等，否则同一份门禁在不同分支
# 退出不同的码，编排层无法把它与「判定失败」区分开。
readonly CI_EINVAL=5

# need_dir OPT DIR —— 目录类选项的前置校验，缺参或目录不存在即退 5。
#
# 7 个 gate 曾各自抄一份同样的三行：两行判据加一行函数头，规范化后与
# 参数解析循环拼成同一个重复窗口。判定只有一份，落点也就只能有一处。
need_dir()
{
    [ -n "$2" ] || { err "$1 需要目录路径"; exit "${CI_EINVAL}"; }
    [ -d "$2" ] || { err "$1 目录不存在: $2"; exit "${CI_EINVAL}"; }
}

# env_set KEY VALUE —— 写入 GitHub Actions 的环境文件；本地运行时只打印，
# 由调用方自行 export。统一入口避免每个 gate 重复判断 $GITHUB_ENV。
env_set()
{
    local key="$1" val="$2" file="${CI_ENV_FILE:-${GITHUB_ENV:-}}"

    if [ -n "${file}" ]; then
        printf '%s=%s\n' "${key}" "${val}" >>"${file}"
    else
        printf 'export %s=%s\n' "${key}" "${val}" >&2
    fi
}

# out_set KEY VALUE —— 写入 GitHub Actions 的 step 输出文件；本地运行时只打印。
# 输出名与写入格式只在这里定义，gate 不再各自拼 "$GITHUB_OUTPUT"。
out_set()
{
    local key="$1" val="$2" file="${GITHUB_OUTPUT:-}"

    if [ -n "${file}" ]; then
        printf '%s=%s\n' "${key}" "${val}" >>"${file}"
    else
        printf 'output %s=%s\n' "${key}" "${val}" >&2
    fi
}
