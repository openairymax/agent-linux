#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_uevent_daemon.sh — uevent-daemon 门禁回归夹具
#
# 门禁横跨 UAPI、封装库、守护接入、构建清单与流水线接线，除内核树外都按
# 仓库根解析。夹具把门禁与它的库复制进临时仓库，在假树里逐组破坏：事件
# 计数、封装层复制内核布局、句柄缺使能位、守护漏上报、构建漏挂用例、
# 文件头缺 SPDX。
#
# 用法: bash ci/tests/test_uevent_daemon.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
BASE="${tmp}/base"
EVENTS="COGN_START COGN_END MEM_ACCESS TOOL_CALL SUPERV_ENFORCE"
fails=0

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

run() { GITHUB_ACTIONS='' bash "${BASE}/ci/gates/uevent-daemon.sh" "$@" 2>&1; }

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

mkdir -p "${BASE}/ci/gates" "${BASE}/ci/lib" "${BASE}/kernel/include/uapi/linux/airymax" \
    "${BASE}/services/common/include/airy" "${BASE}/services/common/tests" \
    "${BASE}/services/daemons/macro_d" "${BASE}/.github/workflows"
cp "${ROOT}/ci/gates/uevent-daemon.sh" "${BASE}/ci/gates/"
cp "${ROOT}/ci/lib/log.sh" "${ROOT}/ci/lib/opts.sh" "${BASE}/ci/lib/"

UAPI="${BASE}/kernel/include/uapi/linux/airymax/user_events.h"
{
    printf '/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */\n'
    for name in ${EVENTS}; do
        printf '#define AIRY_UEVENT_FMT_%s "%s"\n' "${name}" "${name}"
    done
    printf '#define AIRY_UEVENT_SUPERV_ENFORCE\n'
    printf '#define AIRY_UEVENT_SUPERV_SIZE 24\n'
} >"${UAPI}"

LIB="${BASE}/services/common/uevent.c"
{
    printf '// SPDX-License-Identifier: Apache-2.0\n'
    printf '// Copyright (C) 2025-2026 SPHARX Ltd.\n'
    printf '#define DIAG_IOCSREG 1\n#define DIAG_IOCSUNREG 2\n'
    printf 'static void reg(void) { reg.name_args = 0; reg.enable_addr = 0; }\n'
    printf 'static long emit(void) { return writev(0, NULL, 0); }\n'
    printf 'static unsigned hdr(void) { return sizeof(ev->index); }\n'
} >"${LIB}"

HDR="${BASE}/services/common/include/airy/uevent.h"
{
    printf '// SPDX-License-Identifier: Apache-2.0\n'
    printf '// Copyright (C) 2025-2026 SPHARX Ltd.\n'
    printf 'struct airy_uev { u32 index; volatile __u32 enabled; };\n'
} >"${HDR}"

TEST="${BASE}/services/common/tests/uevent_test.c"
{
    printf '// SPDX-License-Identifier: Apache-2.0\n'
    printf '// Copyright (C) 2025-2026 SPHARX Ltd.\n'
    printf 'static void a(void) { airy_uev_open(NULL, 0); }\n'
    printf '/* enabled event writes record */\n/* close clears the handle */\n'
} >"${TEST}"

printf 'add_library(airy_common uevent.c)\n' >"${BASE}/services/common/CMakeLists.txt"
{
    printf 'add_executable(uevent_test uevent_test.c)\n'
    printf 'add_test(NAME uevent_test COMMAND uevent_test)\n'
} >"${BASE}/services/common/tests/CMakeLists.txt"

MACRO="${BASE}/services/daemons/macro_d/macro_d.c"
{
    printf '#include <airy/uevent.h>\n'
    printf 'static void reg(void) { airy_uev_reg(0, AIRY_UEVENT_SUPERV_ENFORCE,'
    printf ' 0, AIRY_UEVENT_FMT_SUPERV_ENFORCE); }\n'
    printf 'static void fire(void)\n{\n'
    printf '        struct airy_user_superv_enforce ue;\n'
    printf '        airy_uev_emit(&c->enf, &ue, sizeof(ue));\n'
    printf '        return ret;\n}\n'
    printf 'static void stop(void) { airy_uev_close(&c->enf); }\n'
} >"${MACRO}"

printf 'steps:\n  - run: ci/ci.sh uevent-daemon\n' >"${BASE}/.github/workflows/c-verify.yml"

# mutate NAME REL SED-EXPR — 复制合规树并改一处，打印新树路径
mutate()
{
    local name="$1" rel="$2" expr="$3" tree="${tmp}/$1"

    cp -a "${BASE}" "${tree}"
    sed -i "${expr}" "${tree}/${rel}"
    printf '%s\n' "${tree}"
}

runin()
{
    local tree="$1"

    shift
    GITHUB_ACTIONS='' bash "${tree}/ci/gates/uevent-daemon.sh" "$@" 2>&1
}

out="$(run -h)"; rc=$?
check "帮助退 0" 0 "${rc}" "${out}" "用法:"

out="$(run)"; rc=$?
check "合规树通过" 0 "${rc}" "${out}" "user_events 守护链门禁通过"

# 负向：UAPI 契约
t="$(mutate four-events kernel/include/uapi/linux/airymax/user_events.h \
    '/^#define AIRY_UEVENT_FMT_TOOL_CALL/d')"
out="$(runin "${t}")"; rc=$?
check "契约事件数不足" 1 "${rc}" "${out}" "契约事件应为 5 个"

t="$(mutate no-size kernel/include/uapi/linux/airymax/user_events.h \
    '/^#define AIRY_UEVENT_SUPERV_SIZE/d')"
out="$(runin "${t}")"; rc=$?
check "缺载荷尺寸" 1 "${rc}" "${out}" "缺少 superv_enforce 载荷尺寸"

# 负向：封装库
t="$(mutate copied-layout services/common/uevent.c \
    's/^static long emit.*/&\nstruct x { int common_type; };/')"
out="$(runin "${t}")"; rc=$?
check "封装库复制内核布局" 1 "${rc}" "${out}" "封装库复制了内核侧 entry 布局"

t="$(mutate literal-name services/common/uevent.c \
    's/^static long emit.*/&\nstatic const char *n = "airy_user_x";/')"
out="$(runin "${t}")"; rc=$?
check "封装库自抄事件名" 1 "${rc}" "${out}" "封装库自抄了事件名字面量"

t="$(mutate no-enabled services/common/include/airy/uevent.h \
    's/volatile __u32 enabled;/unsigned enabled;/')"
out="$(runin "${t}")"; rc=$?
check "句柄缺使能位" 1 "${rc}" "${out}" "句柄缺少使能位"

# 负向：守护接入
t="$(mutate no-emit services/daemons/macro_d/macro_d.c \
    's/airy_uev_emit(&c->enf, &ue, sizeof(ue));//')"
out="$(runin "${t}")"; rc=$?
check "裁决后未上报" 1 "${rc}" "${out}" "裁决后未上报 superv_enforce"

t="$(mutate no-close services/daemons/macro_d/macro_d.c \
    's/airy_uev_close(&c->enf);//')"
out="$(runin "${t}")"; rc=$?
check "停止路径未注销" 1 "${rc}" "${out}" "停止路径未注销事件"

# 负向：构建与流水线
t="$(mutate no-test services/common/tests/CMakeLists.txt \
    '/^add_test/d')"
out="$(runin "${t}")"; rc=$?
check "CMake 未挂载用例" 1 "${rc}" "${out}" "CMake 未挂载 uevent_test"

t="$(mutate no-hook .github/workflows/c-verify.yml \
    's#ci/ci.sh uevent-daemon#true#')"
out="$(runin "${t}")"; rc=$?
check "流水线未调用门禁" 1 "${rc}" "${out}" "c-verify.yml 未调用 uevent-daemon 门禁"

# 负向：文件头与契约文件缺席
t="$(mutate no-spdx services/common/uevent.c '1s/.*/\/\/ no license header/')"
out="$(runin "${t}")"; rc=$?
check "封装库首行缺 SPDX" 1 "${rc}" "${out}" "uevent.c: 首行缺少 SPDX"

t="${tmp}/no-contract"
cp -a "${BASE}" "${t}"
rm -f "${t}/services/daemons/macro_d/macro_d.c"
out="$(runin "${t}")"; rc=$?
check "契约文件缺席退 5" 5 "${rc}" "${out}" "契约文件"

if [ "${fails}" -ne 0 ]; then
    printf 'test_uevent_daemon: %d 项失败\n' "${fails}" >&2
    exit 1
fi
printf 'test_uevent_daemon: 全部通过\n'
