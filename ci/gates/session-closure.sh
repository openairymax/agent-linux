#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/session-closure.sh — 登录/锁屏/显示三段闭包门禁
#
# 用法:
#   ci/gates/session-closure.sh [--source DIR] [--root DIR]
#
# 背景：0.0.3 的 285H 验收里三处缺陷都属于「文件在盘上，但链路没闭合」：
#   1. 锁屏策略只交付 /etc/dconf/db/local.d 源文件，缺 /etc/dconf/profile/user
#      的 system-db:local 与编译产物，策略从未进入会话；
#   2. ukui-screensaver-checkpass 为 0755，PAM account 阶段读不到 shadow，
#      日志为 pam_unix(ukui-screensaver-qt:account): setuid failed；
#   3. gfxmode 从 1920x1080 起选且无 gfxpayload，面板原生 2880x1800 从未启用。
# 三者都不是单点写错，而是「交付物之间的引用关系」缺断言，因此单独成门禁。
#
# 两种模式可同时给：
#   --source DIR  仓库静态闭包：配方与 spec 里必须存在修复条款（默认仓库根）；
#   --root DIR    已安装树闭包：dconf 生效三件套、sudoers、座位、PAM 顺序、
#                 checkpass 模式、显示策略上限，全部按落盘事实判定。
#
# 退出码: 0 通过；1 校验失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

SOURCE=""
ROOT=""
FAILS=0

usage()
{
    cat <<'EOF'
用法: ci/gates/session-closure.sh [--source DIR] [--root DIR]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) SOURCE="${2:-}"; shift 2 ;;
        --root)   ROOT="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${SOURCE}" ] || [ -n "${ROOT}" ] || {
    err "至少给出 --source 或 --root"
    usage >&2
    exit "${EINVAL}"
}

bad() { err "$*"; FAILS=$((FAILS + 1)); }

want_line() { # want_line <文件> <逐字行> <判据名>
    [ -f "$1" ] || { bad "$3: 缺少 $1"; return 0; }
    grep -qxF -- "$2" "$1" && return 0
    bad "$3: $1 缺行「$2」"
}

want_text() { # want_text <文件> <固定串> <判据名>
    [ -f "$1" ] || { bad "$3: 缺少 $1"; return 0; }
    grep -qF -- "$2" "$1" && return 0
    bad "$3: $1 不含「$2」"
}

# ---------------------------------------------------------------------------
# 仓库静态闭包
# ---------------------------------------------------------------------------
check_source()
{
    local d="$1"
    local ks="${d}/distro/kickstart/airymaxos-workstation.ks"
    local spec="${d}/distro/specs/airymaxos-release.spec"
    local mode="${d}/distro/desktop/display_mode.sh"
    local frag="${d}/distro/desktop/01_airymax_display"
    local saver="${d}/distro/login/00-airymax-screensaver"

    say "静态闭包 ${d}"

    # 锁屏三段：策略源、profile 引用、编译与 setuid 收口都必须写在配方里。
    # 只写策略源是 0.0.3 的形态，也是「锁屏照常弹出」的直接原因。
    want_text "${ks}" 'system-db:local' "kickstart/screensaver-profile"
    want_text "${ks}" 'dconf update' "kickstart/screensaver-compile"
    want_text "${ks}" 'chmod 4755' "kickstart/screensaver-setuid"
    want_text "${ks}" 'pam_succeed_if.so user ingroup nopasswdlogin' \
        "kickstart/screensaver-pam"

    # 引导期分辨率：gfxmode 与 gfxpayload 成对，二者缺一都退到低分辨率。
    want_text "${ks}" 'GRUB_GFXPAYLOAD_LINUX=keep' "kickstart/gfxpayload"
    want_text "${ks}" 'set gfxpayload=keep' "kickstart/gfxpayload-cfg"
    want_text "${frag}" 'set gfxpayload=keep' "grub-fragment/gfxpayload"
    want_text "${frag}" '2880x1800,2560x1600,2240x1400' "grub-fragment/gfxmode"
    want_text "${spec}" 'set gfxpayload=keep' "spec-check/gfxpayload"
    want_text "${spec}" '2880x1800,2560x1600,2240x1400' "spec-check/gfxmode"

    # 显示策略上限必须覆盖 285H 原生面板，否则 xrandr 收敛仍会压到 1080p。
    want_text "${mode}" 'CAP_W=2880' "display-mode/cap-w"
    want_text "${mode}" 'CAP_H=1800' "display-mode/cap-h"
    want_text "${mode}" '2880x1800' "display-mode/ladder"

    # 策略源本身与 spec 的键断言：任一被改回 true 都只在装机现场可见。
    want_line "${saver}" 'lock-enabled=false' "screensaver-policy"
    want_line "${saver}" 'idle-lock-enabled=false' "screensaver-policy"
    want_text "${spec}" 'lock-enabled=false' "spec-check/screensaver"
}

# ---------------------------------------------------------------------------
# 已安装树闭包
# ---------------------------------------------------------------------------
check_root()
{
    local r="$1"
    local saver="${r}/etc/dconf/db/local.d/00-airymax-screensaver"
    local profile="${r}/etc/dconf/profile/user"
    local db="${r}/etc/dconf/db/local"
    local sudoers="${r}/etc/sudoers.d/90-airymax"
    local seat="${r}/etc/lightdm/lightdm.conf.d/50-airymax.conf"
    local checkpass
    local pam found

    say "已安装树闭包 ${r}"

    # 1) dconf 生效三件套：源 → profile 引用 → 编译产物。
    want_line "${saver}" 'lock-enabled=false' "root/screensaver-policy"
    want_text "${profile}" 'system-db:local' "root/screensaver-profile"
    if [ -f "${db}" ]; then
        grep -qa 'lock-enabled' "${db}" ||
            bad "root/screensaver-db: 编译产物不含 lock-enabled"
    else
        bad "root/screensaver-db: 缺少 ${db}（dconf update 未执行）"
    fi

    # 2) 提权策略：文件、模式、唯一有效行。
    if [ -f "${sudoers}" ]; then
        [ "$(stat -c %a "${sudoers}")" = 440 ] ||
            bad "root/sudo-policy-mode: $(stat -c %a "${sudoers}") != 440"
        grep -qxF 'Airymax ALL=(ALL) NOPASSWD: ALL' "${sudoers}" ||
            bad "root/sudo-policy-line: 规则不符"
    else
        bad "root/sudo-policy: 缺少 ${sudoers}"
    fi

    # 3) 座位：自动登录账户取自 lightdm 配置，作为后续账户判据的 SSoT。
    want_text "${seat}" 'autologin-user=' "root/lightdm-autologin"

    # 4) 锁屏鉴权：setuid 位与 PAM 首行直通。二进制不存在时跳过（桌面版本
    #    差异），但存在就必须是 4755——否则 account 阶段必然 EPERM。
    for checkpass in "${r}/usr/bin/ukui-screensaver-checkpass" \
        "${r}/usr/lib/ukui-screensaver/ukui-screensaver-checkpass"; do
        [ -f "${checkpass}" ] || continue
        [ "$(stat -c %a "${checkpass}")" = 4755 ] ||
            bad "root/screensaver-setuid: $(stat -c %a "${checkpass}") != 4755"
        break
    done

    # 5) PAM 栈：succeed_if 必须出现在第一条 pam_unix 之前，否则空口令账户
    #    仍会走到读 shadow 的分支。只对存在的服务文件判定。
    found=0
    for pam in "${r}/etc/pam.d/ukui-screensaver" \
        "${r}/etc/pam.d/ukui-screensaver-qt"; do
        [ -f "${pam}" ] || continue
        found=1
        awk '
            /pam_succeed_if\.so +user +ingroup +nopasswdlogin/ { seen = 1 }
            /pam_unix\.so/ && !seen { exit 1 }
        ' "${pam}" || bad "root/screensaver-pam: ${pam} 的首行直通缺失或顺序错误"
    done
    [ "${found}" -eq 1 ] || note "未发现锁屏 PAM 服务文件，跳过顺序判定"
}

[ -n "${SOURCE}" ] && check_source "${SOURCE}"
[ -n "${ROOT}" ] && check_root "${ROOT}"

if [ "${FAILS}" -ne 0 ]; then
    err "登录/锁屏/显示闭包失败 ${FAILS} 项"
    exit 1
fi

say "登录/锁屏/显示闭包通过"
exit 0
