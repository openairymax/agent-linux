#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_incident.sh — ci/gates/incident.sh 的台账与状态机回归
#
# 门禁没有目录选项，夹具按它的 ROOT 约定把门禁、日志库和最小 system 树
# 复制进临时根。钉住：合规复制树退 0（含编译、单测、闭环与告警幂等）；
# 入口失可执行位、缺运行手册、模块残留 TODO，三种坏输入各退 1 并报出条目。
#
# 用法: bash ci/tests/test_incident.sh
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
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/ops" \
        "${FX}/system/bin" "${FX}/system/tests/ops" "${FX}/system/services"
    cp "${ROOT}/ci/gates/incident.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
    cp -r "${ROOT}/system/ops/incident" "${FX}/system/ops/"
    cp -r "${ROOT}/system/services/airylib" "${FX}/system/services/"
    cp -p "${ROOT}/system/bin/airy-incident" "${FX}/system/bin/"
    cp "${ROOT}"/system/tests/ops/test_incident_*.py "${FX}/system/tests/ops/"
    cp "${ROOT}/system/CMakeLists.txt" "${FX}/system/"
}

run()
{
    bash "${FX}/ci/gates/incident.sh" 2>&1
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
check "合规复制树判通过" 0 "${rc}" "${out}" "incident 门禁通过"

mkfix
chmod -x "${FX}/system/bin/airy-incident"
out="$(run)"; rc=$?
check "入口失可执行位判负" 1 "${rc}" "${out}" "入口不可执行: bin/airy-incident"

mkfix
rm -f "${FX}/system/ops/incident/runbooks/crash-loop.md"
out="$(run)"; rc=$?
check "缺运行手册判负" 1 "${rc}" "${out}" "缺少运行手册 crash-loop"

mkfix
printf '# TODO: 尚未接入\n' >>"${FX}/system/ops/incident/model.py"
out="$(run)"; rc=$?
check "模块残留桩标记判负" 1 "${rc}" "${out}" "残留桩标记"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
