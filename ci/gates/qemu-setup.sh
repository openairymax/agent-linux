#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/qemu-setup.sh — 装机段的宿主依赖
#
# 用法: ci/gates/qemu-setup.sh [--firmware both|bios] [--arch ARCH]
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
# 装完必须复判，不能把 apt 的退出码当成结论：0.0.5 的验收镜像里 qemu 装了
# 三轮，三轮都败在 security.ubuntu.com 上 ipxe-qemu 的 503，而本门禁既没
# 看返回值也没再验一次命令在不在，继续打印「UEFI 固件就绪 / KVM 可用」并以
# 0 退出。三条装机腿随后在 0.03 秒内以 rc=77 跳过，失败点被推迟到 20 分钟
# 之后，看起来还像 guest 的问题。现在的契约相反：每一轮之后都按「命令在
# 不在 PATH 上」复判，复判不过就判负并指名缺哪一条；成功路径上，装机段要
# 用的两条命令一定可用。装机入口因此不该再走到它的 SKIP 分支。
#
# 失败先换索引再试一轮，而不是把同一基址重试第四次：托管镜像的 503 是单台
# 基站的问题，上例里紧接着为 ovmf 重取的索引就换到了可用源，同一轮次内装
# 好了。apt.sh 的重试只续传同一个基址，换源要靠这里重新 apt_sync。
#
# 退出码: 0 就绪（两条命令均已复判可用）；1 缺 qemu 或 UEFI 固件且装不上；
#         5 参数不满足。
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
ARCH="x86_64"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --firmware) FIRMWARE="${2:-}"; shift 2 ;;
        --arch) ARCH="${2:-}"; shift 2 ;;
        -h|--help)
            echo "用法: ci/gates/qemu-setup.sh [--firmware both|bios] [--arch ARCH]"
            exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

case "${FIRMWARE}" in both|bios) ;; *)
    err "--firmware 只接受 both|bios"; exit "${EINVAL}" ;; esac

# 每个架构一套宿主依赖：模拟器包、模拟器二进制、UEFI 固件包各不同。
# aarch64 只有 UEFI（AAVMF），没有 BIOS 腿，因此 --firmware bios 在这里
# 是自相矛盾的参数，直接判负而不是静默改成 both。
case "${ARCH}" in
x86_64)
    QEMU_PKGS=(qemu-system-x86 qemu-utils)
    QEMU_BINS=(qemu-system-x86_64 qemu-img)
    FW_PKG=ovmf
    ;;
aarch64)
    [ "${FIRMWARE}" != bios ] || {
        err "aarch64 没有 BIOS 固件腿，--firmware bios 无效"
        exit "${EINVAL}"
    }
    QEMU_PKGS=(qemu-system-arm qemu-utils)
    QEMU_BINS=(qemu-system-aarch64 qemu-img)
    FW_PKG=qemu-efi-aarch64
    ;;
*)
    err "不支持的架构: ${ARCH}"; exit "${EINVAL}" ;;
esac

# 固件枚举只在 ci/lib/host.sh，本门禁不另立一份：装机段对同一组模式下判据，
# 两边一旦分叉就会出现「这里说已就绪、装机段却降级成 BIOS」。
fw_present()
{
    uefi_fw_code "${ARCH}" >/dev/null
}

qemu_ready()
{
    local c

    for c in "${QEMU_BINS[@]}"; do
        command -v "${c}" >/dev/null 2>&1 || return 1
    done
}

qemu_missing()
{
    local c out=""

    for c in "${QEMU_BINS[@]}"; do
        command -v "${c}" >/dev/null 2>&1 || out="${out}${out:+ }${c}"
    done
    printf '%s' "${out}"
}

apt_qemu()
{
    group "apt-get install qemu（第 $1 轮）"
    apt_sync
    apt_install_min "${QEMU_PKGS[@]}"
    endgroup
}

if ! qemu_ready; then
    need_cmd apt-get
    apt_qemu 1
    qemu_ready || {
        warn "qemu 仍不可用（缺: $(qemu_missing)），换索引后再试一轮"
        apt_qemu 2
    }
fi

qemu_ready || {
    err "qemu 工具链装不上，缺: $(qemu_missing)"
    err "装机段缺这两条命令时只会以 rc=77 跳过全部装机腿，故在此判负"
    exit 1
}
say "qemu 已就绪: $("${QEMU_BINS[0]}" --version | head -n 1)"

if [ "${FIRMWARE}" = both ]; then
    if ! fw_present; then
        need_cmd apt-get
        group "apt-get install ${FW_PKG}"
        apt_sync
        apt_install_min "${FW_PKG}"
        endgroup
    fi
    fw_present || {
        err "缺少 UEFI 固件（${FW_PKG}/${ARCH}）：装机段会降级，拒绝放行"
        err "已探测: $(uefi_fw_paths "${ARCH}" | tr '\n' ' ')"
        exit 1
    }
    say "UEFI 固件就绪（${ARCH}）: $(uefi_fw_code "${ARCH}")"
fi

# 加速器与固件同在装机段的前置：分开成两步只会让「qemu 装了但没权限」在
# 装机跑了半小时之后才以超时的形式暴露。
enable_kvm "${ARCH}"
