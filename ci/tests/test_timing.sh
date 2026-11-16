#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_timing.sh — ci/lib/timing.sh 的接口契约回归
#
# 判据：段累计与汇总格式、缺省不落盘、CI artifact 目录默认路径、仓库内
# 路径拒绝、跨进程追加不撕裂、未开始即结束的显式失败，以及 ci/ci.sh 包装
# 后退出码不变、失败也留计时。最后跑一遍 ci/check-workflows.py，确保改动
# 没有破坏 workflow 结构断言。
#
# 并发说明：计时状态只在单进程内，门禁由 ci/ci.sh 顺序调用，不存在共享
# 计时槽，因此不需要锁。多个进程追加同一 AIRY_TIMING_OUT 时，每行一次
# write（远小于 PIPE_BUF），行不会互相截断；跨进程顺序不保证，本回归只
# 断言行完整与条数。
#
# 用法: bash ci/tests/test_timing.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
LIB="${ROOT}/ci/lib/timing.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

ok()
{
    printf 'ok   %s\n' "$1"
}

bad()
{
    printf 'FAIL %s\n%s\n' "$1" "${2:-}" >&2
    fails=$((fails + 1))
}

# --- 1. 段累计、汇总格式与 top 归属 ---------------------------------------
out="$(
    # shellcheck source=../lib/timing.sh
    . "${LIB}"
    AIRY_TIMING_OUT="${tmp}/one.tsv"
    timing_start alpha
    sleep 0.05
    timing_end alpha
    timing_start beta
    sleep 0.01
    timing_end beta
    timing_sum
)"
if printf '%s\n' "${out}" |
    grep -Eq '^timing: 2 segments, total [0-9]+\.[0-9]{3}s, top alpha [0-9]+\.[0-9]{3}s$'; then
    ok "汇总格式与 top 归属"
else
    bad "汇总格式与 top 归属" "${out}"
fi
if [ -f "${tmp}/one.tsv" ] && [ "$(wc -l <"${tmp}/one.tsv" | tr -d ' ')" = "2" ] &&
    awk -F'\t' 'NF != 3 || $1 !~ /^(alpha|beta)$/ || $2 !~ /^[0-9]+\.[0-9][0-9][0-9]$/ { exit 1 }' \
        "${tmp}/one.tsv"; then
    ok "TSV 三列（段名/秒/出处）"
else
    bad "TSV 三列（段名/秒/出处）" "$(cat "${tmp}/one.tsv" 2>/dev/null)"
fi

# --- 2. 本地缺省不落盘 ----------------------------------------------------
empty="${tmp}/empty"
mkdir -p "${empty}"
out="$(
    cd "${empty}" || exit 1
    unset AIRY_TIMING_OUT GITHUB_ACTIONS RUNNER_TEMP
    # shellcheck source=../lib/timing.sh
    . "${LIB}"
    timing_start solo
    timing_end solo
    timing_sum
)"
if [ -z "$(ls -A "${empty}")" ] &&
    printf '%s' "${out}" | grep -Fq 'timing: 1 segments, total '; then
    ok "本地缺省不落盘且仍打印汇总"
else
    bad "本地缺省不落盘且仍打印汇总" "$(ls -A "${empty}") / ${out}"
fi

# --- 3. CI 缺省写入 artifact 目录 ----------------------------------------
out="$(
    export GITHUB_ACTIONS=true RUNNER_TEMP="${tmp}/rt" GITHUB_JOB=unit
    unset AIRY_TIMING_OUT
    # shellcheck source=../lib/timing.sh
    . "${LIB}"
    timing_start ds
    timing_end ds
    timing_sum
)"
if [ -f "${tmp}/rt/airy-timing-unit.tsv" ] &&
    [ "$(wc -l <"${tmp}/rt/airy-timing-unit.tsv" | tr -d ' ')" = "1" ]; then
    ok "CI 缺省写入 RUNNER_TEMP"
else
    bad "CI 缺省写入 RUNNER_TEMP" "${out}"
fi

# --- 4. 仓库内路径拒绝写入 ------------------------------------------------
mkdir -p "${tmp}/repo/ci/lib"
cp "${LIB}" "${tmp}/repo/ci/lib/timing.sh"
out="$(
    (
        . "${tmp}/repo/ci/lib/timing.sh"
        export AIRY_TIMING_OUT="${tmp}/repo/out.tsv"
        timing_start guard
        timing_end guard
    ) 2>&1
)"
if [ ! -e "${tmp}/repo/out.tsv" ] &&
    printf '%s' "${out}" | grep -Fq '跳过落盘'; then
    ok "仓库内路径拒绝落盘"
else
    bad "仓库内路径拒绝落盘" "${out}"
fi

# --- 5. 跨进程追加：行完整、条数正确 -------------------------------------
export AIRY_TIMING_OUT="${tmp}/par.tsv"
for ((i = 0; i < 20; i++)); do
    (
        # shellcheck source=../lib/timing.sh
        . "${LIB}"
        timing_start par
        timing_end par
    ) &
done
wait
unset AIRY_TIMING_OUT
n="$(wc -l <"${tmp}/par.tsv" | tr -d ' ')"
rows="$(awk -F'\t' 'NF != 3 || $1 != "par" || $2 !~ /^[0-9]+\.[0-9][0-9][0-9]$/ { n++ } END { print n + 0 }' \
    "${tmp}/par.tsv")"
if [ "${n}" = "20" ] && [ "${rows}" = "0" ]; then
    ok "并发追加 20 行不撕裂"
else
    bad "并发追加 20 行不撕裂" "lines=${n} bad=${rows}"
fi

# --- 6. 未开始即结束必须显式失败 -----------------------------------------
rc=0
out="$(
    (
        # shellcheck source=../lib/timing.sh
        . "${LIB}"
        timing_end ghost
    ) 2>&1
)" || rc=$?
if [ "${rc}" = "1" ] && printf '%s' "${out}" | grep -Fq '未开始计时'; then
    ok "未开始即结束返回 1"
else
    bad "未开始即结束返回 1" "rc=${rc} ${out}"
fi

# --- 7. ci/ci.sh 成功路径：退出码不变、同名文件跨步累计 ------------------
out="$(AIRY_TIMING_OUT="${tmp}/ci.tsv" bash "${ROOT}/ci/ci.sh" \
    mgmt-files --only gov-files 2>&1)"
rc=$?
AIRY_TIMING_OUT="${tmp}/ci.tsv" bash "${ROOT}/ci/ci.sh" \
    mgmt-files --only gov-files >/dev/null 2>&1
rc2=$?
if [ "${rc}" = "0" ] && [ "${rc2}" = "0" ] &&
    printf '%s\n' "${out}" | tail -n 1 |
    grep -Eq '^timing: 1 segments, total [0-9]+\.[0-9]{3}s, top mgmt-files ' &&
    [ "$(wc -l <"${tmp}/ci.tsv" | tr -d ' ')" = "2" ]; then
    ok "ci.sh 成功路径计时与退出码"
else
    bad "ci.sh 成功路径计时与退出码" "rc=${rc}/${rc2} ${out}"
fi

# --- 8. ci/ci.sh 失败路径：退出码透传、仍留计时 --------------------------
out="$(AIRY_TIMING_OUT="${tmp}/bad.tsv" bash "${ROOT}/ci/ci.sh" \
    ssot-verify --kind bogus 2>&1)"
rc=$?
if [ "${rc}" = "5" ] &&
    printf '%s\n' "${out}" | tail -n 1 |
    grep -Eq '^timing: 1 segments, total .* top ssot-verify ' &&
    [ "$(wc -l <"${tmp}/bad.tsv" | tr -d ' ')" = "1" ]; then
    ok "ci.sh 失败路径计时与退出码"
else
    bad "ci.sh 失败路径计时与退出码" "rc=${rc} ${out}"
fi

# --- 9. workflow 结构断言不因本次改动失效 --------------------------------
if python3 "${ROOT}/ci/check-workflows.py" >"${tmp}/wf.log" 2>&1; then
    ok "check-workflows.py 通过"
else
    bad "check-workflows.py 通过" "$(cat "${tmp}/wf.log")"
fi

if [ "${fails}" -ne 0 ]; then
    printf '\n%d 项失败\n' "${fails}" >&2
    exit 1
fi
printf '\n全部通过\n'
