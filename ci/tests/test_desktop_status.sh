#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_desktop_status.sh — ci/gates/desktop-status.sh 的声明面回归
#
# 实物面（面板对三个生产者的行为）由门禁自己用桩覆盖；这里补的是另一类
# 漏判：声明面若把「菜单项可点」「菜单项可见」「打包三处同源」写成永远为
# 真的 grep，门禁就会在菜单项坏掉时照样放行。每个负例只破坏一处，断言退
# 出码与稳定 token，不看措辞。
#
# 用法: bash ci/tests/test_desktop_status.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/desktop-status.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
cpfix()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/distro/desktop" "${FX}/distro/specs" \
        "${FX}/ci/lib" "${FX}/ci/gates"
    cp -p "${ROOT}/distro/desktop/airy_status.py" "${FX}/distro/desktop/"
    cp -p "${ROOT}/distro/desktop/airymax-status.desktop" "${FX}/distro/desktop/"
    cp -p "${ROOT}/distro/specs/airymaxos-release.spec" "${FX}/distro/specs/"
    cp "${GATE}" "${FX}/ci/gates/desktop-status.sh"
    cp "${ROOT}/ci/lib/log.sh" "${ROOT}/ci/lib/check.sh" "${FX}/ci/lib/"
}

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        printf 'FAIL %s（rc=%s，期望 %s）\n' "${name}" "${got}" "${want}"
        printf '%s\n' "${out}" >&2
        fails=$((fails + 1))
        return 0
    fi
    case "${out}" in
    *"${needle}"*) printf 'ok   %s\n' "${name}" ;;
    *)
        printf 'FAIL %s（输出缺「%s」）\n' "${name}" "${needle}"
        printf '%s\n' "${out}" >&2
        fails=$((fails + 1))
        ;;
    esac
}

run()
{
    bash "${FX}/ci/gates/desktop-status.sh" --tree "${FX}" 2>&1
}

cpfix
out="$(run)"
check "合规树判通过" 0 "$?" "${out}" "桌面状态判定通过"

cpfix
sed -i '/^Exec=/d' "${FX}/distro/desktop/airymax-status.desktop"
out="$(run)"
check "菜单项缺 Exec 判拒绝" 1 "$?" "${out}" "菜单项 Exec"

cpfix
printf 'NoDisplay=true\n' >>"${FX}/distro/desktop/airymax-status.desktop"
out="$(run)"
check "菜单项隐藏判拒绝" 1 "$?" "${out}" "NoDisplay"

cpfix
sed -i 's/^Name\[zh_CN\]=.*//' "${FX}/distro/desktop/airymax-status.desktop"
out="$(run)"
check "菜单项缺中文名判拒绝" 1 "$?" "${out}" "缺中文名"

cpfix
sed -i '/^Source51:/d' "${FX}/distro/specs/airymaxos-release.spec"
out="$(run)"
check "spec 未声明实现判拒绝" 1 "$?" "${out}" \
    "spec 未声明 airy_status.py"

cpfix
sed -i '/^Source52:/d' "${FX}/distro/specs/airymaxos-release.spec"
out="$(run)"
check "spec 未声明菜单项判拒绝" 1 "$?" "${out}" \
    "spec 未声明 airymax-status.desktop"

cpfix
rm -f "${FX}/distro/desktop/airy_status.py"
out="$(run)"
check "实现缺失判拒绝" 1 "$?" "${out}" "缺少 distro/desktop/airy_status.py"

cpfix
sed -i 's/airy-model/airy-llm/' "${FX}/distro/desktop/airy_status.py"
out="$(run)"
check "面板漏掉生产者判拒绝" 1 "$?" "${out}" "未调用生产者 airy-model"

out="$(bash "${FX}/ci/gates/desktop-status.sh" --tree "${tmp}/absent" 2>&1)"
check "树目录不存在按参数错误" 5 "$?" "${out}" "目录不存在"

exit "${fails}"
