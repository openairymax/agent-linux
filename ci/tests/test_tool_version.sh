#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_tool_version.sh — 工具自报版本必须等于树内产品版本
#
# 产品版本的唯一来源是 distro/release/os-release 的 VERSION_ID。交付工具被
# 打成单文件 pyz 后脱离仓库运行，读不到那份声明，只能各自持有字面量。两者
# 分叉过一次：树内已是 0.0.4 时 tools/airy-flash 仍自报 0.0.3，直到发布日的
# 打包复核（ci/gates/tool-pack.sh 实跑 --version）才判负——那时离出包只剩
# 一次 tag。这里在提交时就把两个数字钉在一起。
#
# 用法: bash ci/tests/test_tool_version.sh
# 退出码: 0 一致；1 不一致或读不到声明。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
OSREL="${ROOT}/distro/release/os-release"

want="$(awk -F'"' '/^VERSION_ID="/ { print $2; exit }' "${OSREL}" 2>/dev/null)"
[ -n "${want}" ] || { printf 'FAIL 读不到产品版本: %s\n' "${OSREL}" >&2; exit 1; }

# 只取常量，不进主流程：airy_flash 的 main() 由 __main__ 守卫。
# stderr 单独落盘：宿主动态链接器的告警会顺着 2>&1 混进将要逐字比对的值。
err="$(mktemp)"
trap 'rm -f "${err}"' EXIT
got="$(env -u LD_LIBRARY_PATH python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
import airy_flash
print(airy_flash.TOOL_VERSION)
' "${ROOT}/tools/airy-flash" 2>"${err}")" || {
    printf 'FAIL 无法导入 airy_flash:\n' >&2
    cat "${err}" >&2
    exit 1
}

if [ "${got}" != "${want}" ]; then
    printf 'FAIL airy-flash 自报 %s，树内产品版本 %s\n' "${got}" "${want}" >&2
    exit 1
fi
printf 'ok   airy-flash 自报版本 = 树内产品版本 = %s\n' "${want}"
