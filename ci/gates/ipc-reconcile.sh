#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/ipc-reconcile.sh — IPC 控制面 Reconciliation（P-20 / CA-079）
# 的库、CLI、测试与打包接线门禁
#
# 用法:
#   ci/gates/ipc-reconcile.sh
#
# 断言链：
#   1. 库、CLI、测试与 CMakeLists 齐全，源码带 SPDX。
#   2. 出树 cmake 构建零警告（-Werror），ctest 全绿。
#   3. CLI 正常输入输出指定计数并退 0；用法错退 2、坏输入退 3。
#   4. spec 与 workflow 接线成立，预算登记为 2 个作业。
#   5. security 源码树无构建残留、无占位标记与本地绝对路径。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/verify.sh
. "${ROOT}/ci/lib/verify.sh"

IPC="${ROOT}/security/ipc"

need_cmd cmake ctest gcc make

airy_workdir WORK
BUILD="${WORK}/build"
BIN="${BUILD}/ipc/airy-reconcile"

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "ipc-reconcile: 布局"
for name in rc_api.h rc_internal.h rc_diff.c rc_plan.c rc_loop.c \
    rc_main.c tests/rc_test.c CMakeLists.txt; do
    [ -f "${IPC}/${name}" ] || bad "缺少 security/ipc/${name}"
done
need_file "${ROOT}/security/CMakeLists.txt" " security/CMakeLists.txt"
have '^add_subdirectory\(ipc\)$' "${ROOT}/security/CMakeLists.txt" \
    "security 未接入 ipc 子目录"
have '^add_subdirectory\(landlock\)$' "${ROOT}/security/CMakeLists.txt" \
    "security 丢失 landlock 子目录"
for src in "${IPC}"/rc_api.h "${IPC}"/rc_internal.h "${IPC}"/rc_diff.c \
    "${IPC}"/rc_plan.c "${IPC}"/rc_loop.c "${IPC}"/rc_main.c \
    "${IPC}"/tests/rc_test.c; do
    have '^// SPDX-License-Identifier: GPL-2.0-only$' "${src}" \
        "缺少 GPL-2.0-only SPDX: ${src}"
done
for src in "${IPC}/CMakeLists.txt" "${ROOT}/security/CMakeLists.txt"; do
    have '^# SPDX-License-Identifier: GPL-2.0-only$' "${src}" \
        "缺少 GPL-2.0-only SPDX: ${src}"
done
endgroup

# ─── 2. 出树构建与测试 ───────────────────────────────────────────────
built=0
group "ipc-reconcile: 出树构建与测试"
airy_build_test "${ROOT}/security" "${BUILD}" security && built=1
endgroup

# ─── 3. CLI 行为 ─────────────────────────────────────────────────────
group "ipc-reconcile: CLI"
if [ "${built}" -eq 1 ]; then
    cat >"${WORK}/state.txt" <<'EOF'
# control plane snapshot
ring 2 1
cap 10 2 1
msg 7 normal 90000000000
msg 8 normal 1
EOF
    cat >"${WORK}/scan.txt" <<'EOF'
ring 1 1
ring 2 2
cap 10 1 1
cap 11 1 0
EOF
    rc=0
    out="$("${BIN}" --state "${WORK}/state.txt" \
        --scan "${WORK}/scan.txt" --now 100000000000 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "正常输入退出码 ${rc}，期望 0"
    have '^rings_new=1 rings_miss=0 rings_conf=1 caps_verify=1 caps_revoke=1 msg_sent=1 msg_drop=1 degrade=0$' \
        <(printf '%s\n' "${out}") "正常输入计数不符"

    rc=0
    "${BIN}" --state "${WORK}/state.txt" >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 2 ] || bad "用法错误退出码 ${rc}，期望 2"

    printf 'ring 1\n' >"${WORK}/bad.txt"
    rc=0
    "${BIN}" --state "${WORK}/bad.txt" --scan "${WORK}/scan.txt" \
        >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 3 ] || bad "坏输入退出码 ${rc}，期望 3"
fi
endgroup

# ─── 4. 打包与流水线接线 ─────────────────────────────────────────────
group "ipc-reconcile: 安装接线"
airy_sec_wire ipc-reconcile ipc ipc-reconcile \
    'security-build/ipc/airy-reconcile' '%\{_sbindir\}/airy-reconcile'
endgroup

# ─── 5. 源码树卫生 ───────────────────────────────────────────────────
group "ipc-reconcile: 源码树卫生"
leftover="$(find "${ROOT}/security" \
    \( -name CMakeCache.txt -o -name CMakeFiles -o -name '*.o' \
    -o -name '*.a' -o -name rc_test -o -name airy-reconcile \) \
    -print 2>/dev/null)"
[ -z "${leftover}" ] || bad "源码树残留构建产物: ${leftover}"
for d in "${ROOT}/security-build" "${IPC}/build"; do
    [ ! -e "${d}" ] || bad "源码树残留构建目录: ${d}"
done
for src in "${IPC}"/*.c "${IPC}"/*.h "${IPC}"/tests/*.c; do
    deny '(^|[^A-Za-z])(TODO|FIXME|stub)([^A-Za-z]|$)' "${src}" \
        "残留占位标记: ${src}"
    deny '/(home|Users)/' "${src}" "出现本地绝对路径: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "ipc-reconcile 门禁失败 ${FAILS} 项"
say "ipc-reconcile 门禁通过"
