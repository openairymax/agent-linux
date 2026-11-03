#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_build_drift.sh — 段耗时对账的分支回归
#
# 对账的结论进两处：人看的表与 CI 的注解。两处都只在超基线时才有内容，
# 而「没超基线」与「压根没对账」在页面上长得一样——所以每条分支都要断言
# 具体输出，而不是只看退出码（本门禁按设计恒退 0）。
#
# 用法: bash ci/tests/test_build_drift.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

GATE="${ROOT}/ci/gates/build-drift.sh"
BASE="${tmp}/base.tsv"
printf '# 段\t基线\t出处\nkern\t100\t夹具\niso\t600\t夹具\n' >"${BASE}"

manifest()
{
    printf '# AirymaxOS image CI manifest\n'
    printf 'stage rpm=pass\n'
    local row
    for row in "$@"; do
        printf 'time %s\n' "${row}"
    done
    printf 'time accounted=999\n'
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
manifest toolchain=31 rpm=100 kern=200 iso=114 >"${M}"
check "全部在基线内" 0 "ok" --manifest "${M}" --baseline "${BASE}"

manifest kern=500 >"${M}"
check "超两倍标 DRIFT" 0 "AIRY-BUILD-DRIFT seg=kern" --manifest "${M}" --baseline "${BASE}"

# 恰为两倍不算漂移：判据是「超」，边界值留给抖动。
manifest kern=200 >"${M}"
out="$(bash "${GATE}" --manifest "${M}" --baseline "${BASE}" 2>&1)"
if printf '%s' "${out}" | grep -Fq "AIRY-BUILD-DRIFT"; then
    printf 'FAIL 两倍边界误报\n%s\n' "${out}" >&2
    fails=$((fails + 1))
else
    printf 'ok   两倍边界不报警\n'
fi

manifest newseg=5 >"${M}"
check "无基线只入账" 0 "无基线，仅入账" --manifest "${M}" --baseline "${BASE}"

manifest kern=500 >"${M}"
out="$(GITHUB_ACTIONS=true bash "${GATE}" --manifest "${M}" --baseline "${BASE}" 2>&1)"
if printf '%s' "${out}" | grep -Fq "::warning::AIRY-BUILD-DRIFT seg=kern"; then
    printf 'ok   CI 下出注解\n'
else
    printf 'FAIL CI 注解: %s\n' "${out}" >&2
    fails=$((fails + 1))
fi

SUM="${tmp}/summary.md"
bash "${GATE}" --manifest "${M}" --baseline "${BASE}" --summary "${SUM}" >/dev/null 2>&1
if grep -Fq "| 段 | 实测 (s) |" "${SUM}" && grep -Fq "| kern | 500 | 100 |" "${SUM}"; then
    printf 'ok   step summary 表格\n'
else
    printf 'FAIL step summary\n' >&2
    fails=$((fails + 1))
fi

check "清单缺失按跳过" 0 "跳过" --manifest "${tmp}/absent.txt" --baseline "${BASE}"
check "台账缺失退 5" 5 "基线台账不存在" --manifest "${M}" --baseline "${tmp}/absent.tsv"
check "缺 --manifest 退 5" 5 "--manifest 必填" --baseline "${BASE}"

if [ "${fails}" -eq 0 ]; then
    printf 'test_build_drift: 全部通过\n'
else
    printf 'test_build_drift: %d 项失败\n' "${fails}" >&2
fi
exit "$((fails > 0))"
