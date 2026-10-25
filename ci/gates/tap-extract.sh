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
# 退出码: 0 通过；1 缺 boot.log / 必需时 TAP 为空；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

DIR=""
REQUIRE=0
TOKEN='AIRYMAX-TAP|'

# 验收剖面下必须还原出非空 TAP；--require 与 ACCEPTANCE=true 等价。
case "${ACCEPTANCE:-0}" in
    1|true|yes) REQUIRE=1 ;;
esac

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)     DIR="${2:-}"; shift 2 ;;
        --require) REQUIRE=1; shift ;;
        -h|--help) echo "用法: ci/gates/tap-extract.sh --dir DIR [--require]"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${DIR}" ] || { err "--dir 必填"; exit "${EINVAL}"; }
[ -d "${DIR}" ] || die "目录不存在: ${DIR}"

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
