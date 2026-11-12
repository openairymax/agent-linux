#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lib/kernel.sh — Airymax 内核配置契约
#
# 目的：REQUIRED_CONF 是 airy_defconfig 合并后必须精确出现的 11 行，写成
# 完整 KV 而不是 K 前缀，是为了同时锁住开关的值（y 被改成 m 也算红）。它
# 同时是 tools/defconfig-matrix 的校验载具（OS-BUILD-026），因此只能有一份
# 定义：kernel-airy 与 kernel-refbuild 各抄一份时，增删一项要改两处，漏
# 一处就漏检，而两处都改的时间差里两个门禁验的不是同一个契约。
#
# 调用方：ci/gates/kernel-airy.sh（--kind assert）、
# ci/gates/kernel-refbuild.sh。数组只读，调用方用 "${REQUIRED_CONF[@]}"。
# 增删项必须同时改 tools/defconfig-matrix 的 manifest.yaml，不能只改这里。
# ===========================================================================

# shellcheck disable=SC2034  # 由 kernel-airy / kernel-refbuild 在 source 时读取
readonly REQUIRED_CONF=(
    'CONFIG_SECURITY=y'
    'CONFIG_SECURITY_AIRY=y'
    'CONFIG_AIRY_SYSCALL=y'
    'CONFIG_AIRY_COREKERN=y'
    'CONFIG_AIRY_LOG=y'
    'CONFIG_AIRY_IPC=y'
    'CONFIG_AIRY_AGENT_TRACING=y'
    'CONFIG_AIRY_CAP_TABLE_SIZE=1024'
    'CONFIG_IO_URING=y'
    'CONFIG_USER_NS=y'
    'CONFIG_BPF_UNPRIV_DEFAULT_OFF=y'
)
