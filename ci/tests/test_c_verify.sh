#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_c_verify.sh — ci/gates/c-verify.sh 的分支回归
#
# 真实树上两个模块都编得过是常态，判据只能靠负例驱动：参数缺失与非法模块
# 必须退 5（编排层据此区分「参数不满足」与「判定失败」），编译错误必须退 1
# 且不能跑冒烟。另断言构建目录落在源码树之外——门禁若把产物写回源码区，
# 铁律就被自己破了，而这一条恰恰是真实树上看不出来的。
#
# 用法: bash ci/tests/test_c_verify.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/cloudnative" "${FX}/services"
cp "${ROOT}/ci/gates/c-verify.sh" "${FX}/ci/gates/c-verify.sh"
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
    AIRY_C_BUILD_ROOT="${tmp}/build" bash "${FX}/ci/gates/c-verify.sh" "$@" 2>&1
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
check "缺 --module" 5 "${rc}" "${out}" "--module 必填"

out="$(run --module nope)"; rc=$?
check "非法模块" 5 "${rc}" "${out}" "只接受 cloudnative|services"

# 编译错误：配置能过、编译必失败，断言门禁不把它当成「配置失败」蒙混过去，
# 也不进入冒烟段。
cat >"${FX}/cloudnative/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.20)
project(broken LANGUAGES C)
add_executable(broken bad.c)
EOF
printf 'int main(void) { return nope; }\n' >"${FX}/cloudnative/bad.c"

out="$(run --module cloudnative)"; rc=$?
check "编译失败判负" 1 "${rc}" "${out}" "cloudnative 构建失败"

# 构建目录必须可由 AIRY_C_BUILD_ROOT 指到源码树外；门禁不得在 ${FX} 内留下
# CMakeCache.txt 等产物。
if [ -e "${FX}/CMakeCache.txt" ] || [ -d "${FX}/cloudnative/CMakeFiles" ]; then
    bad "源码树内出现构建产物"
else
    ok "源码树内无构建产物"
fi

[ "${fails}" -eq 0 ] || exit 1
printf '全部通过\n'
