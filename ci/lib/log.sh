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

: "${CI_NS:=ci}"

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
