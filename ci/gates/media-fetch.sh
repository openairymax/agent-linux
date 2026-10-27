#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/media-fetch.sh — 按摘要取固定介质（可换源、可续传、有总预算）
#
# 用法:
#   ci/gates/media-fetch.sh --dir DIR [--budget SECS] [--floor SECS] \
#                           [--mirror URL]... \
#                           NAME PATH SHA256 [NAME PATH SHA256 ...]
#
# 语义:
#   * 介质只按 sha256 认定，源可替换。官方源跨洋链路曾整段停滞（run
#     39543892713 在 Fetch 卡满 55 分钟），国内镜像作等价回退，摘要不符
#     即弃。
#   * 落地目录本身就是缓存：摘要相符的文件直接复用，不再触网。上层
#     workflow 用 actions/cache 以介质摘要为键保存该目录，重复轮次即可
#     跳过整段下载（run 40485745100 的 ISO 单件耗时 43 分钟）。
#   * 换源保留已下字节：镜像内容由 sha256 认定，故跨源续传成立，避免慢
#     链路每次都从头再来。
#   * 预算是按项共享的，但每一项都保底 --floor 秒：先下的大件不得把后续
#     介质挤成零头（同上 run 中 docker 镜像只剩 130 秒即失败）。
#   * 单次尝试取「剩余预算减去后续地板」与 --cap 的较小值；--speed-limit
#     触发即判停滞并换源。
#   * 总预算耗尽后立即失败，不把 job 拖到超时被取消——超时取消拿不到任何
#     诊断信息，预算内失败至少能指出是哪个介质不可得。
#
# 退出码: 0 全部介质就绪；1 任一介质在所有源不可得；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

DIR=""
BUDGET=2700
CAP=3000
FLOOR=600
MIRRORS=()
ITEMS=()

usage()
{
    cat <<'EOF'
用法: ci/gates/media-fetch.sh --dir DIR [--budget SECS] [--cap SECS]
                              [--floor SECS] [--mirror URL]... \
                              NAME PATH SHA256 ...

  --dir DIR       落地目录（必填；目录内已有的合格文件直接复用）
  --budget SECS   介质总预算，默认 2700
  --cap SECS      单次尝试上限，默认 3000
  --floor SECS    每项保底预算，默认 600
  --mirror URL    镜像根（可重复）；缺省用内置三源
  NAME PATH SHA256  依次给出介质文件名、镜像内相对目录、期望摘要
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)    DIR="${2:-}"; shift 2 ;;
        --budget) BUDGET="${2:-}"; shift 2 ;;
        --cap)    CAP="${2:-}"; shift 2 ;;
        --floor)  FLOOR="${2:-}"; shift 2 ;;
        --mirror) MIRRORS+=("${2:-}"); shift 2 ;;
        -h|--help) usage; exit 0 ;;
        --) shift; break ;;
        -*) die "未知选项: $1" ;;
        *) break ;;
    esac
done

ITEMS=("$@")

[ -n "${DIR}" ] || { usage >&2; exit "${EINVAL}"; }
[ "${#ITEMS[@]}" -gt 0 ] || { usage >&2; exit "${EINVAL}"; }
[ $(( ${#ITEMS[@]} % 3 )) -eq 0 ] || {
    err "介质三元组不完整: 需要 NAME PATH SHA256 三的倍数"; exit "${EINVAL}"; }

if [ "${#MIRRORS[@]}" -eq 0 ]; then
    # 顺序即优先级：官方源优先，跨洋链路停滞时自动落到国内镜像。
    MIRRORS=(
        https://repo.openeuler.org/openEuler-24.03-LTS-SP2
        https://mirrors.tuna.tsinghua.edu.cn/openeuler/openEuler-24.03-LTS-SP2
        https://mirrors.huaweicloud.com/openeuler/openEuler-24.03-LTS-SP2
    )
fi

need_cmd curl sha256sum
mkdir -p "${DIR}"

deadline=$(( $(date +%s) + BUDGET ))
total=$(( ${#ITEMS[@]} / 3 ))

# 落地目录本身即缓存：摘要相符就复用，不再触网。
ready()
{
    local name="$1" want="$2" got

    [ -f "${DIR}/${name}" ] || return 1
    got="$(sha256sum "${DIR}/${name}" | cut -d' ' -f1)"
    if [ "${got}" = "${want}" ]; then
        say "${name}: 复用已有文件（sha256 ${got}）"
        return 0
    fi
    warn "${name} 摘要不符，丢弃重取: ${got}"
    rm -f "${DIR}/${name}"
    return 1
}

fetch()
{
    local name="$1" path="$2" want="$3" idx="$4"
    local url got left cap

    ready "${name}" "${want}" && return 0

    for url in "${MIRRORS[@]}"; do
        left=$(( deadline - $(date +%s) ))
        # 后续每项各留 FLOOR 秒：先下的大件不得把后面的挤成零头。
        cap=$(( left - FLOOR * (total - idx - 1) ))
        if [ "${cap}" -lt 60 ]; then
            warn "预算不足以保住后续地板，放弃 ${name}"
            break
        fi
        [ "${cap}" -gt "${CAP}" ] && cap=${CAP}
        url="${url%/}/${path}/${name}"
        group "fetch ${url} (max ${cap}s)"
        if curl -fL --progress-bar -C - \
            --connect-timeout 20 --max-time "${cap}" \
            --retry 2 --retry-delay 5 --retry-all-errors \
            --speed-limit 102400 --speed-time 30 \
            -o "${DIR}/${name}.part" "${url}"; then
            got="$(sha256sum "${DIR}/${name}.part" | cut -d' ' -f1)"
            if [ "${got}" = "${want}" ]; then
                mv "${DIR}/${name}.part" "${DIR}/${name}"
                say "${name}: sha256 ${got} ok"
                endgroup
                return 0
            fi
            warn "${url} 摘要不符: ${got}"
            rm -f "${DIR}/${name}.part"
        else
            warn "${url} 下载失败"
        fi
        endgroup
    done

    err "${name} 在所有镜像源均不可得"
    return 1
}

i=0
rc=0
while [ "${i}" -lt "${#ITEMS[@]}" ]; do
    fetch "${ITEMS[i]}" "${ITEMS[i+1]}" "${ITEMS[i+2]}" "$(( i / 3 ))" || rc=1
    i=$(( i + 3 ))
done

exit "${rc}"
