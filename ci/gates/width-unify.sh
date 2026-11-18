#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/width-unify.sh — 列宽只有一个入口
#
# 用法: ci/gates/width-unify.sh
#
# 版面错位的根因几乎都是「同一段文本在两处用两套口径算列宽」：一处按
# 字符数、一处按东亚宽度。宽字符在这种分叉下会多吃一列，整行右移，
# 而且只在含中文或 emoji 的行上出现，测试里很难撞到。
#
# 判据是静态且唯一的：`system/tui` 下除 `wcwidth.rs` 外，
#   - 不得用 `chars().count()` 推算列宽；
#   - 不得直接依赖 `unicode_width`；
#   - 不得用 `graphemes(true).count()` 当列宽。
# 这三种写法都是绕过唯一入口的迹象，命中即判负并列出文件与行号。
#
# 退出码: 0 通过；1 命中绕过写法；2 源码树缺失。
# ===========================================================================
set -euo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SRC="${ROOT}/system/tui/crates"

[ -d "${SRC}" ] || die "缺少 TUI 源码树: system/tui/crates"

pattern='chars\(\)\.count\(\)|graphemes\(true\)\.count\(\)|unicode_width'

hits="$(grep -rnE "${pattern}" "${SRC}" \
    --include='*.rs' \
    | grep -v '/wcwidth\.rs:' \
    || true)"

if [ -n "${hits}" ]; then
    printf '%s\n' "${hits}" >&2
    die "列宽必须走 wcwidth::display_width（见 0.0.5 方案 §14.4）"
fi

note "列宽入口唯一"
