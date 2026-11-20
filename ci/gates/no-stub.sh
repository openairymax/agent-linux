#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# 判负型（rc=1 阻断合并）
# ===========================================================================
# ci/gates/no-stub.sh — 零桩扫描（QG-02）
#
# 用法:
#   ci/gates/no-stub.sh [DIR...]
#
# 「禁桩」写进方案很容易，拦住它却很难：`todo!()` 与 `unimplemented!()` 编译
# 期完全合法，恒 `Ok(())` 的函数在调用方看来就是成功，三者都不会让构建、测试
# 或 clippy 变红。本门禁把这三类形态连同产品代码里的 `unreachable!()` 与
# `unsafe` 一起判负，判据实现在 ci/checks/no_stub.py，本文件只做参数校验与转调。
#
#   DIR   要扫描的目录（可多个），缺省 system/tui
#
# 不扫 vendor/ 与 target/：第三方快照与构建产物不适用自有代码规范。目录不存在
# 按参数错误处理，不静默跳过——写错路径却拿到绿灯是最坏的结果。
#
# 退出码: 0 通过；1 有命中；5 参数不满足。
# ===========================================================================
set -uo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/gate.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/gate.sh"

# shellcheck disable=SC2034  # ROOT 由 gate.sh 的 gate_py 读取
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

gate_py ci/checks/no_stub.py "$@"
say "零桩扫描通过（todo!/unimplemented!/unreachable!/unsafe/恒 Ok(()) 均无命中）"
exit 0
