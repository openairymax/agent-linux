#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_tap_extract.sh — 冒烟 TAP 还原与「未运行」分类的回归
#
# 断言四类情形：串口里的 AIRYMAX-TAP| 行被逐行还原成 smoke-*.tap；多个启动
# 轮次各成一份；腿目录不存在时判通过而只出 note；验收剖面下「目录在但没留
# 证据」必须判失败。
#
# 第三条是重点：一次失败轮次里，前一条腿失败会让后续腿整段跳过，目录因此
# 不存在。此时若按失败处理，唯一真因会与三条「没跑」并列，排障时反而找不
# 到源头（run 41972665005）。同级的 boot-annotate 一直按 note 处理，本测试
# 把两条路径的结论钉在一起，避免以后又分叉。
#
# 用法: bash ci/tests/test_tap_extract.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${tmp}/out"
cp "${ROOT}/ci/gates/tap-extract.sh" "${FX}/ci/gates/tap-extract.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/lib/profile.sh" "${FX}/ci/lib/profile.sh"

# 剖面只由 ACCEPTANCE 声明，宿主的 LD_LIBRARY_PATH 会把版本告警混进被捕获
# 的输出，一并摘掉。
run()
{
    env -u ACCEPTANCE -u LD_LIBRARY_PATH \
        bash "${FX}/ci/gates/tap-extract.sh" "$@" 2>&1
}

fail()
{
    printf 'FAIL %s\n%s\n' "$1" "$2" >&2
    fails=$((fails + 1))
}

# check NAME WANT_RC WANT_TEXT ARGS… —— 退出码与输出片段都要对上。
check()
{
    local name="$1" want="$2" text="$3" out rc
    shift 3
    out="$(run "$@")"
    rc=$?
    if [ "${rc}" -ne "${want}" ]; then
        fail "${name}: rc=${rc} want ${want}" "${out}"
        return
    fi
    case "${out}" in
        *"${text}"*) printf 'ok   %s\n' "${name}" ;;
        *) fail "${name}: 输出缺少「${text}」" "${out}" ;;
    esac
}

# boot DIR —— 造一段正常串口日志：两条 TAP 行，其余是无关输出。
boot()
{
    mkdir -p "$1"
    {
        printf '[  12.3] random kernel line\n'
        printf 'AIRYMAX-TAP|TAP version 13\n'
        printf 'AIRYMAX-TAP|ok 1 - desktop ready\n'
        printf '[  13.1] more noise\n'
    } >"$1/boot.log"
}

check "腿未运行只出 note" 0 "该腿未运行" \
    --dir "${tmp}/out/install-uefi" --require
[ -e "${tmp}/out/install-uefi/smoke-uefi.tap" ] && \
    fail "腿未运行时不得产出 TAP" "smoke 文件被创建"

boot "${tmp}/out/leg1/run-uefi-1"
check "还原单轮 TAP" 0 "smoke-uefi-1.tap (2 行)" --dir "${tmp}/out/leg1"
[ "$(cat "${tmp}/out/leg1/smoke-uefi-1.tap")" = \
  "$(printf 'TAP version 13\nok 1 - desktop ready')" ] || \
    fail "TAP 原文不一致" "$(cat "${tmp}/out/leg1/smoke-uefi-1.tap")"

boot "${tmp}/out/leg2/run-cdrom-1"
boot "${tmp}/out/leg2/run-cdrom-2"
check "多轮各成一份" 0 "smoke-cdrom-2.tap (2 行)" --dir "${tmp}/out/leg2"

mkdir -p "${tmp}/out/leg3"
check "非验收剖面缺 boot.log 不判负" 0 "没有 boot.log" --dir "${tmp}/out/leg3"
check "验收剖面缺 boot.log 判负" 1 "没有 boot.log" \
    --dir "${tmp}/out/leg3" --require

mkdir -p "${tmp}/out/leg4/run-uefi-1"
printf 'health: ok\n' >"${tmp}/out/leg4/run-uefi-1/boot.log"
check "冒烟未执行判负" 1 "smoke TAP 未能从串口还原" \
    --dir "${tmp}/out/leg4" --require

if [ "${fails}" -ne 0 ]; then
    printf '%d 条断言失败\n' "${fails}"
    exit 1
fi
printf '全部通过\n'
