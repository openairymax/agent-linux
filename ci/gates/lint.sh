#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/lint.sh — CI 层自检入口
#
# 用法: ci/gates/lint.sh
#
# 这是唯一一个此前绕开分发器的门禁：os-chk-verify 直接写了 `ci/lint.sh`，
# 于是「workflow 只允许 checkout / ci/ci.sh <gate> / upload」这条薄化约定
# 在自家 lint 步骤上破了一个口子。本文件只做入口归一，逻辑仍在 ci/lint.sh，
# 不复制任何检查项。
#
# 退出码: 透传 ci/lint.sh。
# ===========================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

exec bash "${ROOT}/ci/lint.sh"
