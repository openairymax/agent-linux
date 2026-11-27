#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_locale_matrix.sh — ci/gates/locale-matrix.sh 的矩阵与载具回归
#
# 钉住判据：矩阵少一行必退 1（数据面）、workstation 剖面缺 ja 语言包必退 1
# （载具面）、spec 漏装 airy-locale 入口必退 1（打包面）；合规复制树退 0，
# 证据行 locales=8 p0=2 未打印时门禁自身会判负。夹具只读临时树，不碰真树。
#
# 用法: bash ci/tests/test_locale_matrix.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/locale-matrix.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

mkfix()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/system/bin" "${FX}/system/services" \
        "${FX}/system/tests/services" "${FX}/distro/specs" \
        "${FX}/distro/kickstart"
    cp -p "${ROOT}/system/bin/airy-locale" "${FX}/system/bin/"
    cp -r "${ROOT}/system/services/airylocale" \
        "${ROOT}/system/services/airylib" "${FX}/system/services/"
    cp "${ROOT}/system/tests/services/test_locale_matrix.py" \
        "${ROOT}/system/tests/services/test_locale_cli.py" \
        "${FX}/system/tests/services/"
    cp "${ROOT}/distro/specs/airymaxos-system.spec" "${FX}/distro/specs/"
    cp "${ROOT}/distro/kickstart/airymaxos-workstation.ks" \
        "${ROOT}/distro/kickstart/airymaxos-minimal.ks" \
        "${FX}/distro/kickstart/"
}

run()
{
    bash "${GATE}" --tree "${FX}" 2>&1
}

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

mkfix
out="$(run)"; rc=$?
check "合规树判通过" 0 "${rc}" "${out}" "locale 矩阵判定通过"

mkfix
sed -i '/^ja_JP\.UTF-8/d' "${FX}/system/services/airylocale/matrix.tsv"
out="$(run)"; rc=$?
check "矩阵少行判负" 1 "${rc}" "${out}" "矩阵数据行 7，应为 8"

mkfix
sed -i '/glibc-langpack-ja/d' "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run)"; rc=$?
check "剖面缺 ja 判负" 1 "${rc}" "${out}" "workstation 未装 ja 语言包"

mkfix
sed -i '\|%{_bindir}/airy-locale|d' "${FX}/distro/specs/airymaxos-system.spec"
out="$(run)"; rc=$?
check "spec 漏装入口判负" 1 "${rc}" "${out}" "spec 未安装 airy-locale 入口"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
