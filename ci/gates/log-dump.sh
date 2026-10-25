#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/log-dump.sh — 失败时摊开产物里的全部日志
#
# 用法: ci/gates/log-dump.sh --dir DIR [--lines N]
#
# 失败现场只存在于 runner 上，job 结束即销毁。此前这一步是 YAML 里的
# while-read-find 组合，节流靠 tail 120 行；抽成脚本后可以本地对着同一份
# 产物目录重放，不必再跑一次两小时的云端 job 才能看到日志尾部。
#
# 恒退出 0：这是呈现步骤，不是判定步骤。
# ===========================================================================
set -uo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

DIR=""
LINES=120

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)   DIR="${2:-}"; shift 2 ;;
        --lines) LINES="${2:-}"; shift 2 ;;
        -h|--help) echo "用法: ci/gates/log-dump.sh --dir DIR [--lines N]"; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

[ -n "${DIR}" ] || { err "--dir 必填"; exit 1; }
[ -d "${DIR}" ] || { warn "目录不存在，无可摊开的日志: ${DIR}"; exit 0; }

n=0
while IFS= read -r log; do
    n=$(( n + 1 ))
    group "${log#"${DIR}"/}"
    tail -n "${LINES}" "${log}"
    endgroup
done < <(find "${DIR}" -type f -name '*.log' | sort)

[ "${n}" -gt 0 ] || warn "没有 .log 文件: ${DIR}"

exit 0
