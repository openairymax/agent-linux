#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/mgmt-leaves.sh — 叶子子仓 CI 编排与仓库自有文档校验
#
# 用法: ci/gates/mgmt-leaves.sh
#
# 三项断言此前是 mgmt-verify.yml 的内联 shell：叶子子仓 CI 状态聚合、
# markdownlint、版权头。它们都是「同一个判据在本地也该先跑一遍」的逻辑，
# 因此下沉到本 gate；YAML 只剩一次分派。
#
# 叶子仓未全部接入 CI 时其状态为 unknown，aggregate 脚本自身只告警；红色
# 才阻断，故此处不再重复判定。
#
# markdownlint 只扫仓库自有 markdown（根目录、.github/、tools/），不扫子模
# 块内容——子仓有自己的 lint，扫进来会把别人的历史债务算到本仓头上。
#
# 退出码: 0 全部通过；1 任一断言失败。
# ===========================================================================
set -euo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "${ROOT}"

readonly MD_GLOBS=('*.md' '.github/**/*.md' 'tools/**/*.md')

leaf_ci()
{
    need_cmd python3
    python3 tools/aggregate-subrepo-ci.py --fail-on-red
    say "叶子子仓 CI 状态已聚合"
}

md_lint()
{
    if ! command -v markdownlint-cli2 >/dev/null 2>&1; then
        need_cmd npm
        npm install -g markdownlint-cli2
    fi
    markdownlint-cli2 "${MD_GLOBS[@]}"
    say "仓库自有 markdown 校验通过"
}

copyright()
{
    need_cmd python3
    python3 tools/check-copyright.py --root .
    say "版权头校验通过"
}

main()
{
    group "叶子子仓编排与文档校验"
    leaf_ci
    md_lint
    copyright
    endgroup
    say "编排门禁通过"
}

main "$@"
