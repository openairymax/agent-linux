#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/cognition-flow.sh — 认知数据流三相循环（P-27 / CA-056）的
# 模式分派、规划守卫、补偿重试、事件账本与记忆落盘门禁
#
# 用法:
#   ci/gates/cognition-flow.sh
#
# 断言链：
#   1. 源码、测试、夹具与文档齐全并带 SPDX；cf_api.h 经 td_api.h 取
#      [SC] 契约，不复制推理模式与定点常量。
#   2. 出树 cmake 构建零警告（-Werror），ctest 全绿。
#   3. FAST 一节点成功，账本记 done，L1 记忆追加一行。
#   4. SLOW 三节点成功，账本顺序含 plan/exec/feedback/memory。
#   5. 守卫拒绝退 6、深度超限退 7、无备用规划的执行失败退 8。
#   6. 失败后有备用规划则补偿并重试，attempt=2 且退 0。
#   7. 规划缺失或无效退 5；状态目录不可写退 4；参数错误退 2。
#   8. status 计数与账本一致；意图原文不落账本。
#   9. spec 源码束、构建、安装与流水线作业、预算接线成立。
#  10. 源码树无构建残留、占位标记与本地绝对路径。
#
# 退出码: 0 通过；1 判定失败；5 前置缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../lib/verify.sh
. "${ROOT}/ci/lib/verify.sh"

COG="${ROOT}/cognition"
FLOW="${COG}/flow"
FIX="${FLOW}/tests/fixtures"
UAPI="${ROOT}/kernel/include/uapi/linux/airymax/cognition_types.h"

need_cmd cmake ctest gcc make

airy_workdir WORK
BUILD="${WORK}/build"
BIN="${BUILD}/airy-cogflow"
RUN="${WORK}/run"
mkdir -p "${RUN}"

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "cognition-flow: 布局"
for name in flow/cf_api.h flow/cf_plan.c flow/cf_event.c \
    flow/cf_loop.c flow/cf_main.c flow/CMakeLists.txt \
    flow/tests/cf_test.c flow/tests/fixtures/plan.yaml \
    flow/tests/fixtures/plan_fail.yaml \
    flow/tests/fixtures/plan_replan.yaml \
    flow/tests/fixtures/plan_deep.yaml \
    flow/tests/fixtures/plan_denied.yaml \
    Documentation/cognition-flow.md; do
    [ -f "${COG}/${name}" ] || bad "缺少 cognition/${name}"
done
for src in "${FLOW}"/*.c "${FLOW}"/*.h "${FLOW}"/tests/*.c; do
    have '^/\* SPDX-License-Identifier: AGPL-3\.0-or-later OR Apache-2\.0 \*/$' \
        "${src}" "缺少 SPDX: ${src}"
done
have '^# SPDX-License-Identifier: AGPL-3\.0-or-later OR Apache-2\.0$' \
    "${FLOW}/CMakeLists.txt" "缺少 SPDX: ${FLOW}/CMakeLists.txt"
endgroup

# ─── 2. 契约同源 ─────────────────────────────────────────────────────
group "cognition-flow: 契约同源"
have '^#include "td_api\.h"$' "${FLOW}/cf_api.h" \
    "cf_api.h 未复用 Thinkdual 的决策契约"
have '^#include <linux/airymax/cognition_types\.h>$' \
    "${COG}/thinkdual/td_api.h" "td_api.h 未引用 [SC] 契约头"
have 'AIRY_THINK_SLOW' "${UAPI}" "内核 UAPI 未定义 SLOW 模式"
deny 'define[[:space:]]+AIRY_THINK_(FAST|SLOW|MODE_MAX)' "${FLOW}/cf_api.h" \
    "cf_api.h 复制了推理模式枚举"
deny 'define[[:space:]]+AIRY_Q16_(ONE|HALF)' "${FLOW}/cf_api.h" \
    "cf_api.h 复制了 Q16.16 常量"
endgroup

# ─── 3. 出树构建与测试 ───────────────────────────────────────────────
built=0
group "cognition-flow: 出树构建与测试"
airy_build_test "${FLOW}" "${BUILD}" cogflow && built=1
endgroup

# ─── 4. FAST 路径 ────────────────────────────────────────────────────
group "cognition-flow: FAST 路径"
if [ "${built}" -eq 1 ]; then
    D="${RUN}/fast"
    rc=0
    out="$("${BIN}" run --state "${D}" --task demo 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "FAST 退出码 ${rc}，期望 0"
    have ' mode=fast ' <(printf '%s\n' "${out}") "空意图未走 FAST"
    have ' nodes=1 ' <(printf '%s\n' "${out}") "FAST 节点数不符"
    have ' result=ok events=' <(printf '%s\n' "${out}") "FAST 未成功收尾"
    have '"type":"done"' "${D}/events.ndjson" "账本未记 done"
    have '"node":"respond"' "${D}/events.ndjson" "FAST 未执行 respond"
    have '"layer":"l1"' "${D}/memory.ndjson" "L1 记忆未落盘"
fi
endgroup

# ─── 5. SLOW 路径 ────────────────────────────────────────────────────
group "cognition-flow: SLOW 路径"
if [ "${built}" -eq 1 ]; then
    D="${RUN}/slow"
    intent="$(printf 'M%.0s' $(seq 1 50))"
    rc=0
    out="$("${BIN}" run --state "${D}" --plan "${FIX}/plan.yaml" \
        --risk high --intent "${intent}" --task demo 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "SLOW 退出码 ${rc}，期望 0"
    have ' mode=slow ' <(printf '%s\n' "${out}") "高风险未走 SLOW"
    have ' nodes=3 ' <(printf '%s\n' "${out}") "SLOW 节点数不符"
    have ' result=ok events=' <(printf '%s\n' "${out}") "SLOW 未成功收尾"
    for pattern in '"type":"plan"' '"node":"transform"' \
        '"type":"feedback"' '"type":"memory"'; do
        have "${pattern}" "${D}/events.ndjson" "账本缺少 ${pattern}"
    done
    deny '"type":"compensate"' "${D}/events.ndjson" "成功路径不应补偿"
    deny 'MMMM' "${D}/events.ndjson" "账本落了意图原文"
fi
endgroup

# ─── 6. 守卫、深度与执行失败 ─────────────────────────────────────────
group "cognition-flow: 守卫与失败归因"
if [ "${built}" -eq 1 ]; then
    D="${RUN}/deny"
    rc=0
    out="$("${BIN}" run --state "${D}" --plan "${FIX}/plan_denied.yaml" \
        --risk high --task demo 2>&1)" || rc=$?
    [ "${rc}" -eq 6 ] || bad "守卫拒绝退出码 ${rc}，期望 6"
    have ' result=permission events=' <(printf '%s\n' "${out}") \
        "守卫拒绝归因不符"
    have '"node":"prune"' "${D}/events.ndjson" "未记拒绝节点"
    have '"result":"deny"' "${D}/events.ndjson" "未记守卫结论"

    D="${RUN}/deep"
    rc=0
    out="$("${BIN}" run --state "${D}" --plan "${FIX}/plan_deep.yaml" \
        --risk high --task demo 2>&1)" || rc=$?
    [ "${rc}" -eq 7 ] || bad "深度超限退出码 ${rc}，期望 7"
    have ' result=depth events=' <(printf '%s\n' "${out}") "深度归因不符"

    D="${RUN}/abort"
    rc=0
    out="$("${BIN}" run --state "${D}" --plan "${FIX}/plan_fail.yaml" \
        --risk high --task demo 2>&1)" || rc=$?
    [ "${rc}" -eq 8 ] || bad "执行失败退出码 ${rc}，期望 8"
    have ' result=retry-exhausted events=' <(printf '%s\n' "${out}") \
        "执行失败归因不符"
    have '"type":"compensate"' "${D}/events.ndjson" "失败未补偿已完成节点"
fi
endgroup

# ─── 7. 补偿后重试 ───────────────────────────────────────────────────
group "cognition-flow: 补偿后重试"
if [ "${built}" -eq 1 ]; then
    D="${RUN}/retry"
    rc=0
    out="$("${BIN}" run --state "${D}" --plan "${FIX}/plan_fail.yaml" \
        --replan "${FIX}/plan_replan.yaml" --risk high --task demo 2>&1)" \
        || rc=$?
    [ "${rc}" -eq 0 ] || bad "重试退出码 ${rc}，期望 0"
    have ' nodes=2 ' <(printf '%s\n' "${out}") "重试节点数不符"
    have ' result=ok events=' <(printf '%s\n' "${out}") "重试未成功收尾"
    for pattern in '"type":"compensate"' '"type":"replan"' '"attempt":2'; do
        have "${pattern}" "${D}/events.ndjson" "账本缺少 ${pattern}"
    done
fi
endgroup

# ─── 8. 计数、规划与退出码 ───────────────────────────────────────────
group "cognition-flow: 计数与退出码"
if [ "${built}" -eq 1 ]; then
    D="${RUN}/count"
    "${BIN}" run --state "${D}" --task demo >/dev/null 2>&1
    "${BIN}" run --state "${D}" --plan "${FIX}/plan.yaml" \
        --risk high --task demo >/dev/null 2>&1
    "${BIN}" run --state "${D}" --plan "${FIX}/plan_fail.yaml" \
        --risk high --task demo >/dev/null 2>&1
    rc=0
    out="$("${BIN}" status --state "${D}" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "status 退出码 ${rc}，期望 0"
    have '^flows=3 fast=1 slow=2 nodes=6 repairs=1 aborts=1 last=aborted$' \
        <(printf '%s\n' "${out}") "计数未按运行累计"

    rc=0
    out="$("${BIN}" status --state "${RUN}/absent" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "缺状态退出码 ${rc}，期望 0"
    have '^flows=0 fast=0 slow=0 nodes=0 repairs=0 aborts=0 last=none$' \
        <(printf '%s\n' "${out}") "缺状态未回零"

    rc=0
    "${BIN}" run --state "${RUN}/noplan" --risk high \
        >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 5 ] || bad "SLOW 缺规划退出码 ${rc}，期望 5"

    rc=0
    "${BIN}" run --state "${RUN}/noplan" --risk high \
        --plan "${RUN}/absent.yaml" >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 5 ] || bad "规划缺失退出码 ${rc}，期望 5"

    rc=0
    "${BIN}" run --bogus >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 2 ] || bad "未知选项退出码 ${rc}，期望 2"

    touch "${RUN}/plain"
    rc=0
    "${BIN}" run --state "${RUN}/plain/sub" --task demo \
        >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 4 ] || bad "状态不可写退出码 ${rc}，期望 4"
fi
endgroup

# ─── 9. 打包与流水线接线 ─────────────────────────────────────────────
group "cognition-flow: 接线"
SPEC="${ROOT}/distro/specs/airymaxos-system.spec"
FLOWYML="${ROOT}/.github/workflows/cognition-verify.yml"
BUDGET="${ROOT}/ci/baseline/workflow-budgets.txt"
for pattern in \
    '^# spec-lint: source-bundle \.\./\.\./cognition$' \
    '^Source109:[[:space:]]+cognition-%\{version\}\.tar\.gz$' \
    'cmake -S cognition/flow -B cognition-flow-build' \
    'cognition-flow-build/airy-cogflow' \
    'test -x %\{buildroot\}%\{_sbindir\}/airy-cogflow' \
    '%\{_sbindir\}/airy-cogflow$'; do
    have "${pattern}" "${SPEC}" "spec 缺少 ${pattern}"
done
have 'cognition-flow\.sh' "${FLOWYML}" "workflow 未监听门禁"
have '^  cogflow:' "${FLOWYML}" "workflow 未新增 cogflow 作业"
have 'ci/ci\.sh cognition-flow' "${FLOWYML}" "workflow 未调用门禁"
have '^cognition-verify\.yml[[:space:]]+0[[:space:]]+[0-9]+$' "${BUDGET}" \
    "预算未登记 cognition-verify"
endgroup

# ─── 10. 源码树卫生 ──────────────────────────────────────────────────
group "cognition-flow: 源码树卫生"
leftover="$(find "${FLOW}" \
    \( -name CMakeCache.txt -o -name CMakeFiles -o -name '*.o' \
    -o -name '*.a' -o -name cf_test -o -name airy-cogflow \) \
    -print 2>/dev/null)"
[ -z "${leftover}" ] || bad "源码树残留构建产物: ${leftover}"
for src in "${FLOW}"/*.c "${FLOW}"/*.h "${FLOW}"/tests/*.c; do
    deny '(^|[^A-Za-z])(TODO|FIXME|stub)([^A-Za-z]|$)' "${src}" \
        "残留占位标记: ${src}"
    deny '/(home|Users)/' "${src}" "出现本地绝对路径: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "cognition-flow 门禁失败 ${FAILS} 项"
say "cognition-flow 门禁通过"
