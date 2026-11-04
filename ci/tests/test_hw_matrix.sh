#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_hw_matrix.sh — ci/gates/hw-matrix.sh 的分支回归
#
# 能力矩阵读的是目标机的 sysfs/procfs，真机只在验收台上才齐全，判据只能由
# 合成证据树驱动：这里按内核导出的节点形态铺出五类能力，再逐条撤掉一个
# 前提，断言只看退出码与稳定判据名。真实机器是否通过由流水线调用本门禁
# 判定，本文件只保证「硬件在位」与「硬件可用」没有被混为一谈，也没有哪一
# 类因为没证据而整行消失。
#
# 用法: bash ci/tests/test_hw_matrix.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/hw-matrix.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib"
cp "${GATE}" "${FX}/ci/gates/hw-matrix.sh"
cp "${ROOT}/ci/lib/hwcap.py" "${FX}/ci/lib/hwcap.py"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

put()
{
    mkdir -p "$(dirname "${FX}/$1")"
    printf '%s\n' "$2" >"${FX}/$1"
}

run()
{
    bash "${FX}/ci/gates/hw-matrix.sh" --root "${FX}" "$@" 2>&1
}

reset()
{
    rm -rf "${FX}/sys" "${FX}/proc"
}

disp()
{
    mkdir -p "${FX}/drv/$1" "${FX}/sys/class/drm/card0/device"
    ln -sfn "${FX}/drv/$1" "${FX}/sys/class/drm/card0/device/driver"
    put "sys/class/drm/card0-$2/status" "$3"
    [ -z "${4:-}" ] || put "sys/class/drm/card0-$2/modes" "$4"
    [ -z "${5:-}" ] || put "sys/class/drm/card0-$2/edid" "$5"
}

good()
{
    reset
    disp i915 DP-1 connected 1920x1080 00ffffffff
    mkdir -p "${FX}/sys/class/ieee80211/phy0" "${FX}/sys/class/net/wlan0/phy80211"
    mkdir -p "${FX}/sys/class/sound/card0/pcm0p"
    put sys/class/input/input3/capabilities/key 40000000
    put sys/class/backlight/intel_backlight/max_brightness 1200
    put sys/class/backlight/intel_backlight/brightness 600
    put sys/class/leds/platform::kbd_backlight/max_brightness 3
    put sys/class/leds/platform::kbd_backlight/brightness 1
}

want()
{
    local out="$1" code="$2" what="$3"; shift 3
    local pat
    for pat in "$@"; do
        printf '%s\n' "${out}" | grep -qxF "${pat}" ||
            bad "${what}: 缺行 \"${pat}\""
    done
    [ "${RC}" -eq "${code}" ] ||
        bad "${what}: 退出码 ${RC}，期望 ${code}"
}

RC=0
good
out=$(run); RC=$?
want "${out}" 0 "全能力" \
    "CLASS display PASS driver=i915,connector=card0-DP-1,edid=1" \
    "CLASS wireless PASS phy=1,net=wlan0" \
    "CLASS audio PASS card=card0,pcm=1" \
    "CLASS input PASS devices=1,keyboards=1" \
    "CLASS backlight PASS node=intel_backlight,kbd=1" \
    "HW-MATRIX pass=5 na=0 fail=0"

reset
mkdir -p "${FX}/sys/class/sound/card0/pcm0p"
out=$(run --require display,wireless); RC=$?
want "${out}" 1 "选机拒绝" \
    "CLASS display N/A no-display-hardware note=本机无显示硬件" \
    "CLASS wireless N/A no-ieee80211 note=本机无无线硬件" \
    "SELECT machine reject missing=display,wireless"

good
out=$(run --require display,audio,input); RC=$?
want "${out}" 0 "选机通过" "SELECT machine accept"

good
put sys/devices/virtual/dmi/id/product_name "Unsupported Model X"
put sys/devices/virtual/dmi/id/sys_vendor "Example Inc"
out=$(run --require display,wireless,audio,input,backlight); RC=$?
want "${out}" 0 "DMI 不参与判定" "SELECT machine accept"

reset
disp simple-framebuffer DP-1 connected 1920x1080 00ffffffff
out=$(run --class display); RC=$?
want "${out}" 1 "simpledrm" "CLASS display FAIL no-kms note=仅 simpledrm 或未绑定 KMS 驱动" \
    "HW-MATRIX pass=0 na=0 fail=1"

reset
disp i915 DP-1 connected 1920x1080 ""
out=$(run --class display); RC=$?
want "${out}" 1 "缺 EDID" "CLASS display FAIL no-edid note=已连接输出 EDID 为空"

reset
disp i915 DP-1 connected "" 00ffffffff
out=$(run --class display); RC=$?
want "${out}" 1 "缺模式" "CLASS display FAIL no-mode note=已连接输出无可枚举模式"

reset
disp i915 DP-1 disconnected 1920x1080 00ffffffff
out=$(run --class display); RC=$?
want "${out}" 1 "无输出" "CLASS display FAIL no-output note=无已连接输出"

reset
mkdir -p "${FX}/sys/class/ieee80211/phy0"
out=$(run --class wireless); RC=$?
want "${out}" 1 "无网络接口" "CLASS wireless FAIL phy-without-netdev note=无线 PHY 已注册但无网络接口"

reset
mkdir -p "${FX}/sys/class/sound/card0"
out=$(run --class audio); RC=$?
want "${out}" 1 "无 PCM" "CLASS audio FAIL card-without-pcm note=声卡无 PCM 节点"

reset
put sys/class/input/input1/capabilities/key 0
put sys/class/input/input2/capabilities/key 100000000000
out=$(run --class input); RC=$?
want "${out}" 1 "无键盘" "CLASS input FAIL input-without-keyboard note=有输入设备但无键盘"

reset
put sys/class/backlight/amdgpu_bl0/max_brightness 0
put sys/class/backlight/amdgpu_bl0/brightness 0
out=$(run --class backlight); RC=$?
want "${out}" 1 "背光不可用" "CLASS backlight FAIL backlight-unusable note=背光节点不可读写"

good
out=$(run --json); RC=$?
printf '%s\n' "${out}" | grep -q '"classes"' || bad "JSON: 缺 classes"
printf '%s\n' "${out}" | grep -q '"accept": true' || bad "JSON: 选机结论缺失"
[ "${RC}" -eq 0 ] || bad "JSON: 退出码 ${RC}"

out=$(run --class nosuch); RC=$?
[ "${RC}" -eq 5 ] || bad "未知能力类: 退出码 ${RC}，期望 5"
out=$(bash "${FX}/ci/gates/hw-matrix.sh" --root "${tmp}/none" 2>&1); RC=$?
[ "${RC}" -eq 5 ] || bad "缺根: 退出码 ${RC}，期望 5"

if grep -nEi 'dmi|product_name|sys_vendor' "${ROOT}/ci/lib/hwcap.py" |
    grep -vE '^[0-9]+:[[:space:]]*#'; then
    bad "判定链出现 DMI/机型名"
fi

if [ "${fails}" -ne 0 ]; then
    printf '失败 %d 项\n' "${fails}"
    exit 1
fi
printf '全部通过\n'
exit 0
