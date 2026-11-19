#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_dead_code.sh — ci/gates/dead-code.sh 的分支回归
#
# 两类判定的正反例各造一遍：`#[allow(dead_code)]` 有理由放行、无理由判负；
# `pub` 项无引用进复核清单（不判负）、有引用不入单，`pub(crate)` 与测试模块
# 不参与。退出码、判据名与清单计数都断言，避免只改措辞就把回归跑绿。
#
# 用法: bash ci/tests/test_dead_code.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/dead-code.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib"
cp "${GATE}" "${FX}/ci/gates/dead-code.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/lib/dirs.sh" "${FX}/ci/lib/dirs.sh"
cp "${ROOT}/ci/lib/gate.sh" "${FX}/ci/lib/gate.sh"
cp "${ROOT}/ci/lib/__init__.py" "${FX}/ci/lib/__init__.py"
cp "${ROOT}/ci/lib/rs_src.py" "${FX}/ci/lib/rs_src.py"
cp "${ROOT}/ci/lib/scan_src.py" "${FX}/ci/lib/scan_src.py"
cp "${ROOT}/ci/no_stub.py" "${FX}/ci/no_stub.py"
cp "${ROOT}/ci/dead_code.py" "${FX}/ci/dead_code.py"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

run()
{
    bash "${FX}/ci/gates/dead-code.sh" "$@" 2>&1
}

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

fresh()
{
    rm -rf "${tmp}/src"
    mkdir -p "${tmp}/src"
}

fresh
cat >"${tmp}/src/lib.rs" <<'EOF'
pub const USED: u8 = 1;

/// 读一个常量。
pub fn read() -> u8 {
    USED
}

fn consume() -> u8 {
    read()
}
EOF
out="$(run "${tmp}/src")"
check "合规树判通过" 0 "$?" "${out}" "0 issue(s), 0 review item(s)"

fresh
printf '#[allow(dead_code)]\nfn helper() -> u8 {\n    1\n}\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "allow(dead_code) 无理由判拒绝" 1 "$?" "${out}" "D001"

fresh
printf '#[allow(dead_code, unused)]\nfn helper() -> u8 {\n    1\n}\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "多属性列表仍判拒绝" 1 "$?" "${out}" "D001"

fresh
printf '#[allow(dead_code)] // 理由: 供下一版接线\nfn helper() -> u8 {\n    1\n}\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "同行理由放行" 0 "$?" "${out}" "0 issue(s)"

fresh
printf '// 理由: 供下一版接线\n#[allow(dead_code)]\nfn helper() -> u8 {\n    1\n}\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "上一行理由放行" 0 "$?" "${out}" "0 issue(s)"

fresh
printf 'pub const LONELY: u8 = 1;\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "pub 项无引用进复核清单" 0 "$?" "${out}" "D100 pub LONELY"

fresh
cat >"${tmp}/src/a.rs" <<'EOF'
pub const USED: u8 = 1;

fn consume() -> u8 {
    USED
}
EOF
out="$(run "${tmp}/src")"
check "pub 项有引用不入单" 0 "$?" "${out}" "0 review item(s)"

fresh
printf 'pub(crate) const INNER: u8 = 1;\n' >"${tmp}/src/a.rs"
out="$(run "${tmp}/src")"
check "pub(crate) 不判" 0 "$?" "${out}" "0 review item(s)"

fresh
cat >"${tmp}/src/a.rs" <<'EOF'
pub const USED: u8 = 1;

#[cfg(test)]
mod tests {
    pub fn helper() -> u8 {
        USED
    }
}
EOF
out="$(run "${tmp}/src")"
check "测试模块的 pub 项不收集" 0 "$?" "${out}" "0 review item(s)"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

out="$(run "${tmp}/nope")"
check "目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --help)"
check "帮助退 0" 0 "$?" "${out}" "用法"

mv "${FX}/ci/dead_code.py" "${tmp}/dead_code.py"
out="$(run "${tmp}/src")"
check "缺少检查器退 5" 5 "$?" "${out}" "缺少检查器"
mv "${tmp}/dead_code.py" "${FX}/ci/dead_code.py"

if [ "${fails}" -ne 0 ]; then
    printf 'test_dead_code: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_dead_code: 全部通过\n'
