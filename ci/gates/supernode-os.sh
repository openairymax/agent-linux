#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/supernode-os.sh — 超节点 OS（P-25 / CA-088）的拓扑发现、
# 令牌池、迁移准入、内核桥降级与快照恢复门禁
#
# 用法:
#   ci/gates/supernode-os.sh
#
# 断言链：
#   1. 源码、测试、夹具与文档齐全并带 SPDX；cloudnative 已接入
#      supernode 子目录。
#   2. 出树 cmake 构建零警告（-Werror），ctest 全绿。
#   3. discover 的节点、芯片、核数、内存、CXL 池与距离逐项符合夹具。
#   4. affinity 的加权得分与建议节点符合公式。
#   5. 令牌池初始化、分配、借用与状态余量正确；超额退 4，可借不足退 6。
#   6. 迁移准入缺能力、未知策略、同源同目的均退 6；内核不支持时退 7
#      并记 rolled-back，目的余量不足记 budget。
#   7. 快照生成与恢复成立；硬件摘要不符与令牌池不符均退 3。
#   8. 549 号调用与 0x03 操作码与内核 UAPI 一致；spec、作业与预算接线。
#   9. 源码树无构建残留、占位标记与本地绝对路径。
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
SN="${CN}/supernode"
FIX="${SN}/tests/fixtures"

need_cmd cmake ctest gcc make

airy_workdir WORK
BUILD="${WORK}/build"
BIN="${BUILD}/supernode/airy-supernode"
RUN="${WORK}/run"
ST="${RUN}/state"

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "supernode-os: 布局"
for name in supernode/sn_api.h supernode/sn_topology.c supernode/sn_pool.c \
    supernode/sn_migrate.c supernode/sn_snap.c supernode/sn_main.c \
    supernode/CMakeLists.txt supernode/tests/sn_test.c \
    supernode/tests/fixtures/trace.txt \
    supernode/tests/fixtures/sys/devices/system/node/node0/meminfo \
    supernode/tests/fixtures/sys/devices/system/node/node1/meminfo \
    supernode/tests/fixtures/sys/devices/system/node/node0/cpulist \
    supernode/tests/fixtures/sys/devices/system/node/node1/cpulist \
    supernode/tests/fixtures/sys/devices/system/node/node0/distance \
    supernode/tests/fixtures/sys/devices/system/node/node1/distance \
    supernode/tests/fixtures/sys/devices/system/cpu/cpu0/topology/physical_package_id \
    supernode/tests/fixtures/sys/devices/system/cpu/cpu5/topology/physical_package_id \
    supernode/tests/fixtures/sys/bus/cxl/devices/mem0/type \
    Documentation/supernode.md; do
    [ -f "${CN}/${name}" ] || bad "缺少 cloudnative/${name}"
done
have '^add_subdirectory\(supernode\)$' "${CN}/CMakeLists.txt" \
    "cloudnative 未接入 supernode 子目录"
for src in "${SN}"/*.c "${SN}"/*.h "${SN}"/tests/*.c; do
    have '^/\* SPDX-License-Identifier: GPL-2\.0-only \*/$' "${src}" \
        "缺少 GPL-2.0-only SPDX: ${src}"
done
have '^# SPDX-License-Identifier: GPL-2\.0-only$' "${SN}/CMakeLists.txt" \
    "缺少 GPL-2.0-only SPDX: ${SN}/CMakeLists.txt"
endgroup

# ─── 2. 出树构建与测试 ───────────────────────────────────────────────
built=0
group "supernode-os: 出树构建与测试"
airy_build_test "${CN}" "${BUILD}" cloudnative && built=1
endgroup

# ─── 3. 拓扑发现 ─────────────────────────────────────────────────────
group "supernode-os: 拓扑发现"
if [ "${built}" -eq 1 ]; then
    rc=0
    out="$("${BIN}" discover --root "${FIX}/sys" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "discover 退出码 ${rc}，期望 0"
    have '^\{"supernode":0,"dies":2,"chips":2,"cores":6,' \
        <(printf '%s\n' "${out}") "拓扑汇总不符"
    have '"memoryKiB":98304,"cxlPools":1,"die":\[' <(printf '%s\n' "${out}") \
        "内存或 CXL 池数量不符"
    have '\{"id":0,"chip":0,"cores":3,"memoryKiB":65536,"distance":\[10,21\]\}' \
        <(printf '%s\n' "${out}") "节点 0 拓扑不符"
    have '\{"id":1,"chip":1,"cores":3,"memoryKiB":32768,"distance":\[21,10\]\}' \
        <(printf '%s\n' "${out}") "节点 1 拓扑不符"

    rc=0
    "${BIN}" discover --root "${WORK}/absent" >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 3 ] || bad "缺拓扑退出码 ${rc}，期望 3"
fi
endgroup

# ─── 4. 亲和度 ───────────────────────────────────────────────────────
group "supernode-os: 亲和度"
if [ "${built}" -eq 1 ]; then
    rc=0
    out="$("${BIN}" affinity --root "${FIX}/sys" --trace "${FIX}/trace.txt" \
        --agent 3 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "affinity 退出码 ${rc}，期望 0"
    have '^agent=3$' <(printf '%s\n' "${out}") "亲和度缺 Agent 标识"
    have '^node=0 score=528571$' <(printf '%s\n' "${out}") \
        "节点 0 加权得分不符"
    have '^node=1 score=947619$' <(printf '%s\n' "${out}") \
        "节点 1 加权得分不符"
    have '^recommend=1$' <(printf '%s\n' "${out}") "建议节点不符"
fi
endgroup

# ─── 5. 令牌池 ───────────────────────────────────────────────────────
group "supernode-os: 令牌池"
if [ "${built}" -eq 1 ]; then
    mkdir -p "${ST}"

    rc=0
    out="$("${BIN}" pool init --state "${ST}" --total 1000 --reserve 100 \
        --dies 2 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "池初始化退出码 ${rc}，期望 0"
    have '^pool=initialized dies=2 total=1000 reserve=100$' \
        <(printf '%s\n' "${out}") "池初始化输出不符"

    rc=0
    out="$("${BIN}" pool alloc --state "${ST}" --die 0 --amount 100 2>&1)" \
        || rc=$?
    [ "${rc}" -eq 0 ] || bad "池分配退出码 ${rc}，期望 0"
    have '^die=0 used=100 free=350 remaining=900 level=ok$' \
        <(printf '%s\n' "${out}") "池分配输出不符"

    rc=0
    out="$("${BIN}" pool borrow --state "${ST}" --src 1 --dst 0 \
        --amount 50 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "池借用退出码 ${rc}，期望 0"
    have '^borrow=ok src=1 dst=0 amount=50 remaining=900$' \
        <(printf '%s\n' "${out}") "池借用输出不符"

    rc=0
    out="$("${BIN}" pool status --state "${ST}" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "池状态退出码 ${rc}，期望 0"
    have '^total=1000 reserve=100 distributed=900 remaining=900$' \
        <(printf '%s\n' "${out}") "池总量不符"
    have '^die=0 budget=500 used=100 free=400$' <(printf '%s\n' "${out}") \
        "芯片 0 配额不符"
    have '^die=1 budget=400 used=0 free=400$' <(printf '%s\n' "${out}") \
        "芯片 1 配额不符"

    rc=0
    "${BIN}" pool alloc --state "${ST}" --die 0 --amount 10000 \
        >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 4 ] || bad "超额分配退出码 ${rc}，期望 4"

    rc=0
    "${BIN}" pool borrow --state "${ST}" --src 0 --dst 1 --amount 10000 \
        >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 6 ] || bad "可借不足退出码 ${rc}，期望 6"

    rc=0
    "${BIN}" pool status --state "${WORK}/absent" >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 3 ] || bad "缺池状态退出码 ${rc}，期望 3"
fi
endgroup

# ─── 6. 迁移准入与内核桥 ─────────────────────────────────────────────
group "supernode-os: 迁移准入"
if [ "${built}" -eq 1 ]; then
    rc=0
    out="$("${BIN}" migrate --state "${ST}" --agent 1 --src 0 --dst 1 2>&1)" \
        || rc=$?
    [ "${rc}" -eq 6 ] || bad "缺能力令牌退出码 ${rc}，期望 6"
    have '^reason=capability$' <(printf '%s\n' "${out}") "缺能力原因不符"

    rc=0
    out="$("${BIN}" migrate --state "${ST}" --agent 1 --src 0 --dst 1 \
        --strategy bogus --cap 2>&1)" || rc=$?
    [ "${rc}" -eq 6 ] || bad "未知策略退出码 ${rc}，期望 6"
    have '^reason=strategy$' <(printf '%s\n' "${out}") "策略拒绝原因不符"

    rc=0
    out="$("${BIN}" migrate --state "${ST}" --agent 1 --src 0 --dst 0 \
        --cap 2>&1)" || rc=$?
    [ "${rc}" -eq 6 ] || bad "同源同目的退出码 ${rc}，期望 6"
    have '^reason=range$' <(printf '%s\n' "${out}") "范围拒绝原因不符"

    rc=0
    out="$("${BIN}" migrate --state "${ST}" --agent 1 --src 0 --dst 1 \
        --cap 2>&1)" || rc=$?
    # 托管 runner 无 549 后端，复核的是降级语义；有 M5 后端的内核走成功
    # 分支，两种结论都必须自洽：未发生搬运不得记 active。
    case "${rc}" in
        0)
            have '^kernel=applied$' <(printf '%s\n' "${out}") \
                "内核应用标记不符"
            have '^state=active$' <(printf '%s\n' "${out}") \
                "搬运成功后状态不符"
            have '"state":"active"' "${ST}/migrate.json" \
                "迁移审计状态不符"
            ;;
        7)
            have '^kernel=unsupported$' <(printf '%s\n' "${out}") \
                "内核不支持标记不符"
            have '^state=rolled-back$' <(printf '%s\n' "${out}") \
                "未搬运不得记完成"
            have '"state":"rolled-back"' "${ST}/migrate.json" \
                "迁移审计状态不符"
            have '"kernel":"unsupported"' "${ST}/migrate.json" \
                "迁移审计内核标记不符"
            ;;
        6)
            have '^kernel=denied$' <(printf '%s\n' "${out}") \
                "内核拒绝标记不符"
            have '^state=rejected$' <(printf '%s\n' "${out}") \
                "内核拒绝状态不符"
            ;;
        *)
            bad "内核桥退出码 ${rc}，期望 0／6／7"
            ;;
    esac

    "${BIN}" pool alloc --state "${ST}" --die 1 --amount 400 >/dev/null 2>&1
    rc=0
    "${BIN}" migrate --state "${ST}" --agent 1 --src 0 --dst 1 --strategy cold \
        --cap >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 6 ] || bad "目的余量不足退出码 ${rc}，期望 6"
    have '"reason":"budget"' "${ST}/migrate.json" "预算拒绝原因不符"
fi
endgroup

# ─── 7. 快照与恢复 ───────────────────────────────────────────────────
group "supernode-os: 快照恢复"
if [ "${built}" -eq 1 ]; then
    SNAP="${RUN}/snap.json"
    rc=0
    out="$("${BIN}" snapshot --root "${FIX}/sys" --state "${ST}" \
        --output "${SNAP}" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "快照退出码 ${rc}，期望 0"
    have '^snapshot=ok dies=2 cores=6 pools=1 file=' \
        <(printf '%s\n' "${out}") "快照输出不符"
    have '^\{"version":1,"dies":2,"chips":2,"cores":6,"memoryKiB":98304,' \
        "${SNAP}" "快照摘要不符"

    rc=0
    out="$("${BIN}" restore --root "${FIX}/sys" --state "${ST}" \
        --input "${SNAP}" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "恢复退出码 ${rc}，期望 0"
    have '^restore=ok dies=2 cores=6 remaining=' <(printf '%s\n' "${out}") \
        "恢复输出不符"

    printf '%s\n' '{"version":1,"dies":3,"chips":2,"cores":6,"memoryKiB":98304,"cxlPools":1,"pool":{"total":1000,"reserve":100,"dies":[{"budget":450,"used":0},{"budget":450,"used":0}]}}' \
        >"${RUN}/bad-hw.json"
    rc=0
    out="$("${BIN}" restore --root "${FIX}/sys" --state "${ST}" \
        --input "${RUN}/bad-hw.json" 2>&1)" || rc=$?
    [ "${rc}" -eq 3 ] || bad "硬件不符退出码 ${rc}，期望 3"
    have '^restore=bad reason=hardware$' <(printf '%s\n' "${out}") \
        "硬件不符未拒绝"

    printf '%s\n' '{"version":1,"dies":2,"chips":2,"cores":6,"memoryKiB":98304,"cxlPools":1,"pool":{"total":1000}}' \
        >"${RUN}/bad-pool.json"
    rc=0
    out="$("${BIN}" restore --root "${FIX}/sys" --state "${ST}" \
        --input "${RUN}/bad-pool.json" 2>&1)" || rc=$?
    [ "${rc}" -eq 3 ] || bad "坏池退出码 ${rc}，期望 3"
    have '^restore=bad reason=pool$' <(printf '%s\n' "${out}") \
        "坏池未拒绝"
fi
endgroup

# ─── 8. 设计常量与接线 ───────────────────────────────────────────────
group "supernode-os: 设计基线"
have 'define SN_SYS_ROVOL_CTL[[:space:]]+549' "${SN}/sn_migrate.c" \
    "迁移桥未用 549 号调用"
have 'define SN_ROVOL_MIGRATE[[:space:]]+0x03' "${SN}/sn_migrate.c" \
    "迁移操作码不是 0x03"
have 'AIRY_SYS_ROVOL_CTL[[:space:]]+549' \
    "${ROOT}/kernel/include/uapi/linux/airymax/syscalls.h" \
    "内核 UAPI 未登记 549 号调用"
have 'AIRY_ROVOL_MIGRATE[[:space:]]*=[[:space:]]*0x03' \
    "${ROOT}/kernel/include/uapi/linux/airymax/memory_types.h" \
    "内核 UAPI 未登记迁移操作码"
have 'define SN_MAX_DIES[[:space:]]+16' "${SN}/sn_api.h" "die 上限不是 16"
for code in 'SN_EXIT_POOL[[:space:]]+4' 'SN_EXIT_DENY[[:space:]]+6' \
    'SN_EXIT_KERN[[:space:]]+7'; do
    have "${code}" "${SN}/sn_api.h" "缺退出码 ${code}"
done

SPEC="${ROOT}/distro/specs/airymaxos-system.spec"
FLOW="${ROOT}/.github/workflows/cloudnative-verify.yml"
BUDGET="${ROOT}/ci/baseline/workflow-budgets.txt"
for pattern in \
    'target airy-crdctl airy-shim airy-cni airy-supernode' \
    'cloudnative-build/supernode/airy-supernode' \
    'test -x %\{buildroot\}%\{_sbindir\}/airy-supernode' \
    '%\{_sbindir\}/airy-supernode$'; do
    have "${pattern}" "${SPEC}" "supernode-os: spec 缺少 ${pattern}"
done
have 'supernode-os\.sh' "${FLOW}" "supernode-os: workflow 未监听门禁"
have '^  supernode:' "${FLOW}" "supernode-os: workflow 未新增作业"
have 'ci/ci\.sh supernode-os' "${FLOW}" "supernode-os: workflow 未调用门禁"
have '^cloudnative-verify\.yml[[:space:]]+0[[:space:]]+[0-9]+$' "${BUDGET}" \
    "supernode-os: 预算未登记 cloudnative-verify"
endgroup

# ─── 9. 源码树卫生 ───────────────────────────────────────────────────
group "supernode-os: 源码树卫生"
leftover="$(find "${CN}" \
    \( -name CMakeCache.txt -o -name CMakeFiles -o -name '*.o' \
    -o -name '*.a' -o -name sn_test -o -name airy-supernode \) \
    -print 2>/dev/null)"
[ -z "${leftover}" ] || bad "源码树残留构建产物: ${leftover}"
for src in "${SN}"/*.c "${SN}"/*.h "${SN}"/tests/*.c; do
    deny '(^|[^A-Za-z])(TODO|FIXME|stub)([^A-Za-z]|$)' "${src}" \
        "残留占位标记: ${src}"
    deny '/(home|Users)/' "${src}" "出现本地绝对路径: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "supernode-os 门禁失败 ${FAILS} 项"
say "supernode-os 门禁通过"
