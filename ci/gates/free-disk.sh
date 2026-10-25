#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/free-disk.sh — 托管 runner 磁盘清理
#
# 用法: ci/gates/free-disk.sh
#
# 无参数：动作是固定的，参数只会带来「这里删一点那里不删」的漂移。逻辑
# 本体在 ci/lib/host.sh，供后续迁移的工作流复用，本文件只作为 YAML 入口。
#
# 退出码: 恒 0；非 CI 环境直接跳过。
# ===========================================================================
set -uo pipefail

CI_NS=free-disk
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"
# shellcheck source=../lib/host.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/host.sh"

free_disk
