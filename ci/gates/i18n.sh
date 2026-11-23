#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/i18n.sh — 错误消息国际化面门禁（CA-093）
#
# 用法:
#   ci/gates/i18n.sh [--tree DIR]
#
# 错误码是 [SC] 契约，消息目录是它的翻译视图。视图与契约分叉时，
# 用户看到的是旧语义，而编译、甚至单元用例都可能照样通过——所以
# 这里不检查「有没有 .po」，而是重新生成一遍映射表与模板，与签入
# 产物逐字比对；再对每个 locale 断言全量覆盖且无空翻译。
#
# 两条负例保证判据不是摆设：改一处契约注释、清空一条译文，都必须
# 让检查失败。没有负例的「检查通过」只证明脚本没崩。
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/check.sh
. "${ROOT}/ci/lib/check.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/i18n.sh [--tree DIR]
EOF
}

gate_boot
opts_dir "$@"
TMP="$(mktemp -d)" || die "无法创建临时目录"

GEN="${TREE}/services/tools/error-msg/error_msg.py"
SERVICE="${TREE}/services"
HEADER="${TREE}/kernel/include/uapi/linux/airymax/error.h"
TABLE="${TREE}/services/common/error_msg_table.c"
API="${TREE}/services/common/include/airy/error_msg.h"
PODIR="${TREE}/services/po"
POT="${PODIR}/airymaxos.pot"
LIB_CM="${TREE}/services/common/CMakeLists.txt"
TEST_CM="${TREE}/services/common/tests/CMakeLists.txt"

chk_layout()
{
    say "消息面实物"
    need_file "${GEN}" "错误消息生成器"
    need_file "${HEADER}" "错误码 [SC] 契约"
    need_file "${TABLE}" "生成的错误消息映射表"
    need_file "${API}" "消息接口头"
    need_file "${POT}" "消息目录模板"
    need_file "${TREE}/services/common/error_msg.c" "消息解析实现"
    need_file "${TREE}/services/common/error_msg_int.h" "生成表内部头"
    need_file "${PODIR}/zh_CN.po" "简体中文消息目录"
}

chk_wiring()
{
    say "构建与用例接线"
    grep_in 'error_msg.c' "${LIB_CM}" "框架库未编入消息解析"
    grep_in 'error_msg_table.c' "${LIB_CM}" "框架库未编入生成映射表"
    grep_in 'add_test(NAME error_msg_test' "${TEST_CM}" "消息用例未登记 CTest"
    grep_in 'AIRY_GETTEXT_DOMAIN "airymaxos"' "${API}" \
        "gettext 域不是 airymaxos"
}

chk_ssot()
{
    say "生成物与 [SC] 契约一致"
    [ "${FAILS}" -eq 0 ] || return 0
    need_cmd python3
    run_bin python3 -B "${GEN}" --check --root "${SERVICE}"
    code_is 0 "生成物与 error.h 漂移（运行 --gen 重新生成）"
    out_has 'AIRYMAX-I18N-CHECK ok' "未打印校验证据行"
}

# 负例一：契约注释改了但未重新生成，检查必须失败。
chk_drift()
{
    local mut="${TMP}/error_drift.h"

    say "漂移检出（契约变更）"
    [ "${FAILS}" -eq 0 ] || return 0
    if ! cp "${HEADER}" "${mut}"; then
        bad "无法复制契约头做负例"
        return 0
    fi
    if ! sed -i 's|/\* Invalid argument \*/|/* Invalid argument (drift) */|' \
            "${mut}"; then
        bad "无法改写契约头做负例"
        return 0
    fi
    run_bin python3 -B "${GEN}" --check --root "${SERVICE}" --header "${mut}"
    [ "${CODE}" -ne 0 ] || bad "注释变更后 --check 仍通过，漂移未被检出"
}

# 负例二：译文被清空，覆盖率断言必须失败。
chk_catalog()
{
    local tmp_po="${TMP}/po"

    say "漂移检出（空翻译）"
    [ "${FAILS}" -eq 0 ] || return 0
    mkdir -p "${tmp_po}" || { bad "无法创建临时消息目录"; return 0; }
    if ! cp "${POT}" "${PODIR}/zh_CN.po" "${tmp_po}/"; then
        bad "无法复制消息目录做负例"
        return 0
    fi
    if ! sed -i 's/^msgstr "成功"$/msgstr ""/' "${tmp_po}/zh_CN.po"; then
        bad "无法清空译文做负例"
        return 0
    fi
    run_bin python3 -B "${GEN}" --check --root "${SERVICE}" --po-dir "${tmp_po}"
    [ "${CODE}" -ne 0 ] || bad "空译文未被检出，覆盖率断言无效"
}

chk_layout
chk_wiring
chk_ssot
chk_drift
chk_catalog

[ "${FAILS}" -eq 0 ] || die "错误消息国际化面 ${FAILS} 项不成立"
say "错误消息国际化面通过"
