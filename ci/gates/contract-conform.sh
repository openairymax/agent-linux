#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/contract-conform.sh — 契约册与代码投影的一致性判据
#
# 用法: ci/gates/contract-conform.sh [--root DIR]
#
# 契约册（system/contracts/）是 airy.intent/1 与四通道的唯一文本，代码里的
# 类型与常量表是它的投影。两份东西分别演化时，靠人读是查不出漂移的：
# 少一个字段、换一个枚举顺序、改一个 errno，都要有机器判据立刻拦下。
#
# 解析与对照在 ci/contract_conform.py；本脚本只做前置校验与统一日志，
# 判定逻辑不在这里再写一份。
#
# 不需要编译器与联网：只读工作树，故与其它只读门禁同层。
# 退出码: 0 通过；1 契约与代码分叉；5 参数不满足或契约册缺失。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly CHECK=ci/contract_conform.py
readonly DOCS=system/contracts
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT=""

usage()
{
    cat <<'EOF'
用法: ci/gates/contract-conform.sh [--root DIR]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --root) need_dir "$1" "${2:-}"; ROOT="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; usage >&2; exit "${EINVAL}" ;;
    esac
done

[ -n "${ROOT}" ] ||
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

need_file "${ROOT}/${CHECK}" 契约判定器
need_dir "${DOCS}" "${ROOT}/${DOCS}"
need_cmd python3

group "contract: airy.intent/1 与四通道"
if ! python3 "${ROOT}/${CHECK}" --root "${ROOT}"; then
    endgroup
    die "契约册与代码投影不一致"
fi
endgroup
say "契约册与代码投影一致"
