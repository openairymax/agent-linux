#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/shim-runtime.sh — containerd shim 运行时引擎（P-23 / CA-086）
# 的 bundle 契约、内核桥、进程监管与接线门禁
#
# 用法:
#   ci/gates/shim-runtime.sh
#
# 断言链：
#   1. shim 源码、测试、夹具与文档齐全并带 SPDX；cloudnative 已接入
#      shim 子目录。
#   2. 出树 cmake 构建零警告（-Werror），ctest 全绿。
#   3. check 对完整 bundle 给出六行规划与 caps=0x103；缺省值符合契约。
#   4. run 真实启动子进程并原样折算退出码；坏 bundle 退 3、用法错退 2。
#   5. 内核桥在无 550 后端时降级为 unsupported，不阻断启动。
#   6. spec 装箱与 cloudnative-verify 作业接线成立，预算已登记本流水线。
#   7. shim 常量与内核 UAPI 一致；源码树无构建残留与本地绝对路径。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/verify.sh
. "${ROOT}/ci/lib/verify.sh"

CN="${ROOT}/cloudnative"
SHM="${CN}/shim"
FIX="${SHM}/tests/fixtures"
UAPI="${ROOT}/kernel/include/uapi/linux/airymax"

need_cmd cmake ctest gcc make

airy_workdir WORK
BUILD="${WORK}/build"
BIN="${BUILD}/shim/airy-shim"

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "shim-runtime: 布局"
for name in shim/shm_api.h shim/shm_spec.c shim/shm_kern.c \
    shim/shm_proc.c shim/shm_main.c shim/CMakeLists.txt \
    shim/tests/shm_test.c shim/tests/fixtures/bundle/config.json \
    shim/tests/fixtures/minimal/config.json \
    shim/tests/fixtures/bad-layer/config.json \
    shim/tests/fixtures/eevdf/config.json Documentation/shim.md; do
    [ -f "${CN}/${name}" ] || bad "缺少 cloudnative/${name}"
done
have '^add_subdirectory\(shim\)$' "${CN}/CMakeLists.txt" \
    "cloudnative 未接入 shim 子目录"
for src in "${SHM}"/*.c "${SHM}"/*.h "${SHM}"/tests/*.c; do
    have '^/\* SPDX-License-Identifier: GPL-2\.0-only \*/$' "${src}" \
        "缺少 GPL-2.0-only SPDX: ${src}"
done
have '^# SPDX-License-Identifier: GPL-2\.0-only$' "${SHM}/CMakeLists.txt" \
    "缺少 GPL-2.0-only SPDX: ${SHM}/CMakeLists.txt"
endgroup

# ─── 2. 出树构建与测试 ───────────────────────────────────────────────
built=0
group "shim-runtime: 出树构建与测试"
airy_build_test "${CN}" "${BUILD}" cloudnative && built=1
endgroup

# ─── 3. bundle 契约与规划 ────────────────────────────────────────────
group "shim-runtime: bundle 契约"
if [ "${built}" -eq 1 ]; then
    rc=0
    out="$("${BIN}" check --bundle "${FIX}/bundle" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "check 完整 bundle 退出码 ${rc}，期望 0"
    have '^shim admit id=local args=3$' <(printf '%s\n' "${out}") \
        "规划未给出 admit 摘要"
    have '^shim budget total=250000 per_req=250000 refill=0 policy=throttle$' \
        <(printf '%s\n' "${out}") "预算契约不符"
    have '^shim memory layers=L1,L2 capacity=default$' \
        <(printf '%s\n' "${out}") "记忆层契约不符"
    have '^shim sched class=stc_agent cycle=CoreLoopThree$' \
        <(printf '%s\n' "${out}") "调度契约不符"
    have '^shim security caps=0x103$' <(printf '%s\n' "${out}") \
        "能力位不符"
    have '^shim exec argv0=/bin/sh env=1$' <(printf '%s\n' "${out}") \
        "执行摘要不符"

    rc=0
    out="$("${BIN}" check --bundle "${FIX}/minimal" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "check 缺省 bundle 退出码 ${rc}，期望 0"
    have '^shim budget total=1000000 per_req=1000000 refill=0 policy=throttle$' \
        <(printf '%s\n' "${out}") "缺省预算不符"
    have '^shim memory layers=none capacity=default$' \
        <(printf '%s\n' "${out}") "缺省记忆层不符"
    have '^shim security caps=0x1$' <(printf '%s\n' "${out}") \
        "缺省能力位不符"

    rc=0
    out="$("${BIN}" check --bundle "${FIX}/eevdf" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "check eevdf bundle 退出码 ${rc}，期望 0"
    have '^shim sched class=eevdf cycle=CoreLoopThree$' \
        <(printf '%s\n' "${out}") "eevdf 调度类不符"
fi
endgroup

# ─── 4. 进程监管与退出码 ─────────────────────────────────────────────
group "shim-runtime: 进程监管"
if [ "${built}" -eq 1 ]; then
    rc=0
    out="$("${BIN}" run --bundle "${FIX}/bundle" --id gate 2>&1)" || rc=$?
    [ "${rc}" -eq 7 ] || bad "run 退出码 ${rc}，期望子进程的 7"
    have '^shim phase=Creating$' <(printf '%s\n' "${out}") \
        "缺少 Creating 阶段"
    have '^shim phase=Running pid=[0-9]+$' <(printf '%s\n' "${out}") \
        "缺少 Running 阶段"
    have '^shim exit code=7$' <(printf '%s\n' "${out}") \
        "退出码折算不符"
    have '^shim phase=Dead$' <(printf '%s\n' "${out}") \
        "缺少 Dead 阶段"

    rc=0
    out="$("${BIN}" run --bundle "${FIX}/minimal" --cgroup /sys/fs/cgroup \
        2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "run 带 cgroup 退出码 ${rc}，期望 0"
    have '^shim sched class=stc_agent result=(applied|unsupported)$' \
        <(printf '%s\n' "${out}") "内核桥结果未落在既定集合"

    rc=0
    err="$("${BIN}" check --bundle "${FIX}/bad-layer" 2>&1)" || rc=$?
    [ "${rc}" -eq 3 ] || bad "坏 bundle 退出码 ${rc}，期望 3"
    have '未知层 L5' <(printf '%s\n' "${err}") \
        "坏 bundle 未指出非法层名"

    rc=0
    "${BIN}" check >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 2 ] || bad "用法错误退出码 ${rc}，期望 2"
fi
endgroup

# ─── 5. 内核桥常量与 UAPI 一致 ───────────────────────────────────────
group "shim-runtime: UAPI"
have 'define AIRY_SYS_SCHED_CTL[[:space:]]+550' "${UAPI}/syscalls.h" \
    "内核 UAPI 未把调度控制登记为 550"
have 'AIRY_SCHED_SET_POLICY[[:space:]]*=[[:space:]]*0x01' \
    "${UAPI}/sched.h" "内核 UAPI 的 SET_POLICY 不是 0x01"
for pair in 'AIRY_EACCES 1' 'AIRY_EINVAL 5' 'AIRY_ENOTSUP 11' \
    'AIRY_EPERM 12'; do
    set -- ${pair}
    have "define $1[[:space:]]+$2" "${UAPI}/error.h" \
        "内核 UAPI 错误码 $1 不是 $2"
done
have 'define SHM_SYS_SCHED[[:space:]]+550' "${SHM}/shm_kern.c" \
    "shim 调度入口未对齐 550"
have 'define SHM_OP_POLICY[[:space:]]+1$' "${SHM}/shm_kern.c" \
    "shim 策略操作码未对齐 SET_POLICY"
endgroup

# ─── 6. 打包与流水线接线 ─────────────────────────────────────────────
group "shim-runtime: 安装接线"
SPEC="${ROOT}/distro/specs/airymaxos-system.spec"
FLOW="${ROOT}/.github/workflows/cloudnative-verify.yml"
BUDGET="${ROOT}/ci/baseline/workflow-budgets.txt"
for pattern in \
    'target airy-crdctl airy-shim' \
    'cloudnative-build/shim/airy-shim' \
    '%\{_sbindir\}/airy-shim$'; do
    have "${pattern}" "${SPEC}" "shim-runtime: spec 缺少 ${pattern}"
done
have 'shim-runtime\.sh' "${FLOW}" "shim-runtime: workflow 未监听门禁"
have '^  shim:' "${FLOW}" "shim-runtime: workflow 未新增 shim 作业"
have 'ci/ci\.sh shim-runtime' "${FLOW}" "shim-runtime: workflow 未调用门禁"
have '^cloudnative-verify\.yml[[:space:]]+0[[:space:]]+[0-9]+$' "${BUDGET}" \
    "shim-runtime: 预算未登记 cloudnative-verify"
endgroup

# ─── 7. 源码树卫生 ───────────────────────────────────────────────────
group "shim-runtime: 源码树卫生"
leftover="$(find "${CN}" \
    \( -name CMakeCache.txt -o -name CMakeFiles -o -name '*.o' \
    -o -name '*.a' -o -name shm_test -o -name airy-shim \) \
    -print 2>/dev/null)"
[ -z "${leftover}" ] || bad "源码树残留构建产物: ${leftover}"
for src in "${SHM}"/*.c "${SHM}"/*.h "${SHM}"/tests/*.c; do
    deny '(^|[^A-Za-z])(TODO|FIXME|stub)([^A-Za-z]|$)' "${src}" \
        "残留占位标记: ${src}"
    deny '/(home|Users)/' "${src}" "出现本地绝对路径: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "shim-runtime 门禁失败 ${FAILS} 项"
say "shim-runtime 门禁通过"
