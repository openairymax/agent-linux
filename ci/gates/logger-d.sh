#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/logger-d.sh — A-ULP 日志守护的构建、单测与契约门禁
#
# 用法:
#   ci/gates/logger-d.sh
#
# 断言链（缺一不可）：
#   1. 出树构建 logger_d，构建目录不得落在源码树内。
#   2. logd_cfg / logd_file 单测全绿：出厂配置与内置默认值逐键一致、
#      非法配置可归因、轮转保留窗口成立。
#   3. 拒绝路径的退出码可归因：非法配置 78、未知参数 64、--help 为 0。
#   4. unit 契约：notify 就绪、配置路径与 ExecStart 一致、看门狗与可写
#      路径覆盖 /var/log/agentrt。
#
# 退出码: 0 通过；1 判定失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/verify.sh
. "${ROOT}/ci/lib/verify.sh"

UNIT="${ROOT}/system/units/agentrt-logger.service"
CONF="${ROOT}/services/daemons/logger_d/etc/logger.conf"
BUILD="$(airy_build_dir "${ROOT}" AIRY_LOGD_BUILD_ROOT airy-logd-verify)"

need_cmd cmake
need_cmd ctest

airy_workdir WORK

# ─── 1. 出树构建 ─────────────────────────────────────────────────────
airy_build "${ROOT}/services" "${BUILD}" logger_d \
    logger_d logd_cfg_test logd_file_test

BIN="${BUILD}/daemons/logger_d/logger_d"
[ -x "${BIN}" ] || die "未生成 logger_d: ${BIN}"

# ─── 2. 单测 ─────────────────────────────────────────────────────────
group "logger_d: 单测"
ctest --test-dir "${BUILD}" -R '^logd_' --output-on-failure ||
    die "logd 单测未通过"
endgroup
say "配置装载与落盘轮转单测通过"

# ─── 3. 拒绝路径 ─────────────────────────────────────────────────────
group "logger_d: 拒绝路径"
airy_expect_rc "未知参数" 64 "${BIN}" --nope
airy_expect_rc "帮助" 0 "${BIN}" --help
printf '[other]\nlevel = info\n' > "${WORK}/bad.conf"
airy_expect_rc "未知分组" 78 "${BIN}" --config="${WORK}/bad.conf"
printf '[logger]\nmax_bytes = 12X\n' > "${WORK}/bad.conf"
airy_expect_rc "非法尺寸" 78 "${BIN}" --config="${WORK}/bad.conf"
endgroup

# ─── 4. unit 契约 ────────────────────────────────────────────────────
group "logger_d: unit 契约"
grep -qx 'Type=notify' "${UNIT}" || die "unit 未声明 Type=notify"
grep -qx 'ExecStart=/usr/sbin/logger_d --config=/etc/agentrt/logger.conf' \
    "${UNIT}" || die "unit 的 ExecStart 与出厂配置路径不一致"
grep -qx 'ExecReload=/bin/kill -HUP \$MAINPID' "${UNIT}" ||
    die "unit 未提供 ExecReload"
grep -qx 'WatchdogSec=30s' "${UNIT}" || die "unit 未声明看门狗"
grep -q '/var/log/agentrt' "${UNIT}" ||
    die "unit 的 ReadWritePaths 未覆盖落盘目录"
[ -f "${CONF}" ] || die "缺少出厂 logger.conf"
endgroup
say "unit 契约成立"

say "logger-d 门禁通过"
