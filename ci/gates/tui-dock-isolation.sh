#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/tui-dock-isolation.sh — 双通路依赖隔离（§2.2 铁律①）
#
# 引导期 TUI 与 GUI dock 共享契约、不共享代码；后者经 AirymaxRT gateway，
# 前者不链 agentrt。判据落在 Cargo.toml 上，不跑 cargo：一次判定不该付整树
# 编译的代价，也不该要求宿主装工具链。
#
# 正向永远可判；反向（agentrt 依赖 TUI crate）要看对侧树在不在。多仓开发
# 机自动判，单仓检出打印一句「反向未判」——不假装通过，也不因此判负。
#
# 判定本体在 ci/checks/tui_isolation.py，负例由 test_tui_dock_isolation.sh
# 用夹具覆盖并随 lint 的回归组一起跑。
#
# 用法: ci/gates/tui-dock-isolation.sh [--agentrt DIR] [根...]
#       根缺省 system/tui；--agentrt 缺省取同级 agent-workload/agentrt。
# 退出码: 0 无交叉边；1 有交叉或清单不可解析；5 参数不满足。
# ===========================================================================
set -uo pipefail

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

need_file "${ROOT}/ci/checks/tui_isolation.py" 检查器
need_cmd python3
python3 "${ROOT}/ci/checks/tui_isolation.py" "$@"
rc=$?
[ "${rc}" -eq 0 ] || exit "${rc}"
say "双通路隔离成立"
