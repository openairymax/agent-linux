#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/coupling-scan.sh — 基底耦合点两向对账（L-50 / B-02）
#
# 用法:
#   ci/gates/coupling-scan.sh [--source DIR] [--ledger FILE] [--json]
#
# 背景：成册件 baseline-coupling.md 的 §10 是基底耦合点的唯一事实源，
# 每行 `BC-NNN|路径|正则`。换基底时必须改动的每一处都登记在册；册外多出
# 的耦合点会让替换评估少算一处，册内条目在实物里消失则说明判据已失真。
# 两个方向都判：
#   - 登记了但实物扫不到 → 失败；
#   - 实物扫到但册上没登记 → 失败。
# 册的条数与 §9 统计的总量也必须一致，删行而不改总量同样判负。
#
# 实物面按成册件的章节划定：软件源与镜像根、lockfile、安装配方、spec 的
# 基底包依赖、容器基底与 workflow 的介质/runner 引用。注释行不参与扫描：
# 提及基底不构成必须改动点（成册件 §1.1）。
#
#   --source DIR   仓库根，默认本脚本上两级
#   --ledger FILE  成册件，默认超级仓的 docs/docs-closed/agent-linux-dev/
#                  10-references/baseline-coupling.md
#   --json         输出单个 JSON 对象，供流水线消费
#
# 退出码: 0 通过（或未检出成册件，本门禁不适用）；1 对账失败；
#         5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SOURCE=""
LEDGER=""
JSON=0

# 耦合面与「基底声明行」谓词，顺序与成册件章节一致。谓词只认声明形态，
# 不认具体值：换基底后行仍在而值变了，由 BC 行的正则负责判。
SURF_GLOB=(
    'distro/repo/*.repo'
    'distro/repo/mirrors.list'
    'distro/lockfile/base.yaml'
    'distro/lockfile/ukui-components.yaml'
    'distro/lockfile/rpms.in.yaml'
    'distro/kickstart/*.ks'
    'distro/specs/*.spec'
    'distro/specs/kernel/*.spec'
    'ci/container/Containerfile.agent-base'
    '.github/workflows/image-build.yml'
)
SURF_PAT=(
    '^baseurl='
    '^https?://'
    '^(selected|kernel_line):'
    '^base:'
    '^[[:space:]]*baseurl:'
    '^[[:space:]]*url[[:space:]]+--url=|^[[:space:]]*repo[[:space:]].*--baseurl=https?://|^-openEuler-'
    '^[[:space:]]*(Requires|BuildRequires|Obsoletes):.*(openEuler|openeuler)'
    '^[[:space:]]*(Requires|BuildRequires|Obsoletes):.*(openEuler|openeuler)'
    '^ARG BASE_IMAGE='
    '^[[:space:]]*(iso_name|iso_sha256|img_name|img_sha256|runner|runs-on):|Fetch fixed openEuler media|key: dnf-'
)

RD_ID=()
RD_PATH=()
RD_PAT=()
MISS=()
UNDECL=()
TOTAL=""

usage()
{
    cat <<'EOF'
用法: ci/gates/coupling-scan.sh [--source DIR] [--ledger FILE] [--json]
EOF
}

json_list()
{
    local out="" item
    for item in "$@"; do
        out+="${out:+,}\"${item}\""
    done
    printf '%s' "${out}"
}

emit_json()
{
    local declared="$1" found="$2" total="null"
    [ -n "${TOTAL}" ] && total="${TOTAL}"
    printf '{"declared":%d,"found":%d,"delta":%d,"total":%s,"missing":[%s],"undeclared":[%s]}\n' \
        "${declared}" "${found}" "$((found - declared))" "${total}" \
        "$(json_list "${MISS[@]+"${MISS[@]}"}")" \
        "$(json_list "${UNDECL[@]+"${UNDECL[@]}"}")"
}

# 一行命中是否已被成册件覆盖。文件有主表行时只认同一路径的正则，避免把
# 已登记文件的形态漂移当成「仍是同一耦合点」；文件没有主表行时按耦合形态
# 匹配任一正则（同一耦合点可落在多份配方里，例如 minimal 与 workstation）。
covered()
{
    local rel="$1" text="$2" i same=0
    for i in "${!RD_ID[@]}"; do
        [ "${RD_PATH[$i]}" = "${rel}" ] || continue
        same=1
        if [[ "${text}" =~ ${RD_PAT[$i]} ]]; then
            return 0
        fi
    done
    [ "${same}" -eq 1 ] && return 1
    for i in "${!RD_ID[@]}"; do
        if [[ "${text}" =~ ${RD_PAT[$i]} ]]; then
            return 0
        fi
    done
    return 1
}

scan_surf()
{
    local glob="$1" pat="$2" f rel hit num text
    # shellcheck disable=SC2086
    for f in "${SOURCE}"/${glob}; do
        [ -f "${f}" ] || continue
        rel="${f#"${SOURCE}"/}"
        while IFS= read -r hit; do
            num="${hit%%:*}"
            text="${hit#*:}"
            [[ "${text}" =~ ^[[:space:]]*# ]] && continue
            covered "${rel}" "${text}" || UNDECL+=("${rel}:${num}")
        done < <(grep -nE -- "${pat}" "${f}" || true)
    done
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) need_dir "$1" "${2:-}"; SOURCE="$2"; shift 2 ;;
        --ledger)
            [ -n "${2:-}" ] || { err "--ledger 需要文件路径"; exit "${EINVAL}"; }
            LEDGER="$2"; shift 2 ;;
        --json) JSON=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${SOURCE}" ] || SOURCE="${ROOT}"
if [ -z "${LEDGER}" ]; then
    LEDGER="${ROOT}/../docs/docs-closed/agent-linux-dev/10-references/baseline-coupling.md"
    if [ ! -f "${LEDGER}" ]; then
        note "未检出成册件（${LEDGER}），本门禁不适用"
        exit 0
    fi
elif [ ! -f "${LEDGER}" ]; then
    err "成册件不存在: ${LEDGER}"
    exit "${EINVAL}"
fi

while IFS='|' read -r id path pat; do
    [ -n "${id}" ] || continue
    RD_ID+=("${id}")
    RD_PATH+=("${path}")
    RD_PAT+=("${pat}")
done < <(grep -E '^BC-[0-9][0-9][0-9]\|' "${LEDGER}" || true)

TOTAL="$(awk -F'|' '/耦合点总数/ {gsub(/[[:space:]]/, "", $3); print $3; exit}' "${LEDGER}")"

found=0
for i in "${!RD_ID[@]}"; do
    file="${SOURCE}/${RD_PATH[$i]}"
    if [ -f "${file}" ] && grep -qE -- "${RD_PAT[$i]}" "${file}" 2>/dev/null; then
        found=$((found + 1))
    else
        MISS+=("${RD_ID[$i]}")
    fi
done

for i in "${!SURF_GLOB[@]}"; do
    scan_surf "${SURF_GLOB[$i]}" "${SURF_PAT[$i]}"
done

declared="${#RD_ID[@]}"
rc=0
if [ -z "${TOTAL}" ]; then
    err "ledger/total 成册件未声明耦合点总数"
    rc=1
elif [ "${declared}" -ne "${TOTAL}" ]; then
    err "ledger/total 主表 ${declared} 条，统计声明 ${TOTAL} 条"
    rc=1
fi
for id in "${MISS[@]+"${MISS[@]}"}"; do
    err "coupling/missing ${id} 成册件已登记但实物未命中"
    rc=1
done
for hit in "${UNDECL[@]+"${UNDECL[@]}"}"; do
    err "coupling/undeclared ${hit} 实物存在但成册件未登记"
    rc=1
done

if [ "${JSON}" -eq 1 ]; then
    emit_json "${declared}" "${found}"
else
    say "coupling: ${declared} declared, ${found} found, delta $((declared - found))"
fi
exit "${rc}"
