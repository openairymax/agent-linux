#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/input-matrix.sh — 四通道端到端矩阵（§9 V-1/V-2）
#
# 用法:
#   ci/gates/input-matrix.sh [--root DIR]
#
# 判据本体在 crates/boot-tui/tests/matrix/main.rs：键盘（中文）、语音、API、
# 批处理各一条成功路径与一条失败路径。用例直接驱动适配器与输入法/语音会话
# 的公开接口，不在测试里另造协议；任何一条断言失败都以非零退出码报告。
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/tui-gate.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/tui-gate.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/input-matrix.sh [--root DIR]
EOF
}

tui_cli usage "$@"

tui_setup "${BASH_SOURCE[0]}"

group "tui: input matrix"
if ! cargo_run test --offline --locked -p boot-tui --test matrix; then
    endgroup
    die "四通道矩阵未通过"
fi
endgroup
say "四通道矩阵通过：键盘/语音/API/批处理 成功与失败各一路"
