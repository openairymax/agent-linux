#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/build-budget.sh — 整轮预算棘轮：台账必须继续描述现实
#
# build-drift 逐段对照基线，判据是「单段超基线 2 倍」，只打告警。本门禁看
# 合计，且判负。两者不重复：六个段各自慢 1.4 倍，逐段都还在上界之内，合起
# 来却已经吃掉整轮的时间余量——这种弥散式变慢只有合计能看见。
#
# 判据：本轮实测到的每一段，其台账基线之和必须不少于实测合计的 1.2 倍。
# 低于即 AIRY-BUILD-BUDGET：台账已不再留有余量，继续按它排期等于把下一轮
# 的正常抖动直接推到时限上。此时的正确动作是重新推导基线（第三列写清取
# 值依据），而不是重跑一遍把失败当噪声。
#
# 只对「本轮实测到的段」求和：没跑的段不占预算，否则一个被裁掉的段会永远
# 抬高合计，恰好把弥散变慢藏起来。
#
# 用法: ci/gates/build-budget.sh --manifest FILE [--ledger FILE]
#                               [--summary FILE]
# 退出码: 0 预算足额（含无实测跳过）；1 有段未入账或余量不足；
#         5 参数不满足、台账缺失或清单缺少合计行。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

MANIFEST=""
LEDGER="${ROOT}/ci/baseline/build-segments.tsv"
SUMMARY="${GITHUB_STEP_SUMMARY:-}"

usage()
{
    cat <<'EOF'
用法: ci/gates/build-budget.sh --manifest FILE [选项]

  --manifest FILE   assemble 产物中的 image-ci-manifest.txt
  --ledger FILE     段基线台账，缺省 ci/baseline/build-segments.tsv
  --summary FILE    追加 Markdown 表格（CI 里传 $GITHUB_STEP_SUMMARY）
  -h, --help        显示本帮助

台账列：段名 <TAB> 基线秒数 <TAB> 出处。以 # 开头的行与空行忽略。
EOF
}

parse_args()
{
    while [ $# -gt 0 ]; do
        case "$1" in
        --manifest) MANIFEST="${2:-}"; shift 2 ;;
        --ledger) LEDGER="${2:-}"; shift 2 ;;
        --summary) SUMMARY="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit 5 ;;
        esac
    done
    [ -n "${MANIFEST}" ] || { err "--manifest 必填"; exit 5; }
    [ -f "${LEDGER}" ] || { err "基线台账不存在: ${LEDGER}"; exit 5; }
}

load_ledger()
{
    local seg lim src
    while IFS=$'\t' read -r seg lim src; do
        case "${seg}" in ''|'#'*) continue ;; esac
        [ -n "${lim}" ] || continue
        BASE["${seg}"]="${lim}"
        SRC["${seg}"]="${src:-未注明}"
    done <"${LEDGER}"
}

# 清单行格式固定为 `time <段>=<秒>`。`time accounted=` 是合计行，单独取值，
# 不参与逐段求和，否则它会被当成一个未入账的段。
segs()
{
    sed -n 's/^time \([a-z][a-z_-]*\)=\([0-9]\+\)$/\1 \2/p' "${MANIFEST}" |
        while read -r seg secs; do
            [ "${seg}" = accounted ] || printf '%s %s\n' "${seg}" "${secs}"
        done
}

# 逐段打印并把基线累加进 BASE_SUM。累加不能写在管道里：管道右侧是子
# shell，累加结果传不回来，合计会永远是 0。
report()
{
    local row seg secs lim mark note
    printf '%-10s %8s %8s  %s\n' 段 实测s 基线s 结论
    if [ -n "${SUMMARY}" ]; then
        printf '| 段 | 实测 (s) | 基线 (s) | 结论 |\n| --- | ---: | ---: | --- |\n' \
            >>"${SUMMARY}"
    fi
    for row in "${ROWS[@]}"; do
        seg="${row% *}"
        secs="${row##* }"
        lim="${BASE[${seg}]:-}"
        if [ -z "${lim}" ]; then
            mark="FAIL"; note="未入账（无预算依据）"
            MISS=1
        else
            mark="ok"; note="${SRC[${seg}]}"
            BASE_SUM=$((BASE_SUM + lim))
        fi
        printf '%-10s %8s %8s  %s %s\n' "${seg}" "${secs}" "${lim:--}" "${mark}" "${note}"
        if [ -n "${SUMMARY}" ]; then
            printf '| %s | %s | %s | %s |\n' "${seg}" "${secs}" "${lim:--}" "${note}" \
                >>"${SUMMARY}"
        fi
    done
}

main()
{
    local acc need rest
    parse_args "$@"
    if [ ! -f "${MANIFEST}" ]; then
        say "无组装清单（assemble 未产出），跳过预算核对: ${MANIFEST}"
        exit 0
    fi
    acc="$(sed -n 's/^time accounted=\([0-9]\+\)$/\1/p' "${MANIFEST}" |
        tail -n 1)"
    ROWS=()
    mapfile -t ROWS < <(segs)
    if [ "${#ROWS[@]}" -eq 0 ] && [ -z "${acc}" ]; then
        say "清单无段耗时（组装未进入计时阶段），跳过预算核对"
        exit 0
    fi
    if [ -z "${acc}" ]; then
        err "清单缺少 time accounted= 行，无法核对预算: ${MANIFEST}"
        exit 5
    fi
    declare -gA BASE=() SRC=()
    load_ledger
    BASE_SUM=0
    MISS=0
    report
    # 上取整：(acc × 6) / 5 在整数域等价于 acc × 1.2，避免引入浮点。
    need=$(( (acc * 6 + 4) / 5 ))
    if [ -n "${SUMMARY}" ]; then
        printf '| **合计** | **%s** | **%s** | 需 ≥ %s |\n' \
            "${acc}" "${BASE_SUM}" "${need}" >>"${SUMMARY}"
    fi
    printf '合计: 实测 %ss，台账 %ss，需 ≥ %ss（实测 × 1.2）\n' \
        "${acc}" "${BASE_SUM}" "${need}"
    if [ "${MISS}" -ne 0 ]; then
        err "AIRY-BUILD-BUDGET 有段未在台账登记，合计无处可算"
        exit 1
    fi
    if [ "${BASE_SUM}" -lt "${need}" ]; then
        err "AIRY-BUILD-BUDGET ledger=${BASE_SUM}s need=${need}s actual=${acc}s"
        exit 1
    fi
    rest=$((BASE_SUM - acc))
    say "预算足额：台账 ${BASE_SUM}s ≥ ${need}s，未用余量 ${rest}s"
    exit 0
}

main "$@"
