#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/base-eol.sh — openEuler 基底选型同源门禁（M-35 / OE-01）
#
# 用法:
#   ci/gates/base-eol.sh [--source DIR]
#
# 背景：24.03 LTS 的 SP 分支有大、小两种节奏，小 SP 的维护窗口只有 9 个
# 月，不足以覆盖本版的收口日。基底一旦落后，repo 五段、冻结输入、安装
# 配方与构建宏会各自指向不同 SP，dnf 只会在装机现场报「找不到仓库」。
#
# 判据全部实现在 distro/lockfile/base_check.py：本文件只做参数校验与
# 调用，不复制判定逻辑，避免 Bash 门禁与 Python 台账两份口径分叉。
#
#   --source DIR  仓库根，须含 distro/lockfile；默认取本脚本上两级
#
# 退出码: 0 通过；1 校验失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly CHECK=distro/lockfile/base_check.py
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

SOURCE=""

usage()
{
    cat <<'EOF'
用法: ci/gates/base-eol.sh [--source DIR]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) need_dir "$1" "${2:-}"; SOURCE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${SOURCE}" ] ||
    SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[ -f "${SOURCE}/${CHECK}" ] || {
    err "缺少校验器: ${SOURCE}/${CHECK}"
    exit "${EINVAL}"
}
need_cmd python3

python3 "${SOURCE}/${CHECK}" verify --root "${SOURCE}" || exit 1
say "openEuler 基底选型同源通过"
exit 0
