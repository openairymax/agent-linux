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
readonly TUI=system/tui
readonly PIN=1.90.0
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT=""
TUIDIR=""

usage()
{
    cat <<'EOF'
用法: ci/gates/tui-frame.sh [--root DIR]
EOF
}

pin_check()
{
    local got

    need_cmd cargo rustc awk
    got="$(cd "${TUIDIR}" && rustc --version 2>/dev/null | awk '{print $2}')"
    [ "${got}" = "${PIN}" ] ||
        die "编译器 ${got:-未知} 与钉版 ${PIN} 不一致（见 ${TUI}/rust-toolchain.toml）"
}

# 产物落在 TMPDIR 下的独立目标目录，不在源码树里留 target/。
cargo_run()
{
    local tgt="${TMPDIR:-/tmp}/airy-tui-target"

    ( cd "${TUIDIR}" && CARGO_TARGET_DIR="${tgt}" cargo "$@" )
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --root) need_dir "$1" "${2:-}"; ROOT="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${ROOT}" ] ||
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TUIDIR="${ROOT}/${TUI}"
need_dir "${TUI}" "${TUIDIR}"
[ -f "${TUIDIR}/Cargo.lock" ] || die "缺少 ${TUI}/Cargo.lock"

pin_check

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
