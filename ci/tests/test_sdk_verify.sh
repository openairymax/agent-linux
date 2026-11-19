#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_sdk_verify.sh — ci/gates/sdk-verify.sh 的分支回归
#
# 真实 SDK 四门语言都能编过，正例无法区分「判据在跑」与「判据空转」。这里
# 用两个负例钉住契约：参数缺失/非法语言退 5（编排层据此区分参数错误与判定
# 失败），一门语言的编译错误必须让该语言判负并退 1。
#
# 用法: bash ci/tests/test_sdk_verify.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/sdk/rust/airyx"
cp "${ROOT}/ci/gates/sdk-verify.sh" "${FX}/ci/gates/sdk-verify.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

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
    TMPDIR="${tmp}" bash "${FX}/ci/gates/sdk-verify.sh" "$@" 2>&1
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

out="$(run)"; rc=$?
check "缺 --lang" 5 "${rc}" "${out}" "--lang 必填"

out="$(run --lang java)"; rc=$?
check "非法语言" 5 "${rc}" "${out}" "只接受 go|python|rust|ts"

printf 'pub fn broken() -> i32 { "not an int" }\n' >"${FX}/sdk/rust/airyx/bad.rs"

out="$(run --lang rust)"; rc=$?
check "编译失败判负" 1 "${rc}" "${out}" "sdk/rust 编译失败"

[ "${fails}" -eq 0 ] || exit 1
printf '全部通过\n'
