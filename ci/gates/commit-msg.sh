#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/commit-msg.sh — 提交信息不得写进本机路径与低级措辞
#
# 用法: ci/gates/commit-msg.sh [--range REV | REV..REV] [--repo DIR]
#                              [--max-bytes 72]
#
# 提交信息会同步到三个公开仓库，也会被发布说明引用。家目录、挂载盘、盘符
# 一旦写进去，撤回只能改写历史；而改写历史会连带换掉发布树上的子仓指针
# （取证见内部册的提交信息治理册）。另一类判据是措辞：把拼写失误、临时
# 缓冲这类内部小问题写成永久记录，既没有信息量，也让历史看起来潦草。
#
# 缺省只判 HEAD 一条：流水线里是浅检出，跨提交区间不一定完整。要判一段
# 历史时显式给 --range A..B。
#
# 退出码: 0 合规；1 有违规；5 参数或仓库不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
RNG="HEAD"
REPO="${ROOT}"
MAXB=72

usage()
{
    cat <<'EOF'
用法: ci/gates/commit-msg.sh [--range REV | REV..REV] [--repo DIR]
                             [--max-bytes 72]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    --range)     RNG="${2:-}"; shift 2 ;;
    --repo)      REPO="${2:-}"; shift 2 ;;
    --max-bytes) MAXB="${2:-}"; shift 2 ;;
    -h|--help)   usage; exit 0 ;;
    *)           err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

command -v python3 >/dev/null 2>&1 || { err "缺少 python3"; exit "${EINVAL}"; }
git -C "${REPO}" rev-parse --git-dir >/dev/null 2>&1 ||
    { err "不是 git 仓库: ${REPO}"; exit "${EINVAL}"; }

group "提交信息卫生（${RNG}）"
python3 "${ROOT}/ci/checks/commit_msg.py" \
    --range "${RNG}" --repo "${REPO}" --max-bytes "${MAXB}"
rc=$?
endgroup

if [ "${rc}" -eq 0 ]; then
    say "提交信息合规"
else
    err "提交信息未通过（${RNG}）"
fi
exit "${rc}"
