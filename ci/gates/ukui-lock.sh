#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/ukui-lock.sh — UKUI 组件台账同源门禁（R1-10 / UK-01 / UK-02）
#
# 用法:
#   ci/gates/ukui-lock.sh [--source DIR] [--docs DIR]
#
# 背景：基底五段 repodata 中 UKUI 只在 EPOL/main 有，且没有 ukui-shell；
# 元包 ukui 与 ukui-desktop-environment 有，但本版刻意不选。桌面闭包只能
# 逐组件声明；0.0.3 的 comps 曾把元包列为必装，与基底实际内容不符。
# 台账 lockfile/ukui-components.yaml 是包名与 NVRA 的唯一事实源，本门禁
# 断言台账、comps、冻结输入（以及可选取证册）四者同源，任一环漂移即判红。
#
# 判据全部实现在 distro/lockfile/ukui_check.py：本文件只做参数校验与
# 调用，不复制判定逻辑，避免两处口径分叉。
#
#   --source DIR  仓库根，须含 distro/lockfile；默认取本脚本上两级
#   --docs DIR    内部取证册目录（可选；CI 不检出 docs-closed 仓）
#
# 退出码: 0 通过；1 校验失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly CHECK=distro/lockfile/ukui_check.py
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

SOURCE=""
DOCS=""

usage()
{
    cat <<'EOF'
用法: ci/gates/ukui-lock.sh [--source DIR] [--docs DIR]
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
        --docs)   need_dir "$1" "${2:-}"; DOCS="$2"; shift 2 ;;
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

args=(verify --root "${SOURCE}")
[ -z "${DOCS}" ] || args+=(--docs "${DOCS}")

python3 "${SOURCE}/${CHECK}" "${args[@]}" || exit 1
say "UKUI 台账同源通过"
exit 0
