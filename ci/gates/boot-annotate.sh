#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/boot-annotate.sh — 把冷启动失败点标注成 PR 可点的注解
#
# 用法: ci/gates/boot-annotate.sh --dir DIR
#
# 注解集与 install-test.sh 的 BOOT_FAIL 保持一致：单元失败以会话内探针的
# AIRYMAX-BOOT-DEGRADED 为准，串口里的「Failed to start」不是判据——把它
# 标成 error 会与真实结论相左（TCG 下常见的非关键单元失败会淹没真因）。
# 因此失败单元只出 warning，error 只留给判定性标记。
#
# 非 CI 环境退化为在终端打印命中行，行为一致但不出注解。
#
# 退出码: 恒 0——本 gate 只做呈现，判定权在 install-test.sh。
# ===========================================================================
set -uo pipefail

CI_NS=boot-annotate
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

DIR=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)     DIR="${2:-}"; shift 2 ;;
        -h|--help) echo "用法: ci/gates/boot-annotate.sh --dir DIR"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${DIR}" ] || die "--dir 必填"

pattern='Kernel panic|Welcome to emergency mode|dracut-initqueue.*timeout|AIRYMAX-BOOT-DEGRADED|AIRYMAX-DESKTOP-FAIL|AIRYMAX-SMOKE-FAIL'

shopt -s nullglob
logs=("${DIR}"/run-*/boot.log)
shopt -u nullglob

[ "${#logs[@]}" -gt 0 ] || { note "没有 boot.log，跳过注解"; exit 0; }

for log in "${logs[@]}"; do
    while IFS= read -r hit; do
        if [ "${GITHUB_ACTIONS:-}" = true ]; then
            printf '::error file=%s::%s\n' "${log}" "${hit}"
        else
            printf '%s: %s\n' "${log}" "${hit}"
        fi
    done < <(grep -nE "${pattern}" "${log}" | head -n 5)

    grep -oE "systemctl status [^']+" "${log}" 2>/dev/null |
        awk '{ print $3 }' | sort -u | head -n 8 |
        while IFS= read -r unit; do
            [ -n "${unit}" ] || continue
            if [ "${GITHUB_ACTIONS:-}" = true ]; then
                printf '::warning file=%s::boot unit failed: %s\n' "${log}" "${unit}"
            else
                printf 'warning: %s: boot unit failed: %s\n' "${log}" "${unit}"
            fi
        done
done

exit 0
