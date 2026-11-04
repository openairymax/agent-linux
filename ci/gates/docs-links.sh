#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/docs-links.sh — 文档树悬空链接门禁（DOC-02）
#
# 用法:
#   ci/gates/docs-links.sh [--source DIR] [--exclude NAME]
#
# 背景：docs-linux 的目录在 0.0.x 期间重组过两轮，编号变了、文档合并了，
# 而正文里的相对链接是按旧布局写的。读者点到 404 才知道文档没跟上，这
# 比缺一篇文档更伤——缺了看得见，链接坏了看不出来。本门禁把这条变成
# 可复算的判定。
#
# 判定实现在 ci/doc_links.py：本文件只做参数校验与调用，不复制解析逻辑。
#
#   --source DIR   文档树根，默认为超级仓里的 docs/docs-linux
#   --exclude NAME 整目录跳过的名字，默认 20-archive（冻结归档不改写）
#
# 检查范围只到 docs-linux，理由有两层：本节交付物就是它，以及 docs 超级仓
# 的另外两个目录不归本仓管——docs-closed/agent-workload-dev 与 docs-workload
# 是 agentrt 项目的设计文档，链接按那边的布局写，在这边判只会把别人的债
# 算到本仓头上；要单独核对时用 --source 显式指过去，不改默认口径。
#
# 独立检出本仓时（如 agent-linux 的 GitHub Actions）没有文档树，此时打印
# note 后返回 0：把「不适用」判成失败只会逼人在 CI 里写 if，把「跳过」判成
# 通过才是假绿，所以跳过必须留在输出里，而不是留在退出码里。
#
# 退出码: 0 通过或本仓无文档树；1 存在悬空链接；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly CHECK=ci/doc_links.py
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SOURCE=""
EXCLUDE="20-archive"

usage()
{
    cat <<'EOF'
用法: ci/gates/docs-links.sh [--source DIR] [--exclude NAME]
EOF
}

need_dir()
{
    [ -n "$2" ] || { err "$1 需要目录路径"; exit "${EINVAL}"; }
    [ -d "$2" ] || { err "$1 目录不存在: $2"; exit "${EINVAL}"; }
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) need_dir "$1" "${2:-}"; SOURCE="$2"; shift 2 ;;
        --exclude) [ -n "${2:-}" ] || { err "--exclude 需要名字"; exit "${EINVAL}"; }
                   EXCLUDE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

if [ -z "${SOURCE}" ]; then
    SOURCE="$(cd "${ROOT}/.." && pwd -P)/docs/docs-linux"
    if [ ! -d "${SOURCE}" ]; then
        note "未检出文档树（${SOURCE}），本门禁不适用"
        exit 0
    fi
fi

[ -f "${ROOT}/${CHECK}" ] || {
    err "缺少检查器: ${ROOT}/${CHECK}"
    exit "${EINVAL}"
}
need_cmd python3

python3 "${ROOT}/${CHECK}" --source "${SOURCE}" --exclude "${EXCLUDE}" ||
    exit 1
say "文档树无悬空链接"
exit 0
