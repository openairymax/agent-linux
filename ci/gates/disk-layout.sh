#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/disk-layout.sh — 系统与数据分区布局判定
#
# 用法: ci/gates/disk-layout.sh [--snapshot FILE]
#
# DISK-01 判据①②：装机后 / 与 /data 必须落在各自的分区上，且数据分区是
# 所在盘的最后一个分区。非末位分区无法原地扩容，用户真要扩 /data 时只剩
# 重装一条路，而重装会连带改写系统分区——这正是该判据要挡住的结果。
#
# 判定本体在 system 子仓的 airydisk.layout，本 gate 只调用同一份实现并把
# token 转给流水线，规则不在 YAML 里重写第二遍。
#
# runner 上没有受测机器（285H 是物理机），--snapshot 因此是常规入口：实机
# 取证时存一份 `airy-disk --json status`，此后每次流水线都对着同一份证据
# 复算，结论不随机器是否在场而变。
#
# 退出码: 0 判定通过；1 判定未通过；5 参数或快照不可用。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
CLI="${ROOT}/system/bin/airy-disk"

SNAP=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --snapshot)
            [ -n "${2:-}" ] || { err "--snapshot 需要文件路径"; exit "${EINVAL}"; }
            SNAP=(--snapshot "$2")
            shift 2
            ;;
        -h|--help)
            echo "用法: ci/gates/disk-layout.sh [--snapshot FILE]"
            exit 0
            ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

need_cmd python3

group "布局判定"
out="$(python3 "${CLI}" --json layout "${SNAP[@]+"${SNAP[@]}"}" 2>&1)"
rc=$?

case "${rc}" in
    0) say "布局判定通过: ${out}" ;;
    2) err "快照不可用: ${out}"; exit "${EINVAL}" ;;
    3) die "布局判定未通过: ${out}" ;;
    *) die "布局判定异常退出（rc=${rc}）: ${out}" ;;
esac
endgroup
