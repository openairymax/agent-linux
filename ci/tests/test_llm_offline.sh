#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_llm_offline.sh — ci/gates/llm-offline.sh 的离线自检回归
#
# 门禁没有目录选项，夹具按它的 ROOT 约定复制门禁、契约库与模型栈到临时根。
# 钉住：合规复制树退 0 且打印 passed；自检包引入 socket、单文件 selftest.py
# 与包并存、单元丢掉 ConditionPathExists，三种坏输入各退 1 并报出条目。
#
# 用法: bash ci/tests/test_llm_offline.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

mkfix()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/bin" \
        "${FX}/system/units" "${FX}/system/services"
    cp "${ROOT}/ci/gates/llm-offline.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${ROOT}/ci/lib/check.sh" \
        "${ROOT}/ci/lib/model_assets.sh" "${ROOT}/ci/lib/llm_contract.py" \
        "${FX}/ci/lib/"
    cp -p "${ROOT}/system/bin/airy-model" "${FX}/system/bin/"
    cp -r "${ROOT}/system/services/airyllm" \
        "${ROOT}/system/services/airylib" \
        "${ROOT}/system/services/airymax_rt" "${FX}/system/services/"
    cp "${ROOT}/system/units/airy-llm.service" "${FX}/system/units/"
}

run()
{
    bash "${FX}/ci/gates/llm-offline.sh" 2>&1
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
check "合规复制树判通过" 0 "${rc}" "${out}" "== llm-offline passed =="

mkfix
printf 'import socket\n' >>"${FX}/system/services/airyllm/selftest/verdict.py"
out="$(run)"; rc=$?
check "自检包引网络判负" 1 "${rc}" "${out}" "自检包引入网络依赖"

mkfix
: >"${FX}/system/services/airyllm/selftest.py"
out="$(run)"; rc=$?
check "单文件并存判负" 1 "${rc}" "${out}" \
    "单文件 selftest.py 与自检包并存"

mkfix
sed -i '/^ConditionPathExists=/d' "${FX}/system/units/airy-llm.service"
out="$(run)"; rc=$?
check "单元未跳过启动判负" 1 "${rc}" "${out}" \
    "单元未在清单缺失时跳过启动"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
