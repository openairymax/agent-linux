#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/log.sh — 统一日志与注解原语
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
