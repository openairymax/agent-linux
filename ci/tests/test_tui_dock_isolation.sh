#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_tui_dock_isolation.sh — 双通路隔离门禁的分支回归
#
# 真实树零交叉只说明今天没连上。判据要能拦住三条连接方式：按名字依赖
# agentrt、用 path 依赖绕过名字检查、以及反向从运行时依赖 TUI crate。
# 三种各造一份夹具，缺一即判负；对侧树缺席时只判本侧，不因看不见而对。
#
# 用法: bash ci/tests/test_tui_dock_isolation.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
PY="${ROOT}/ci/checks/tui_isolation.py"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0
TUI="${tmp}/work/system/tui"
RT="${tmp}/work/agentrt"

seed()
{
    rm -rf "${tmp}/work"
    mkdir -p "${TUI}/crates/airy-adapters" "${RT}/core"
    printf '[workspace]\nmembers = ["crates/*"]\n' >"${TUI}/Cargo.toml"
    {
        printf '[package]\nname = "airy-adapters"\nversion = "0.0.5"\n'
        printf '[dependencies]\nserde = { version = "1" }\n'
    } >"${TUI}/crates/airy-adapters/Cargo.toml"
    printf '[package]\nname = "agentrt-core"\nversion = "0.0.5"\n[dependencies]\n' \
        >"${RT}/core/Cargo.toml"
}

verdict()
{
    out="$(python3 "${PY}" --agentrt "${RT}" "$@" 2>&1)"
    code=$?
}

want()
{
    local label="$1" expect="$2" needle="$3"

    if [ "${code}" -eq "${expect}" ] && printf '%s' "${out}" | grep -qF "${needle}"; then
        printf 'ok   %s\n' "${label}"
        return 0
    fi
    printf 'FAIL %s（rc=%s，期望 %s，判据「%s」）\n' "${label}" "${code}" "${expect}" "${needle}"
    printf '%s\n' "${out}" >&2
    fails=$((fails + 1))
}

seed
verdict "${TUI}"
want "两侧无交叉时全绿" 0 "0 处交叉"

seed
printf 'agentrt-core = "0.0.5"\n' >>"${TUI}/crates/airy-adapters/Cargo.toml"
verdict "${TUI}"
want "按名字依赖 agentrt 判负" 1 "双通路耦合"

seed
printf 'leak = { path = "../../../elsewhere" }\n' >>"${TUI}/crates/airy-adapters/Cargo.toml"
verdict "${TUI}"
want "path 依赖出根判负" 1 "路径出根"

seed
printf 'airy-adapters = { path = "../../system/tui/crates/airy-adapters" }\n' \
    >>"${RT}/core/Cargo.toml"
verdict "${TUI}"
want "反向依赖 TUI crate 判负" 1 "反向耦合"

seed
mv "${RT}" "${tmp}/away"
verdict "${TUI}"
want "对侧缺席只判本侧" 0 "反向未判"

verdict --bogus "${TUI}"
want "未知参数退 5" 5 "未知参数"

verdict "${tmp}/nope"
want "扫描根不存在退 5" 5 "目录不存在"

verdict --help
want "帮助含用法行" 0 "用法:"

if [ "${fails}" -ne 0 ]; then
    printf 'test_tui_dock_isolation: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_tui_dock_isolation: 全部通过\n'
