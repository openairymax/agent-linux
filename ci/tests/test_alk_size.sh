#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_alk_size.sh — ci/gates/alk-size.sh 的判据回归
#
# 钉住四条判据：两侧字节数相同才放行；差一字节即判负并报出差值；镜像既可
# 传文件也可传构建目录（bzImage/Image 等固定名）；输入缺失或参数不全一律
# 退 5，不与「体积不守恒」的 1 混同。
#
# 用法: bash ci/tests/test_alk_size.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib"
cp "${ROOT}/ci/gates/alk-size.sh" "${FX}/ci/gates/"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
GATE="${FX}/ci/gates/alk-size.sh"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

run()
{
    out="$(bash "${GATE}" "$@" 2>&1)"
    rc=$?
}

check()
{
    local name="$1" want="$2" needle="$3"

    if [ "${rc}" -ne "${want}" ]; then
        bad "${name}（rc=${rc}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *)
        bad "${name}（输出缺「${needle}」）"
        printf '%s\n' "${out}" >&2
        ;;
    esac
}

printf '0123456789' >"${tmp}/base.img"
printf '0123456789' >"${tmp}/same.img"
printf '0123456789abcdefg' >"${tmp}/big.img"
mkdir -p "${tmp}/build/arch/x86/boot"
printf '0123456789' >"${tmp}/build/arch/x86/boot/bzImage"

run --base "${tmp}/base.img" --cand "${tmp}/same.img"
check "同字节放行" 0 "== alk-size passed =="

run --base "${tmp}/base.img" --cand "${tmp}/big.img"
check "差七字节判负" 1 "ALK 体积不守恒: 差 7 字节"

run --base-size 10 --cand "${tmp}/same.img"
check "台账字节数放行" 0 "base=10 cand=10 diff=0"

run --base "${tmp}/base.img" --cand "${tmp}/build"
check "构建目录取 bzImage" 0 "diff=0"

run --base "${tmp}/base.img" --cand "${tmp}/absent.img"
check "构件缺失退 5" 5 "找不到内核镜像"

run --base "${tmp}/base.img"
check "缺 --cand 退 5" 5 "缺少 --cand"

run --base "${tmp}/base.img" --cand "${tmp}/same.img" --bogus
check "未知参数退 5" 5 "未知参数"

if [ "${fails}" -ne 0 ]; then
    exit 1
fi
printf '== %s passed ==\n' "$(basename "$0")"
exit $((fails > 0 ? 1 : 0))
