#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/accept.sh — 装机 + 冷启动验收（L2/L3）
#
# 用法: ci/gates/accept.sh --iso FILE --out DIR [--acceptance]
#                          [--firmware both|bios] [--runs N]
#                          [--install-timeout SECS] [--boot-timeout SECS]
#
# 判定权在 distro/scripts/install-test.sh（判负集 verdict.env），本 gate 只
# 负责两件事：按剖面选期望标记、把 TAP 同时留在文件里供归档。
#
# 标记语义：
#   * 产品 ISO  —— AIRYMAX-DESKTOP-READY（桌面会话真的起来了）
#   * 验收 ISO  —— AIRYMAX-SMOKE-PASS（会话内冒烟全部通过）
# 两者都是「冷启动后被 guest 自己写进串口」的字符串，宿主机不参与判定，
# 因此不存在「宿主以为好了」的假通过。
#
# 退出码: 0 通过；1 装机或冷启动失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
CI_NS=accept
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
RUNNER="${ROOT}/distro/scripts/install-test.sh"

ISO=""
OUT=""
ACCEPTANCE=0
FIRMWARE="both"
RUNS=1
INSTALL_TIMEOUT=3600
BOOT_TIMEOUT=900

# 剖面由调用方以环境变量声明（ACCEPTANCE=true|false），与本地重放时
# `ACCEPTANCE=true ci/ci.sh accept ...` 的写法一致；--acceptance 仍可用。
case "${ACCEPTANCE:-0}" in
    1|true|yes) ACCEPTANCE=1 ;;
    *)          ACCEPTANCE=0 ;;
esac

while [ "$#" -gt 0 ]; do
    case "$1" in
        --iso)             ISO="${2:-}"; shift 2 ;;
        --out)             OUT="${2:-}"; shift 2 ;;
        --acceptance)      ACCEPTANCE=1; shift ;;
        --firmware)        FIRMWARE="${2:-}"; shift 2 ;;
        --runs)            RUNS="${2:-}"; shift 2 ;;
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

marker=AIRYMAX-DESKTOP-READY
[ "${ACCEPTANCE}" -eq 1 ] && marker=AIRYMAX-SMOKE-PASS

mkdir -p "${OUT}"

set -o pipefail
"${RUNNER}" --iso "${ISO}" --out "${OUT}" \
    --runs "${RUNS}" --firmware "${FIRMWARE}" --boot-marker "${marker}" \
    --install-timeout "${INSTALL_TIMEOUT}" --boot-timeout "${BOOT_TIMEOUT}" \
    | tee "${OUT}/tap.txt"
rc=$?

[ "${rc}" -eq 0 ] || die "装机/冷启动失败（rc=${rc}），TAP 见 ${OUT}/tap.txt"
say "验收通过: marker=${marker} firmware=${FIRMWARE} runs=${RUNS}"
