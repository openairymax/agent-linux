#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/kernel-uapi.sh — UAPI 头文件字节级冻结（K-03）
#
# 用法:
#   ci/gates/kernel-uapi.sh [--root DIR] [--check]
#   ci/gates/kernel-uapi.sh [--root DIR] --update
#
# 判据有两层：集合面——kernel/include/uapi/linux/airymax/ 下的 .h 集合必
# 须与快照逐条相同；字节面——每份文件的 sha256 必须与登记摘要相同。一条
# 注释的改动不会破坏结构兼容，但它改变了发布物料，必须走一次显式刷新。
#
# 判定本体在 ci/checks/abi_snapshot.py：本文件只做前置校验、调用与退出码
# 透传。参数语义、判定细节与刷新时的差异打印都留在本体一处，操作员入口
# 与 CI 入口因此不会各自演化。
#
# --update 是操作员动作：重写快照不等于接受改动，快照必须与头文件改动一并
# 提交评审。默认 --check，CI 不回写任何文件。
#
# 退出码: 0 一致；1 漂移或快照缺失；5 参数或环境不合法。
# ===========================================================================
set -uo pipefail

readonly CHECK=ci/checks/abi_snapshot.py
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

usage()
{
    cat <<'EOF'
用法: ci/gates/kernel-uapi.sh [--root DIR] [--check|--update]

  --root DIR   被检仓库根，缺省为本脚本上两级
  --check      只判定，不写盘（缺省）
  --update     重写 tools/abi-check/contract/uapi_headers.sha256
  -h, --help   显示本帮助
EOF
}

# 只接管帮助；其余参数原样交给判定本体，参数错误由它统一折算为 5。
while [ "$#" -gt 0 ]; do
    case "$1" in
    -h|--help) usage; exit 0 ;;
    *) break ;;
    esac
done

need_file "${ROOT}/${CHECK}" 判定本体
need_cmd python3

python3 "${ROOT}/${CHECK}" "$@"
rc=$?
[ "${rc}" -eq 0 ] || exit "${rc}"
say "UAPI 头文件集合与字节面一致"
