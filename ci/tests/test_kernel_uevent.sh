#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_kernel_uevent.sh — kernel-uevent 门禁回归夹具
#
# 门禁判 UAPI 契约、契约表、挂载接线、传输开关、传输归属与文件头六组。
# 夹具先造一棵满足全部契约的最小源树，再逐组破坏一处；负向断言覆盖
# 事件名缺席、契约表抄字面量、传输实现下沉与开关方向颠倒四类回归。
#
# 用法: bash ci/tests/test_kernel_uevent.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/kernel-uevent.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0
EVENTS="COGN_START COGN_END MEM_ACCESS TOOL_CALL SUPERV_ENFORCE"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

run() { GITHUB_ACTIONS='' bash "${GATE}" "$@" 2>&1; }

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

# uapi SRC — 五个事件名的完整 UAPI 契约
uapi()
{
    local f="$1/include/uapi/linux/airymax/user_events.h" name

    mkdir -p "$(dirname "${f}")"
    {
        printf '/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */\n'
        printf '/* Copyright (C) 2025-2026 SPHARX Ltd. */\n'
        printf '#define AIRY_UEVENT_ABI 1\n'
        printf '#define AIRY_UEVENT_PREFIX "airy_user_"\n'
        for name in ${EVENTS}; do
            printf '#define AIRY_UEVENT_%s AIRY_UEVENT_PREFIX "%s"\n' \
                "${name}" "${name}"
            printf '#define AIRY_UEVENT_FMT_%s "%s"\n' "${name}" "${name}"
        done
        for name in ${EVENTS}; do
            printf '_Static_assert(sizeof(struct ev_%s) == 8, "%s");\n' \
                "${name}" "${name}"
        done
    } >"${f}"
}

# uevent SRC — 契约表与目录输出
uevent()
{
    local f="$1/kernel/obs/airy_uevent.c" name

    mkdir -p "$(dirname "${f}")"
    {
        printf '// SPDX-License-Identifier: GPL-2.0-only\n'
        printf '// Copyright (C) 2025-2026 SPHARX Ltd.\n'
        printf 'static const struct airy_uevent_def airy_uevents[] = {\n'
        for name in ${EVENTS}; do
            printf '        { AIRY_UEVENT_FMT_%s }\n' "${name}"
        done
        printf '};\n'
        printf 'static void show(void) { sysfs_emit_at(NULL, 0, "x"); }\n'
    } >"${f}"
}

mkvalid()
{
    local src="$1" arch cfg

    mkdir -p "${src}/kernel/obs" "${src}/configs"
    uapi "${src}"
    uevent "${src}"
    {
        printf '// SPDX-License-Identifier: GPL-2.0-only\n'
        printf '// Copyright (C) 2025-2026 SPHARX Ltd.\n'
        printf 'static void airy_obs_core(void) { airy_obs_uevent_init(root); }\n'
    } >"${src}/kernel/obs/airy_obs_core.c"
    printf 'void airy_obs_uevent_init(void *root);\n' \
        >"${src}/kernel/obs/airy_obs.h"
    printf 'airy_obs-y := airy_uevent.o\n' >"${src}/kernel/obs/Kbuild"
    printf 'static void obs_uevent_table(void) { }\n' \
        >"${src}/kernel/obs/airy_obs_test.c"
    printf 'static void obs_uevent_layout(void) { }\n' \
        >>"${src}/kernel/obs/airy_obs_test.c"
    for arch in x86 arm64 riscv loongarch; do
        mkdir -p "${src}/arch/${arch}/configs"
        printf 'CONFIG_USER_EVENTS=y\n' \
            >"${src}/arch/${arch}/configs/airy_defconfig"
    done
    for cfg in configs/defconfig configs/defconfig-agent; do
        printf 'CONFIG_USER_EVENTS=y\n' >"${src}/${cfg}"
    done
    printf '# CONFIG_USER_EVENTS is not set\n' \
        >"${src}/configs/defconfig-embedded"
}

# mutate NAME REL SED-EXPR — 复制合规树并改一处，打印新树路径
mutate()
{
    local name="$1" rel="$2" expr="$3" src="${tmp}/$1"

    mkvalid "${src}"
    sed -i "${expr}" "${src}/${rel}"
    printf '%s\n' "${src}"
}

out="$(run --src "${tmp}/none")"; rc=$?
check "缺目录退 5" 5 "${rc}" "${out}" "--src 目录不存在"

out="$(run -h)"; rc=$?
check "帮助退 0" 0 "${rc}" "${out}" "用法:"

mkvalid "${tmp}/valid"
out="$(run --src "${tmp}/valid")"; rc=$?
check "合规树通过" 0 "${rc}" "${out}" "user_events 契约门禁通过"

# 负向：UAPI 契约
src="$(mutate no-abi include/uapi/linux/airymax/user_events.h \
    '/^#define AIRY_UEVENT_ABI/d')"
out="$(run --src "${src}")"; rc=$?
check "缺 ABI 版本定义" 1 "${rc}" "${out}" "缺少 AIRY_UEVENT_ABI 版本定义"

src="$(mutate no-fmt include/uapi/linux/airymax/user_events.h \
    '/^#define AIRY_UEVENT_FMT_MEM_ACCESS/d')"
out="$(run --src "${src}")"; rc=$?
check "缺事件格式串" 1 "${rc}" "${out}" "缺少格式串 AIRY_UEVENT_FMT_MEM_ACCESS"

src="$(mutate short-asserts include/uapi/linux/airymax/user_events.h \
    '/_Static_assert.*TOOL_CALL/d')"
out="$(run --src "${src}")"; rc=$?
check "载荷断言不足 5 处" 1 "${rc}" "${out}" "载荷尺寸断言应为 5 处"

# 负向：契约表
src="$(mutate literal kernel/obs/airy_uevent.c \
    's/^static const struct airy_uevent_def airy_uevents\[\] = {/&\n        { "airy_user_x" },/')"
out="$(run --src "${src}")"; rc=$?
check "契约表抄事件名字面量" 1 "${rc}" "${out}" "另抄了事件名字面量"

src="$(mutate no-emit kernel/obs/airy_uevent.c \
    's/sysfs_emit_at(/pr_info(/')"
out="$(run --src "${src}")"; rc=$?
check "目录未逐条输出" 1 "${rc}" "${out}" "未逐条输出事件名与格式串"

# 负向：挂载接线
src="$(mutate no-mount kernel/obs/airy_obs_core.c 's/airy_obs_uevent_init(root);//')"
out="$(run --src "${src}")"; rc=$?
check "顶层未挂载 events 目录" 1 "${rc}" "${out}" "未挂载 events 目录"

# 负向：传输开关（方向颠倒）
src="$(mutate embedded-on configs/defconfig-embedded \
    's/^# CONFIG_USER_EVENTS is not set/CONFIG_USER_EVENTS=y/')"
out="$(run --src "${src}")"; rc=$?
check "embedded 片段应保持关闭" 1 "${rc}" "${out}" "embedded 片段应保持传输关闭"

# 负向：传输归属
src="$(mutate fork-transport kernel/obs/airy_uevent.c \
    's/^static void show.*/&\n#define DIAG_IOCSREG 1/')"
out="$(run --src "${src}")"; rc=$?
check "内核侧复制传输实现" 1 "${rc}" "${out}" "复制了 user_events 传输实现"

# 负向：文件头与契约源缺席
src="$(mutate no-spdx kernel/obs/airy_uevent.c '1s/.*/\/\/ no license header/')"
out="$(run --src "${src}")"; rc=$?
check "源文件首行缺 SPDX" 1 "${rc}" "${out}" "首行缺少 SPDX"

src="${tmp}/no-uapi"
mkvalid "${src}"
rm -f "${src}/include/uapi/linux/airymax/user_events.h"
out="$(run --src "${src}")"; rc=$?
check "缺 UAPI 头退 5" 5 "${rc}" "${out}" "缺少 UAPI 头"

if [ "${fails}" -ne 0 ]; then
    printf 'test_kernel_uevent: %d 项失败\n' "${fails}" >&2
    exit 1
fi
printf 'test_kernel_uevent: 全部通过\n'
