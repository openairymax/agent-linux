#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/ci.sh — 仓库级门禁分发器
#
# 用法: ci/ci.sh --list
#       ci/ci.sh <gate> [参数...]
#
# 分层：
#   ci/          仓库级门禁：编排（取介质、起容器、跑装机）与断言（清单、
#                摘要、TAP、日志）。可在任意一台留有产物的机器上重放。
#   distro/ci/   distro 子模块内部构建入口（run-image-ci.sh、run-lint.sh），
#                由本层调用，不反向依赖本层。
#   .github/    只留 checkout → ci/ci.sh <gate> → upload artifact。
#
# 之所以要有分发器：workflow 里每多一段内联 shell，就多一处只能靠云端 job
# 验证的逻辑。收敛到 gate 之后，同一条命令在本地和 runner 上行为一致，
# 而且可以对着历史产物做回归（B50 现场机首次就是这样复算了 0.0.3 的 ISO）。
#
# 门禁清单不另设注册表：每个 gate 的头部注释自带「用法:」，--list 直接
# 读取，避免注册表与实际脚本漂移。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
GATES="${ROOT}/gates"

usage()
{
    cat <<'EOF'
用法: ci/ci.sh --list
      ci/ci.sh <gate> [参数...]

  --list   列出可用门禁及其用法
  -h       显示本帮助
EOF
}

list_gates()
{
    local f name line
    for f in "${GATES}"/*.sh; do
        [ -f "${f}" ] || continue
        name="$(basename "${f}" .sh)"
        # 「用法:」后面可能换行续写（多行调用形态），取紧随其后的缩进行。
        line="$(sed -n '/^# 用法:/{s/^# 用法:[[:space:]]*//p;q}' "${f}")"
        if [ -z "${line}" ]; then
            line="$(sed -n '/^# 用法:/{n;s/^#[[:space:]]*//p;q}' "${f}")"
        fi
        printf '%-16s %s\n' "${name}" "${line:-（未登记用法）}"
    done
}

case "${1:-}" in
    ''|-h|--help) usage; exit 0 ;;
    --list)       list_gates; exit 0 ;;
esac

gate="$1"
shift

gate_file="${GATES}/${gate}.sh"
[ -f "${gate_file}" ] || {
    printf 'error: 未知门禁 "%s"\n\n' "${gate}" >&2
    list_gates >&2
    exit 2
}

exec bash "${gate_file}" "$@"
