#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_tui_e2e.sh — ci/gates/tui-e2e.sh 的参数契约回归
#
# 门禁本体要调 cargo 并跑真实二进制，耗时以十秒计；参数契约却是秒级可判的。
# 这里只把门禁与它依赖的两个 lib 复制到临时根，断言 --help、未知参数、
# --root 不存在这三条分支的退出码，以及缺 Cargo.lock 时 tui_setup 的失败
# 路径；不调 cargo，保证回归本身秒级完成。
#
# 用法: bash ci/tests/test_tui_e2e.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
GATE="${FX}/ci/gates/tui-e2e.sh"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/system/tui"
cp "${ROOT}/ci/gates/tui-e2e.sh" "${GATE}"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/lib/tui-gate.sh" "${FX}/ci/lib/tui-gate.sh"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

# 门禁会读 CI 专属环境文件；继承它们会让诊断输出静默改道。
run()
{
    env -u GITHUB_OUTPUT -u GITHUB_ENV -u GITHUB_ACTIONS \
        bash "${GATE}" "$@" 2>&1
}

expect()
{
    local name="$1" want="$2" got="$3" out="$4"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    ok "${name}"
}

out="$(run --help)"
expect "--help 退出 0" 0 "$?" "${out}"

out="$(run --bogus)"
expect "未知参数退出 5" 5 "$?" "${out}"

out="$(run --root "${tmp}/absent")"
expect "--root 不存在退出 5" 5 "$?" "${out}"

# tui_setup 的缺 Cargo.lock 分支用 die（退出 1），属于「工作区不完整」，
# 与参数类失败（5）分开；这里按实现断言，避免把两种失败混成一个码。
out="$(run --root "${FX}")"
expect "缺 Cargo.lock 退出 1" 1 "$?" "${out}"

if [ "${fails}" -ne 0 ]; then
    printf 'test_tui_e2e: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_tui_e2e: 全部通过\n'
