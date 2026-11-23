#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/locale-matrix.sh — locale 矩阵与落地判定（CA-090）
#
# 用法:
#   ci/gates/locale-matrix.sh [--tree DIR]
#
# 背景：locale 支持面此前只有 kickstart 里的一行 LANG，没有矩阵、没有生成
# 入口，也没有任何门禁能回答「镜像声明支持的 locale 是否真被生成」。本门禁
# 分三层取证：数据（矩阵 8 条、默认 en_US、P0 两条）、载具（源码包登记、
# 二进制与站点包安装、两个剖面的语言包）、行为（成功列印证据行，未知项与
# 坏矩阵各判负一次）。
#
# 判定面：
#
#   * 矩阵数据文件、工具包、入口与单元测试都在位，入口可执行。
#   * 矩阵恰 8 条数据行；`check --matrix-only` 打印证据行且 P0 为 2。
#   * spec 登记源包、安装 %{_bindir}/airy-locale 与 %{python3_sitelib}/airylocale。
#   * 两个剖面都装 en/zh 语言包；workstation 另装 ja/ko，并在 %post 里以
#     `airy-locale check` 判负——语言包缺失在装机阶段暴露，而不是开机后。
#   * 未知 locale 退 5；坏矩阵退 5；两者都不能被当成「已通过」。
#
# 退出码: 0 通过；1 检查失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

TREE=""
TMP=""
FAILS=0
CODE=0
OUT=""

usage()
{
    cat <<'EOF'
用法: ci/gates/locale-matrix.sh [--tree DIR]
EOF
}

# shellcheck source=../lib/check.sh
. "${ROOT}/ci/lib/check.sh"

trap cleanup EXIT

opts_dir "$@"

PY="${TREE}/system/bin/airy-locale"
PKG="${TREE}/system/services/airylocale"
MATRIX="${PKG}/matrix.tsv"
SPEC="${TREE}/distro/specs/airymaxos-system.spec"
KS="${TREE}/distro/kickstart"

run()
{
    OUT=$(python3 -B "${PY}" "$@" 2>&1)
    CODE=$?
}

chk_files()
{
    say "数据与载具 ${TREE}"

    local f
    for f in "${PY}" "${MATRIX}" "${PKG}/matrix.py" "${PKG}/cli.py" \
        "${TREE}/system/tests/services/test_locale_matrix.py" \
        "${TREE}/system/tests/services/test_locale_cli.py"; do
        [ -f "${f}" ] || bad "缺少 ${f#"${ROOT}/"}"
    done
    [ -x "${PY}" ] || bad "airy-locale 不可执行"

    local rows
    rows=$(grep -cvE '^[[:space:]]*(#|$)' "${MATRIX}" 2>/dev/null)
    [ "${rows}" = "8" ] || bad "矩阵数据行 ${rows:-0}，应为 8"

    grep -q '^[[:space:]]*zh_CN\.UTF-8[[:space:]]' "${MATRIX}" ||
        bad "矩阵缺 zh_CN.UTF-8"
    grep -q '^[[:space:]]*en_US\.UTF-8[[:space:]]' "${MATRIX}" ||
        bad "矩阵缺 en_US.UTF-8"
}

chk_spec()
{
    say "打包与剖面"

    [ -f "${SPEC}" ] || { bad "缺少 ${SPEC#"${ROOT}/"}"; return 0; }
    grep -q 'source-bundle ../../system/services/airylocale' "${SPEC}" ||
        bad "spec 未登记 airylocale 源包"
    grep -q '%{python3_sitelib}/airylocale' "${SPEC}" ||
        bad "spec 未安装 airylocale 站点包"
    grep -q '%{_bindir}/airy-locale' "${SPEC}" ||
        bad "spec 未安装 airy-locale 入口"

    local ks
    for ks in "${KS}/airymaxos-workstation.ks" "${KS}/airymaxos-minimal.ks"; do
        [ -f "${ks}" ] || { bad "缺少 ${ks#"${ROOT}/"}"; continue; }
        grep -q 'glibc-langpack-en' "${ks}" || bad "${ks##*/} 未装 en 语言包"
        grep -q 'glibc-langpack-zh' "${ks}" || bad "${ks##*/} 未装 zh 语言包"
    done
    grep -q 'glibc-langpack-ja' "${KS}/airymaxos-workstation.ks" ||
        bad "workstation 未装 ja 语言包"
    grep -q 'glibc-langpack-ko' "${KS}/airymaxos-workstation.ks" ||
        bad "workstation 未装 ko 语言包"
    grep -q 'airy-locale check' "${KS}/airymaxos-workstation.ks" ||
        bad "workstation 未在装机段校验 locale"
}

chk_cases()
{
    say "行为（成功与失败各一路）"

    run check --matrix-only
    [ "${CODE}" -eq 0 ] || bad "矩阵校验退出码 ${CODE}"
    printf '%s\n' "${OUT}" |
        grep -q 'AIRYMAX-LOCALE-CHECK ok locales=8 p0=2' ||
        bad "矩阵校验未打印证据行: ${OUT}"

    run gen --dry-run
    [ "${CODE}" -eq 0 ] || bad "dry-run 退出码 ${CODE}"
    printf '%s\n' "${OUT}" | grep -q 'AIRYMAX-LOCALE-GEN' ||
        bad "dry-run 未打印证据行"

    run gen --dry-run xx_YY.UTF-8
    [ "${CODE}" -eq 5 ] || bad "未知 locale 应退 5，实为 ${CODE}"

    printf 'en_US.UTF-8\ten\tUS\tUTF-8\n' >"${TMP}/bad.tsv"
    run check --matrix "${TMP}/bad.tsv" --matrix-only
    [ "${CODE}" -eq 5 ] || bad "坏矩阵应退 5，实为 ${CODE}"
}

TMP="$(mktemp -d)"

chk_files
chk_spec
chk_cases

if [ "${FAILS}" -ne 0 ]; then
    err "locale 矩阵判定失败 ${FAILS} 项"
    exit 1
fi

say "locale 矩阵判定通过"
exit 0
