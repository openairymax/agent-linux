#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_nightly_helpers.sh — ci/gates/nightly-helpers.sh 的分派契约回归
#
# 调度器决定 nightly 的六个 helper 怎么被调用：未落地时打印与清单一致的
# SKIP 标记，落地后按固定 argv 调用，并把 helper 的退出码原样上抛。三点都
# 会随改动漂移：标记变了 nightly.py 判契约漂移，argv 变了 helper 收到错误
# 输入，退出码被吞则失败的长稳与故障注入会显示为绿。
#
# 用例用桩顶替 helper，并记录每次调用的 argv，断言三个正式验证目标被依次
# 调用，而不只断言「跑过了」。
#
# 用法: bash ci/tests/test_nightly_helpers.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
GATE="${FX}/ci/gates/nightly-helpers.sh"

setup()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/tools"
    cp "${ROOT}/ci/gates/nightly-helpers.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
}

stub()
{
    local name="$1" code="$2"

    cat >"${FX}/tools/${name}" <<EOF
import pathlib
import sys

with pathlib.Path("${FX}/calls").open("a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
sys.exit(${code})
EOF
}

run()
{
    out="$(cd "${FX}" && bash "${GATE}" "$@" 2>&1)"
    rc=$?
}

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

check()
{
    local name="$1" want="$2" needle="$3"

    if [ "${rc}" -ne "${want}" ]; then
        bad "${name}（rc=${rc}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *)
        bad "${name}（输出缺「${needle}」）"
        printf '%s\n' "${out}" >&2
        ;;
    esac
}

setup
run --help
check "帮助打印用法" 0 "用法:"

run no-such-helper
check "未知 helper 退 5" 5 "未知 helper"

run soak-runner
check "未落地 helper 打 SKIP" 0 "SKIP: soak-runner.py not yet implemented"

run create-revert-pr
check "回滚缺提交号退 5" 5 "需要回归提交号"

run create-revert-pr abc123456789
check "回滚未落地打 SKIP" 0 "SKIP: create-revert-pr.py not yet implemented"

setup
stub formal-verify.py 0
run formal-verify
check "正式验证放行" 0 ""
if [ "$(cat "${FX}/calls" 2>/dev/null)" = "--target kernel/sched/core.c
--target drivers/airymax/ipc/airy_ipc.c
--target mm/share_pool.c" ]; then
    ok "三个目标依次调用"
else
    bad "调用序列不符"
    printf '%s\n' "$(cat "${FX}/calls" 2>/dev/null)" >&2
fi

setup
stub chaos-runner.py 3
run chaos-runner
check "helper 退出码上抛" 3 ""

if [ "${fails}" -ne 0 ]; then
    exit 1
fi
printf '== %s passed ==\n' "$(basename "$0")"
exit $((fails > 0 ? 1 : 0))
