#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_evidence.sh — 证据包四件套与缺件判据的回归
#
# 证据包的用途是「失败轮次带走的东西不能少」。因此断言分两侧：收齐时
# 包内四件套与清单都在，缺件时逐条写出、且是否判失败只由剖面决定——
# 验收剖面缺件必须红，产品剖面缺冒烟 TAP 属正常。
#
# 用法: bash ci/tests/test_evidence.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/out"
cp "${ROOT}/ci/gates/evidence.sh" "${FX}/ci/gates/evidence.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/lib/profile.sh" "${FX}/ci/lib/profile.sh"

leg()
{
    local dir="$1"
    mkdir -p "${dir}/run-uefi-1"
    printf 'ok 1 install\n1..1\n' >"${dir}/tap.txt"
    printf 'result=pass\n' >"${dir}/install-manifest.txt"
    printf 'AIRYMAX-TAP|ok 1 smoke\n' >"${dir}/run-uefi-1/boot.log"
    printf 'ok 1 smoke\n' >"${dir}/smoke-uefi-1.tap"
}

run()
{
    env -u ACCEPTANCE bash "${FX}/ci/gates/evidence.sh" "$@" 2>&1
}

case_out=""
check()
{
    local name="$1" want_rc="$2" text="$3"
    shift 3
    local out rc
    out="$(run "$@")"
    rc=$?
    case_out="${out}"
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

leg "${tmp}/install-usb"
leg "${tmp}/install"
printf '{"channel":"stable"}\n' >"${tmp}/channel.json"

check "两条腿四件套齐全" 0 "四件套齐全" \
    --out "${FX}/out" --dir "${tmp}/install-usb" --dir "${tmp}/install" \
    --extra "${tmp}/channel.json"

# 包内层次决定归因：两腿同名文件不能互相覆盖。
for f in tap.txt install-manifest.txt run-uefi-1/boot.log smoke-uefi-1.tap; do
    [ -f "${FX}/out/install-usb/${f}" ] && [ -f "${FX}/out/install/${f}" ] || {
        printf 'FAIL 包内缺 %s\n' "${f}" >&2; fails=$((fails + 1)); }
done
[ -f "${FX}/out/extras/channel.json" ] || {
    printf 'FAIL 附加件未入包\n' >&2; fails=$((fails + 1)); }

# 清单必须能自证：每行三列，且不含两个记账文件自身。
lines="$(wc -l <"${FX}/out/evidence-manifest.txt")"
if [ "${lines}" -eq 9 ] &&
   ! grep -qE 'evidence-(manifest|missing)\.txt' "${FX}/out/evidence-manifest.txt"; then
    printf 'ok   清单覆盖 9 个文件且不含记账文件\n'
else
    printf 'FAIL 清单: %d 行\n' "${lines}" >&2
    fails=$((fails + 1))
fi

# 产品剖面缺冒烟 TAP 属正常：只告警，退出 0。
mkdir -p "${FX}/prod/run-bios-1"
printf 'ok 1 install\n' >"${FX}/prod/tap.txt"
printf 'result=pass\n' >"${FX}/prod/install-manifest.txt"
printf 'boot\n' >"${FX}/prod/run-bios-1/boot.log"
check "产品剖面缺冒烟 TAP 只告警" 0 "" --out "${FX}/out2" --dir "${FX}/prod"
printf '%s' "${case_out}" | grep -Fq "非验收剖面" || {
    printf 'FAIL 产品剖面未说明降级理由\n' >&2; fails=$((fails + 1)); }

# 验收剖面的冒烟 TAP 是判据：同一份产物必须判红。剖面只能由 ACCEPTANCE
# 声明，而 run() 为了验证产品剖面会先摘掉它，故这里直接调用门禁。
out="$(ACCEPTANCE=1 bash "${FX}/ci/gates/evidence.sh" \
    --out "${FX}/out3" --dir "${FX}/prod" 2>&1)"
rc=$?
if [ "${rc}" -eq 1 ]; then
    printf 'ok   验收剖面缺冒烟 TAP 判失败\n'
else
    printf 'FAIL 验收剖面: 退出码 %d\n%s\n' "${rc}" "${out}" >&2
    fails=$((fails + 1))
fi

# 装机没跑起来时四件套全缺：缺项要逐条写全，而不是只报第一条。
mkdir -p "${FX}/bare"
check "空腿逐条登记缺项" 1 "缺 4 项" --out "${FX}/out4" --dir "${FX}/bare" --require
grep -Fq 'run-*/boot.log' "${FX}/out4/evidence-missing.txt" &&
    grep -Fq 'smoke-*.tap' "${FX}/out4/evidence-missing.txt" || {
    printf 'FAIL 缺项清单不完整\n' >&2; fails=$((fails + 1)); }

check "缺 --out 退 5" 5 "" --dir "${FX}/bare"
check "无 --dir 退 5" 5 "" --out "${FX}/out5"

if [ "${fails}" -eq 0 ]; then
    printf 'test_evidence: 全部通过\n'
else
    printf 'test_evidence: %d 项失败\n' "${fails}" >&2
fi
exit "$((fails > 0))"
