#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/net-online.sh — 网络使能与联机验收（I-29 / D51 / D57）
#
# 用法: ci/gates/net-online.sh [--root DIR] [--live [--wait SEC]]
#
# 装机验收的第一道门禁，判据只有一条：首启之后 60 秒内至少一路网络拿到
# DHCP 租约并可达网关。网络失败即整机失败，不能降级成警告——后面的
# 桌面、驱动、取证各项都需要能取到包、对得上时间，网络不通时它们的
# 结论都会变成「无法判定」，而不是「不合格」。
#
# 三个观察面，缺一不可：
#   ① 声明面（无参数）：comps 装齐 NetworkManager 与无线后端、且不装
#      NetworkManager-config-server；固件清单覆盖 wifi 器件；桌面策略
#      文件键齐全；spec 同时声明、安装并打包该文件；两个 kickstart 剖面
#      都声明 DHCP 引导，且经 boot-set 清单启用 NetworkManager。
#   ② 实物面（--root）：镜像内策略文件、nmcli、NetworkManager.service
#      都在，且没有残留的服务器策略。包在而内容不在，正是声明面看不
#      出来的形态。
#   ③ 联机面（--live）：在装机后的机器上轮询 airy-observe 的 net 段，
#      要求地址来自 DHCP、有默认网关与 DNS，且网关可 ping 通。
#
# 为什么判 net.src 而不只判「有没有地址」：静态地址与 DHCP 租约在报告上
# 都是一行 IPv4，区别只在续约失败时前者还在、后者消失。装机验收要的是
# 后者能被发现，否则「配了静态地址的故障机」会被判成联机成功。
#
# 退出码: 0 判定通过；1 判定未通过；5 参数或证据不可用。
# ===========================================================================
set -uo pipefail

# 调用 [ 与 grep 的比较只涉及 ASCII，钉死码位序可避免宿主 locale 影响
# 正则字符类（同 fw-closure 的理由）。
export LC_ALL=C

readonly EINVAL=5
readonly NMDIR=/etc/NetworkManager/conf.d
readonly V4RE='([0-9]{1,3}\.){3}[0-9]{1,3}'

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
COMPS="${ROOT}/distro/comps/airymaxos-comps.xml"
MAN="${ROOT}/distro/firmware/fw-manifest.txt"
CONF="${ROOT}/distro/network/99-airymax.conf"
SPEC="${ROOT}/distro/specs/airymaxos-release.spec"
KSDIR="${ROOT}/distro/kickstart"
BOOTSET="${ROOT}/distro/presets/boot-set"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

# 必备包：NetworkManager 本身、Wi-Fi 设备插件与 WPA 后端、监管域数据库、
# nl80211 诊断工具。缺任何一项，无线在装机后都表现为「未发现设备」。
WANT_PKGS="NetworkManager NetworkManager-wifi wpa_supplicant
wireless-regdb iw"
# 服务器语义：no-auto-default=* 关掉有线自动默认连接，ignore-carrier=* 关掉
# 载波检测。两者与笔记本「插线即 DHCP」直接冲突，不允许随镜像进来。
BAN_PKG=NetworkManager-config-server
BAN_KEYS="no-auto-default ignore-carrier"
CONF_KEYS="[main] dns=default rc-manager=symlink [connectivity] enabled=false"

SYSROOT=
MODE=static
WAIT=60
bad=0
WHY=

usage()
{
    printf '用法: ci/gates/net-online.sh [--root DIR] [--live [--wait SEC]]\n'
}

# field TEXT KEY —— 从 airy-observe 的输出里取一个键的值（首条命中）。
field()
{
    sed -n "s/^AIRYMAX-OBSERVE $2=//p" <<<"$1" | head -n 1
}

is_v4()
{
    [[ $1 =~ ^${V4RE}$ ]]
}

# addr_ok LIST —— net.ipv4 形如 eth0=10.0.0.5,wlp1s0=10.0.0.6，任一项是
# 合法 IPv4 即可；接口名由采集侧给出，这里只关心地址本身。
addr_ok()
{
    local a
    while IFS= read -r a; do
        is_v4 "${a##*=}" && return 0
    done <<<"$(printf '%s' "$1" | tr ',' '\n')"
    return 1
}

pkg_check()
{
    local p

    grep -o '<packagereq[^>]*>[^<]*' "${COMPS}" | sed 's/.*>//' >"${TMP}/pkgs"
    for p in ${WANT_PKGS}; do
        grep -qxF "${p}" "${TMP}/pkgs" || {
            err "comps 未安装 ${p}"
            bad=1
        }
    done
    if grep -qxF "${BAN_PKG}" "${TMP}/pkgs"; then
        err "comps 装入了 ${BAN_PKG}（服务器语义关闭有线自动默认连接）"
        bad=1
    fi
}

# 固件清单必须有一条 wifi 类别：清单与 comps 的双向对账由 fw-closure 负责，
# 这里只问「无线器件有没有被覆盖」——linux-firmware 装了却只声明 GPU，
# 那份对账同样是绿的，而无线会静默失效。
fw_check()
{
    if ! awk '$1 !~ /^#/ && $2 == "wifi" { n = 1 }
        END { exit(n ? 0 : 1) }' "${MAN}"; then
        err "固件清单未覆盖 wifi 器件: ${MAN}"
        bad=1
    fi
}

# conf_check FILE LABEL —— 源文件与镜像内文件走同一条判定，避免两处各写
# 一份键清单后各自漂移。
conf_check()
{
    local f="$1" label="$2" k

    if [ ! -r "${f}" ]; then
        err "${label}缺失: ${f}"
        bad=1
        return 0
    fi
    for k in ${CONF_KEYS}; do
        grep -qxF "${k}" "${f}" || {
            err "${label}缺少键 ${k}: ${f}"
            bad=1
        }
    done
    for k in ${BAN_KEYS}; do
        if grep -qE "^[[:space:]]*${k}[[:space:]]*=" "${f}"; then
            err "${label}含服务器语义 ${k}: ${f}"
            bad=1
        fi
    done
}

# spec_check —— 一处声明、一处安装、一处打包：只有三处同时存在，文件才会
# 既进 RPM 又在升级时保留管理员改动。
spec_check()
{
    local n

    grep -qE '^Source[0-9]+:[[:space:]]+99-airymax\.conf[[:space:]]*$' \
        "${SPEC}" || {
        err "spec 未声明 Source 99-airymax.conf"
        bad=1
    }
    n=$(grep -cF '%{_sysconfdir}/NetworkManager/conf.d/99-airymax.conf' \
        "${SPEC}")
    [ "${n}" -ge 2 ] || {
        err "spec 未同时安装与打包 99-airymax.conf（命中 ${n} 处）"
        bad=1
    }
    grep -qF '%config(noreplace) %{_sysconfdir}/NetworkManager/conf.d/99-airymax.conf' \
        "${SPEC}" || {
        err "99-airymax.conf 未按 %config(noreplace) 打包"
        bad=1
    }
}

# 单元启用只有一条实现（SSoT）：kickstart 调 boot_set.sh --root=/ 离线落位，
# 清单是 distro/presets/boot-set，与升级路径的 %posttrans 共用同一份。判据
# 因此问的是这条链是否完整——只管 kickstart 里逐项 enable 的写法，就是又抄
# 了一份清单，清单搬走之后它只会稳定地报假警。
nm_on()
{
    grep -qF 'boot_set.sh' "$1" &&
        grep -qF -- '--root=/' "$1" &&
        grep -qxF 'NetworkManager.service' "${BOOTSET}"
}

ks_check()
{
    local f

    for f in "${KSDIR}"/*.ks; do
        grep -qE '^network .*--bootproto=dhcp' "${f}" || {
            err "$(basename "${f}") 未声明 DHCP 引导网络"
            bad=1
        }
        nm_on "${f}" || {
            err "$(basename "${f}") 未启用 NetworkManager"
            bad=1
        }
    done
}

root_check()
{
    conf_check "${SYSROOT}${NMDIR}/99-airymax.conf" "镜像内网络策略"
    if compgen -G "${SYSROOT}${NMDIR}/*-server.conf" >/dev/null; then
        err "镜像内残留服务器语义策略: ${NMDIR}"
        bad=1
    fi
    if [ ! -x "${SYSROOT}/usr/bin/nmcli" ]; then
        err "镜像内缺 nmcli: /usr/bin/nmcli"
        bad=1
    fi
    if [ ! -r "${SYSROOT}/usr/lib/systemd/system/NetworkManager.service" ]; then
        err "镜像内缺 NetworkManager.service"
        bad=1
    fi
}

# probe —— 一次联机判定；失败时把原因留在 WHY，由超时处一次性报出，
# 避免轮询期间每秒刷一行。
probe()
{
    local out addr src gw dns

    WHY=
    if ! out=$(airy-observe --section net 2>/dev/null); then
        WHY="airy-observe 无法执行"
        return 1
    fi
    addr=$(field "${out}" net.ipv4)
    src=$(field "${out}" net.src)
    gw=$(field "${out}" net.gw)
    dns=$(field "${out}" net.dns)
    if ! addr_ok "${addr}"; then
        WHY="无 IPv4 地址（net.ipv4=${addr:-absent}）"
        return 1
    fi
    if [ "${src}" != dhcp ]; then
        WHY="地址非 DHCP 租约（net.src=${src:-absent}）"
        return 1
    fi
    if ! is_v4 "${gw}"; then
        WHY="无默认网关（net.gw=${gw:-absent}）"
        return 1
    fi
    case "${dns}" in
    ''|none|absent)
        WHY="未取得 DNS（net.dns=${dns:-absent}）"
        return 1
        ;;
    esac
    if ! ping -c 1 -W 1 "${gw}" >/dev/null 2>&1; then
        WHY="网关不可达: ${gw}"
        return 1
    fi
    say "联机成立: ${addr} src=${src} gw=${gw} dns=${dns}"
    return 0
}

live_wait()
{
    local end reason

    end=$((SECONDS + WAIT))
    while :; do
        probe && return 0
        reason=${WHY}
        [ "${SECONDS}" -ge "${end}" ] && break
        sleep 2
    done
    err "联机验收超时（${WAIT}s）: ${reason}"
    return 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --root)
            [ -n "${2:-}" ] || { err "--root 需要目录路径"; exit "${EINVAL}"; }
            SYSROOT="$2"
            shift 2
            ;;
        --live)
            MODE=live
            shift
            ;;
        --wait)
            case "${2:-}" in
                ''|*[!0-9]*) err "--wait 需要非负整数"; exit "${EINVAL}" ;;
            esac
            WAIT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            err "未知参数: $1"
            exit "${EINVAL}"
            ;;
    esac
done

if [ "${MODE}" = live ]; then
    for c in airy-observe ping; do
        command -v "${c}" >/dev/null 2>&1 || {
            err "缺少命令: ${c}"
            exit "${EINVAL}"
        }
    done
    group "联机验收"
    live_wait || exit 1
    endgroup
    exit 0
fi

for f in "${COMPS}" "${MAN}" "${CONF}" "${SPEC}"; do
    [ -r "${f}" ] || { err "不可读: ${f}"; exit "${EINVAL}"; }
done
if [ -n "${SYSROOT}" ] && [ ! -d "${SYSROOT}" ]; then
    err "--root 目录不存在: ${SYSROOT}"
    exit "${EINVAL}"
fi

group "网络子系统静态判定"
pkg_check
fw_check
conf_check "${CONF}" "源策略"
spec_check
ks_check
[ -z "${SYSROOT}" ] || root_check
if [ "${bad}" -ne 0 ]; then
    die "网络子系统判定未通过"
fi
say "网络子系统判定通过"
endgroup
