#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/boot-annotate.sh — 把冷启动失败点标注成 PR 可点的注解
#
# 用法: ci/gates/boot-annotate.sh --dir DIR
#
# 注解集不在此另立：直接取 distro/scripts/lib/verdict.env 的 BOOT_FAIL。
# 先前这里抄了一份同样的正则，新增一条判据要改两处，漏改时表现为「判定已
# 失败但注解空白」——两条路径对同一份串口日志得出不同结论。单元失败以会话
# 内探针的 AIRYMAX-BOOT-DEGRADED 为准，串口里的「Failed to start」不是判据
# ——把它标成 error 会与真实结论相左（TCG 下常见的非关键单元失败会淹没真因）。
# 因此失败单元只出 warning，error 只留给判定性标记。
#
# 单元名有两个来源：会话内健康自检打印的 systemctl 提示，以及卡点自证探针
# 打印的挂起作业。后者是卡住时唯一能指名的证据，缺了它注解只剩「超时」。
#
# 非 CI 环境退化为在终端打印命中行，行为一致但不出注解。
#
# 退出码: 恒 0——本 gate 只做呈现，判定权在 install-test.sh。
# ===========================================================================
set -uo pipefail

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"

ROOT="$(cd "${LIB}/../.." && pwd -P)"
ENV_FILE="${ROOT}/distro/scripts/lib/verdict.env"
# shellcheck source=../../distro/scripts/lib/verdict.env
. "${ENV_FILE}"

DIR=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)     DIR="${2:-}"; shift 2 ;;
        -h|--help) echo "用法: ci/gates/boot-annotate.sh --dir DIR"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${DIR}" ] || die "--dir 必填"
[ -r "${ENV_FILE}" ] || die "缺少判定集: ${ENV_FILE}"

# 卡住的单元名优先取探针的挂起作业行：它由 guest 自己的作业表得来，比从
# 提示字符串里回推可靠；两条来源合并去重，顺序不影响注解内容。
units()
{
    grep -oE 'AIRYMAX-BOOT-JOB unit=[^ ]+' "$1" 2>/dev/null | cut -d= -f2
    grep -oE "systemctl status [^']+" "$1" 2>/dev/null | awk '{ print $3 }'
}

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
    done < <(grep -nE "${BOOT_FAIL}" "${log}" | head -n 5)

    units "${log}" | sort -u | head -n 8 |
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
