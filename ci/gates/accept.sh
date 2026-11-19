#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/accept.sh — 装机 + 冷启动验收（L2/L3）
#
# 用法: ci/gates/accept.sh --iso FILE --out DIR [--acceptance]
#                          [--arch x86_64|aarch64]
#                          [--firmware both|bios] [--runs N]
#                          [--media cdrom|usb]
#                          [--install-timeout SECS] [--boot-timeout SECS]
#
# --arch 决定宿主上的模拟器与固件（qemu-system-<arch>、OVMF/AAVMF），缺省
# x86_64。跨架构没有 KVM，装机入口会自动回落到 TCG；调用方要相应放宽
# --install-timeout 与步骤时限。
#
# 判定权在 distro/scripts/install-test.sh（判负集 verdict.env），本 gate 只
# 负责两件事：按剖面选期望标记、把 TAP 同时留在文件里供归档。
#
# 介质形态透传给装机入口：cdrom 走 El Torito，usb 走分区表。真机 U 盘装机
# 是后者，因此 usb 腿才是 isohybrid 三元一致性的有效证据。
#
# 标记语义：
#   * 产品 ISO  —— AIRYMAX-DESKTOP-READY 与 AIRYMAX-NET-READY（桌面会话真的
#                  起来了，且首启联机四元组成立）
#   * 验收 ISO  —— AIRYMAX-SMOKE-PASS（会话内冒烟全部通过）
# 两者都是「冷启动后被 guest 自己写进串口」的字符串，宿主机不参与判定，
# 因此不存在「宿主以为好了」的假通过。验收剖面由 T31 判联机，产品探针在
# 该剖面被 mask，同一观测点只有一个判定者。
#
# 退出码: 0 通过；1 装机或冷启动失败；5 参数不满足；
#         77 宿主缺 QEMU 工具链（由装机入口透传，未执行任何腿）。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"
# shellcheck source=../lib/profile.sh
. "${LIB}/profile.sh"
# shellcheck source=../lib/progress.sh
. "${LIB}/progress.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
RUNNER="${ROOT}/distro/scripts/install-test.sh"

ISO=""
OUT=""
FIRMWARE="both"
MEDIA="cdrom"
RUNS=1
INSTALL_TIMEOUT=3600
BOOT_TIMEOUT=900
ARCH="x86_64"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --iso)             ISO="${2:-}"; shift 2 ;;
        --out)             OUT="${2:-}"; shift 2 ;;
        --acceptance)      export ACCEPTANCE=1; shift ;;
        --firmware)        FIRMWARE="${2:-}"; shift 2 ;;
        --media)           MEDIA="${2:-}"; shift 2 ;;
        --runs)            RUNS="${2:-}"; shift 2 ;;
        --arch)            ARCH="${2:-}"; shift 2 ;;
        --install-timeout) INSTALL_TIMEOUT="${2:-}"; shift 2 ;;
        --boot-timeout)    BOOT_TIMEOUT="${2:-}"; shift 2 ;;
        -h|--help)
            echo "用法: ci/gates/accept.sh --iso FILE --out DIR [--acceptance]"
            exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${ISO}" ] && [ -n "${OUT}" ] || {
    err "--iso/--out 均为必填"; exit "${EINVAL}"; }
[ -f "${ISO}" ] || die "ISO 不存在: ${ISO}"
[ -x "${RUNNER}" ] || die "缺少装机入口: ${RUNNER}"

markers=(AIRYMAX-DESKTOP-READY AIRYMAX-NET-READY)
[ "$(airy_profile)" -eq 1 ] && markers=(AIRYMAX-SMOKE-PASS)
marker_args=()
for m in "${markers[@]}"; do
    marker_args+=(--boot-marker "${m}")
done

mkdir -p "${OUT}"

set -o pipefail
# 装机腿每腿最多 3600 秒，串口 TAP 只在结束时成批落盘；期间没有任何一行
# 输出能说明它在装机、冷启动还是已经挂死。心跳以产物目录里的日志为准。
progress_start "${OUT}"
trap progress_stop EXIT
"${RUNNER}" --iso "${ISO}" --out "${OUT}" --arch "${ARCH}" \
    --runs "${RUNS}" --firmware "${FIRMWARE}" --media "${MEDIA}" \
    "${marker_args[@]}" \
    --install-timeout "${INSTALL_TIMEOUT}" --boot-timeout "${BOOT_TIMEOUT}" \
    | tee "${OUT}/tap.txt"
rc=$?
progress_stop

# 77 不是腿的失败：装机入口在宿主缺 qemu 时直接跳过，一条腿都没跑。报成
# 「装机/冷启动失败」会把现场引向 guest（镜像、kickstart、串口），而真正的
# 缺口在宿主依赖上。CI 里 qemu-setup 已改成装不上即判负，这里只服务本地或
# 其它入口，但仍要指名病根。
[ "${rc}" -eq 77 ] &&
    die "宿主缺 QEMU 工具链（rc=77），装机腿未执行；先跑 ci/ci.sh qemu-setup"
[ "${rc}" -eq 0 ] || die "装机/冷启动失败（rc=${rc}），TAP 见 ${OUT}/tap.txt"
say "验收通过: marker=${markers[*]} firmware=${FIRMWARE} media=${MEDIA} runs=${RUNS}"
