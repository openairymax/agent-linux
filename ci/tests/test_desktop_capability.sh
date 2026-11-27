#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_desktop_capability.sh — ci/gates/desktop-capability.sh 回归
#
# 钉住声明面判据真能判负：座位缺可执行位、代码行出现显卡型号、smoke 不再
# 核对驱动标记，三种坏输入各退 1 并报出对应条目；原样复制树退 0，说明实物
# 面（临时 DRM 树 + 伪 Xorg）的判定链路可以整体跑通。
#
# 用法: bash ci/tests/test_desktop_capability.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/desktop-capability.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

mkfix()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/distro/desktop" "${FX}/distro/ci"
    cp -p "${ROOT}/distro/desktop/xserver.sh" "${FX}/distro/desktop/"
    cp "${ROOT}/distro/ci/run-smoke.sh" "${FX}/distro/ci/"
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
check "合规座位判通过" 0 "${rc}" "${out}" "显示能力判定通过"

mkfix
chmod -x "${FX}/distro/desktop/xserver.sh"
out="$(run)"; rc=$?
check "座位不可执行判负" 1 "${rc}" "${out}" "xserver.sh 不可执行"

mkfix
printf 'echo intel\n' >>"${FX}/distro/desktop/xserver.sh"
out="$(run)"; rc=$?
check "代码硬编码型号判负" 1 "${rc}" "${out}" "代码里硬编码了显卡型号"

mkfix
printf '# TODO: refine\n' >>"${FX}/distro/desktop/xserver.sh"
out="$(run)"; rc=$?
check "桩标记判负" 1 "${rc}" "${out}" "存在桩标记"

mkfix
sed -i '/seat_marker/d' "${FX}/distro/ci/run-smoke.sh"
out="$(run)"; rc=$?
check "smoke 未核对标记判负" 1 "${rc}" "${out}" "smoke T1 未核对驱动标记"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $(( fails > 0 ? 1 : 0 ))
