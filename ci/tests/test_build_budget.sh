#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_build_budget.sh — 整轮预算棘轮的分支回归
#
# 判据只有一条，但边界在整数上：1.2 倍必须上取整，等号必须放行。拿捏错
# 一格，要么把恰好够用的台账判死，要么让「差一点」的台账过关——两种错误
# 在页面上都只表现为一次构建的成败，事后无从分辨。所以边界单列断言。
#
# 另一条容易退化的语义是「只对实测到的段求和」：若把整张台账一律累加，
# 一个被裁掉的段会永远抬高合计，弥散变慢就再也看不见。
#
# 用法: bash ci/tests/test_build_budget.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

GATE="${ROOT}/ci/gates/build-budget.sh"
LEDGER="${tmp}/ledger.tsv"
# 1/1.2 的整数边界：toolchain+rpm = 1020，恰好在 acc=850 时相等。
printf '# 段\t基线\t出处\ntoolchain\t120\t夹具\nrpm\t900\t夹具\nkern\t1800\t夹具\n' \
    >"${LEDGER}"

manifest()
{
    local acc="$1"
    shift
    {
        printf '# AirymaxOS image CI manifest\n'
        printf 'arch=x86_64\n'
        local row
        for row in "$@"; do
            printf 'time %s\n' "${row}"
        done
        [ -z "${acc}" ] || printf 'time accounted=%s\n' "${acc}"
    } >"${tmp}/manifest.txt"
}

check()
{
    local name="$1" want_rc="$2" text="$3"
    shift 3
    local out rc
    out="$(bash "${GATE}" "$@" 2>&1)"
    rc=$?
    if [ "${rc}" -ne "${want_rc}" ]; then
        printf 'FAIL %s: 退出码 %d，期望 %d\n%s\n' "${name}" "${rc}" "${want_rc}" "${out}" >&2
        fails=$((fails + 1))
        return
    fi
    if [ -n "${text}" ] && ! printf '%s' "${out}" | grep -Fq -- "${text}"; then
        printf 'FAIL %s: 输出缺少 %s\n%s\n' "${name}" "${text}" "${out}" >&2
        fails=$((fails + 1))
        return
    fi
    printf 'ok   %s\n' "${name}"
}

M="${tmp}/manifest.txt"

manifest 651 toolchain=31 rpm=620
check "余量充足" 0 "预算足额" --manifest "${M}" --ledger "${LEDGER}"

# 台账 1020，实测 850 → 850 × 1.2 = 1020，等号放行；851 则 1022 > 1020 判负。
manifest 850 toolchain=100 rpm=750
check "恰好 1.2 倍放行" 0 "预算足额" --manifest "${M}" --ledger "${LEDGER}"

manifest 851 toolchain=100 rpm=751
check "越过 1.2 倍判负" 1 "AIRY-BUILD-BUDGET ledger=1020s" --manifest "${M}" --ledger "${LEDGER}"

# kern 未实测就不占预算：若把整张台账累加（2820），这一轮会被误判为足额。
manifest 2400 toolchain=31 rpm=620
check "未实测的段不占预算" 1 "AIRY-BUILD-BUDGET" --manifest "${M}" --ledger "${LEDGER}"

manifest 10 newseg=10
check "段未入账判负" 1 "未在台账登记" --manifest "${M}" --ledger "${LEDGER}"

manifest 651 toolchain=31 rpm=620
check "清单缺失按跳过" 0 "跳过" --manifest "${tmp}/absent.txt" --ledger "${LEDGER}"
check "台账缺失退 5" 5 "基线台账不存在" --manifest "${M}" --ledger "${tmp}/absent.tsv"
check "缺 --manifest 退 5" 5 "--manifest 必填" --ledger "${LEDGER}"

manifest "" toolchain=31 rpm=620
check "缺合计行退 5" 5 "time accounted" --manifest "${M}" --ledger "${LEDGER}"

manifest "" 
check "无段耗时按跳过" 0 "跳过" --manifest "${M}" --ledger "${LEDGER}"

manifest 651 toolchain=31 rpm=620
SUM="${tmp}/summary.md"
bash "${GATE}" --manifest "${M}" --ledger "${LEDGER}" --summary "${SUM}" \
    >/dev/null 2>&1
if grep -Fq "| 段 | 实测 (s) |" "${SUM}" && grep -Fq "| **合计** | **651** | **1020** |" "${SUM}"; then
    printf 'ok   step summary 表格\n'
else
    printf 'FAIL step summary\n' >&2
    fails=$((fails + 1))
fi

if [ "${fails}" -eq 0 ]; then
    printf 'test_build_budget: 全部通过\n'
else
    printf 'test_build_budget: %d 项失败\n' "${fails}" >&2
fi
exit "$((fails > 0))"
