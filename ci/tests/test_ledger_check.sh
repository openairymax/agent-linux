#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/tests/test_ledger_check.sh — ci/gates/ledger-check.sh 的分支回归
#
# 门禁判「已提交账本 = 种子产物」与「§11 债号不增不减」。真实账本常年为绿，
# 判定逻辑只能靠负例驱动：造一份漂移的账本、一份缺债号的账本、一棵没有生成器
# 的树，断言只看退出码与稳定判据名。
#
# 生成器用桩替代：本文件判的是门禁的调用与转译（参数、退出码、跳过），不是
# 账本内容；账本内容由生成器自己的 --check 与 ci/ci.sh ledger-check 判。
#
# 用法: bash ci/tests/test_ledger_check.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/ledger-check.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib"
cp "${GATE}" "${FX}/ci/gates/ledger-check.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"

ok()
{
    printf 'ok   %s\n' "$*"
}

bad()
{
    printf 'FAIL %s\n' "$*"
    fails=$((fails + 1))
}

run()
{
    bash "${FX}/ci/gates/ledger-check.sh" "$@" 2>&1
}

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"

    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return 0
    fi
    case "${out}" in
    *"${needle}"*) ok "${name}" ;;
    *) bad "${name}（输出缺「${needle}」）"; printf '%s\n' "${out}" >&2 ;;
    esac
}

# 桩生成器：打印收到的参数，再按给定退出码与判据行作答。
stub()
{
    local rc="$1" msg="$2" dir="$3"

    mkdir -p "${dir}/10-references/generators"
    cat >"${dir}/10-references/generators/bp_004_gen.py" <<EOF
import sys
print("argv:" + " ".join(sys.argv[1:]))
print("${msg}")
sys.exit(${rc})
EOF
}

TREE="${tmp}/tree"
stub 0 "账本可复现：与种子产物逐字节一致" "${TREE}"
out="$(run --tree "${TREE}")"
check "可复现账本判通过" 0 "$?" "${out}" "账本可复现且债号稳定"

case "${out}" in
*"argv:--check"*) ok "以 --check 只读模式调用生成器" ;;
*) bad "生成器调用参数不是 --check"; printf '%s\n' "${out}" >&2 ;;
esac

stub 1 "账本漂移：第 3 行起不一致" "${TREE}"
out="$(run --tree "${TREE}")"
check "账本漂移判拒绝" 1 "$?" "${out}" "账本漂移"

stub 1 "§11 债号与种子不一致：缺 ['TD-09']" "${TREE}"
out="$(run --tree "${TREE}")"
check "债号缺项判拒绝" 1 "$?" "${out}" "债号与种子不一致"

stub 0 "账本可复现" "${tmp}/bare"
out="$(run --tree "${tmp}/bare")"
check "显式树生效" 0 "$?" "${out}" "账本可复现且债号稳定"

rm -rf "${tmp}/bare/10-references"
out="$(run --tree "${tmp}/bare")"
check "缺少生成器退 5" 5 "$?" "${out}" "缺少生成器"

out="$(run --tree)"
check "缺 --tree 参数退 5" 5 "$?" "${out}" "需要目录路径"

out="$(run --tree "${tmp}/nope")"
check "树目录不存在退 5" 5 "$?" "${out}" "目录不存在"

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

out="$(run -h)"
check "帮助返回 0" 0 "$?" "${out}" "用法:"

rm -rf "${TREE}"
out="$(run)"
check "无文档树时跳过不假绿" 0 "$?" "${out}" "本门禁不适用"

stub 0 "账本可复现" "${tmp}/docs/docs-closed/agent-linux-dev"
out="$(run)"
check "默认树指向 docs-closed 施工目录" 0 "$?" "${out}" "账本可复现且债号稳定"

stub 1 "账本漂移：第 12 行起不一致" "${tmp}/docs/docs-closed/agent-linux-dev"
out="$(run)"
check "默认树漂移判拒绝" 1 "$?" "${out}" "账本漂移"

if [ "${fails}" -ne 0 ]; then
    printf 'test_ledger_check: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_ledger_check: 全部通过\n'
