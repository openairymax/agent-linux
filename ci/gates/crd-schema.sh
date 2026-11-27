#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/crd-schema.sh — 云原生 CRD 清单、校验与规划（P-22 / CA-012、
# CA-085）的库、CLI、清单与打包接线门禁
#
# 用法:
#   ci/gates/crd-schema.sh
#
# 断言链：
#   1. JSON 读取件、模式表、CLI、测试与清单齐全，源码带 SPDX。
#   2. 出树 cmake 构建零警告（-Werror），ctest 全绿。
#   3. CLI 合法输入退 0 并给出规整摘要；用法错退 2、坏输入退 3。
#   4. 四类 CRD 清单的 apiVersion、kind、group、storage 与名字一致。
#   5. spec 与 workflow 接线成立，预算登记为 2 个作业。
#   6. cloudnative 源码树无构建残留、无占位标记与本地绝对路径。
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
CRD="${CN}/crd"
FIXTURE="${CRD}/tests/fixtures/agent.json"

need_cmd cmake ctest gcc make

airy_workdir WORK
BUILD="${WORK}/build"
BIN="${BUILD}/crd/airy-crdctl"

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "crd-schema: 布局"
for name in lib/cn_json.h lib/cn_json.c lib/CMakeLists.txt \
    crd/crd_api.h crd/crd_schema.c crd/crd_units.c crd/crd_plan.c \
    crd/crd_state.c crd/crd_main.c crd/CMakeLists.txt \
    crd/tests/crd_test.c crd/tests/fixtures/agent.json \
    crd/manifests/crd-agent.yaml crd/manifests/crd-agentworkflow.yaml \
    crd/manifests/crd-agentbudget.yaml \
    crd/manifests/crd-memoryrovolclaim.yaml \
    crd/examples/agent-cognition.yaml Documentation/crd.md; do
    [ -f "${CN}/${name}" ] || bad "缺少 cloudnative/${name}"
done
have '^add_subdirectory\(lib\)$' "${CN}/CMakeLists.txt" \
    "cloudnative 未接入 lib 子目录"
have '^add_subdirectory\(crd\)$' "${CN}/CMakeLists.txt" \
    "cloudnative 未接入 crd 子目录"
have '^option\(BUILD_TESTING ' "${CN}/CMakeLists.txt" \
    "cloudnative 未提供 BUILD_TESTING 开关"
for src in "${CN}"/lib/*.c "${CN}"/lib/*.h "${CRD}"/*.c "${CRD}"/*.h \
    "${CRD}"/tests/*.c; do
    have '^/\* SPDX-License-Identifier: GPL-2\.0-only \*/$' "${src}" \
        "缺少 GPL-2.0-only SPDX: ${src}"
done
for src in "${CN}/CMakeLists.txt" "${CN}/lib/CMakeLists.txt" \
    "${CRD}/CMakeLists.txt" "${CRD}"/manifests/*.yaml \
    "${CRD}"/examples/*.yaml; do
    have '^# SPDX-License-Identifier: GPL-2\.0-only$' "${src}" \
        "缺少 GPL-2.0-only SPDX: ${src}"
done
endgroup

# ─── 2. 出树构建与测试 ───────────────────────────────────────────────
built=0
group "crd-schema: 出树构建与测试"
airy_build_test "${CN}" "${BUILD}" cloudnative && built=1
endgroup

# ─── 3. CLI 行为 ─────────────────────────────────────────────────────
group "crd-schema: CLI"
if [ "${built}" -eq 1 ]; then
    rc=0
    out="$("${BIN}" validate "${FIXTURE}" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "validate 合法输入退出码 ${rc}，期望 0"
    have '^ok kind=Agent name=cognition-agent-01 ns=production$' \
        <(printf '%s\n' "${out}") "validate 摘要不符"

    rc=0
    out="$("${BIN}" plan "${FIXTURE}" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "plan 合法输入退出码 ${rc}，期望 0"
    have '^plan summary actions=5$' <(printf '%s\n' "${out}") \
        "plan 未给出五步摘要"
    have '^plan admit cpu_milli=' <(printf '%s\n' "${out}") \
        "plan 缺少资源准入"
    have '^plan register image=' <(printf '%s\n' "${out}") \
        "plan 缺少注册步骤"

    printf '{}\n' >"${WORK}/bad.json"
    rc=0
    "${BIN}" validate "${WORK}/bad.json" >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 3 ] || bad "validate 坏输入退出码 ${rc}，期望 3"

    rc=0
    "${BIN}" >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 2 ] || bad "用法错误退出码 ${rc}，期望 2"

    rc=0
    out="$("${BIN}" state Ready run 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "state 合法转移退出码 ${rc}，期望 0"
    have '^state Running$' <(printf '%s\n' "${out}") "state 转移不符"

    rc=0
    "${BIN}" state Dead stop >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 3 ] || bad "state 非法转移退出码 ${rc}，期望 3"
fi
endgroup

# ─── 4. 清单一致性 ───────────────────────────────────────────────────
group "crd-schema: 清单"
for src in "${CRD}"/manifests/*.yaml; do
    have '^apiVersion: apiextensions\.k8s\.io/v1$' "${src}" \
        "${src}: apiVersion 不符"
    have '^kind: CustomResourceDefinition$' "${src}" \
        "${src}: kind 不符"
    have '^[[:space:]]+group: agent\.airymaxos\.dev$' "${src}" \
        "${src}: group 不符"
    have '^[[:space:]]+storage: true$' "${src}" \
        "${src}: 未声明 storage 版本"
    have '\.agent\.airymaxos\.dev$' "${src}" \
        "${src}: 名字未落在 agent.airymaxos.dev"
done
endgroup

# ─── 5. 打包与流水线接线 ─────────────────────────────────────────────
group "crd-schema: 安装接线"
SPEC="${ROOT}/distro/specs/airymaxos-system.spec"
FLOW="${ROOT}/.github/workflows/cloudnative-verify.yml"
BUDGET="${ROOT}/ci/baseline/workflow-budgets.txt"
for pattern in \
    '^Source108:[[:space:]]+cloudnative-%\{version\}\.tar\.gz$' \
    '^# spec-lint: source-bundle \.\./\.\./cloudnative$' \
    'tar -xzf %\{SOURCE108\}' \
    'cmake -S cloudnative -B cloudnative-build' \
    'cloudnative-build/crd/airy-crdctl' \
    '%\{_sbindir\}/airy-crdctl' \
    '%\{_datadir\}/airymax/crd'; do
    have "${pattern}" "${SPEC}" "crd-schema: spec 缺少 ${pattern}"
done
have 'crd-schema\.sh' "${FLOW}" "crd-schema: workflow 未监听门禁"
have '^  crd:' "${FLOW}" "crd-schema: workflow 未新增 crd 作业"
have 'ci/ci\.sh crd-schema' "${FLOW}" "crd-schema: workflow 未调用门禁"
have '^cloudnative-verify\.yml[[:space:]]+0[[:space:]]+[0-9]+$' \
    "${BUDGET}" "crd-schema: 预算未登记 cloudnative-verify"
endgroup

# ─── 6. 源码树卫生 ───────────────────────────────────────────────────
group "crd-schema: 源码树卫生"
leftover="$(find "${CN}" \
    \( -name CMakeCache.txt -o -name CMakeFiles -o -name '*.o' \
    -o -name '*.a' -o -name crd_test -o -name airy-crdctl \) \
    -print 2>/dev/null)"
[ -z "${leftover}" ] || bad "源码树残留构建产物: ${leftover}"
[ ! -e "${CN}/build" ] || bad "源码树残留构建目录: ${CN}/build"
for src in "${CN}"/lib/*.c "${CN}"/lib/*.h "${CRD}"/*.c "${CRD}"/*.h \
    "${CRD}"/tests/*.c; do
    deny '(^|[^A-Za-z])(TODO|FIXME|stub)([^A-Za-z]|$)' "${src}" \
        "残留占位标记: ${src}"
    deny '/(home|Users)/' "${src}" "出现本地绝对路径: ${src}"
done
deny 'airy\.os/v1' "${CN}/Documentation/crd.md" \
    "文档残留旧 API 组"
endgroup

[ "${FAILS}" -eq 0 ] || die "crd-schema 门禁失败 ${FAILS} 项"
say "crd-schema 门禁通过"
