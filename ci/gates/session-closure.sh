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
# 候选串是 GRUB 与 spec 的共同事实源：降序并集之后必须以 auto 兜底。
# 两侧只比前缀的话，改动末位兜底不会被发现，故按整串逐字比对。
readonly GFXMODE='2880x1800,2560x1600,2240x1400,1920x1200,1920x1080,1680x1050,1600x900,1280x1024,1024x768,auto'
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

need_dir()
{
    [ -n "$2" ] || { err "$1 需要目录路径"; exit "${EINVAL}"; }
    [ -d "$2" ] || { err "$1 目录不存在: $2"; exit "${EINVAL}"; }
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) need_dir "$1" "${2:-}"; SOURCE="$2"; shift 2 ;;
        --root)   need_dir "$1" "${2:-}"; ROOT="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
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

want_absent() { # want_absent <文件> <扩展正则> <判据名>
    [ -f "$1" ] || { bad "$3: 缺少 $1"; return 0; }
    grep -qE -- "$2" "$1" || return 0
    bad "$3: $1 含禁止的「$2」"
}

# console_count FILE WANT NAME —— bootloader 追加行的 console= 项数。
#
# 控制台集合按架构展开后就是定值；项数漂移只有两种来路：把别的架构的
# 控制台抄进来，或有人追加了第三条。两者都会让「最后注册者路由
# /dev/console」不可读，因此在静态闭包阶段就按计数断言。
console_count() { # console_count <文件> <期望项数> <判据名>
    local line n
    line=$(sed -n 's/^bootloader .*--append="\([^"]*\)".*/\1/p' "$1" | head -n 1)
    [ -n "${line}" ] || { bad "$3: $1 无 bootloader --append"; return 0; }
    n=$(printf '%s\n' "${line}" | tr ' ' '\n' | grep -c '^console=')
    [ "${n}" -eq "$2" ] || bad "$3: console= 计数 ${n} != $2"
}

# ---------------------------------------------------------------------------
# 仓库静态闭包
# ---------------------------------------------------------------------------
check_source()
{
    local d="$1"
    local ks="${d}/distro/kickstart/airymaxos-workstation.ks"
    local min="${d}/distro/kickstart/airymaxos-minimal.ks"
    local spec="${d}/distro/specs/airymaxos-release.spec"
    local mode="${d}/distro/desktop/display_mode.sh"
    local frag="${d}/distro/desktop/01_airymax_display"
    local brand="${d}/distro/boot/efi_brand.sh"
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
    want_text "${frag}" "${GFXMODE}" "grub-fragment/gfxmode"
    want_text "${spec}" 'set gfxpayload=keep' "spec-check/gfxpayload"
    want_text "${spec}" "${GFXMODE}" "spec-check/gfxmode"

    # 控制台集合按架构收敛（D-05）：本产品配方只产 x86_64，命令行就是
    # tty0 + ttyS0。0.0.3 把 ARM64 的 ttyAMA0 抄进来，无该设备的机器上
    # console= 会注册成幽灵项，systemd-vconsole-setup 随之失败；三项
    # console= 也让「最后一项路由 /dev/console」的语义不可读。
    #
    # 判据落在 console= 赋值上，不落在设备名本身：包内 changelog 与注释
    # 需要能如实写明「此处故意不写 ttyAMA0」，禁设备名会把解释也判死，
    # 逼人删掉唯一说明为什么这么做的证据。
    want_absent "${ks}" 'console=ttyAMA0' "kickstart/console-arch"
    want_absent "${min}" 'console=ttyAMA0' "kickstart-min/console-arch"
    want_absent "${spec}" 'console=ttyAMA0' "spec-check/console-arch"
    console_count "${ks}" 2 "kickstart/console-count"
    console_count "${min}" 2 "kickstart-min/console-count"

    # 活动配置副本（D-07）：NVRAM 走 EFI/airymaxos，mkconfig 只写
    # EFI/openEuler，不同步品牌副本则改了完全不生效。判据落在「谁把
    # grub.cfg 复制进品牌目录」，不钉复制函数的具体写法。
    want_text "${ks}" 'efi_brand.sh' "kickstart/brand-copy"
    want_text "${brand}" 'EFI/airymaxos' "brand-copy/target"
    want_text "${brand}" 'grub.cfg' "brand-copy/payload"

    # 显示策略上限必须由 EDID 推导，不得回落成机型绑定值（G-02）。
    # CAP_W=/CAP_H= 是 0.0.3 按 285H 定上限的形态，一旦重新出现，别的面板
    # 都会被这个常数压掉；EDID 判定与阶梯是替代它的两条事实来源。
    want_absent "${mode}" 'CAP_[WH]=' "display-mode/no-cap"
    want_text "${mode}" 'mm x [0-9]+mm' "display-mode/edid-probe"
    want_text "${mode}" 'LADDER="2880x1800 2560x1600 2240x1400' \
        "display-mode/ladder"

    # 策略源本身与 spec 的键断言：任一被改回 true 都只在装机现场可见。
    want_line "${saver}" 'idle-activation-enabled=false' "screensaver-policy"
    want_line "${saver}" 'lock-enabled=false' "screensaver-policy"
    want_line "${saver}" 'idle-lock-enabled=false' "screensaver-policy"
    want_line "${saver}" 'sleep-activation-enabled=false' "screensaver-policy"
    want_text "${spec}" 'idle-activation-enabled=false' \
        "spec-check/screensaver-idle"
    want_text "${spec}" 'lock-enabled=false' "spec-check/screensaver"
    want_text "${spec}" 'sleep-activation-enabled=false' \
        "spec-check/screensaver-sleep"

    # 展示名一处定义、多处派生：/etc/issue 首行、引导菜单、system-release
    # 全部取自 PRETTY_NAME，改版只改 os-release 时其余落点必须跟着走。
    brand_sso "${d}"

    # 主机名必须是合法 RFC 1123 标签：0.0.3 的下划线名被 systemd 剥成
    # airymaxos，只能靠 sysctl.d 在内核层写回；0.0.4 由安装器直接落盘，
    # 回写链整体删除。
    host_sso "${d}"

    # 账户名一处定义、多处派生：0.0.3 的现场缺陷正是三处资产账户名不一致，
    # 桌面永远等不到自动登录的用户。
    acct_sso "${d}"
}

# brand_sso DIR —— 命名 SSoT 静态断言。
#
# PRETTY_NAME 是机器链的唯一展示串，形状固定为「ASCII 短名 + 空格 +
# VERSION_ID」；NAME 可以含中文，但不能进入任何 ASCII-only 通道。派生点
# 缺失或有人把完整展示串抄进配方，都说明出现了第二个事实源——那正是
# 「展示名与账户名不一致」这类现场缺陷的来源。
brand_sso()
{
    local d="$1"
    local osrel="${d}/distro/release/os-release"
    local spec="${d}/distro/specs/airymaxos-release.spec"
    local iso="${d}/distro/scripts/build-iso.sh"
    local pretty vid dist hits name

    [ -f "${osrel}" ] || { bad "brand/os-release: 缺少 ${osrel}"; return 0; }
    pretty=$(sed -n 's/^PRETTY_NAME="\(.*\)"$/\1/p' "${osrel}")
    vid=$(sed -n 's/^VERSION_ID="\(.*\)"$/\1/p' "${osrel}")
    name=$(sed -n 's/^NAME="\(.*\)"$/\1/p' "${osrel}")
    [ -n "${pretty}" ] || { bad "brand/os-release: 无 PRETTY_NAME"; return 0; }
    [ -n "${vid}" ] || { bad "brand/os-release: 无 VERSION_ID"; return 0; }

    dist="${pretty% *}"
    # NAME 走的是与控制台同一条非 UTF-8 通道：openEuler 的
    # /etc/grub.d/10_linux 把 NAME 原样写进引导菜单标题，而 GRUB 只加载
    # 无 CJK 字形的 unicode.pf2。故 NAME 必须等于 ASCII 短名本身，不允许
    # 再挂人读后缀——285H 实测「开源极境」四字在菜单上整段不显示。
    if [ "${name}" != "${dist}" ]; then
        bad "brand/name: NAME「${name:-absent}」不等于短名「${dist}」"
        return 0
    fi
    if [ "${pretty}" != "${dist} ${vid}" ]; then
        bad "brand/pretty-shape: PRETTY_NAME「${pretty}」不等于「${dist} ${vid}」"
        return 0
    fi
    printf '%s' "${dist}" | LC_ALL=C grep -qxE '[ -~]+' ||
        bad "brand/ascii: 短名「${dist}」含非 ASCII 或控制字符"

    want_text "${iso}" 'PRETTY_NAME' "brand/build-iso"
    want_text "${spec}" 'test "${airy_pretty}" = "${airy_dist} ${airy_vid}"' \
        "brand/spec-invariant"

    hits=$(grep -rIlF -- "${pretty}" "${d}/distro/scripts" \
        "${d}/distro/specs" "${d}/distro/kickstart" \
        "${d}/distro/desktop" "${d}/distro/ci" 2>/dev/null)
    [ -z "${hits}" ] || bad "brand/dup-literal: 展示串被写死在 ${hits}"
}

# host_sso DIR —— 主机名静态断言。
#
# 两个剖面的主机名都由安装器的 network --hostname 落盘，%post 只读回校验。
# 0.0.3 的 AIRYMAX_PC 含下划线，systemd 读取时按 RFC 1123 删除，配方于是
# 手工写 /etc/hostname 再用 sysctl.d 在内核层写回，静态名与运行名常年分叉。
# 判据只看可执行条款：注释与 changelog 里的历史叙述不算残留。
host_sso()
{
    local d="$1"
    local ws="${d}/distro/kickstart/airymaxos-workstation.ks"
    local min="${d}/distro/kickstart/airymaxos-minimal.ks"
    local spec="${d}/distro/specs/airymaxos-release.spec"

    want_text "${ws}" '--hostname=airymax-pc' "host/kickstart-ws"
    want_text "${min}" '--hostname=airymax-pc' "host/kickstart-min"
    want_text "${ws}" '[ "$(cat /etc/hostname)" = airymax-pc ]' \
        "host/kickstart-readback"

    want_absent "${ws}" '--hostname=AIRYMAX_PC' "host/ws-underscore"
    want_absent "${min}" '--hostname=AIRYMAX_PC' "host/min-underscore"
    want_absent "${spec}" '^%config.*99-airymax-hostname' "host/spec-files"
    want_absent "${spec}" 'install .*99-airymax-hostname' "host/spec-install"

    [ ! -e "${d}/distro/boot/99-airymax-hostname.conf" ] ||
        bad "host/sysctl-dropin: 内核层回写文件不应存在"
}

# acct_sso DIR —— 交付账户名一处定义、多处派生。
#
# 账户名只在工作站配方的 user --name= 定义；最小剖面、座位、sudo 唯一规则、
# spec %check 与配方 %post 全部同值。0.0.3 的现场缺陷正是三处资产账户名
# 不一致，桌面永远等不到自动登录的用户，而这一点在装机现场只表现为
# 「登录界面停住」。名必须是小写：大写名依赖各基底 NAME_REGEX 与大小写
# 敏感文件系统的策略，家目录可能因此与账户自身分叉。
acct_sso()
{
    local d="$1"
    local ws="${d}/distro/kickstart/airymaxos-workstation.ks"
    local min="${d}/distro/kickstart/airymaxos-minimal.ks"
    local seat="${d}/distro/desktop/lightdm/50-airymax.conf"
    local sudoers="${d}/distro/login/90-airymax-sudoers"
    local spec="${d}/distro/specs/airymaxos-release.spec"
    local name

    [ -f "${ws}" ] || { bad "acct/kickstart-ws: 缺少 ${ws}"; return 0; }
    name=$(sed -n 's/^user --name=\([^ ]*\).*/\1/p' "${ws}")
    [ -n "${name}" ] || {
        bad "acct/kickstart-name: ${ws} 无 user --name= 事实源"
        return 0
    }
    printf '%s' "${name}" | LC_ALL=C grep -qxE '[a-z][a-z0-9]+' || {
        bad "acct/name-lower: 账户名「${name}」不是小写标签"
        return 0
    }

    want_text "${min}" "user --name=${name} " "acct/kickstart-min"
    want_text "${ws}" "passwd -d ${name}" "acct/kickstart-unlock"
    want_text "${ws}" "usermod -aG nopasswdlogin ${name}" "acct/kickstart-group"
    want_line "${seat}" "autologin-user=${name}" "acct/seat"
    want_line "${sudoers}" "${name} ALL=(ALL) NOPASSWD: ALL" "acct/sudoers"
    want_text "${spec}" "session.autologin=${name}" "acct/spec-autologin"
    want_text "${spec}" "autologin-user=${name}" "acct/spec-seat"
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
    local pam found acct

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

    # 2) 两份 grub.cfg 的模式行必须逐字一致：品牌副本才是 NVRAM 实际读取的
    #    那一份，只更新 openEuler 副本时本机分辨率毫无变化（D-07）。
    grub_pair "${r}"

    # 3) 提权策略：账户名从座位取自 SSoT，改名不让门禁失效；有效行必须唯一，
    #    多出的规则同样属于「源在盘上但语义被放大」。
    acct=$(seat_user "${seat}")
    if [ -n "${acct}" ]; then
        if [ -f "${sudoers}" ]; then
            [ "$(stat -c %a "${sudoers}")" = 440 ] ||
                bad "root/sudo-policy-mode: $(stat -c %a "${sudoers}") != 440"
            [ "$(awk 'NF && $1 !~ /^#/' "${sudoers}" | wc -l)" -eq 1 ] ||
                bad "root/sudo-policy-lines: ${sudoers} 有效行不唯一"
            grep -qxF "${acct} ALL=(ALL) NOPASSWD: ALL" "${sudoers}" ||
                bad "root/sudo-policy-line: 规则与账户 ${acct} 不符"
        else
            bad "root/sudo-policy: 缺少 ${sudoers}"
        fi
    fi

    # 4) 座位：自动登录账户必须存在，它同时是上面 sudoers 判据的账户来源。
    want_text "${seat}" 'autologin-user=' "root/lightdm-autologin"

    # 5) 锁屏鉴权：setuid 位与 PAM 首行直通。二进制不存在时跳过（桌面版本
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

# seat_user FILE —— 从 lightdm 座位读自动登录账户。账户名只在这一处取值，
# 命名调整（Airymax→amuser）不需要改门禁。
seat_user()
{
    [ -f "$1" ] || return 0
    sed -n 's/^[[:space:]]*autologin-user[[:space:]]*=[[:space:]]*//p' "$1" |
        head -n 1 | tr -d '[:space:]'
}

# grub_set FILE KEY —— 取一份 grub.cfg 里 set KEY=VALUE 的 VALUE（首条）。
grub_set()
{
    awk -v k="$2" '{
        sub(/^[ \t]*set[ \t]+/, "")
        if (index($0, k "=") == 1) {
            sub(/^[^=]*=/, "")
            print
            exit
        }
    }' "$1"
}

# grub_pair ROOT —— 主配置与品牌副本的 gfxmode/gfxpayload 必须逐字一致。
# NVRAM 实际读取品牌副本，只改主配置时重启后分辨率不会变化（D-07）。
grub_pair()
{
    local r="$1" f k a b
    local main="${r}/boot/grub2/grub.cfg"
    local brand="${r}/boot/efi/EFI/airymaxos/grub.cfg"

    for f in "${main}" "${brand}"; do
        [ -f "${f}" ] || { bad "root/grub-pair: 缺少 ${f}"; return 0; }
    done
    for k in gfxmode gfxpayload; do
        a=$(grub_set "${main}" "${k}")
        b=$(grub_set "${brand}" "${k}")
        if [ -z "${a}" ]; then
            bad "root/grub-pair: ${main} 无 set ${k}="
            continue
        fi
        [ "${a}" = "${b}" ] ||
            bad "root/grub-pair: ${k} 不一致（${a} vs ${b}）"
    done
}

[ -n "${SOURCE}" ] && check_source "${SOURCE}"
[ -n "${ROOT}" ] && check_root "${ROOT}"

if [ "${FAILS}" -ne 0 ]; then
    err "登录/锁屏/显示闭包失败 ${FAILS} 项"
    exit 1
fi

say "登录/锁屏/显示闭包通过"
exit 0
