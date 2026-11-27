#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/kernel-uevent.sh — user_events 事件契约门禁
#
# 用法:
#   ci/gates/kernel-uevent.sh [--src DIR]
#
# 守护进程上报 trace 事件只有 user_events 一条通道（OS-OBS-061）。名字和
# 格式串一旦与内核侧发布的表不一致，注册依旧返回 0：事件落到另一个
# tracefs 目录，或者字段从错位处开始解析，只有事后读 trace 才发现。这类
# 漂移在编译期不报，因此把契约固化成静态门禁。
#
# 判据：
#   * UAPI 头给出事件名、格式串与载荷布局，每份载荷都有尺寸断言。
#   * 内核侧契约表只引用 UAPI 宏，不另抄一份字面量；目录经 sysfs 发布。
#   * 顶层观测面挂载 events 目录，Kbuild 引入该对象，KUnit 有布局用例。
#   * 四架构与完整/agent 片段开启 CONFIG_USER_EVENTS（OS-KER-151）；
#     embedded 片段保持关闭，运行时应报告 absent 而不是虚假通道。
#   * 内核侧不复制传输实现（DIAG_IOCSREG/user_events_data/trace_events_user）。
#   * 两个新增源文件带 SPDX 与版权行。
#
# 退出码: 0 通过；1 契约不成立；5 参数或目录不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/opts.sh
. "${ROOT}/ci/lib/opts.sh"

SRC="${ROOT}/kernel"
opts_src kernel-uevent.sh "$@"

UAPI="${SRC}/include/uapi/linux/airymax/user_events.h"
OBS="${SRC}/kernel/obs"
EVENT="${OBS}/airy_uevent.c"

[ -f "${UAPI}" ] || { err "缺少 UAPI 头: ${UAPI}"; exit "${CI_EINVAL}"; }
[ -f "${EVENT}" ] || { err "缺少契约源文件: ${EVENT}"; exit "${CI_EINVAL}"; }

# UAPI 契约 ----------------------------------------------------------------
chk_uapi()
{
    local name count

    group "UAPI 契约"
    have '^#define AIRY_UEVENT_ABI[[:space:]]+1$' "${UAPI}" \
        "缺少 AIRY_UEVENT_ABI 版本定义"
    for name in COGN_START COGN_END MEM_ACCESS TOOL_CALL SUPERV_ENFORCE; do
        have "^#define AIRY_UEVENT_${name}[[:space:]]+AIRY_UEVENT_PREFIX" \
            "${UAPI}" "缺少事件名 AIRY_UEVENT_${name}"
        have "^#define AIRY_UEVENT_FMT_${name}" "${UAPI}" \
            "缺少格式串 AIRY_UEVENT_FMT_${name}"
    done
    count=$(grep -c '_Static_assert' "${UAPI}")
    [ "${count}" -eq 5 ] || bad "载荷尺寸断言应为 5 处，实为 ${count}"
    endgroup
}

# 契约表 -------------------------------------------------------------------
chk_table()
{
    local name

    group "契约表"
    have 'static const struct airy_uevent_def airy_uevents\[\]' "${EVENT}" \
        "契约表不是 airy_uevents[]"
    for name in COGN_START COGN_END MEM_ACCESS TOOL_CALL SUPERV_ENFORCE; do
        have "AIRY_UEVENT_FMT_${name}[[:space:]]*\}" "${EVENT}" \
            "契约表未使用 AIRY_UEVENT_FMT_${name}"
    done
    deny '"airy_user_' "${EVENT}" "契约表另抄了事件名字面量，未走 UAPI 宏"
    have 'sysfs_emit_at\(' "${EVENT}" "目录未逐条输出事件名与格式串"
    endgroup
}

# 挂载与测试 ---------------------------------------------------------------
chk_mount()
{
    group "挂载与测试"
    have 'airy_obs_uevent_init\(root\);' "${OBS}/airy_obs_core.c" \
        "顶层观测面未挂载 events 目录"
    have 'airy_obs_uevent_init' "${OBS}/airy_obs.h" \
        "未声明 airy_obs_uevent_init"
    have 'airy_uevent\.o' "${OBS}/Kbuild" "Kbuild 未引入 airy_uevent.o"
    have 'obs_uevent_table' "${OBS}/airy_obs_test.c" \
        "缺少目录与运输状态用例"
    have 'obs_uevent_layout' "${OBS}/airy_obs_test.c" \
        "缺少格式串与结构体布局用例"
    endgroup
}

# 传输开关 -----------------------------------------------------------------
chk_transport()
{
    local arch cfg

    group "传输开关"
    for arch in x86 arm64 riscv loongarch; do
        cfg="${SRC}/arch/${arch}/configs/airy_defconfig"
        if [ ! -f "${cfg}" ]; then
            bad "缺少 ${arch} 的 airy_defconfig"
            continue
        fi
        have '^CONFIG_USER_EVENTS=y$' "${cfg}" \
            "${arch}/airy_defconfig 未开启 CONFIG_USER_EVENTS"
    done
    for cfg in configs/defconfig configs/defconfig-agent; do
        have '^CONFIG_USER_EVENTS=y$' "${SRC}/${cfg}" \
            "${cfg} 未开启 CONFIG_USER_EVENTS"
    done
    have '^# CONFIG_USER_EVENTS is not set$' \
        "${SRC}/configs/defconfig-embedded" \
        "embedded 片段应保持传输关闭"
    endgroup
}

# 传输归属 -----------------------------------------------------------------
chk_nofork()
{
    group "传输归属"
    deny 'DIAG_IOCSREG|user_events_data|trace_events_user' "${EVENT}" \
        "内核侧复制了 user_events 传输实现"
    endgroup
}

# 文件头 -------------------------------------------------------------------
chk_headers()
{
    group "文件头"
    need_spdx "${UAPI}" "${EVENT}"
    endgroup
}

chk_uapi
chk_table
chk_mount
chk_transport
chk_nofork
chk_headers

if [ "${FAILS}" -ne 0 ]; then
    err "user_events 契约门禁失败 ${FAILS} 项"
    exit 1
fi

say "user_events 契约门禁通过"
exit 0
