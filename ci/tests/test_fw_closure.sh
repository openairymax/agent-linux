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
# 后半段覆盖需要外部命令的三面：内核 API 对账（假 modinfo）、.pnvm 与
# .ucode 成对（真实临时文件树）、SELinux 标签（假 matchpathcon）。两条
# 外部命令都经 AIRY_MODINFO / AIRY_MATCHPATHCON 注入，宿主有没有 kmod 与
# 策略都不影响结论，这才是「同一份判据在任何机器上同结论」。
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

# ---------------------------------------------------------------------------
# 内核 API 对账、pnvm 成对、标签断言
#
# 三条外部事实都用夹具替代：modinfo 只回答「这个驱动要哪些固件」，标签只
# 回答「策略给什么」，这样每条分支在任何宿主机上都可复现，也不依赖 kmod
# 或 SELinux 是否安装。真实镜像里两个命令的输出由发行版提供，判定逻辑
# 与它们无关。
# ---------------------------------------------------------------------------
mkdir -p "${tmp}/bin" "${tmp}/mods" "${tmp}/selroot/usr/lib/firmware/i915"
cat >"${tmp}/bin/modinfo" <<'EOF'
#!/usr/bin/env bash
# 形如 iwlwifi 的驱动声明 ucode 与同 stem 的 pnvm；其余驱动声明一个
# 镜像内不存在的文件，用来验证差集非空会被报出来。
case "${3##*/}" in
iwlwifi*) printf 'iwlwifi-gl-c0-fm-c0-83.ucode\niwlwifi-gl-c0-fm-c0.pnvm\n' ;;
*) printf 'vendor/extra.bin\n' ;;
esac
EOF
cat >"${tmp}/bin/matchpathcon" <<'EOF'
#!/usr/bin/env bash
printf '%s\t%s\n' "$2" "${MOCK_LABEL:-system_u:object_r:lib_t:s0}"
EOF
cat >"${tmp}/bin/nocvf" <<'EOF'
#!/usr/bin/env bash
printf '%s\t<<none>>\n' "$2"
EOF
chmod +x "${tmp}/bin/"*
: >"${tmp}/mods/iwlwifi.ko"
: >"${tmp}/mods/e1000e.ko"
: >"${tmp}/selroot/usr/lib/firmware/iwlwifi-gl-c0-fm-c0-83.ucode"
: >"${tmp}/selroot/usr/lib/firmware/iwlwifi-gl-c0-fm-c0.pnvm"
: >"${tmp}/selroot/usr/lib/firmware/regulatory.db"
: >"${tmp}/selroot/usr/lib/firmware/regulatory.db.p7s"
: >"${tmp}/selroot/usr/lib/firmware/i915/adlp_dmc.bin"

# 两个注入点都优先取调用方给的值，便于单独替换其中一条。MOCK_LABEL 显式
# 转给子进程：函数前缀赋值只在函数作用域内生效，不保证已导出。
runx()
{
    MOCK_LABEL="${MOCK_LABEL:-}" \
    AIRY_MODINFO="${AIRY_MODINFO:-${tmp}/bin/modinfo}" \
    AIRY_MATCHPATHCON="${AIRY_MATCHPATHCON:-${tmp}/bin/matchpathcon}" \
    bash "${GATE}" "$@" 2>&1
}

base=(--manifest "${tmp}/good.txt" --comps "${tmp}/comps.xml")

out="$(runx "${base[@]}" --root "${tmp}/selroot" \
    --kernel "${tmp}/mods" --drv iwlwifi)"
check "驱动固件依赖全部命中判通过" 0 "$?" "${out}" \
    "内核固件依赖核对: 驱动 1"

out="$(runx "${base[@]}" --root "${tmp}/selroot" --kernel "${tmp}/mods")"
check "给模块树却不给驱动名判拒绝" 5 "$?" "${out}" "至少一个 --drv"

out="$(runx "${base[@]}" --kernel "${tmp}/mods" --drv iwlwifi)"
check "模块树缺镜像根判拒绝" 5 "$?" "${out}" "需要同时给 --root"

out="$(runx "${base[@]}" --root "${tmp}/selroot" --drv iwlwifi)"
check "驱动名缺模块树判拒绝" 5 "$?" "${out}" "--drv 需要同时给"

out="$(runx "${base[@]}" --root "${tmp}/selroot" \
    --kernel "${tmp}/mods" --drv ath12k)"
check "模块树无此驱动判拒绝" 1 "$?" "${out}" "模块树内无此驱动: ath12k"

out="$(runx "${base[@]}" --root "${tmp}/selroot" \
    --kernel "${tmp}/mods" --drv e1000e)"
check "驱动声明固件不在镜像判拒绝" 1 "$?" "${out}" \
    "驱动固件缺失 [e1000e] vendor/extra.bin"

out="$(AIRY_MODINFO="${tmp}/bin/absent" runx "${base[@]}" \
    --root "${tmp}/selroot" --kernel "${tmp}/mods" --drv iwlwifi)"
check "无 modinfo 按证据不可用" 5 "$?" "${out}" "读不出驱动的固件依赖"

cp -a "${tmp}/selroot" "${tmp}/nopnvm"
rm -f "${tmp}/nopnvm/usr/lib/firmware/iwlwifi-gl-c0-fm-c0.pnvm"
out="$(runx "${base[@]}" --root "${tmp}/nopnvm" \
    --kernel "${tmp}/mods" --drv iwlwifi)"
check "驱动要的 pnvm 缺失判拒绝" 1 "$?" "${out}" \
    "驱动固件缺失 [iwlwifi] iwlwifi-gl-c0-fm-c0.pnvm"

# 发行版把固件存成 .xz/.zst 是常态，压缩变体必须同样参与对账。
cp -a "${tmp}/selroot" "${tmp}/xzroot"
mv "${tmp}/xzroot/usr/lib/firmware/iwlwifi-gl-c0-fm-c0-83.ucode" \
    "${tmp}/xzroot/usr/lib/firmware/iwlwifi-gl-c0-fm-c0-83.ucode.xz"
out="$(runx "${base[@]}" --root "${tmp}/xzroot" \
    --kernel "${tmp}/mods" --drv iwlwifi)"
check "压缩固件参与对账" 0 "$?" "${out}" "内核固件依赖核对: 驱动 1"

cp -a "${tmp}/selroot" "${tmp}/lone"
: >"${tmp}/lone/usr/lib/firmware/iwlwifi-bz-b0-gf-a0.pnvm"
out="$(runx "${base[@]}" --root "${tmp}/lone")"
check "孤立 pnvm 判拒绝" 1 "$?" "${out}" "伴生 ucode 缺失"

out="$(MOCK_LABEL=system_u:object_r:usr_t:s0 runx "${base[@]}" \
    --root "${tmp}/selroot")"
check "固件标签非 lib_t 判拒绝" 1 "$?" "${out}" "固件标签非 lib_t"

out="$(AIRY_MATCHPATHCON="${tmp}/bin/absent" runx "${base[@]}" \
    --root "${tmp}/selroot")"
check "无 matchpathcon 只提示不判负" 0 "$?" "${out}" "跳过固件标签断言"

out="$(AIRY_MATCHPATHCON="${tmp}/bin/nocvf" runx "${base[@]}" \
    --root "${tmp}/selroot")"
check "本机无策略只提示不判负" 0 "$?" "${out}" "无 SELinux 策略"

exit "${fails}"
