#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/macro-d.sh — A-ULS 监督守护的构建、单测与契约门禁
#
# 用法:
#   ci/gates/macro-d.sh
#
# 断言链（缺一不可）：
#   1. 出树构建 macro_d，构建目录不得落在源码树内。
#   2. macro_cfg 单测全绿：出厂配置与内置默认值逐键一致、六个故障码
#      的默认裁决成立、非法分组/键/动作可归因。
#   3. 拒绝路径的退出码可归因：非法配置 78、未知参数 64、--help 为 0。
#   4. unit 契约：notify 就绪、配置路径与 ExecStart 一致、看门狗成立，
#      私有 /dev 下监督通道被显式放行并可见。
#
# 设备本身由内核提供，本门禁不触碰 /dev：接入失败发生在配置文件校验
# 之后，因此拒绝路径在不含该设备的 runner 上同样可复现。
#
# 退出码: 0 通过；1 判定失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/verify.sh
. "${ROOT}/ci/lib/verify.sh"

UNIT="${ROOT}/system/units/agentrt-macro-superv.service"
CONF="${ROOT}/services/daemons/macro_d/etc/macro-superv.conf"
DEV="/dev/airy_superv"
BUILD="$(airy_build_dir "${ROOT}" AIRY_MACRO_BUILD_ROOT airy-macro-verify)"

need_cmd cmake
need_cmd ctest

airy_workdir WORK

# ─── 1. 出树构建 ─────────────────────────────────────────────────────
airy_build "${ROOT}/services" "${BUILD}" macro_d \
    macro_d macro_cfg_test

BIN="${BUILD}/daemons/macro_d/macro_d"
[ -x "${BIN}" ] || die "未生成 macro_d: ${BIN}"

# ─── 2. 单测 ─────────────────────────────────────────────────────────
group "macro_d: 单测"
ctest --test-dir "${BUILD}" -R '^macro_' --output-on-failure ||
    die "macro 单测未通过"
endgroup
say "配置装载与默认裁决单测通过"

# ─── 3. 拒绝路径 ─────────────────────────────────────────────────────
group "macro_d: 拒绝路径"
airy_expect_rc "未知参数" 64 "${BIN}" --nope
airy_expect_rc "帮助" 0 "${BIN}" --help
airy_expect_rc "配置缺失" 78 "${BIN}" --config="${WORK}/absent.conf"
printf '[other]\nkey = 1\n' > "${WORK}/bad.conf"
airy_expect_rc "未知分组" 78 "${BIN}" --config="${WORK}/bad.conf"
printf '[policy]\ncap_leak = reboot\n' > "${WORK}/bad.conf"
airy_expect_rc "非法动作" 78 "${BIN}" --config="${WORK}/bad.conf"
printf '[superv]\ndevice = dev/airy_superv\n' > "${WORK}/bad.conf"
airy_expect_rc "相对设备路径" 78 "${BIN}" --config="${WORK}/bad.conf"
endgroup

# ─── 4. unit 契约 ────────────────────────────────────────────────────
group "macro_d: unit 契约"
grep -qx 'Type=notify' "${UNIT}" || die "unit 未声明 Type=notify"
grep -qx "ExecStart=/usr/sbin/macro_d --config=/etc/agentrt/macro-superv.conf" \
    "${UNIT}" || die "unit 的 ExecStart 与出厂配置路径不一致"
grep -qx 'ExecReload=/bin/kill -HUP \$MAINPID' "${UNIT}" ||
    die "unit 未提供 ExecReload"
grep -qx 'WatchdogSec=30s' "${UNIT}" || die "unit 未声明看门狗"
grep -qx 'User=airy' "${UNIT}" || die "unit 未以 airy 身份运行"
grep -qx 'PrivateDevices=yes' "${UNIT}" || die "unit 未启用私有 /dev"
grep -qx "DeviceAllow=${DEV} rw" "${UNIT}" ||
    die "unit 未放行监督通道设备"
grep -qx "BindReadOnlyPaths=${DEV}" "${UNIT}" ||
    die "unit 未把监督通道映射进私有 /dev"
[ -f "${CONF}" ] || die "缺少出厂 macro-superv.conf"
grep -qx "device = ${DEV}" "${CONF}" ||
    die "出厂配置的设备路径与 unit 不一致"
endgroup
say "unit 契约成立"

say "macro-d 门禁通过"
