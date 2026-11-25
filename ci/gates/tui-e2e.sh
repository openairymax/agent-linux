#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/tui-e2e.sh — 会话选择与运行期切换的端到端门禁
#
# 用法:
#   ci/gates/tui-e2e.sh [--root DIR]
#
# 判据本体在 crates/boot-tui/tests/e2e/main.rs。它启动 airy-session-pick 与
# airy-session-mode 的真实二进制，通过唯一的 AIRY_SESSION_ROOT 接缝把
# systemctl／journalctl／chvt／loginctl／fgconsole／ps、会话状态目录、
# /dev/tty1 与 /proc/cmdline 指向临时根里的假命令，覆盖冷启动两侧选择、
# 命令行旁路、TUI↔GUI 双向切换、注销回落、忙碌拒绝、失败回滚与幂等。
#
# 必须跑真实二进制：切换的次序（写 desired → 停旧确认 → 起新 → 等就绪 →
# 写 mode → 落 journal → 失败回滚）就是被测对象本身；在测试里另写一遍编排
# 只能证明测试自己会演戏，证明不了「不重启机器就能双向切换」。
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/tui_gate.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/tui_gate.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/tui-e2e.sh [--root DIR]
EOF
}

tui_cli usage "$@"

tui_setup "${BASH_SOURCE[0]}"

group "tui: session e2e"
if ! cargo_run test -p boot-tui --test e2e -- --test-threads=1; then
    endgroup
    die "会话切换端到端未通过"
fi
endgroup
say "会话端到端通过：冷启动两侧选择与 TUI↔GUI 双向切换（含回滚与幂等）"
