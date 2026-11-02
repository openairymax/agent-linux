#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_session_closure.sh — ci/gates/session-closure.sh 的分支回归
#
# 门禁判的是「交付物之间的引用关系」，真实树常年为绿，因此判定逻辑本身必须
# 用负例驱动：静态面逐条删掉配方里的修复条款，安装树面逐件破坏 dconf 三件套、
# sudoers、座位、PAM 顺序、setuid 位与两份 grub.cfg 的一致性。断言只看退出码
# 与稳定判据名，不看措辞。
#
# 真实树是否合规不在这里判：流水线的 ci/ci.sh session-closure 负责那一条，
# 本文件只保证门禁没有漏判与误判，且账户名确实取自座位而非写死。
#
# 用法: bash ci/tests/test_session_closure.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/session-closure.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" \
    "${FX}/distro/kickstart" "${FX}/distro/specs" "${FX}/distro/desktop" \
    "${FX}/distro/boot" "${FX}/distro/login" "${FX}/distro/release" \
    "${FX}/distro/scripts"
cp "${GATE}" "${FX}/ci/gates/session-closure.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

readonly GFXMODE='2880x1800,2560x1600,2240x1400,1920x1200,1920x1080,1680x1050,1600x900,1280x1024,1024x768,auto'

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
    bash "${FX}/ci/gates/session-closure.sh" "$@" 2>&1
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

# w REL — 用标准输入覆盖源树里的一件资产。
w()
{
    cat >"${FX}/distro/$1"
}

ks()
{
    w kickstart/airymaxos-workstation.ks <<EOF
printf 'user-db:user\\\\nsystem-db:local\\\\n' >"\${profile}"
dconf update
chmod 4755 "\${checkpass}"
printf 'auth\tsufficient\tpam_succeed_if.so user ingroup nopasswdlogin\n' >>"\${pam}"
printf 'GRUB_GFXPAYLOAD_LINUX=keep\n' >>/etc/default/grub
grep -qxF '  set gfxpayload=keep' "\${grub_cfg}"
efi_brand.sh --esp /boot/efi
EOF
}

spec()
{
    w specs/airymaxos-release.spec <<EOF
%check
grep -qxF 'lock-enabled=false' "\${airy_saver}"
printf '%s\n' "\${airy_grub}" | grep -qxF '  set gfxmode=${GFXMODE}'
printf '%s\n' "\${airy_grub}" | grep -qxF '  set gfxpayload=keep'
test "\${airy_pretty}" = "\${airy_dist} \${airy_vid}"
EOF
}

# 命名 SSoT 的两处派生点：构建脚本读 PRETTY_NAME，配方自证同一条不变式。
osrel()
{
    w release/os-release <<'EOF'
NAME="OpenAirymax 开源极境"
VERSION_ID="0.0.4"
PRETTY_NAME="OpenAirymax 0.0.4"
EOF
}

iso()
{
    w scripts/build-iso.sh <<'EOF'
pretty=$(sed -n 's/^PRETTY_NAME="\(.*\)"$/\1/p' "${osrel}")
EOF
}

frag()
{
    w desktop/01_airymax_display <<EOF
cat <<'GRUB'
  insmod gfxterm
  set gfxmode=${GFXMODE}
  set gfxpayload=keep
  terminal_output gfxterm
GRUB
EOF
}

brand()
{
    w boot/efi_brand.sh <<'EOF'
dst=${esp}/EFI/airymaxos
for f in shimx64.efi grubx64.efi grub.cfg grubenv; do
    [ -f "${src}/${f}" ] || continue
    install -m 0644 "${src}/${f}" "${dst}/${f}"
done
EOF
}

mode()
{
    w desktop/display_mode.sh <<'EOF'
LADDER="2880x1800 2560x1600 2240x1400 1920x1200 1920x1080"
if (match($0, /[0-9]+mm x [0-9]+mm/)) {
    edid = 1
}
EOF
}

saver_body()
{
    cat <<'EOF'
[org/ukui/screensaver]
lock-enabled=false
idle-lock-enabled=false
EOF
}

saver()
{
    saver_body >"${FX}/distro/login/00-airymax-screensaver"
}

mkfix()
{
    ks
    spec
    frag
    brand
    mode
    saver
    osrel
    iso
}

ROOTDIR="${tmp}/root"
pamfile()
{
    cat >"${ROOTDIR}/etc/pam.d/ukui-screensaver"
}

mkroot()
{
    rm -rf "${ROOTDIR}"
    mkdir -p "${ROOTDIR}/etc/dconf/db/local.d" \
        "${ROOTDIR}/etc/dconf/profile" "${ROOTDIR}/etc/sudoers.d" \
        "${ROOTDIR}/etc/lightdm/lightdm.conf.d" "${ROOTDIR}/etc/pam.d" \
        "${ROOTDIR}/usr/bin" "${ROOTDIR}/boot/grub2" \
        "${ROOTDIR}/boot/efi/EFI/airymaxos"
    saver_body >"${ROOTDIR}/etc/dconf/db/local.d/00-airymax-screensaver"
    printf 'user-db:user\nsystem-db:local\n' >"${ROOTDIR}/etc/dconf/profile/user"
    printf 'blob\nlock-enabled=true\n' >"${ROOTDIR}/etc/dconf/db/local"
    printf 'amuser ALL=(ALL) NOPASSWD: ALL\n' \
        >"${ROOTDIR}/etc/sudoers.d/90-airymax"
    chmod 440 "${ROOTDIR}/etc/sudoers.d/90-airymax"
    printf '[Seat:*]\nautologin-user=amuser\n' \
        >"${ROOTDIR}/etc/lightdm/lightdm.conf.d/50-airymax.conf"
    printf '#!/bin/sh\n' >"${ROOTDIR}/usr/bin/ukui-screensaver-checkpass"
    chmod 4755 "${ROOTDIR}/usr/bin/ukui-screensaver-checkpass"
    pamfile <<'EOF'
auth    sufficient  pam_succeed_if.so user ingroup nopasswdlogin
auth    required    pam_unix.so
account required    pam_unix.so
EOF
    grubcfgs
}

grubcfgs()
{
    printf '  set gfxmode=%s\n  set gfxpayload=keep\n' "${GFXMODE}" \
        >"${ROOTDIR}/boot/grub2/grub.cfg"
    printf '  set gfxmode=%s\n  set gfxpayload=keep\n' "${GFXMODE}" \
        >"${ROOTDIR}/boot/efi/EFI/airymaxos/grub.cfg"
}

mkfix
mkroot

out="$(run --source "${FX}")"
check "源树合规判定通过" 0 "$?" "${out}" "闭包通过"

out="$(run)"
check "无参数退 5" 5 "$?" "${out}" "至少给出"

out="$(run --source "${tmp}/nope")"
check "源目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

ks
sed -i '/system-db:local/d' "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run --source "${FX}")"
check "kickstart 缺 profile 引用判拒绝" 1 "$?" "${out}" \
    "kickstart/screensaver-profile"

ks
sed -i '/^dconf update$/d' "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run --source "${FX}")"
check "kickstart 缺 dconf update 判拒绝" 1 "$?" "${out}" \
    "kickstart/screensaver-compile"

ks
sed -i '/chmod 4755/d' "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run --source "${FX}")"
check "kickstart 缺 setuid 收口判拒绝" 1 "$?" "${out}" \
    "kickstart/screensaver-setuid"

ks
sed -i '/pam_succeed_if/d' "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run --source "${FX}")"
check "kickstart 缺 PAM 直通判拒绝" 1 "$?" "${out}" \
    "kickstart/screensaver-pam"

ks
sed -i '/GRUB_GFXPAYLOAD_LINUX/d' "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run --source "${FX}")"
check "kickstart 缺 gfxpayload 判拒绝" 1 "$?" "${out}" \
    "kickstart/gfxpayload"

ks
sed -i '/efi_brand.sh/d' "${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run --source "${FX}")"
check "kickstart 缺品牌副本同步判拒绝" 1 "$?" "${out}" \
    "kickstart/brand-copy"

frag
sed -i 's/,auto$//' "${FX}/distro/desktop/01_airymax_display"
out="$(run --source "${FX}")"
check "候选串缺 auto 兜底判拒绝" 1 "$?" "${out}" \
    "grub-fragment/gfxmode"

spec
sed -i '/set gfxmode=/d' "${FX}/distro/specs/airymaxos-release.spec"
out="$(run --source "${FX}")"
check "spec 缺候选串判拒绝" 1 "$?" "${out}" "spec-check/gfxmode"

brand
sed -i '/EFI\/airymaxos/d' "${FX}/distro/boot/efi_brand.sh"
out="$(run --source "${FX}")"
check "品牌副本目标缺失判拒绝" 1 "$?" "${out}" "brand-copy/target"

# G-02：机型上限常数、EDID 判定、阶梯三条各自独立，缺任一条都会让策略
# 退回「按某一台机器定分辨率」的形态。夹具先回到合规态，只破坏一条。
mkfix
printf 'CAP_W=2880\nCAP_H=1800\n' >>"${FX}/distro/desktop/display_mode.sh"
out="$(run --source "${FX}")"
check "机型上限常数判拒绝" 1 "$?" "${out}" "display-mode/no-cap"

mkfix
sed -i '/mm x /d' "${FX}/distro/desktop/display_mode.sh"
out="$(run --source "${FX}")"
check "缺 EDID 判定判拒绝" 1 "$?" "${out}" "display-mode/edid-probe"

mkfix
sed -i 's/^LADDER=.*/LADDER="1920x1080 1280x720"/' \
    "${FX}/distro/desktop/display_mode.sh"
out="$(run --source "${FX}")"
check "阶梯缺高分段判拒绝" 1 "$?" "${out}" "display-mode/ladder"

saver
sed -i 's/^lock-enabled=false$/lock-enabled=true/' \
    "${FX}/distro/login/00-airymax-screensaver"
out="$(run --source "${FX}")"
check "策略源键为 true 判拒绝" 1 "$?" "${out}" "screensaver-policy"

# 命名 SSoT：PRETTY_NAME 形状、ASCII 纯度、派生点存在、无第二事实源。
mkfix
w release/os-release <<'EOF'
NAME="OpenAirymax 开源极境"
VERSION_ID="0.0.4"
PRETTY_NAME="OpenAirymax"
EOF
out="$(run --source "${FX}")"
check "展示名缺版本号判拒绝" 1 "$?" "${out}" "brand/pretty-shape"

mkfix
w release/os-release <<'EOF'
NAME="OpenAirymax 开源极境"
VERSION_ID="0.0.4"
PRETTY_NAME="开源极境 0.0.4"
EOF
out="$(run --source "${FX}")"
check "非 ASCII 展示名判拒绝" 1 "$?" "${out}" "brand/ascii"

mkfix
printf '  menu label Install OpenAirymax 0.0.4\n' \
    >>"${FX}/distro/kickstart/airymaxos-workstation.ks"
out="$(run --source "${FX}")"
check "展示串写死在配方判拒绝" 1 "$?" "${out}" "brand/dup-literal"

mkfix
printf '#!/bin/sh\n' >"${FX}/distro/scripts/build-iso.sh"
out="$(run --source "${FX}")"
check "构建脚本不派生展示名判拒绝" 1 "$?" "${out}" "brand/build-iso"

mkfix
out="$(run --root "${ROOTDIR}")"
check "安装树合规判定通过" 0 "$?" "${out}" "闭包通过"

rm -f "${ROOTDIR}/etc/dconf/profile/user"
out="$(run --root "${ROOTDIR}")"
check "安装树缺 profile 判拒绝" 1 "$?" "${out}" \
    "root/screensaver-profile"

mkroot
printf 'blob\n' >"${ROOTDIR}/etc/dconf/db/local"
out="$(run --root "${ROOTDIR}")"
check "编译产物缺 lock-enabled 判拒绝" 1 "$?" "${out}" \
    "root/screensaver-db"

mkroot
chmod 644 "${ROOTDIR}/etc/sudoers.d/90-airymax"
out="$(run --root "${ROOTDIR}")"
check "sudoers 模式非 0440 判拒绝" 1 "$?" "${out}" \
    "root/sudo-policy-mode"

mkroot
chmod 644 "${ROOTDIR}/etc/sudoers.d/90-airymax"
printf 'other ALL=(ALL) NOPASSWD: ALL\n' \
    >"${ROOTDIR}/etc/sudoers.d/90-airymax"
out="$(run --root "${ROOTDIR}")"
check "sudoers 账户与座位不符判拒绝" 1 "$?" "${out}" \
    "root/sudo-policy-line"

mkroot
chmod 644 "${ROOTDIR}/etc/sudoers.d/90-airymax"
printf 'amuser ALL=(ALL) NOPASSWD: ALL\nroot ALL=(ALL) ALL\n' \
    >"${ROOTDIR}/etc/sudoers.d/90-airymax"
out="$(run --root "${ROOTDIR}")"
check "sudoers 有效行不唯一判拒绝" 1 "$?" "${out}" \
    "root/sudo-policy-lines"

mkroot
pamfile <<'EOF'
auth    required    pam_unix.so
auth    sufficient  pam_succeed_if.so user ingroup nopasswdlogin
EOF
out="$(run --root "${ROOTDIR}")"
check "PAM 直通晚于 pam_unix 判拒绝" 1 "$?" "${out}" \
    "root/screensaver-pam"

mkroot
chmod 755 "${ROOTDIR}/usr/bin/ukui-screensaver-checkpass"
out="$(run --root "${ROOTDIR}")"
check "锁屏二进制非 4755 判拒绝" 1 "$?" "${out}" \
    "root/screensaver-setuid"

mkroot
rm -f "${ROOTDIR}/usr/bin/ukui-screensaver-checkpass"
out="$(run --root "${ROOTDIR}")"
check "二进制缺失跳过并判通过" 0 "$?" "${out}" "闭包通过"

mkroot
printf '  set gfxmode=%s\n  set gfxpayload=keep\n' "${GFXMODE}" \
    >"${ROOTDIR}/boot/grub2/grub.cfg"
printf '  set gfxmode=1920x1080\n  set gfxpayload=keep\n' \
    >"${ROOTDIR}/boot/efi/EFI/airymaxos/grub.cfg"
out="$(run --root "${ROOTDIR}")"
check "两份 grub.cfg 模式行不一致判拒绝" 1 "$?" "${out}" \
    "root/grub-pair"

mkroot
rm -f "${ROOTDIR}/boot/efi/EFI/airymaxos/grub.cfg"
out="$(run --root "${ROOTDIR}")"
check "品牌副本缺失判拒绝" 1 "$?" "${out}" "root/grub-pair"

# 账户 SSoT：改名后门禁必须跟着座位走，而不是继续认旧名字。
mkroot
printf '[Seat:*]\nautologin-user=Airymax\n' \
    >"${ROOTDIR}/etc/lightdm/lightdm.conf.d/50-airymax.conf"
chmod 644 "${ROOTDIR}/etc/sudoers.d/90-airymax"
printf 'Airymax ALL=(ALL) NOPASSWD: ALL\n' \
    >"${ROOTDIR}/etc/sudoers.d/90-airymax"
chmod 440 "${ROOTDIR}/etc/sudoers.d/90-airymax"
out="$(run --root "${ROOTDIR}")"
check "账户名取自座位" 0 "$?" "${out}" "闭包通过"

if [ "${fails}" -ne 0 ]; then
    printf 'test_session_closure: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_session_closure: 全部通过\n'