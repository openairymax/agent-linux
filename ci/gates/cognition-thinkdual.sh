#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/cognition-thinkdual.sh — Thinkdual 双系统思考（P-26 / CA-010、
# CA-056）的复杂度评分、模式判定、策略热更新与审计门禁
#
# 用法:
#   ci/gates/cognition-thinkdual.sh
#
# 断言链：
#   1. 源码、测试、夹具与文档齐全并带 SPDX；td_api.h 只引用 [SC] 契约头，
#      不复制枚举与定点常量。
#   2. 出树 cmake 构建零警告（-Werror），ctest 全绿。
#   3. 文档边界输入得 0.315，越过默认阈值 0.300，判定 SLOW 与 200ms。
#   4. policy 逐字段与夹具一致；缺策略文件回退内置默认值。
#   5. decide 追加审计账本并累计计数，status 回读；意图原文不入账本。
#   6. hot_reload 为真时新阈值即时生效，为假时继续用缓存策略。
#   7. 参数错误退 2，策略无效退 3，状态目录不可写退 4。
#   8. spec 源码束、构建、安装与流水线作业、预算接线成立。
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

COG="${ROOT}/cognition"
TD="${COG}/thinkdual"
FIX="${TD}/tests/fixtures"
UAPI="${ROOT}/kernel/include/uapi/linux/airymax/cognition_types.h"

need_cmd cmake ctest gcc make

airy_workdir WORK
BUILD="${WORK}/build"
BIN="${BUILD}/airy-thinkdual"
RUN="${WORK}/run"
mkdir -p "${RUN}"

# ─── 1. 布局与文件头 ─────────────────────────────────────────────────
group "cognition-thinkdual: 布局"
for name in thinkdual/td_api.h thinkdual/td_score.c thinkdual/td_policy.c \
    thinkdual/td_state.c thinkdual/td_main.c thinkdual/CMakeLists.txt \
    thinkdual/tests/td_test.c thinkdual/tests/fixtures/policy.yaml \
    thinkdual/tests/fixtures/policy_bad_key.yaml \
    thinkdual/tests/fixtures/policy_bad_range.yaml \
    Documentation/thinkdual.md; do
    [ -f "${COG}/${name}" ] || bad "缺少 cognition/${name}"
done
for src in "${TD}"/*.c "${TD}"/*.h "${TD}"/tests/*.c; do
    have '^/\* SPDX-License-Identifier: AGPL-3\.0-or-later OR Apache-2\.0 \*/$' \
        "${src}" "缺少 SPDX: ${src}"
done
have '^# SPDX-License-Identifier: AGPL-3\.0-or-later OR Apache-2\.0$' \
    "${TD}/CMakeLists.txt" "缺少 SPDX: ${TD}/CMakeLists.txt"
endgroup

# ─── 2. 契约同源 ─────────────────────────────────────────────────────
group "cognition-thinkdual: 契约同源"
have '^#include <linux/airymax/cognition_types\.h>$' "${TD}/td_api.h" \
    "td_api.h 未引用 [SC] 契约头"
have 'enum airy_think_mode' "${TD}/td_api.h" \
    "td_api.h 未使用 [SC] 推理模式枚举"
have 'AIRY_Q16_ONE[[:space:]]*\(1 << 16\)' "${UAPI}" \
    "内核 UAPI 未定义 Q16.16 的 1.0"
deny 'define[[:space:]]+AIRY_THINK_(FAST|SLOW|MODE_MAX)' "${TD}/td_api.h" \
    "td_api.h 复制了推理模式枚举"
deny 'define[[:space:]]+AIRY_Q16_(ONE|HALF)' "${TD}/td_api.h" \
    "td_api.h 复制了 Q16.16 常量"
endgroup

# ─── 3. 出树构建与测试 ───────────────────────────────────────────────
built=0
group "cognition-thinkdual: 出树构建与测试"
airy_build_test "${TD}" "${BUILD}" thinkdual && built=1
endgroup

# ─── 4. 复杂度评分 ───────────────────────────────────────────────────
group "cognition-thinkdual: 复杂度评分"
if [ "${built}" -eq 1 ]; then
    intent="$(printf 'M%.0s' $(seq 1 50))"

    rc=0
    out="$("${BIN}" score --intent "${intent}" --similarity 0.85 \
        --risk medium --tokens 1000 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "score 退出码 ${rc}，期望 0"
    have '^mode=slow score=0\.315 threshold=0\.300 latency_ms=200 reason=score$' \
        <(printf '%s\n' "${out}") "文档边界输入判定不符"

    rc=0
    out="$("${BIN}" score 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "空输入退出码 ${rc}，期望 0"
    have '^mode=fast score=0\.000 ' <(printf '%s\n' "${out}") \
        "空输入应走 FAST"

    rc=0
    "${BIN}" score --similarity 2 >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 2 ] || bad "越界相似度退出码 ${rc}，期望 2"
fi
endgroup

# ─── 5. 策略面 ───────────────────────────────────────────────────────
group "cognition-thinkdual: 策略面"
if [ "${built}" -eq 1 ]; then
    rc=0
    out="$("${BIN}" policy --policy "${FIX}/policy.yaml" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "policy 退出码 ${rc}，期望 0"
    have '^source=file system1=true system2=true threshold=0\.300 ' \
        <(printf '%s\n' "${out}") "策略来源或阈值不符"
    have 'max_latency_ms=10 cache_ttl_s=300 dag_max_depth=10 ' \
        <(printf '%s\n' "${out}") "System 1 或 DAG 深度不符"
    have 'llm_timeout_ms=200 feedback=true hot_reload=true audit=true ' \
        <(printf '%s\n' "${out}") "System 2 或切换开关不符"
    have 'strategy=score_based$' <(printf '%s\n' "${out}") \
        "策略名不是 score_based"

    rc=0
    out="$("${BIN}" policy --policy "${RUN}/absent.yaml" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "缺策略退出码 ${rc}，期望 0"
    have '^source=defaults ' <(printf '%s\n' "${out}") \
        "缺策略文件未回退默认值"
fi
endgroup

# ─── 6. 切换、审计与计数 ─────────────────────────────────────────────
group "cognition-thinkdual: 切换与审计"
if [ "${built}" -eq 1 ]; then
    D1="${RUN}/state1"
    rc=0
    out="$("${BIN}" decide --policy "${FIX}/policy.yaml" --state "${D1}" \
        --intent "${intent}" --similarity 0.85 --risk medium \
        --tokens 1000 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "decide 退出码 ${rc}，期望 0"
    have '^mode=slow score=0\.315 ' <(printf '%s\n' "${out}") \
        "decide 未按评分切到 SLOW"
    have '^reloaded=no journal=' <(printf '%s\n' "${out}") \
        "首次决策不应标记重新加载"
    [ -f "${D1}/journal.ndjson" ] || bad "decide 未写审计账本"
    have '"intent_len":50' "${D1}/journal.ndjson" "账本未记录意图长度"
    deny 'MMMM' "${D1}/journal.ndjson" "账本落了意图原文"

    rc=0
    "${BIN}" decide --policy "${FIX}/policy.yaml" --state "${D1}" \
        >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 0 ] || bad "第二次 decide 退出码 ${rc}，期望 0"

    rc=0
    out="$("${BIN}" status --state "${D1}" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "status 退出码 ${rc}，期望 0"
    have '^decisions=2 fast=1 slow=1 last=fast$' \
        <(printf '%s\n' "${out}") "计数未累计"

    rc=0
    out="$("${BIN}" status --state "${RUN}/absent" 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "缺状态退出码 ${rc}，期望 0"
    have '^decisions=0 fast=0 slow=0 last=none$' \
        <(printf '%s\n' "${out}") "缺状态未回零"
fi
endgroup

# ─── 7. 策略热更新 ───────────────────────────────────────────────────
group "cognition-thinkdual: 策略热更新"
if [ "${built}" -eq 1 ]; then
    HOT="${RUN}/hot"
    HPOL="${RUN}/hot.yaml"
    sed 's/complexity_threshold: 0.3/complexity_threshold: 0.5/' \
        "${FIX}/policy.yaml" >"${HPOL}"
    rc=0
    out="$("${BIN}" decide --policy "${HPOL}" --state "${HOT}" \
        --intent "${intent}" --similarity 0.85 --risk medium \
        --tokens 1000 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "热更新首轮退出码 ${rc}，期望 0"
    have '^mode=fast ' <(printf '%s\n' "${out}") \
        "0.5 阈值下应走 FAST"
    have '^reloaded=no ' <(printf '%s\n' "${out}") \
        "首轮不应标记重新加载"

    sed -i 's/complexity_threshold: 0.5/complexity_threshold: 0.1/' "${HPOL}"
    rc=0
    out="$("${BIN}" decide --policy "${HPOL}" --state "${HOT}" \
        --intent "${intent}" --similarity 0.85 --risk medium \
        --tokens 1000 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "热更新次轮退出码 ${rc}，期望 0"
    have '^mode=slow ' <(printf '%s\n' "${out}") "热更新未即时生效"
    have '^reloaded=yes ' <(printf '%s\n' "${out}") \
        "热更新未标记重新加载"

    COLD="${RUN}/cold"
    CPOL="${RUN}/cold.yaml"
    sed 's/hot_reload: true/hot_reload: false/;
        s/complexity_threshold: 0.3/complexity_threshold: 0.5/' \
        "${FIX}/policy.yaml" >"${CPOL}"
    rc=0
    out="$("${BIN}" decide --policy "${CPOL}" --state "${COLD}" \
        --intent "${intent}" --similarity 0.85 --risk medium \
        --tokens 1000 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "冷更新首轮退出码 ${rc}，期望 0"
    have '^mode=fast ' <(printf '%s\n' "${out}") \
        "冷更新首轮应在 0.5 阈值下走 FAST"

    sed -i 's/complexity_threshold: 0.5/complexity_threshold: 0.1/' "${CPOL}"
    rc=0
    out="$("${BIN}" decide --policy "${CPOL}" --state "${COLD}" \
        --intent "${intent}" --similarity 0.85 --risk medium \
        --tokens 1000 2>&1)" || rc=$?
    [ "${rc}" -eq 0 ] || bad "冷更新次轮退出码 ${rc}，期望 0"
    have '^mode=fast ' <(printf '%s\n' "${out}") \
        "hot_reload=false 时不应换用新阈值"
    have '^reloaded=no ' <(printf '%s\n' "${out}") \
        "冷更新不应标记重新加载"
fi
endgroup

# ─── 8. 退出码 ───────────────────────────────────────────────────────
group "cognition-thinkdual: 退出码"
if [ "${built}" -eq 1 ]; then
    rc=0
    "${BIN}" decide --bogus >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 2 ] || bad "未知选项退出码 ${rc}，期望 2"

    for bad_cfg in policy_bad_key policy_bad_range; do
        rc=0
        "${BIN}" policy --policy "${FIX}/${bad_cfg}.yaml" \
            >/dev/null 2>&1 || rc=$?
        [ "${rc}" -eq 3 ] || bad "${bad_cfg} 退出码 ${rc}，期望 3"
    done

    touch "${RUN}/plain"
    rc=0
    "${BIN}" decide --policy "${FIX}/policy.yaml" \
        --state "${RUN}/plain/sub" >/dev/null 2>&1 || rc=$?
    [ "${rc}" -eq 4 ] || bad "状态不可写退出码 ${rc}，期望 4"
fi
endgroup

# ─── 9. 打包与流水线接线 ─────────────────────────────────────────────
group "cognition-thinkdual: 接线"
SPEC="${ROOT}/distro/specs/airymaxos-system.spec"
FLOW="${ROOT}/.github/workflows/cognition-verify.yml"
BUDGET="${ROOT}/ci/baseline/workflow-budgets.txt"
for pattern in \
    '^# spec-lint: source-bundle \.\./\.\./cognition$' \
    '^Source109:[[:space:]]+cognition-%\{version\}\.tar\.gz$' \
    'tar -xzf %\{SOURCE109\}' \
    'cmake -S cognition/thinkdual -B cognition-build' \
    'cognition-build/airy-thinkdual' \
    'test -x %\{buildroot\}%\{_sbindir\}/airy-thinkdual' \
    '%\{_sbindir\}/airy-thinkdual$'; do
    have "${pattern}" "${SPEC}" "spec 缺少 ${pattern}"
done
have 'cognition-thinkdual\.sh' "${FLOW}" "workflow 未监听门禁"
have '^  thinkdual:' "${FLOW}" "workflow 未新增 thinkdual 作业"
have 'ci/ci\.sh cognition-thinkdual' "${FLOW}" "workflow 未调用门禁"
have '^cognition-verify\.yml[[:space:]]+0[[:space:]]+2$' "${BUDGET}" \
    "预算未把 cognition-verify 登记为双作业"
endgroup

# ─── 10. 源码树卫生 ──────────────────────────────────────────────────
group "cognition-thinkdual: 源码树卫生"
leftover="$(find "${TD}" \
    \( -name CMakeCache.txt -o -name CMakeFiles -o -name '*.o' \
    -o -name '*.a' -o -name td_test -o -name airy-thinkdual \) \
    -print 2>/dev/null)"
[ -z "${leftover}" ] || bad "源码树残留构建产物: ${leftover}"
for src in "${TD}"/*.c "${TD}"/*.h "${TD}"/tests/*.c; do
    deny '(^|[^A-Za-z])(TODO|FIXME|stub)([^A-Za-z]|$)' "${src}" \
        "残留占位标记: ${src}"
    deny '/(home|Users)/' "${src}" "出现本地绝对路径: ${src}"
done
endgroup

[ "${FAILS}" -eq 0 ] || die "cognition-thinkdual 门禁失败 ${FAILS} 项"
say "cognition-thinkdual 门禁通过"
