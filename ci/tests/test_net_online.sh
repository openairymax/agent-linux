#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_net_online.sh — ci/gates/net-online.sh 的分支回归
#
# 门禁的三个观察面都依赖真实资产：comps、固件清单、桌面策略、spec 与两份
# kickstart。这里把门禁与日志库复制进临时夹具树，用最小合规资产驱动全部
# 判定分支——声明面逐项破坏、实物面缺件、联机面的地址来源与网关可达性、
# 以及前置命令缺失与参数错误。断言只看退出码与稳定 token，不看措辞。
#
# 真实树是否合规不在这里判：流水线里的 ci/ci.sh net-online 负责那一条，
# 本文件只保证判定逻辑本身没有漏判与误判。
#
# 用法: bash ci/tests/test_net_online.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/net-online.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

# 夹具根：门禁按 $(dirname $0)/../.. 定位仓库根，复制到 FX 后即指向夹具。
FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" \
    "${FX}/distro/comps" "${FX}/distro/firmware" "${FX}/distro/network" \
    "${FX}/distro/specs" "${FX}/distro/kickstart"
cp "${GATE}" "${FX}/ci/gates/net-online.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

# run ARGS... —— 跑夹具里的门禁，stderr 并入 stdout 便于断言。
run()
{
    bash "${FX}/ci/gates/net-online.sh" "$@" 2>&1
}

# live DIR ARGS... —— 以 DIR 打头的 PATH 跑联机面，用桩替换 airy-observe
# 与 ping：这两个命令只有装机后的机器上才有，行为由断言逐条给定。
live()
{
    local d="$1"

    shift
    PATH="${d}:${PATH}" bash "${FX}/ci/gates/net-online.sh" "$@" 2>&1
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

# w PATH —— 用标准输入覆盖夹具里的一件资产。
w()
{
    cat >"${FX}/distro/$1"
}

# obs SRC —— 联机面的桩采集器：net.ipv4 恒定有一个合法地址，来源由参数
# 决定，网关与 DNS 恒定可用。判定在来源处就该分开，下游字段不必配合。
obs()
{
    cat >"${tmp}/stub/airy-observe" <<EOF
#!/bin/sh
cat <<REPORT
AIRYMAX-OBSERVE net.ipv4=eth0=192.168.3.57
AIRYMAX-OBSERVE net.src=$1
AIRYMAX-OBSERVE net.gw=192.168.3.1
AIRYMAX-OBSERVE net.dns=192.168.3.1
REPORT
EOF
    chmod +x "${tmp}/stub/airy-observe"
}

comps()
{
    w comps/airymaxos-comps.xml <<'EOF'
<comps><group><packagelist>
<packagereq type="mandatory">NetworkManager</packagereq>
<packagereq type="default">NetworkManager-wifi</packagereq>
<packagereq type="default">wpa_supplicant</packagereq>
<packagereq type="default">wireless-regdb</packagereq>
<packagereq type="default">iw</packagereq>
</packagelist></group></comps>
EOF
}

conf()
{
    w network/99-airymax.conf <<'EOF'
[main]
dns=default
rc-manager=symlink

[connectivity]
enabled=false
EOF
}

spec()
{
    w specs/airymaxos-release.spec <<'EOF'
Source37: 99-airymax.conf

%install
install -m 0644 %{SOURCE37} \
    %{buildroot}%{_sysconfdir}/NetworkManager/conf.d/99-airymax.conf

%files
%dir %{_sysconfdir}/NetworkManager/conf.d
%config(noreplace) %{_sysconfdir}/NetworkManager/conf.d/99-airymax.conf
EOF
}

fwman()
{
    w firmware/fw-manifest.txt <<'EOF'
# 包名 类别 路径
linux-firmware  wifi  /usr/lib/firmware/iwlwifi-*.ucode*
linux-firmware  gpu   /usr/lib/firmware/i915/*.bin*
EOF
}

# 两个剖面启用单元的方式不同：min 走 services 指令，ws 在 %post 里离线
# 启用。合规夹具同时覆盖两条路径，判据不把任何一种写法钉成唯一做法。
ks()
{
    w kickstart/min.ks <<'EOF'
network --bootproto=dhcp --activate --hostname=airymax-pc
services --enabled=sshd,chronyd,NetworkManager
EOF
    w kickstart/ws.ks <<'EOF'
network --bootproto=dhcp --activate
systemctl --root=/ enable NetworkManager.service
EOF
}

mkfix()
{
    comps
    conf
    spec
    fwman
    ks
}

mkfix

out="$(run)"
check "合规资产判定通过" 0 "$?" "${out}" "网络子系统判定通过"

comps
sed -i '/>iw</d' "${FX}/distro/comps/airymaxos-comps.xml"
out="$(run)"
check "comps 缺无线包判拒绝" 1 "$?" "${out}" "comps 未安装 iw"

comps
sed -i 's|</packagelist>|<packagereq>NetworkManager-config-server</packagereq></packagelist>|' \
    "${FX}/distro/comps/airymaxos-comps.xml"
out="$(run)"
check "comps 装服务器策略包判拒绝" 1 "$?" "${out}" \
    "comps 装入了 NetworkManager-config-server"

fwman
sed -i 's/^linux-firmware  wifi/linux-firmware  gpu/' \
    "${FX}/distro/firmware/fw-manifest.txt"
out="$(run)"
check "固件清单无 wifi 类判拒绝" 1 "$?" "${out}" \
    "固件清单未覆盖 wifi 器件"

conf
sed -i '/^dns=default$/d' "${FX}/distro/network/99-airymax.conf"
out="$(run)"
check "策略缺键判拒绝" 1 "$?" "${out}" "源策略缺少键 dns=default"

conf
printf 'no-auto-default=*\n' >>"${FX}/distro/network/99-airymax.conf"
out="$(run)"
check "策略含服务器语义判拒绝" 1 "$?" "${out}" "源策略含服务器语义"

spec
sed -i '/^Source37:/d' "${FX}/distro/specs/airymaxos-release.spec"
out="$(run)"
check "spec 未声明 Source 判拒绝" 1 "$?" "${out}" \
    "spec 未声明 Source 99-airymax.conf"

w specs/airymaxos-release.spec <<'EOF'
Source37: 99-airymax.conf

%files
%config(noreplace) %{_sysconfdir}/NetworkManager/conf.d/99-airymax.conf
EOF
out="$(run)"
check "spec 未安装该文件判拒绝" 1 "$?" "${out}" "未同时安装与打包"

w specs/airymaxos-release.spec <<'EOF'
Source37: 99-airymax.conf

%files
%{_sysconfdir}/NetworkManager/conf.d/99-airymax.conf
%{_sysconfdir}/NetworkManager/conf.d/99-airymax.conf
EOF
out="$(run)"
check "spec 未按 noreplace 打包判拒绝" 1 "$?" "${out}" \
    "未按 %config(noreplace) 打包"

ks
sed -i '/^network /d' "${FX}/distro/kickstart/ws.ks"
out="$(run)"
check "kickstart 未声明 DHCP 判拒绝" 1 "$?" "${out}" \
    "ws.ks 未声明 DHCP 引导网络"

ks
printf 'network --bootproto=dhcp --activate\n' >"${FX}/distro/kickstart/ws.ks"
out="$(run)"
check "kickstart 未启用 NetworkManager 判拒绝" 1 "$?" "${out}" \
    "ws.ks 未启用 NetworkManager"

mkfix
mkdir -p "${tmp}/root/etc/NetworkManager/conf.d" "${tmp}/root/usr/bin" \
    "${tmp}/root/usr/lib/systemd/system"
cp "${FX}/distro/network/99-airymax.conf" \
    "${tmp}/root/etc/NetworkManager/conf.d/99-airymax.conf"
printf '#!/bin/sh\n' >"${tmp}/root/usr/bin/nmcli"
chmod +x "${tmp}/root/usr/bin/nmcli"
printf '[Unit]\n' >"${tmp}/root/usr/lib/systemd/system/NetworkManager.service"
out="$(run --root "${tmp}/root")"
check "镜像内资产齐备判通过" 0 "$?" "${out}" "网络子系统判定通过"

chmod -x "${tmp}/root/usr/bin/nmcli"
out="$(run --root "${tmp}/root")"
check "镜像内缺 nmcli 判拒绝" 1 "$?" "${out}" "镜像内缺 nmcli"
chmod +x "${tmp}/root/usr/bin/nmcli"

printf '[main]\n' >"${tmp}/root/etc/NetworkManager/conf.d/00-server.conf"
out="$(run --root "${tmp}/root")"
check "镜像内残留服务器策略判拒绝" 1 "$?" "${out}" \
    "镜像内残留服务器语义策略"
rm -f "${tmp}/root/etc/NetworkManager/conf.d/00-server.conf"

mv "${tmp}/root/usr/lib/systemd/system/NetworkManager.service" \
    "${tmp}/root/usr/lib/systemd/system/nm.service"
out="$(run --root "${tmp}/root")"
check "镜像内缺服务单元判拒绝" 1 "$?" "${out}" \
    "镜像内缺 NetworkManager.service"
mv "${tmp}/root/usr/lib/systemd/system/nm.service" \
    "${tmp}/root/usr/lib/systemd/system/NetworkManager.service"

out="$(run --root "${tmp}/absent")"
check "root 目录不存在按参数错误" 5 "$?" "${out}" "目录不存在"

mkdir -p "${tmp}/stub"
obs dhcp
cat >"${tmp}/stub/ping" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "${tmp}/stub/ping"

out="$(live "${tmp}/stub" --live --wait 5)"
check "联机成立判通过" 0 "$?" "${out}" "联机成立: eth0=192.168.3.57"

obs static
out="$(live "${tmp}/stub" --live --wait 0)"
check "非 DHCP 租约判失败" 1 "$?" "${out}" "地址非 DHCP 租约"

obs dhcp
printf '#!/bin/sh\nexit 1\n' >"${tmp}/stub/ping"
chmod +x "${tmp}/stub/ping"
out="$(live "${tmp}/stub" --live --wait 0)"
check "网关不可达判失败" 1 "$?" "${out}" "网关不可达: 192.168.3.1"

# 前置命令只校验存在性，因此在受控 PATH 里逐个藏起来即可覆盖两个分支；
# 其余工具软链进同一目录，让门禁本身还能启动。
lnk()
{
    local d="$1" t

    shift
    mkdir -p "${d}"
    for t in "$@"; do
        ln -sf "$(command -v "${t}")" "${d}/${t}"
    done
}

# 前缀赋值同时作用于命令名查找，解释器本身必须给绝对路径，否则在受控
# PATH 里连 bash 都找不到。
lnk "${tmp}/nopin" dirname basename mktemp rm
printf '#!/bin/sh\n' >"${tmp}/nopin/airy-observe"
chmod +x "${tmp}/nopin/airy-observe"
out="$(PATH="${tmp}/nopin" "${BASH}" "${FX}/ci/gates/net-online.sh" --live 2>&1)"
check "缺 ping 按参数错误" 5 "$?" "${out}" "缺少命令: ping"

lnk "${tmp}/noobs" dirname basename mktemp rm
printf '#!/bin/sh\nexit 0\n' >"${tmp}/noobs/ping"
chmod +x "${tmp}/noobs/ping"
out="$(PATH="${tmp}/noobs" "${BASH}" "${FX}/ci/gates/net-online.sh" --live 2>&1)"
check "缺 airy-observe 按参数错误" 5 "$?" "${out}" \
    "缺少命令: airy-observe"

out="$(run --wait abc)"
check "等待时长非数字按参数错误" 5 "$?" "${out}" "--wait 需要非负整数"

out="$(run --bogus)"
check "未知参数按参数错误" 5 "$?" "${out}" "未知参数"

out="$(run --help)"
check "帮助以 0 退出" 0 "$?" "${out}" "用法"

exit "${fails}"
