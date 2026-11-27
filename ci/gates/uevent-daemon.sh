#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/uevent-daemon.sh — user_events 守护侧注册与上报链门禁
#
# 用法:
#   ci/gates/uevent-daemon.sh [--src DIR]
#
# 内核只负责发布事件名、格式串与载荷布局；用户态若自抄一份字符串，或
# 注册路径与写入布局偏离 Linux 6.6 的实现，编译期不会报错。注册成功但
# 布局错位时，事件仍会出现在 tracefs 目录，字段却从错误偏移解析，只有
# 读 trace 才发现。把这条链固化成静态门禁。
#
# 判据：
#   * UAPI 仍发布 5 个契约事件，macro_d 用到的名字与格式串来自宏。
#   * 封装库走 DIAG_IOCSREG/DIAG_IOCSUNREG，注册串由 name_args 给出，
#     写入是「4 字节写索引 + 载荷」的 writev，不复制内核侧结构。
#   * 使能位在写入前读取，未启用不发写；关闭撤销使能位。
#   * macro_d 启动注册 superv_enforce，失败即不进入 RUNNING；停止时注销；
#     每次裁决后上报同一 UAPI 载荷。
#   * 构建清单引入库与用例，流水线调用本门禁。
#   * 新文件带 SPDX 与版权行。
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
opts_src uevent-daemon.sh "$@"

UAPI="${SRC}/include/uapi/linux/airymax/user_events.h"
LIB="${ROOT}/services/common/uevent.c"
HDR="${ROOT}/services/common/include/airy/uevent.h"
TEST="${ROOT}/services/common/tests/uevent_test.c"
BUILD="${ROOT}/services/common/CMakeLists.txt"
TBUILD="${ROOT}/services/common/tests/CMakeLists.txt"
MACRO="${ROOT}/services/daemons/macro_d/macro_d.c"
WORKFLOW="${ROOT}/.github/workflows/c-verify.yml"

for f in "${UAPI}" "${LIB}" "${HDR}" "${TEST}" "${BUILD}" "${TBUILD}" \
         "${MACRO}" "${WORKFLOW}"; do
    need_file "${f}" 契约文件
done

# UAPI 契约 ----------------------------------------------------------------
chk_uapi()
{
    local count

    group "UAPI 契约"
    count=$(grep -c '^#define AIRY_UEVENT_FMT_' "${UAPI}")
    [ "${count}" -eq 5 ] || bad "契约事件应为 5 个，实为 ${count}"
    have '^#define AIRY_UEVENT_SUPERV_ENFORCE' "${UAPI}" \
        "缺少 superv_enforce 事件名"
    have '^#define AIRY_UEVENT_FMT_SUPERV_ENFORCE' "${UAPI}" \
        "缺少 superv_enforce 格式串"
    have 'AIRY_UEVENT_SUPERV_SIZE' "${UAPI}" \
        "缺少 superv_enforce 载荷尺寸"
    endgroup
}

# 封装库 -------------------------------------------------------------------
chk_lib()
{
    group "封装库"
    have 'DIAG_IOCSREG' "${LIB}" "注册未走 DIAG_IOCSREG"
    have 'DIAG_IOCSUNREG' "${LIB}" "注销未走 DIAG_IOCSUNREG"
    have 'reg\.name_args' "${LIB}" "注册串未通过 name_args 交给内核"
    have 'reg\.enable_addr' "${LIB}" "未把使能位地址交给内核"
    have 'writev\(' "${LIB}" "上报未走 writev"
    have 'sizeof\(ev->index\)' "${LIB}" "写入头部不是 4 字节写索引"
    deny 'common_type' "${LIB}" "封装库复制了内核侧 entry 布局"
    deny '"airy_user_' "${LIB}" "封装库自抄了事件名字面量"
    have 'u32 index;' "${HDR}" "句柄缺少写索引"
    have 'volatile __u32 enabled;' "${HDR}" "句柄缺少使能位"
    endgroup
}

# 守护接入 -----------------------------------------------------------------
chk_daemon()
{
    group "守护接入"
    have '#include <airy/uevent\.h>' "${MACRO}" "macro_d 未引入封装库"
    have 'AIRY_UEVENT_SUPERV_ENFORCE,' "${MACRO}" \
        "macro_d 未注册 superv_enforce"
    have 'AIRY_UEVENT_FMT_SUPERV_ENFORCE\)' "${MACRO}" \
        "macro_d 未使用 UAPI 格式串"
    have 'struct airy_user_superv_enforce' "${MACRO}" \
        "macro_d 未用 UAPI 载荷"
    have 'airy_uev_emit\(&c->enf, &ue, sizeof\(ue\)\)' "${MACRO}" \
        "裁决后未上报 superv_enforce"
    have 'airy_uev_close\(&c->enf\)' "${MACRO}" \
        "停止路径未注销事件"
    have 'return ret;' "${MACRO}" "注册失败后守护仍继续启动"
    endgroup
}

# 构建与用例 ---------------------------------------------------------------
chk_build()
{
    group "构建与用例"
    have 'uevent\.c' "${BUILD}" "框架库未编入 uevent.c"
    have 'add_executable\(uevent_test uevent_test\.c\)' "${TBUILD}" \
        "未登记 uevent 用例源"
    have 'add_test\(NAME uevent_test' "${TBUILD}" \
        "CMake 未挂载 uevent_test"
    have 'airy_uev_open\(NULL' "${TEST}" \
        "缺少参数校验用例"
    have 'enabled event writes record' "${TEST}" \
        "缺少上报布局用例"
    have 'close clears the handle' "${TEST}" \
        "缺少注销用例"
    have 'ci/ci\.sh uevent-daemon' "${WORKFLOW}" \
        "c-verify.yml 未调用 uevent-daemon 门禁"
    endgroup
}

# 文件头 -------------------------------------------------------------------
chk_headers()
{
    group "文件头"
    need_spdx "${LIB}" "${HDR}" "${TEST}"
    endgroup
}

chk_uapi
chk_lib
chk_daemon
chk_build
chk_headers

if [ "${FAILS}" -ne 0 ]; then
    err "user_events 守护链门禁失败 ${FAILS} 项"
    exit 1
fi

say "user_events 守护链门禁通过"
exit 0
