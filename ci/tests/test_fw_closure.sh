#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_fw_closure.sh — ci/gates/fw-closure.sh 的分支回归
#
# runner 上没有镜像根，门禁的常规入口是「清单 + comps」两份文本对账。
# 这里用临时夹具驱动全部分支：合规对账、双向集合缺口、字段数、类别、
# 路径前缀、上级引用、重复条目、comps 无固件包、空清单，以及 --root 的
# 实物核对通过与缺文件。断言只看退出码与稳定 token，不看措辞。
#
# 用法: bash ci/tests/test_fw_closure.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/fw-closure.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

run()
{
    bash "${GATE}" "$@" 2>&1
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

comps()
{
    printf '<comps><group><packagelist>%s</packagelist></group></comps>\n' "$2" >"$1"
}

# 合规清单：一个固件包 + 一个名称不含 firmware 的监管域包。
cat >"${tmp}/good.txt" <<'EOF'
# 注释与空行不参与判定
linux-firmware   wifi       /usr/lib/firmware/iwlwifi-*.ucode*
linux-firmware   gpu        /usr/lib/firmware/i915/*.bin*
wireless-regdb   regdomain  /usr/lib/firmware/regulatory.db*
EOF

comps "${tmp}/comps.xml" \
    '<packagereq type="mandatory">bash</packagereq>
<packagereq type="default">linux-firmware</packagereq>
<packagereq type="default">wireless-regdb</packagereq>'

out="$(run --manifest "${tmp}/good.txt" --comps "${tmp}/comps.xml")"
check "清单与 comps 对账通过" 0 "$?" "${out}" "固件闭包判定通过"

cat >"${tmp}/lack.txt" <<'EOF'
wireless-regdb   regdomain  /usr/lib/firmware/regulatory.db*
EOF
out="$(run --manifest "${tmp}/lack.txt" --comps "${tmp}/comps.xml")"
check "comps 固件包漏声明判拒绝" 1 "$?" "${out}" \
    "comps 固件包未在清单声明: linux-firmware"

cat >"${tmp}/extra.txt" <<'EOF'
linux-firmware       wifi       /usr/lib/firmware/iwlwifi-*.ucode*
linux-firmware-ath   wifi       /usr/lib/firmware/ath10k/*.bin*
wireless-regdb       regdomain  /usr/lib/firmware/regulatory.db*
EOF
out="$(run --manifest "${tmp}/extra.txt" --comps "${tmp}/comps.xml")"
check "清单声明镜像不装的包判拒绝" 1 "$?" "${out}" \
    "清单包不在 comps: linux-firmware-ath"

printf 'linux-firmware  wifi\n' >"${tmp}/short.txt"
out="$(run --manifest "${tmp}/short.txt" --comps "${tmp}/comps.xml")"
check "字段数不足判拒绝" 1 "$?" "${out}" "字段数不等于 3"

printf 'linux-firmware  wifi  /usr/lib/firmware/x  y\n' >"${tmp}/long.txt"
out="$(run --manifest "${tmp}/long.txt" --comps "${tmp}/comps.xml")"
check "字段数过多判拒绝" 1 "$?" "${out}" "字段数不等于 3"

printf 'linux-firmware  WiFi  /usr/lib/firmware/x.ucode*\n' >"${tmp}/cls.txt"
out="$(run --manifest "${tmp}/cls.txt" --comps "${tmp}/comps.xml")"
check "器件类别非小写判拒绝" 1 "$?" "${out}" "器件类别非法"

printf 'linux-firmware  wifi  /lib/firmware/x.ucode*\n' >"${tmp}/path.txt"
out="$(run --manifest "${tmp}/path.txt" --comps "${tmp}/comps.xml")"
check "路径不在固件树判拒绝" 1 "$?" "${out}" "不在 /usr/lib/firmware 下"

printf 'linux-firmware  wifi  /usr/lib/firmware/../etc/x*\n' >"${tmp}/up.txt"
out="$(run --manifest "${tmp}/up.txt" --comps "${tmp}/comps.xml")"
check "路径含上级引用判拒绝" 1 "$?" "${out}" "含上级引用"

cat >"${tmp}/dup.txt" <<'EOF'
linux-firmware  wifi  /usr/lib/firmware/iwlwifi-*.ucode*
linux-firmware  wifi  /usr/lib/firmware/iwlwifi-*.ucode*
wireless-regdb  regdomain  /usr/lib/firmware/regulatory.db*
EOF
out="$(run --manifest "${tmp}/dup.txt" --comps "${tmp}/comps.xml")"
check "重复条目判拒绝" 1 "$?" "${out}" "清单条目重复"

comps "${tmp}/bare.xml" \
    '<packagereq type="mandatory">bash</packagereq>
<packagereq type="default">wireless-regdb</packagereq>'
out="$(run --manifest "${tmp}/good.txt" --comps "${tmp}/bare.xml")"
check "comps 无固件包判拒绝" 1 "$?" "${out}" "comps 未声明固件包"

printf '# 只有注释\n\n' >"${tmp}/empty.txt"
out="$(run --manifest "${tmp}/empty.txt" --comps "${tmp}/comps.xml")"
check "空清单判拒绝" 1 "$?" "${out}" "固件清单为空"

mkdir -p "${tmp}/root/usr/lib/firmware/i915"
: >"${tmp}/root/usr/lib/firmware/iwlwifi-gl-c0-fm-c0-83.ucode"
: >"${tmp}/root/usr/lib/firmware/regulatory.db"
: >"${tmp}/root/usr/lib/firmware/regulatory.db.p7s"
: >"${tmp}/root/usr/lib/firmware/i915/adlp_dmc.bin"
out="$(run --manifest "${tmp}/good.txt" --comps "${tmp}/comps.xml" \
    --root "${tmp}/root")"
check "实物核对全部命中判通过" 0 "$?" "${out}" "固件闭包判定通过"

cat >"${tmp}/gpu.txt" <<'EOF'
linux-firmware  gpu  /usr/lib/firmware/i915/*.bin*
wireless-regdb  regdomain  /usr/lib/firmware/regulatory.db*
EOF
rm -f "${tmp}/root/usr/lib/firmware/i915/adlp_dmc.bin"
out="$(run --manifest "${tmp}/gpu.txt" --comps "${tmp}/comps.xml" \
    --root "${tmp}/root")"
check "实物核对缺文件判拒绝" 1 "$?" "${out}" "固件缺失 [gpu]"

out="$(run --manifest "${tmp}/absent.txt" --comps "${tmp}/comps.xml")"
check "清单不存在按参数错误" 5 "$?" "${out}" "清单不可读"

mkdir -p "${tmp}/bare-root"
out="$(run --manifest "${tmp}/good.txt" --comps "${tmp}/comps.xml" \
    --root "${tmp}/bare-root")"
check "root 无固件树按参数错误" 5 "$?" "${out}" "无固件树"

out="$(run --bogus)"
check "未知参数按参数错误" 5 "$?" "${out}" "未知参数"

out="$(run --help)"
check "帮助以 0 退出" 0 "$?" "${out}" "用法"

exit "${fails}"
