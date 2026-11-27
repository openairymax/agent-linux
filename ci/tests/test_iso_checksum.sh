#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_iso_checksum.sh — ci/gates/iso-checksum.sh 的落盘校验回归
#
# 钉住三条判据：摘要一致才放行、摘要不符退 1、校验和文件缺失退 1；另钉
# --dir 缺参退 5。artifact 里的 ISO 只有真算过摘要才算「就绪」，缺文件或
# 内容损坏都不能被当成通过。
#
# 用法: bash ci/tests/test_iso_checksum.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/iso-checksum.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

ART="${tmp}/art"
rm -rf "${ART}"
mkdir -p "${ART}"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

check()
{
    local name="$1" want="$2" rc="$3" out="$4" needle="$5"

    if [ "${rc}" != "${want}" ]; then
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

# gen —— 铺一份摘要正确的 ISO 与同名校验和文件。
gen()
{
    rm -f "${ART}"/*
    head -c 4096 /dev/urandom >"${ART}/image.iso"
    ( cd "${ART}" && sha256sum image.iso >image.iso.sha256 )
}

run()
{
    bash "${GATE}" --dir "${ART}" 2>&1
}

gen
out="$(run)"; rc=$?
check "摘要一致判就绪" 0 "${rc}" "${out}" "ISO 就绪"

gen
printf 'x' >>"${ART}/image.iso"
out="$(run)"; rc=$?
check "内容被改判摘要不符" 1 "${rc}" "${out}" "ISO 摘要校验失败"

gen
rm -f "${ART}/image.iso.sha256"
out="$(run)"; rc=$?
check "校验和缺失判负" 1 "${rc}" "${out}" "缺少 ISO 校验和"

rm -f "${ART}"/*
out="$(run)"; rc=$?
check "ISO 缺失判负" 1 "${rc}" "${out}" "artifact 中缺少 ISO"

out="$(bash "${GATE}" 2>&1)"; rc=$?
check "缺 --dir 按参数错误" 5 "${rc}" "${out}" "--dir 必填"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
