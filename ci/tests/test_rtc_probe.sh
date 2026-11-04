#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_rtc_probe.sh — ci/gates/rtc-probe.sh 的分支回归
#
# 门禁三个面各自可以被单独驱动，用例按面分组：
#
#   * 声明面：在临时树里放真实文件再逐项破坏（删标记、让限时器能判负、
#     改默认限时、删嵌入行、打乱步号、断开主题分发），断言每一处都判负
#     ——只测「合规树通过」不测拒绝，等于没测门禁。
#   * 取证面：日志是纯文本，直接写四类夹具（正常、断在 BEGIN 后、有 END
#     无结论、超限却报 OK）与两类设计内降级（SLOW、SKIP）。
#   * 实物面：假 airy-observe 放临时 PATH，覆盖设备缺席、偏差正常、偏差
#     超阈值、读数不可解析四种现场。
#
# 用法: bash ci/tests/test_rtc_probe.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/distro/kickstart" \
    "${FX}/distro/desktop" "${tmp}/bin" "${tmp}/log"
cp "${ROOT}/ci/gates/rtc-probe.sh" "${FX}/ci/gates/rtc-probe.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# tree —— 重新铺一份合规树；每个用例从干净状态起，避免互相污染。
tree()
{
    cp "${ROOT}/distro/kickstart/rtc-sync.sh" "${FX}/distro/kickstart/"
    cp "${ROOT}"/distro/kickstart/*.ks "${FX}/distro/kickstart/"
    cp "${ROOT}/distro/desktop/airy_observe.sh" "${FX}/distro/desktop/"
}

run()
{
    bash "${FX}/ci/gates/rtc-probe.sh" --tree "${FX}" "$@" 2>&1
}

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

# --- 声明面 ---------------------------------------------------------------

tree
out="$(run)"
check "合规树判定通过" 0 "$?" "${out}" "RTC 门禁通过"

rm -f "${FX}/distro/kickstart/rtc-sync.sh"
out="$(run)"
check "限时器缺失判拒绝" 1 "$?" "${out}" "缺少 distro/kickstart/rtc-sync.sh"

tree
printf '\nairy_fail RTC nope\n' >>"${FX}/distro/kickstart/rtc-sync.sh"
out="$(run)"
check "限时器能判负判拒绝" 1 "$?" "${out}" "能判负"

tree
printf '\nexit 1\n' >>"${FX}/distro/kickstart/rtc-sync.sh"
out="$(run)"
check "限时器可非零退出判拒绝" 1 "$?" "${out}" "能判负"

tree
sed -i '/AIRYMAX-RTC-SLOW/d' "${FX}/distro/kickstart/rtc-sync.sh"
out="$(run)"
check "缺 SLOW 标记判拒绝" 1 "$?" "${out}" "缺少标记 AIRYMAX-RTC-SLOW"

tree
sed -i 's/AIRY_RTC_LIMIT:-60/AIRY_RTC_LIMIT:-20/' \
    "${FX}/distro/kickstart/rtc-sync.sh"
out="$(run)"
check "默认限时被改判拒绝" 1 "$?" "${out}" "默认限时不是 60 秒"

tree
printf '# TODO\n' >>"${FX}/distro/kickstart/rtc-sync.sh"
out="$(run)"
check "限时器带桩标记判拒绝" 1 "$?" "${out}" "存在桩标记"

tree
sed -i '/%airy-embed: rtc-sync.sh/d' "${FX}/distro/kickstart/airymaxos-minimal.ks"
out="$(run)"
check "剖面未嵌入限时器判拒绝" 1 "$?" "${out}" "未嵌入 rtc-sync.sh"

tree
sed -i 's/^airy_rtc_sync$/:/' \
    "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run)"
check "剖面未调用限时器判拒绝" 1 "$?" "${out}" "未调用 airy_rtc_sync"

tree
sed -i 's/^airy_step 8 8 rtc$/airy_step 8 9 rtc/' \
    "${FX}/distro/kickstart/airymaxos-minimal.ks"
out="$(run)"
check "步号与总数不一致判拒绝" 1 "$?" "${out}" "总数不唯一"

tree
sed -i 's/^airy_step 10 10 rtc$/airy_step 9 10 rtc/' \
    "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run)"
check "rtc 阶段号错位判拒绝" 1 "$?" "${out}" "阶段号与总数不一致"

tree
sed -i '/^        rtc) rtc_report ;;$/d' "${FX}/distro/desktop/airy_observe.sh"
out="$(run)"
check "观察面未接主题分发判拒绝" 1 "$?" "${out}" "未把 rtc 接入主题分发"

tree
sed -i '/emit rtc.hctosys/d' "${FX}/distro/desktop/airy_observe.sh"
out="$(run)"
check "观察面缺字段判拒绝" 1 "$?" "${out}" "缺少 rtc.hctosys"

# --- 取证面 ---------------------------------------------------------------

log()
{
    cat >"${tmp}/log/install.log"
    printf '%s' "${tmp}/log/install.log"
}

f="$(log <<'EOF'
AIRYMAX-INSTALL-STEP 1/8 base
AIRYMAX-RTC-BEGIN t=1762300000
AIRYMAX-RTC-END ms=120
AIRYMAX-RTC-OK ms=120
AIRYMAX-RTC-DRIFT sec=1
AIRYMAX-INSTALL-STEP 8/8 rtc
EOF
)"
out="$(run --log "$f")"
check "正常写入判定通过" 0 "$?" "${out}" "ok=1"

f="$(log <<'EOF'
AIRYMAX-RTC-BEGIN t=1762300000
AIRYMAX-RTC-END ms=0
AIRYMAX-RTC-SKIP reason=no-device
EOF
)"
out="$(run --log "$f")"
check "跳过只记不判负" 0 "$?" "${out}" "skip=1"

f="$(log <<'EOF'
AIRYMAX-RTC-BEGIN t=1762300000
AIRYMAX-RTC-END ms=61000
AIRYMAX-RTC-SLOW ms=61000 limit=60000
EOF
)"
out="$(run --log "$f")"
check "超限告警只记不判负" 0 "$?" "${out}" "slow=1"

f="$(log <<'EOF'
AIRYMAX-RTC-BEGIN t=1762300000
AIRYMAX-INSTALL-STEP 8/8 rtc
EOF
)"
out="$(run --log "$f")"
check "只有 BEGIN 判拒绝" 1 "$?" "${out}" "有 BEGIN 无 END"

f="$(log <<'EOF'
AIRYMAX-RTC-BEGIN t=1762300000
AIRYMAX-RTC-END ms=90
EOF
)"
out="$(run --log "$f")"
check "有 END 无结论判拒绝" 1 "$?" "${out}" "有 END 无结论"

f="$(log <<'EOF'
AIRYMAX-RTC-BEGIN t=1762300000
AIRYMAX-RTC-END ms=90000
AIRYMAX-RTC-OK ms=90000
EOF
)"
out="$(run --log "$f")"
check "超限却报 OK 判拒绝" 1 "$?" "${out}" "却没有 SLOW"

f="$(log <<'EOF'
AIRYMAX-INSTALL-STEP 1/8 base
EOF
)"
out="$(run --log "$f")"
check "日志无打点判拒绝" 1 "$?" "${out}" "没有 RTC 打点"

f="$(log <<'EOF'
AIRYMAX-RTC-BEGIN t=1762300000
AIRYMAX-RTC-END ms=10
AIRYMAX-RTC-OK ms=10
AIRYMAX-RTC-BEGIN t=1762300100
AIRYMAX-RTC-END ms=20
AIRYMAX-RTC-SKIP reason=no-tool
EOF
)"
out="$(run --log "$f")"
check "多次装机逐块独立判定" 0 "$?" "${out}" "ok=1 slow=0 skip=1"

# --- 实物面 ---------------------------------------------------------------

observe()
{
    cat >"${tmp}/bin/airy-observe"
    chmod +x "${tmp}/bin/airy-observe"
}

live()
{
    PATH="${tmp}/bin:${PATH}" bash "${FX}/ci/gates/rtc-probe.sh" --live "$@" 2>&1
}

observe <<'EOF'
#!/bin/sh
printf 'AIRYMAX-OBSERVE rtc.device=absent\n'
printf 'AIRYMAX-OBSERVE rtc.name=absent\n'
printf 'AIRYMAX-OBSERVE rtc.epoch=absent\n'
printf 'AIRYMAX-OBSERVE rtc.drift=absent\n'
printf 'AIRYMAX-OBSERVE rtc.hctosys=absent\n'
EOF
out="$(live)"
check "无 RTC 设备判不适用" 5 "$?" "${out}" "不适用"

observe <<'EOF'
#!/bin/sh
printf 'AIRYMAX-OBSERVE rtc.device=rtc0\n'
printf 'AIRYMAX-OBSERVE rtc.name=rtc_cmos\n'
printf 'AIRYMAX-OBSERVE rtc.epoch=1762300000\n'
printf 'AIRYMAX-OBSERVE rtc.drift=1\n'
printf 'AIRYMAX-OBSERVE rtc.hctosys=1\n'
EOF
out="$(live)"
check "偏差在阈值内判定通过" 0 "$?" "${out}" "RTC 门禁通过"

out="$(live --drift 0)"
check "偏差超阈值只警告" 0 "$?" "${out}" "可能未落地"

observe <<'EOF'
#!/bin/sh
printf 'AIRYMAX-OBSERVE rtc.device=rtc0\n'
printf 'AIRYMAX-OBSERVE rtc.name=rtc_cmos\n'
printf 'AIRYMAX-OBSERVE rtc.epoch=absent\n'
printf 'AIRYMAX-OBSERVE rtc.drift=absent\n'
printf 'AIRYMAX-OBSERVE rtc.hctosys=1\n'
EOF
out="$(live)"
check "读数不可解析判失败" 1 "$?" "${out}" "写入未落地"

rm -f "${tmp}/bin/airy-observe"
out="$(PATH="${tmp}/bin:/usr/bin:/bin" bash "${FX}/ci/gates/rtc-probe.sh" --live 2>&1)"
check "缺观察命令按环境不满足" 5 "$?" "${out}" "缺少命令"

out="$(run --bogus)"
check "未知参数按参数错误" 5 "$?" "${out}" "未知参数"

out="$(run --help)"
check "帮助以 0 退出" 0 "$?" "${out}" "用法"

exit "${fails}"
