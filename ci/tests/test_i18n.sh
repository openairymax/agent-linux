#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_i18n.sh — ci/gates/i18n.sh 的消息面回归
#
# 钉住三条判据：签入映射表/模板与 error.h 契约漂移必退 1，CTest 用例未
# 接线必退 1，消息目录出现空译文必退 1；未改动的真实产物退 0。夹具把门禁
# 读取的整套消息面复制进临时树，再逐处破坏一次，证明「重新生成后逐字
# 比对」不是摆设。
#
# 用法: bash ci/tests/test_i18n.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/i18n.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

mkfix()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/services/tools/error-msg" \
        "${FX}/services/common/tests" \
        "${FX}/services/common/include/airy" \
        "${FX}/services/po" \
        "${FX}/kernel/include/uapi/linux/airymax"
    cp "${ROOT}/services/tools/error-msg/error_msg.py" \
        "${FX}/services/tools/error-msg/"
    cp "${ROOT}/services/common/error_msg_table.c" \
        "${ROOT}/services/common/error_msg.c" \
        "${ROOT}/services/common/error_msg_int.h" \
        "${ROOT}/services/common/CMakeLists.txt" \
        "${FX}/services/common/"
    cp "${ROOT}/services/common/tests/CMakeLists.txt" \
        "${FX}/services/common/tests/"
    cp "${ROOT}/services/common/include/airy/error_msg.h" \
        "${FX}/services/common/include/airy/"
    cp "${ROOT}/services/po/airymaxos.pot" "${ROOT}/services/po/zh_CN.po" \
        "${FX}/services/po/"
    cp "${ROOT}/kernel/include/uapi/linux/airymax/error.h" \
        "${FX}/kernel/include/uapi/linux/airymax/"
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
check "合规消息面判通过" 0 "${rc}" "${out}" "错误消息国际化面通过"

mkfix
sed -i 's|/\* Invalid argument \*/|/* Invalid argument (drift) */|' \
    "${FX}/kernel/include/uapi/linux/airymax/error.h"
out="$(run)"; rc=$?
check "契约注释漂移判负" 1 "${rc}" "${out}" "漂移"

mkfix
sed -i '/add_test(NAME error_msg_test/d' "${FX}/services/common/tests/CMakeLists.txt"
out="$(run)"; rc=$?
check "用例未接线判负" 1 "${rc}" "${out}" "消息用例未登记 CTest"

mkfix
sed -i 's/^msgstr "成功"$/msgstr ""/' "${FX}/services/po/zh_CN.po"
out="$(run)"; rc=$?
check "空译文判负" 1 "${rc}" "${out}" "生成物与 error.h 漂移"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
