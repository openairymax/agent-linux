#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/wp-scope.sh — 提交范围与施工图纸产出物的对账（§2.1／§4.1）
#
# 本版改动限定在施工图纸的卡片里：每张卡声明自己会碰哪些路径。判据把提交
# 分两步归属——先从提交信息取 WP 号，再按号查出产出物，最后逐个改动路径
# 核对。取不到号的提交记为未归属，留痕但不判负。
#
# 施工图纸在 docs-closed，单仓检出看不到：找不到册子退 3（不适用），不冒充
# 通过。判定本体在 ci/checks/wp_scope.py，负例由 test_wp_scope.sh 用夹具
# 覆盖并随 lint 的回归组一起跑。
#
# 用法: ci/gates/wp-scope.sh [--plan FILE] [--range A..B] [--root DIR]
# 退出码: 0 全部归属且无越界；1 有越界；3 册子或提交缺席；5 参数或仓库不满足。
# ===========================================================================
set -uo pipefail

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

need_file "${ROOT}/ci/checks/wp_scope.py" 检查器
need_cmd python3 git
python3 "${ROOT}/ci/checks/wp_scope.py" "$@"
rc=$?
[ "${rc}" -eq 0 ] || exit "${rc}"
say "改动均在卡片范围内"
