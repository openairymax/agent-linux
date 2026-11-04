#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/ledger-check.sh — 施工账本可复现与债号稳定门禁（DOC-01）
#
# 用法:
#   ci/gates/ledger-check.sh [--tree DIR]
#
# 背景：0.0.3 的账本被人手改过——改完之后前后数字对不上，也无法判断哪一版
# 是真的，于是「进度」变成看谁最后编辑。0.0.4 把账本改成脚本产出，纪律写在
# 生成器里；但纪律只有被执行才算纪律，所以还要有一条门禁每隔一段重算一次：
# 已提交的账本必须与种子产物逐字节一致，§11 债表的编号集合与顺序必须等于
# 种子里的债号。前者防手改，后者防「改判据时顺手增删债号」——后者比前者
# 更隐蔽：文字看起来对，闭合关系已经断了。
#
# 判定实现在 docs-closed 的生成器（--check 模式）：本文件只做参数校验与
# 调用，不复制任何账本逻辑，也不写文件。账本是生成物，门禁永不落盘。
#
#   --tree DIR  文档树根，默认为超级仓里的 docs/docs-closed/agent-linux-dev
#
# 独立检出本仓时（如 agent-linux 的 GitHub Actions）没有文档树，此时打印
# note 后返回 0：账本不在本仓，判失败只会逼人在 CI 里写 if；判通过才是假绿。
# 与 docs-links.sh 同一条口径——跳过留在输出里，不留在退出码里。
#
# 退出码: 0 通过或本仓无文档树；1 账本漂移或债号不一致；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly GEN=10-references/generators/bp_004_gen.py
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TREE=""

usage()
{
    cat <<'EOF'
用法: ci/gates/ledger-check.sh [--tree DIR]
EOF
}

need_dir()
{
    [ -n "$2" ] || { err "$1 需要目录路径"; exit "${EINVAL}"; }
    [ -d "$2" ] || { err "$1 目录不存在: $2"; exit "${EINVAL}"; }
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tree) need_dir "$1" "${2:-}"; TREE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

if [ -z "${TREE}" ]; then
    TREE="$(cd "${ROOT}/.." && pwd -P)/docs/docs-closed/agent-linux-dev"
    if [ ! -d "${TREE}" ]; then
        note "未检出文档树（${TREE}），本门禁不适用"
        exit 0
    fi
fi

[ -f "${TREE}/${GEN}" ] || {
    err "缺少生成器: ${TREE}/${GEN}"
    exit "${EINVAL}"
}
need_cmd python3

python3 "${TREE}/${GEN}" --check || exit 1
say "账本可复现且债号稳定"
exit 0
