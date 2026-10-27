#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/distro-lint.sh — distro 子仓静态门禁
#
# 用法: ci/gates/distro-lint.sh
#
# distro 子仓的 spec / comps / kickstart / preset 规则与工具单测，此前只有
# 开发者本地手动执行：os-chk-verify 的 paths 不含 distro gitlink，子仓内
# 改坏一条断言不会触发任何流水线，要等下一次组装镜像才暴露，一轮几十
# 分钟。本 gate 把子仓入口接到 CI，让同类缺陷停在 L0。
#
# 逻辑本体仍在 distro/ci/run-lint.sh，只依赖 python3 标准库，本地与 runner
# 跑的是同一条命令；本文件只作为 YAML 入口，不复制任何检查项。
#
# 退出码: 透传 distro/ci/run-lint.sh。
# ===========================================================================
set -euo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

group "distro 静态门禁"
bash "${ROOT}/distro/ci/run-lint.sh"
endgroup
