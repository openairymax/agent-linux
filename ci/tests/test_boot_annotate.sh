#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_boot_annotate.sh — 启动失败注解的回归
#
# 注解集不另立，取自 distro 侧 verdict.env 的 BOOT_FAIL。这里断言四件事：
# 判定性标记被标成 error；卡点自证探针打印的挂起作业被点名为 warning；健康
# 启动不出任何注解；判定集缺失时 gate 立即失败。
#
# 最后一条是这次回归的重点：先前注解集在 gate 里另抄了一份，新增判据漏改
# 时 gate 仍「成功」，只是没有任何注解——红着却说不清原因，比不注解更坏。
#
# 用法: bash ci/tests/test_boot_annotate.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
LOG="${FX}/out/run-bios-1/boot.log"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/distro/scripts/lib" \
    "${FX}/out/run-bios-1"
cp "${ROOT}/ci/gates/boot-annotate.sh" "${FX}/ci/gates/"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
cp "${ROOT}/distro/scripts/lib/verdict.env" "${FX}/distro/scripts/lib/"

# 判定只由 verdict.env 的 BOOT_FAIL 决定；这里摘掉宿主注入的 LD_LIBRARY_PATH，
# 否则每条外部命令都会把 libselinux 的版本告警打到 stderr，混进被捕获的输出。
run()
{
    env -u GITHUB_ACTIONS -u LD_LIBRARY_PATH \
        bash "${FX}/ci/gates/boot-annotate.sh" --dir "${FX}/out" 2>&1
}

fail()
{
    printf 'FAIL %s\n%s\n' "$1" "$2"
    fails=$((fails + 1))
}

# check NAME WANT_RC WANT_TEXT —— 退出码与输出片段都要对上。
check()
{
    local out rc
    out="$(run)"
    rc=$?
    if [ "${rc}" -ne "$2" ]; then
        fail "$1: rc=${rc} want $2" "${out}"
        return
    fi
    case "${out}" in
        *"$3"*) return 0 ;;
        *) fail "$1: 缺少「$3」" "${out}" ;;
    esac
}

# silent —— 健康启动不得有任何一行注解。
silent()
{
    local out
    out="$(run)"
    [ -z "${out}" ] || fail "$1: 不该有输出" "${out}"
}

printf 'AIRYMAX-BOOT-STUCK target=graphical.target\n' >"${LOG}"
check "卡点标记判 error" 0 "AIRYMAX-BOOT-STUCK"

printf 'AIRYMAX-BOOT-JOB unit=lightdm.service type=start state=running\n' \
    >"${LOG}"
check "挂起作业点名" 0 "boot unit failed: lightdm.service"

printf 'AIRYMAX-BOOT-DEGRADED failed=1 units=sshd.service\n' >"${LOG}"
check "降级标记仍注解" 0 "AIRYMAX-BOOT-DEGRADED"

printf 'AIRYMAX-DESKTOP-READY tap=ok\n' >"${LOG}"
silent "健康启动"

rm -f "${FX}/distro/scripts/lib/verdict.env"
check "判定集缺失即失败" 1 "缺少判定集"

if [ "${fails}" -ne 0 ]; then
    printf '%d 条断言失败\n' "${fails}"
    exit 1
fi
printf '全部通过\n'
