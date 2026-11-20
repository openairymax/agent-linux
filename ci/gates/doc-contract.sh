#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/doc-contract.sh — 文档与代码的契约一致性门禁（Q-03）
#
# 判据只有一条：同一份登记在文档侧和代码侧必须逐字段相同。当前登记的
# 是 docs-linux 十九个功能模块的入口映射——册子在 system/tui/modmap.toml，
# 投影在 system/tui/crates/airy-adapters/src/modmap.rs。两份文件的服务对象
# 不同（评审读册子，编译读投影），因此它们必然同时存在；只要允许改一侧，
# 两个人就会看到不同的十九个模块。
#
# 判据本体在 ci/checks/modmap_conform.py：比对七列、核对代号集合、要求
# covered 的入口在仓库内真实存在，并要求 deferred 写明承接版本。本脚本只
# 做参数解析与退出码归一，不重复实现判定。
#
# 为什么不在门禁里读 docs 树：册子与投影都在本仓，单仓检出即可判；读
# docs-closed 才能判的那部分属功能归属册（ci/gates/completeness.sh）。
#
# 用法:
#   ci/gates/doc-contract.sh [--root DIR]
#
# 退出码: 0 一致；1 判定失败；5 参数或环境不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

usage()
{
    sed -n '/^# 用法:/,/^# 退出码:/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

root="${ROOT}"
while [ "$#" -gt 0 ]; do
    case "$1" in
    --root)
        [ "$#" -ge 2 ] || { err "--root 需要目录"; exit 5; }
        root="$2"
        shift
        ;;
    -h|--help) usage; exit 0 ;;
    *) err "未知参数: $1"; exit 5 ;;
    esac
    shift
done

checker="${ROOT}/ci/checks/modmap_conform.py"
if [ ! -f "${checker}" ]; then
    err "缺少检查器: ci/checks/modmap_conform.py"
    exit 5
fi

out="$(python3 "${checker}" --root "${root}" 2>&1)"
rc=$?
if [ "${rc}" -eq 0 ]; then
    say "${out}"
    exit 0
fi
printf '%s\n' "${out}" >&2
exit "${rc}"
