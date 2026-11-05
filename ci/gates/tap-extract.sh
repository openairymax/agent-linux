#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/tap-extract.sh — 从串口日志还原会话内冒烟 TAP
#
# 用法: ci/gates/tap-extract.sh --dir DIR [--require]
#
# 会话内 reporter 以 AIRYMAX-TAP| 前缀把 TAP 原文逐行转发到串口，宿主机
# 只观测串口。此处从 run-*/boot.log 还原 smoke-*.tap，补齐验收发行方案
# §9.3 的 L3 归档证据。
#
# --require：验收剖面下每个启动腿都必须还原出非空 TAP（空文件等于冒烟
# 根本没有执行，不能当成「通过但无输出」）。
#
# 腿目录不存在与「目录在但没有 TAP」是两件事，必须分开判：
#   * 目录不存在 = 该腿整段没运行。accept 的三条腿（U 盘、CD-ROM 两固件）
#     顺序执行，前一条失败后其后的腿被 Actions 跳过，目录自然不存在。此时
#     没有可还原的对象，也无从判断这条腿做得好不好——判定权在该腿自己的
#     步骤，它已经红了。这里再判一次失败，只会让一次真因变成好几处红叉，
#     排障时反而找不到源头（run 41972665005：唯一真因是 airy-rt-bandwidth
#     启动失败，输出里却并列四条失败）。
#   * 目录在但没有 boot.log、或有 boot.log 而 TAP 为空 = 该腿跑了却没留下
#     证据，--require 下判失败。这才是本 gate 要守的东西。
# 同级的 boot-annotate 一直按这个分类（没有 boot.log 只出 note），两条路径
# 对同一事实曾给出两种结论，现在统一。
#
# 退出码: 0 通过，或该腿未运行；1 缺 boot.log / 必需时 TAP 为空；
#         5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)"
# shellcheck source=../lib/log.sh
. "${LIB}/log.sh"
# shellcheck source=../lib/profile.sh
. "${LIB}/profile.sh"

DIR=""
REQUIRE=0
TOKEN='AIRYMAX-TAP|'

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)     DIR="${2:-}"; shift 2 ;;
        --require) REQUIRE=1; shift ;;
        -h|--help) echo "用法: ci/gates/tap-extract.sh --dir DIR [--require]"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

# 验收剖面下必须还原出非空 TAP；--require 与 ACCEPTANCE=1|true|yes 等价。
[ "$(airy_profile)" -eq 1 ] && REQUIRE=1

[ -n "${DIR}" ] || { err "--dir 必填"; exit "${EINVAL}"; }

# 被跳过的腿不归本 gate 判：目录都不存在时没有任何串口字节可还原。
if [ ! -d "${DIR}" ]; then
    note "该腿未运行，跳过 TAP 还原: ${DIR}"
    exit 0
fi

shopt -s nullglob
logs=("${DIR}"/run-*/boot.log)
shopt -u nullglob
if [ "${#logs[@]}" -eq 0 ]; then
    # 非验收剖面下 TAP 只是归档证据，缺失不改变结论；验收剖面下它就是判据。
    if [ "${REQUIRE}" -eq 1 ]; then
        die "没有 boot.log: ${DIR}/run-*/boot.log"
    fi
    warn "没有 boot.log，跳过 TAP 还原: ${DIR}"
    exit 0
fi

for log in "${logs[@]}"; do
    dir="$(basename "$(dirname "${log}")")"
    out="${DIR}/smoke-${dir#run-}.tap"
    awk -v t="${TOKEN}" 'i = index($0, t) { print substr($0, i + length(t)) }' \
        "${log}" >"${out}"
    lines="$(wc -l <"${out}")"
    say "${log} → ${out} (${lines} 行)"
done

if [ "${REQUIRE}" -eq 1 ]; then
    shopt -s nullglob
    taped=("${DIR}"/smoke-*.tap)
    shopt -u nullglob
    for f in "${taped[@]}"; do
        [ -s "${f}" ] || die "smoke TAP 未能从串口还原: ${f}"
    done
fi
