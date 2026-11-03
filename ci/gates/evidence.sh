#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/evidence.sh — 把一装机会话的证据收成一个可自证的包
#
# 用法: ci/gates/evidence.sh --out DIR [--dir DIR]… [--extra FILE]… [--require]
#
# 为什么需要它：证据原先由 workflow 里的手写 glob 收集，两处清单各自演化
# ——一边补了 run-*/*.log，另一边没有；某次失败轮次缺的正是没补的那项。
# 收集规则属于判据：写进 YAML 就只能在云端验证，也只能在云端修正。
#
# 四件套（每个 --dir 为一条腿，同一腿内按相对路径保留层次）：
#   tap.txt               装机 TAP 总账
#   install-manifest.txt  轮次结果与 ISO 摘要
#   run-*/boot.log        冷启动串口日志，每个启动轮次一条
#   smoke-*.tap           会话内冒烟 TAP，验收剖面必有
# 缺项逐条写入 evidence-missing.txt；验收剖面（ACCEPTANCE=1，或 --require）
# 判失败，产品剖面只告警——产品 ISO 本就不产冒烟 TAP，缺它不算缺件。
#
# 包内另生成 evidence-manifest.txt（sha256 + 字节数 + 包内路径）。包因此
# 可以脱离会话自证未被改动，也可以对着旧产物复算当时到底收进了什么。
#
# 退出码: 0 收齐或缺项已告警；1 验收剖面缺件；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"
# shellcheck source=../lib/profile.sh
. "${LIB}/profile.sh"

OUT=""
DIRS=()
EXTRAS=()
REQUIRE=0
MISS=0
NOTAP=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)     OUT="${2:-}"; shift 2 ;;
        --dir)     DIRS+=("${2:-}"); shift 2 ;;
        --extra)   EXTRAS+=("${2:-}"); shift 2 ;;
        --require) REQUIRE=1; shift ;;
        -h|--help) echo "用法: ci/gates/evidence.sh --out DIR [--dir DIR]… [--extra FILE]…"; exit 0 ;;
        *)         err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

# 验收剖面的冒烟 TAP 是判据的一部分，缺件不能降级成告警。
[ "$(airy_profile)" -eq 1 ] && REQUIRE=1

[ -n "${OUT}" ] || { err "--out 必填"; exit "${EINVAL}"; }
[ "${#DIRS[@]}" -gt 0 ] || { err "至少需要一个 --dir"; exit "${EINVAL}"; }
for d in "${DIRS[@]}"; do
    [ -d "${d}" ] || { err "目录不存在: ${d}"; exit "${EINVAL}"; }
done

mkdir -p "${OUT}"
missing="${OUT}/evidence-missing.txt"
: >"${missing}"
# 重跑复放同一产物目录时，清单必须按本次收集重算，不能在旧行上追加。
: >"${OUT}/evidence-manifest.txt"

# 缺项只记录、不中断：一次失败轮次往往同时缺几件，逐条报全才知道是
# 「装机没跑起来」还是「跑完了但没留证」。
miss()
{
    printf '%s\n' "$1" >>"${missing}"
    MISS=$((MISS + 1))
}

take()
{
    local leg="$1" dir="$2" src="$3"
    local rel dst
    rel="${src#"${dir}"/}"
    dst="${OUT}/${leg}/${rel}"
    mkdir -p "${dst%/*}"
    cp -p "${src}" "${dst}"
}

# 一条腿的收集：四件套按类判有无，不按文件个数——run-* 目录数随固件与
# 轮次变化，用个数判断会把「两轮都成功」当成缺件。
collect()
{
    local dir="$1" leg f
    leg="$(basename "${dir}")"
    for f in tap.txt install-manifest.txt; do
        if [ -f "${dir}/${f}" ]; then
            take "${leg}" "${dir}" "${dir}/${f}"
        else
            miss "${leg}/${f}"
        fi
    done

    shopt -s nullglob
    local boots=("${dir}"/run-*/boot.log) taps=("${dir}"/smoke-*.tap)
    shopt -u nullglob
    if [ "${#boots[@]}" -eq 0 ]; then
        miss "${leg}/run-*/boot.log"
    else
        for f in "${boots[@]}"; do take "${leg}" "${dir}" "${f}"; done
    fi
    if [ "${#taps[@]}" -eq 0 ]; then
        # 产品 ISO 本就不产冒烟 TAP：不记缺件，但要在结论里说明，否则
        # 「四件套齐全」会让人以为冒烟那一路也验过了。
        if [ "${REQUIRE}" -eq 1 ]; then
            miss "${leg}/smoke-*.tap"
        else
            NOTAP=$((NOTAP + 1))
        fi
    else
        for f in "${taps[@]}"; do take "${leg}" "${dir}" "${f}"; done
    fi

    # 同目录下的其余日志是失败归因的一手材料，存在即收，缺失不判。
    shopt -s nullglob
    for f in "${dir}"/run-*/*.log; do
        case "${f}" in */boot.log) continue ;; esac
        take "${leg}" "${dir}" "${f}"
    done
    shopt -u nullglob
}

for d in "${DIRS[@]}"; do
    collect "${d}"
done

for f in "${EXTRAS[@]}"; do
    [ -f "${f}" ] || continue
    mkdir -p "${OUT}/extras"
    cp -p "${f}" "${OUT}/extras/$(basename "${f}")"
done

# 清单只列包内文件，两列摘要文件自身不入账（否则清单无法自证）。
while IFS= read -r f; do
    printf '%s  %s  %s\n' \
        "$(sha256sum "${f}" | cut -d' ' -f1)" "$(wc -c <"${f}" | tr -d ' ')" \
        "${f#"${OUT}"/}" >>"${OUT}/evidence-manifest.txt"
done < <(find "${OUT}" -type f ! -name 'evidence-manifest.txt' \
    ! -name 'evidence-missing.txt' | sort)

files="$(wc -l <"${OUT}/evidence-manifest.txt" | tr -d ' ')"
[ "${NOTAP}" -gt 0 ] && \
    warn "非验收剖面：${NOTAP} 条腿无冒烟 TAP，不作为缺件"
if [ "${MISS}" -eq 0 ]; then
    say "证据包 ${files} 项，四件套齐全"
    exit 0
fi

err "证据包缺 ${MISS} 项（清单见 ${missing}）:"
sed 's/^/  missing: /' "${missing}" >&2
[ "${REQUIRE}" -eq 1 ] && exit 1
warn "非验收剖面：缺件保留在包内，不改变本次结论"
exit 0
