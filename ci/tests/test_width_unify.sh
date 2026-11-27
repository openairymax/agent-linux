#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_width_unify.sh — width-unify 门禁回归夹具
#
# 钉住的判据：
#   负向：.rs 内出现 unicode_width / chars().count() / graphemes(true).count()
#         时门禁判负（rc=1）并点名文件；缺 system/tui/crates 时判负。
#   正向：只经 wcwidth::display_width 的实现判 0；wcwidth.rs 内的 unicode_width
#         是唯一豁免点；非 .rs 文件里的同名调用不参与判定。
#
# 用法: bash ci/tests/test_width_unify.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
FX="${tmp}/fx"
fails=0

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

seed()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" \
        "${FX}/system/tui/crates/airy-ui/src"
    cp "${ROOT}/ci/gates/width-unify.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
    printf 'pub fn width(s: &str) -> usize {\n    wcwidth::display_width(s)\n}\n' \
        >"${FX}/system/tui/crates/airy-ui/src/ui.rs"
    printf 'pub fn display_width(s: &str) -> usize {\n    s.unicode_width().unwrap_or(0)\n}\n' \
        >"${FX}/system/tui/crates/airy-ui/src/wcwidth.rs"
}

run() { bash "${FX}/ci/gates/width-unify.sh" 2>&1; }

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"
    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    if [ -n "${needle}" ] && ! printf '%s' "${out}" | grep -qF -- "${needle}"; then
        bad "${name}（输出缺「${needle}」）"
        printf '%s\n' "${out}" >&2
        return
    fi
    ok "${name}"
}

# 正向：唯一入口 + wcwidth.rs 豁免
seed
out="$(run)"; rc=$?
check "唯一入口判 0" 0 "${rc}" "${out}" "列宽入口唯一"

# 负向：直接调用 unicode_width
seed
printf 'pub fn w(s: &str) -> usize { s.unicode_width().unwrap_or(0) }\n' \
    >>"${FX}/system/tui/crates/airy-ui/src/ui.rs"
out="$(run)"; rc=$?
check "unicode_width 判负" 1 "${rc}" "${out}" "列宽必须走 wcwidth::display_width"
printf '%s' "${out}" | grep -qF 'ui.rs' &&
    ok "判负输出点名文件" || bad "判负输出未点名 ui.rs"

# 负向：字符计数冒充列宽
seed
printf 'pub fn w(s: &str) -> usize { s.chars().count() }\n' \
    >>"${FX}/system/tui/crates/airy-ui/src/ui.rs"
out="$(run)"; rc=$?
check "chars().count 判负" 1 "${rc}" "${out}" "列宽必须走 wcwidth::display_width"

# 负向：字素簇计数冒充列宽
seed
printf 'pub fn w(s: &str) -> usize { s.graphemes(true).count() }\n' \
    >>"${FX}/system/tui/crates/airy-ui/src/ui.rs"
out="$(run)"; rc=$?
check "graphemes(true).count 判负" 1 "${rc}" "${out}" "列宽必须走 wcwidth::display_width"

# 负向：缺 TUI 源码树（门禁走 die，实际退出码为 1）
seed
rm -rf "${FX}/system/tui/crates"
out="$(run)"; rc=$?
check "缺源码树判负" 1 "${rc}" "${out}" "缺少 TUI 源码树"

# 正向：非 .rs 文件不参与扫描
seed
printf 'unicode_width chars().count()\n' \
    >"${FX}/system/tui/crates/airy-ui/src/notes.txt"
out="$(run)"; rc=$?
check "非 .rs 文件不参与" 0 "${rc}" "${out}" "列宽入口唯一"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $((fails > 0 ? 1 : 0))
