#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/tui-field.sh — 实机与降级取证门禁
#
# 用法:
#   ci/gates/tui-field.sh [--root DIR] [--input DIR]
#
# 不带 --input 时只跑格式自检与仓库夹具：模拟记录能证明「格式不会被误读」，
# 不能证明「目标机上真的这样」。带 --input 时按发版口径判：x86_64 与
# aarch64 各 8 格（降级矩阵七格 + 中文显示）必须有 evidence=field 且
# result=pass 的记录，夹具不计入。
#
# 两种模式共用 crates/boot-tui/tests/field/main.rs 的同一个判定实现，
# 避免「自检通过、发版另一套校验」这种两套判据的裂缝。
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/tui-gate.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/tui-gate.sh"

INPUT=""

usage()
{
    cat <<'EOF'
用法: ci/gates/tui-field.sh [--root DIR] [--input DIR]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --root) tui_root "$1" "${2:-}"; shift 2 ;;
        --input) need_dir "$1" "${2:-}"; INPUT="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${TUI_EINVAL}" ;;
    esac
done

tui_setup "${BASH_SOURCE[0]}"

if [ -n "${INPUT}" ]; then
    group "tui: field evidence"
    if ! AIRYMAX_FIELD_DIR="${INPUT}" AIRYMAX_FIELD_REQUIRE=1 \
        cargo_run test -p boot-tui --test field -- --test-threads=1; then
        endgroup
        die "实机取证不完整或含失败读数（${INPUT}）"
    fi
    endgroup
    say "实机取证通过：两条发行架构 × 8 格覆盖齐全"
    exit 0
fi

group "tui: field format"
if ! cargo_run test -p boot-tui --test field -- --test-threads=1; then
    endgroup
    die "取证记录格式自检未通过"
fi
endgroup
say "取证格式自检通过；实机读数需 --input 提供（发版门禁不采信夹具）"
