#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/lint.sh — 仓库级 CI 层自检
#
# 用法: ci/lint.sh
#
# 检查三件事：
#   1. workflow 薄化与命名约束（ci/check-workflows.py）；
#   2. ci/ 下每个脚本可被 bash -n 解析；
#   3. 有 shellcheck 就用上，没有只告警——本地开发者不该被工具缺失拦住。
#
# 与 distro/ci/run-lint.sh 的分工：后者管 distro 子模块自己的单元测试与
# 打包脚本；本文件只覆盖仓库根这一层（门禁 + workflow）。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=lib/log.sh
. "${ROOT}/ci/lib/log.sh"

rc=0

group "workflow 薄化与命名约束"
if python3 "${ROOT}/ci/check-workflows.py"; then
    say "通过"
else
    err "ci/check-workflows.py 未通过"
    rc=1
fi
endgroup

group "bash 语法"
n=0
while IFS= read -r f; do
    n=$(( n + 1 ))
    bash -n "${f}" || { err "语法错误: ${f}"; rc=1; }
done < <(find "${ROOT}/ci" -type f -name '*.sh' | sort)
say "已解析 ${n} 个脚本"
endgroup

if command -v shellcheck >/dev/null 2>&1; then
    group "shellcheck"
    while IFS= read -r f; do
        shellcheck -S warning -x "${f}" || { err "shellcheck: ${f}"; rc=1; }
    done < <(find "${ROOT}/ci" -type f -name '*.sh' | sort)
    endgroup
else
    warn "未安装 shellcheck，跳过静态检查"
fi

if [ "${rc}" -eq 0 ]; then
    say "== ci lint passed =="
fi
exit "${rc}"
