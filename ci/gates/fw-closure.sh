#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/fw-closure.sh — 固件闭包判定（QG-10 / D-01 / FW-01）
#
# 用法: ci/gates/fw-closure.sh [--manifest FILE] [--comps FILE] [--root DIR]
#
# 两件事，缺一不可：
#   ① 集合对账（构建期）：comps 里名称含 firmware 或以 -ucode 结尾的包，
#      必须全部出现在 fw-manifest.txt；清单声明的包必须全部由 comps 安装。
#      两个方向都判，是因为少声明会让器件在装机后静默失效，多声明则让
#      清单慢慢变成与镜像无关的愿望列表。
#   ② 实物核对（运行期证据）：给 --root 时逐条模式在系统内至少命中一个
#      文件。包在而内容不在，正是 D-01「构建期与运行期双判」要拦的形态。
#
# 谓词只认「是不是固件包」，不认具体包名：仓库元数据在离线环境不可得，
# 而 comps 是「镜像装什么包」的唯一声明；新增固件包时判定自动生效，不用
# 改代码。
#
# 清单里为什么会有监管域包：wireless-regdb 的包名不含 firmware，谓词认不
# 出来，但它提供 /usr/lib/firmware/regulatory.db，缺失即 0.0.3 真机缺陷
# D-03。这类「名字不说自己是固件」的包由清单数据兜底，判定不留别名。
#
# 退出码: 0 判定通过；1 判定未通过；5 参数或证据不可用。
# ===========================================================================
set -uo pipefail

# 集合对账用 sort/comm，二者必须按同一码位序比较。宿主的 C.UTF-8 排序会
# 忽略大小写（NetworkManager 排到 n 段），comm 随即报「not in sorted
# order」并给出错误的差集；钉死 C 后同一份输入在任何机器上得到同一结论。
export LC_ALL=C

readonly EINVAL=5
readonly FWDIR=/usr/lib/firmware

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
MAN="${ROOT}/distro/firmware/fw-manifest.txt"
COMPS="${ROOT}/distro/comps/airymaxos-comps.xml"
SYSROOT=""
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

usage()
{
    printf '用法: ci/gates/fw-closure.sh'
    printf ' [--manifest FILE] [--comps FILE] [--root DIR]\n'
}

# comp_pkgs FILE —— comps 内全部包名，一行一个，不去重（重复声明本身
# 由 comps_lint 判，这里保留原始形态）。用 grep -o 而不是按行取末尾，
# 是为了不依赖「一行一个 packagereq」的排版习惯。
comp_pkgs()
{
    grep -o '<packagereq[^>]*>[^<]*' "$1" | sed 's/.*>//'
}

# fw_pkgs FILE —— 固件包谓词：名称含 firmware，或以 -ucode 结尾。
fw_pkgs()
{
    comp_pkgs "$1" | grep -E 'firmware|-ucode$' || true
}

# rows FILE —— 去掉注释与空行后的数据行。
rows()
{
    sed 's/#.*//' "$1" | awk 'NF'
}

# row_check —— 逐行校验字段数、类别、路径前缀与上级引用，合格行写入
# data 供后续对账；置 bad=1 而不提前退出，让一轮跑完全部问题。
row_check()
{
    local n=0 pkg cls pat extra

    while read -r pkg cls pat extra; do
        n=$((n + 1))
        if [ -z "${pat}" ] || [ -n "${extra}" ]; then
            err "清单第 ${n} 行字段数不等于 3"
            bad=1
            continue
        fi
        case "${cls}" in
            ''|*[!a-z0-9-]*)
                err "清单第 ${n} 行器件类别非法: ${cls}"
                bad=1
                continue
                ;;
        esac
        case "${pat}" in
            "${FWDIR}"/*) ;;
            *)
                err "清单第 ${n} 行不在 ${FWDIR} 下: ${pat}"
                bad=1
                continue
                ;;
        esac
        case "${pat}" in
            *..*)
                err "清单第 ${n} 行含上级引用: ${pat}"
                bad=1
                continue
                ;;
        esac
        printf '%s\t%s\t%s\n' "${pkg}" "${cls}" "${pat}" >>"${TMP}/data"
    done <"${TMP}/rows"

    if [ "${n}" -eq 0 ]; then
        err "固件清单为空: ${MAN}"
        bad=1
    fi
}

# dup_check —— 同一 (包, 路径) 出现两次即判红：重复条目会让按器件汇总
# 的计数虚高，且两条中一条被删时另一条会掩盖缺口。
dup_check()
{
    local d

    while IFS= read -r d; do
        err "清单条目重复: ${d}"
        bad=1
    done < <(cut -f1,3 "${TMP}/data" | sort | uniq -d)
}

# set_check —— 双向对账。
set_check()
{
    local p

    cut -f1 "${TMP}/data" | sort -u >"${TMP}/man"
    comp_pkgs "${COMPS}" | sort -u >"${TMP}/all"
    fw_pkgs "${COMPS}" | sort -u >"${TMP}/fw"

    if [ ! -s "${TMP}/fw" ]; then
        err "comps 未声明固件包: ${COMPS}"
        bad=1
    fi

    while IFS= read -r p; do
        if [ -n "${p}" ]; then
            err "comps 固件包未在清单声明: ${p}"
            bad=1
        fi
    done < <(comm -23 "${TMP}/fw" "${TMP}/man")

    while IFS= read -r p; do
        if [ -n "${p}" ]; then
            err "清单包不在 comps: ${p}"
            bad=1
        fi
    done < <(comm -23 "${TMP}/man" "${TMP}/all")
}

# file_check —— 实物核对：每条路径模式在 --root 内至少命中一个文件。
file_check()
{
    local pkg cls pat

    while IFS=$'\t' read -r pkg cls pat; do
        if ! compgen -G "${SYSROOT}${pat}" >/dev/null; then
            err "固件缺失 [${cls}] ${pat}"
            bad=1
        fi
    done <"${TMP}/data"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --manifest)
            [ -n "${2:-}" ] || { err "--manifest 需要文件路径"; exit "${EINVAL}"; }
            MAN="$2"
            shift 2
            ;;
        --comps)
            [ -n "${2:-}" ] || { err "--comps 需要文件路径"; exit "${EINVAL}"; }
            COMPS="$2"
            shift 2
            ;;
        --root)
            [ -n "${2:-}" ] || { err "--root 需要目录路径"; exit "${EINVAL}"; }
            SYSROOT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            err "未知参数: $1"
            exit "${EINVAL}"
            ;;
    esac
done

[ -r "${MAN}" ] || { err "清单不可读: ${MAN}"; exit "${EINVAL}"; }
[ -r "${COMPS}" ] || { err "comps 不可读: ${COMPS}"; exit "${EINVAL}"; }
if [ -n "${SYSROOT}" ] && [ ! -d "${SYSROOT}${FWDIR}" ]; then
    err "根目录下无固件树: ${SYSROOT}${FWDIR}"
    exit "${EINVAL}"
fi

bad=0
group "固件闭包"

rows "${MAN}" >"${TMP}/rows"
: >"${TMP}/data"
row_check
dup_check
set_check
[ -z "${SYSROOT}" ] || file_check

row_n="$(wc -l <"${TMP}/data" | tr -d '[:space:]')"
man_n="$(wc -l <"${TMP}/man" | tr -d '[:space:]')"
fw_n="$(wc -l <"${TMP}/fw" | tr -d '[:space:]')"

if [ "${bad}" -ne 0 ]; then
    die "固件闭包判定未通过（条目 ${row_n}，包 ${man_n}，comps 固件包 ${fw_n}）"
fi

say "固件闭包判定通过: 条目 ${row_n}，包 ${man_n}，comps 固件包 ${fw_n}"
endgroup
