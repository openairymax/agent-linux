#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_abi_snapshot.sh — ci/gates/abi-snapshot.py 的分支回归
#
# 冻结门禁有两种失效方式，都会让「快照一致」变成空话：
#   · 只比摘要不比集合——新增或改名一个头文件，快照里没有对应条目，
#     逐条比对仍然通过；
#   · --update 在快照损坏时静默重写——本应报警的输入错误被吞掉，
#     等于任何改动都能自助放行。
# 本回归用一棵最小假树逐条断言这两类失效，并钉住确定性与退出码：
# 同一棵树两次 --update 必须逐字节相同，--check 必须不写盘。
#
# 用法: bash ci/tests/test_abi_snapshot.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TOOL="${ROOT}/ci/gates/abi-snapshot.py"
UAPI="kernel/include/uapi/linux/airymax"
SNAP="tools/abi-check/contract/uapi_headers.sha256"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
FX="${tmp}/fx"
zero="$(printf '0%.0s' $(seq 1 64))"
fails=0

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
    python3 "${TOOL}" --root "${FX}" "$@" 2>&1
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

# plant —— 两枚头文件的最小受检树；每个用例都从同一初始态开始。
plant()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/${UAPI}" "$(dirname "${FX}/${SNAP}")"
    printf '#define AIRY_A 1\n' >"${FX}/${UAPI}/alpha.h"
    printf '#define AIRY_B 2\n' >"${FX}/${UAPI}/beta.h"
}

plant
out="$(run --update)"
check "首次登记成功" 0 "$?" "${out}" "已登记 2 份"

out="$(run --check)"
check "一致时判通过" 0 "$?" "${out}" "2 份一致"

# --- 字节面：改动一个字节就必须判负，且 --check 不得回写 -------------------

cp "${FX}/${SNAP}" "${tmp}/before"
printf '#define AIRY_A 3\n' >"${FX}/${UAPI}/alpha.h"
out="$(run --check)"
check "字节改动判漂移" 1 "$?" "${out}" "变更 ${UAPI}/alpha.h"
if cmp -s "${tmp}/before" "${FX}/${SNAP}"; then
    ok "判负时不回写快照"
else
    bad "判负时把快照写回了磁盘"
fi

# --- 集合面：新增、删除都不许被逐条比对放过 --------------------------------

plant
run --update >/dev/null
printf '#define AIRY_C 4\n' >"${FX}/${UAPI}/gamma.h"
out="$(run --check)"
check "新增头文件判漂移" 1 "$?" "${out}" "新增 ${UAPI}/gamma.h"

plant
run --update >/dev/null
rm -f "${FX}/${UAPI}/beta.h"
out="$(run --check)"
check "删除头文件判漂移" 1 "$?" "${out}" "缺失 ${UAPI}/beta.h"

plant
out="$(run --check)"
check "快照缺失判漂移" 1 "$?" "${out}" "快照缺失"

# --- 输入面：坏快照是输入错误，--update 也不许静默重写 ----------------------

plant
run --update >/dev/null
printf 'deadbeef  %s/alpha.h\n' "${UAPI}" >"${FX}/${SNAP}"
cp "${FX}/${SNAP}" "${tmp}/before"
out="$(run --check)"
check "短摘要判输入错误" 5 "$?" "${out}" "格式错误"
out="$(run --update)"
check "坏快照不许自动重写" 5 "$?" "${out}" "格式错误"
if cmp -s "${tmp}/before" "${FX}/${SNAP}"; then
    ok "坏快照保持原样"
else
    bad "坏快照被 --update 覆盖"
fi

printf '%s  %s/alpha.h\n%s  %s/alpha.h\n' "${zero}" "${UAPI}" "${zero}" \
    "${UAPI}" >"${FX}/${SNAP}"
out="$(run --check)"
check "重复登记判输入错误" 5 "$?" "${out}" "重复登记"

rm -rf "${FX:?}/${UAPI:?}"
mkdir -p "${FX}/${UAPI}"
out="$(run --check)"
check "空受检目录判输入错误" 5 "$?" "${out}" "没有 .h"

# --- 确定性：同一棵树两次渲染逐字节相同 -------------------------------------

plant
run --update >/dev/null
cp "${FX}/${SNAP}" "${tmp}/first"
run --update >/dev/null
if cmp -s "${tmp}/first" "${FX}/${SNAP}"; then
    ok "两次 --update 输出一致"
else
    bad "两次 --update 输出不同"
fi

# --- 参数面 ----------------------------------------------------------------

out="$(python3 "${TOOL}" --root "${tmp}/absent" 2>&1)"
check "根目录不存在判参数错误" 5 "$?" "${out}" "根目录不存在"

out="$(run --bogus)"
check "未知参数判参数错误" 5 "$?" "${out}" "usage"

out="$(run --check --update)"
check "两个模式互斥" 5 "$?" "${out}" "usage"

out="$(run --help)"
check "帮助以 0 退出" 0 "$?" "${out}" "usage"

exit "${fails}"
