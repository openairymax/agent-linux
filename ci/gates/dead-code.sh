#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# 判负型（rc=1 阻断合并）
# ===========================================================================
# ci/gates/dead-code.sh — 死代码扫描（QG-03）
#
# 用法:
#   ci/gates/dead-code.sh [DIR...]
#
# 死代码不会让任何门禁变红：编译器对 `pub` 项一律沉默（外部 crate 可能在用），
# 而 `#[allow(dead_code)]` 一写就把告警永久关掉。本门禁补这两处：
#
#   · `#[allow(dead_code)]` 未给出理由 → 判负（同行或上一行注释写 `理由:`）；
#   · `pub` 项在扫描根内只有定义、没有任何引用 → 复核清单，不判负——跨仓
#     消费方在本仓不可见，判负会把合法的对外接口全部拖红。
#
# 判定实现在 ci/dead_code.py；`pub(crate)` 与私有项交给 clippy 的 dead_code，
# 两边不重复实现同一条判据。
#
#   DIR   要扫描的目录（可多个），缺省 system/tui
#
# 退出码: 0 通过（复核清单不影响退出码）；1 有判负项；5 参数不满足。
# ===========================================================================
set -uo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/gate.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/gate.sh"

# shellcheck disable=SC2034  # ROOT 由 gate.sh 的 gate_py 读取
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

gate_py ci/dead_code.py "$@"
say "死代码扫描通过（allow(dead_code) 均有理由）"
exit 0
