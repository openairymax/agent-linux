#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/completeness.sh — 功能归属册的三态对账
#
# 用法: ci/ci.sh completeness [audit] [--doc FILE] [--source DIR]
#                              [--docs DIR] [--release] [--json]
#
# 归属册是 docs-linux 功能面缺口的唯一记账处（施工方案 §2.4）：每条缺口
# 落在已实现、0.0.5、移出（承接版本 X）三态之一；§3 统计必须与明细逐项
# 相等；证据路径、门禁与设计锚点必须能在工作树里打开。
#
# 归属册住在 docs-closed，只随开发机存在，不进源码仓。文档缺席时本门禁
# 不做判定（打印一行 note 后退 0），与 coupling-scan 同口径；显式给出
# --doc 却读不到，则是一次参数错误，立即退 5。
#
# --release 多一条判据：账上不许再有 0.0.5——发布前每条缺口都必须落成
# 已实现或移出（承接版本），不接受「还没做」。
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
DOC="${ROOT}/../docs/docs-closed/agent-linux-dev/10-references/completeness-audit.md"
DOCS="$(dirname "${DOC}")/../../../docs-linux"
SRC="${ROOT}"
SET=0
ARGS=()

usage()
{
    cat <<'EOF'
用法: ci/ci.sh completeness [audit] [--doc FILE] [--source DIR]
                             [--docs DIR] [--release] [--json]
EOF
}

cmd="audit"
if [ "$#" -gt 0 ] && [ "${1#--}" = "$1" ]; then
    cmd="$1"
    shift
fi

while [ "$#" -gt 0 ]; do
    case "$1" in
        --doc)            DOC="${2:-}"; SET=1; shift 2 ;;
        --source)         SRC="${2:-}"; shift 2 ;;
        --docs)           DOCS="${2:-}"; shift 2 ;;
        --release|--json) ARGS+=("$1"); shift ;;
        -h|--help)        usage; exit 0 ;;
        *)                err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ "$cmd" = audit ] || { usage >&2; exit "${EINVAL}"; }
[ -n "${DOC}" ] || { err "--doc 需要文件路径"; exit "${EINVAL}"; }

if [ ! -f "${DOC}" ]; then
    [ "${SET}" -eq 0 ] || { err "归属册不存在: ${DOC}"; exit "${EINVAL}"; }
    note "归属册未检出（docs-closed 仅开发机持有），本门禁跳过"
    exit 0
fi

need_dir --source "${SRC}"
need_dir --docs "${DOCS}"
need_cmd python3
python3 "${ROOT}/ci/lib/completeness.py" "${DOC}" "${SRC}" "${DOCS}" \
    ${ARGS[@]+"${ARGS[@]}"}
