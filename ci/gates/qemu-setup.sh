#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/qemu-setup.sh — 装机段的宿主依赖
#
# 用法: ci/gates/qemu-setup.sh [--firmware both|bios]
#
# 已在宿主装齐就跳过，不做无条件 apt-get update：离线环境里不希望一次
# 门禁触发整轮源同步。
#
# UEFI 固件不随 qemu 一起就绪：托管 runner 的 qemu 是预装的，ovmf 往往
# 没有，于是这一步很容易在「qemu 已在」的分支里被整段跳过。固件缺失本身
# 不致命——install-test.sh 的 --firmware both 会降级只跑 BIOS 腿——但装机
# 段会因此静默地不再覆盖 UEFI，而 UEFI 正是交付形态（真机 U 盘、isohybrid
# 的 GPT 条目、gummiboot/grub EFI 装载）。一条全绿却从未验证 UEFI 的
# 门禁比失败更危险，所以这里缺固件就补装，补不上直接判负。
#
# 退出码: 0 就绪；1 缺 qemu 或 UEFI 固件且装不上；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/host.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/host.sh"
# shellcheck source=../lib/apt.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/apt.sh"

FIRMWARE="both"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --firmware) FIRMWARE="${2:-}"; shift 2 ;;
        -h|--help) echo "用法: ci/gates/qemu-setup.sh [--firmware both|bios]"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

case "${FIRMWARE}" in both|bios) ;; *)
    err "--firmware 只接受 both|bios"; exit "${EINVAL}" ;; esac

# 固件落点随发行版而变：Debian/Ubuntu 在 /usr/share/OVMF，Fedora 系在
# /usr/share/edk2/ovmf。install-test.sh 探测同一组路径，两处必须一致，
# 否则这里判「已就绪」而装机段仍降级。
fw_present()
{
    [ -e /usr/share/OVMF/OVMF_CODE.fd ] ||
        [ -e /usr/share/edk2/ovmf/OVMF_CODE.fd ]
}

have_qemu=0
for c in qemu-system-x86_64 qemu-img; do
    command -v "${c}" >/dev/null 2>&1 || have_qemu=1
done

if [ "${have_qemu}" -eq 0 ]; then
    say "qemu 已就绪: $(qemu-system-x86_64 --version | head -n 1)"
else
    need_cmd apt-get
    group "apt-get install qemu"
    apt_sync
    apt_install_min qemu-system-x86 qemu-utils
    endgroup
fi

if [ "${FIRMWARE}" = both ]; then
    if ! fw_present; then
        need_cmd apt-get
        group "apt-get install ovmf"
        apt_sync
        apt_install_min ovmf
        endgroup
    fi
    fw_present || {
        err "缺少 UEFI 固件（ovmf）：装机段会降级成只跑 BIOS 腿，拒绝放行"
        exit 1
    }
    say "UEFI 固件就绪"
fi

# 加速器与固件同在装机段的前置：分开成两步只会让「qemu 装了但没权限」在
# 装机跑了半小时之后才以超时的形式暴露。
enable_kvm
