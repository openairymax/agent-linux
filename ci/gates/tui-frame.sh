#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/tui-frame.sh — 帧预算与渲染性能判据（QG-08）
#
# 用法:
#   ci/gates/tui-frame.sh [--root DIR]
#
# 两条判据同源：85×24 下整屏重绘 p99 ≤ 33 ms、帧常驻堆 ≤ 64 KiB。
# 先跑 tests/frame_budget.rs（判据本体，含静止帧零写出与一万帧堆不增长），
# 再用 benches/frame.rs 以 release 档复跑同一组读数。两者超预算都以非零
# 退出码报告，CI 与发行前实机复跑读到的是同一条线。
#
# 工具链版本不在这里声明：system/tui/rust-toolchain.toml 是唯一事实源，
# 本门禁只断言实际生效的编译器与钉版一致，避免换一套编译器得出
# 「取决于机器」的结论。
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/tui-gate.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/tui-gate.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/tui-frame.sh [--root DIR]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --root) tui_root "$1" "${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

tui_setup "${BASH_SOURCE[0]}"

group "tui: frame"
if ! cargo_run test --offline --locked -p boot-tui --test frame_budget; then
    endgroup
    die "帧预算判据未通过"
fi
if ! cargo_run bench --offline --locked -p boot-tui --bench frame; then
    endgroup
    die "帧预算基准未通过"
fi
endgroup
say "帧预算通过：p99 ≤ 33 ms，常驻堆 ≤ 64 KiB"
