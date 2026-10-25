#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/qemu-setup.sh — 装机段的宿主依赖
#
# 用法: ci/gates/qemu-setup.sh [--firmware both|bios]
#
# 已在宿主装齐就跳过，不做无条件 apt-get update：B50 现场机离线时不希望
# 一次门禁触发整轮源同步。ovmf 缺失不致命——install-test.sh 的 --firmware
# both 会在缺 UEFI 固件时降级为 BIOS 腿，此处同样只告警。
#
# 退出码: 0 就绪（含降级）；1 缺 qemu 且装不上；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
CI_NS=qemu-setup
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/host.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/host.sh"

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

have_qemu=0
for c in qemu-system-x86_64 qemu-img; do
    command -v "${c}" >/dev/null 2>&1 || have_qemu=1
done

if [ "${have_qemu}" -eq 0 ]; then
    say "qemu 已就绪: $(qemu-system-x86_64 --version | head -n 1)"
else
    need_cmd apt-get
    group "apt-get install qemu"
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends \
        qemu-system-x86 qemu-utils $( [ "${FIRMWARE}" = both ] && echo ovmf )
    endgroup
fi

if [ "${FIRMWARE}" = both ]; then
    if [ -e /usr/share/OVMF/OVMF_CODE.fd ] || [ -e /usr/share/edk2/ovmf/OVMF_CODE.fd ]; then
        say "UEFI 固件就绪"
    else
        warn "未找到 UEFI 固件，装机段将只跑 BIOS 腿"
    fi
fi

# 加速器与固件同在装机段的前置：分开成两步只会让「qemu 装了但没权限」在
# 装机跑了半小时之后才以超时的形式暴露。
enable_kvm
