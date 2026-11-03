#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/build-drift.sh — 组装段耗时的入账与漂移告警
#
# 组装的六个段各自计时后，耗时要能回答两个问题：这一轮慢在哪一段，以及
# 「这一段比平时慢了是不是正常」。前者由 run-image-ci 打印的 `# timing:`
# 行回答，后者需要一份可比的上界——即本门禁对照的基线台账。
#
# 判据：实际耗时超基线的 2 倍即打 AIRY-BUILD-DRIFT。基线是「该段已知的
# 正常上界」，取实测值或由 step 时限推导，出处逐行写在台账第三列，避免
# 变成一组来路不明的数字。告警不改变构建结论：一轮慢但产物正确的构建
# 不该因为慢而判负，但必须留下可检索的标记。
#
# 无基线登记的段只入账、不告警——新加的段要先跑几轮才有可比对象，凭空
# 填一个数只会制造噪声告警，告警一有噪声就没人看了。
#
# 用法: ci/gates/build-drift.sh --manifest FILE [--baseline FILE]
#                               [--summary FILE]
# 退出码: 0 入账完成（含无清单跳过）；5 参数不满足或台账缺失。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
. "${ROOT}/ci/lib/log.sh"

MANIFEST=""
BASELINE="${ROOT}/ci/baseline/build-segments.tsv"
SUMMARY="${GITHUB_STEP_SUMMARY:-}"

usage()
{
    cat <<'EOF'
用法: ci/gates/build-drift.sh --manifest FILE [选项]

  --manifest FILE   assemble 产物中的 image-ci-manifest.txt
  --baseline FILE   基线台账，缺省 ci/baseline/build-segments.tsv
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
        --baseline) BASELINE="${2:-}"; shift 2 ;;
        --summary) SUMMARY="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit 5 ;;
        esac
    done
    [ -n "${MANIFEST}" ] || { err "--manifest 必填"; exit 5; }
    [ -f "${BASELINE}" ] || { err "基线台账不存在: ${BASELINE}"; exit 5; }
}

load_baseline()
{
    local seg lim ev
    while IFS=$'\t' read -r seg lim ev; do
        case "${seg}" in ''|'#'*) continue ;; esac
        [ -n "${lim}" ] || continue
        LIMIT["${seg}"]="${lim}"
        ORIGIN["${seg}"]="${ev:-未注明}"
    done <"${BASELINE}"
}

# 行格式固定为 `time <段>=<秒>`，`time accounted=<合计>` 是汇总而非段，
# 排除在外，否则它会以「无基线」的身份出现在表里。
rows()
{
    sed -n 's/^time \([a-z][a-z_-]*\)=\([0-9]\+\)$/\1 \2/p' "${MANIFEST}" |
        while read -r seg secs; do
            [ "${seg}" = accounted ] || printf '%s %s\n' "${seg}" "${secs}"
        done
}

report()
{
    local seg secs lim mark note drift=0
    printf '%-10s %8s %8s  %s\n' 段 实测s 基线s 结论
    if [ -n "${SUMMARY}" ]; then
        printf '| 段 | 实测 (s) | 基线 (s) | 结论 |\n| --- | ---: | ---: | --- |\n' \
            >>"${SUMMARY}"
    fi
    while read -r seg secs; do
        lim="${LIMIT[${seg}]:-}"
        if [ -z "${lim}" ]; then
            mark="-"; note="无基线，仅入账"
        elif [ "${secs}" -gt "$((lim * 2))" ]; then
            mark="DRIFT"; note="超基线 2 倍"
            drift=$((drift + 1))
            if [ "${GITHUB_ACTIONS:-}" = true ]; then
                printf '::warning::AIRY-BUILD-DRIFT seg=%s actual=%ss limit=%ss\n' \
                    "${seg}" "${secs}" "${lim}"
            else
                warn "AIRY-BUILD-DRIFT seg=${seg} actual=${secs}s limit=${lim}s"
            fi
        else
            mark="ok"; note="在基线内（${ORIGIN[${seg}]:-}）"
        fi
        printf '%-10s %8s %8s  %s %s\n' "${seg}" "${secs}" "${lim:--}" "${mark}" "${note}"
        if [ -n "${SUMMARY}" ]; then
            printf '| %s | %s | %s | %s |\n' "${seg}" "${secs}" "${lim:--}" "${note}" \
                >>"${SUMMARY}"
        fi
    done < <(rows)
    return "${drift}"
}

main()
{
    parse_args "$@"
    if [ ! -f "${MANIFEST}" ]; then
        say "无组装清单（assemble 未产出），跳过耗时对账: ${MANIFEST}"
        exit 0
    fi
    declare -gA LIMIT=() ORIGIN=()
    load_baseline
    report || say "本轮有段超出基线，见上方 AIRY-BUILD-DRIFT 标记"
    exit 0
}

main "$@"
