#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_contract_conform.sh — ci/gates/contract-conform.sh 的判据回归
#
# 钉住五类漂移：schema 常量不一致、字段表少一项、枚举少一变体、信任矩阵
# 增删一级、contract 代码块未闭合；前三类走判定（门禁退 1 并报 FAIL），
# 未闭合属不可判定（校验器退 5）。另钉契约册目录缺失退 5，与判定失败区分。
#
# 用法: bash ci/tests/test_contract_conform.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
CRATES=(
    intent/src/schema.rs intent/src/trust.rs intent/src/status.rs
    boot-tui/src/input/event.rs boot-tui/src/input/source.rs
    boot-tui/src/input/escape.rs boot-tui/src/frame.rs
    airy-tui-exec/src/exec.rs airy-tui-exec/src/normalize.rs
    airy-tui-exec/src/approve.rs
)

setup()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/ci/checks" "${FX}/system"
    cp "${ROOT}/ci/gates/contract-conform.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
    cp "${ROOT}/ci/checks/contract_conform.py" "${FX}/ci/checks/"
    cp -R "${ROOT}/system/contracts" "${FX}/system/contracts"
    local rel
    for rel in "${CRATES[@]}"; do
        mkdir -p "${FX}/system/tui/crates/$(dirname "${rel}")"
        cp "${ROOT}/system/tui/crates/${rel}" "${FX}/system/tui/crates/${rel}"
    done
}

run()
{
    out="$(bash "${FX}/ci/gates/contract-conform.sh" "$@" 2>&1)"
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
run --root "${FX}"
check "原树一致放行" 0 "契约册与代码投影一致"

setup
sed -i 's|^schema = airy.intent/1$|schema = airy.intent/2|' \
    "${FX}/system/contracts/intent.md"
run --root "${FX}"
check "schema 漂移判负" 1 "FAIL schema: 契约 'airy.intent/2' 与代码不一致"

setup
sed -i '/^payload.lang = optional$/d' "${FX}/system/contracts/intent.md"
run --root "${FX}"
check "字段缺失判负" 1 "fields: 代码有、契约缺: payload.lang"

setup
sed -i '/^voice$/d' "${FX}/system/contracts/intent.md"
run --root "${FX}"
check "枚举缺项判负" 1 "FAIL enum:source:"

setup
sed -i 's|^api = T1 T2$|api = T1|' "${FX}/system/contracts/intent.md"
run --root "${FX}"
check "信任矩阵漂移判负" 1 "FAIL trust: 契约"

setup
printf '\n```contract\n[meta]\nschema = airy.intent/1\n' \
    >>"${FX}/system/contracts/intent.md"
run --root "${FX}"
check "代码块未闭合判负" 1 "contract 代码块未闭合"

setup
rm -rf "${FX}/system/contracts"
run --root "${FX}"
check "契约册缺失退 5" 5 "system/contracts 目录不存在"

setup
run --root "${FX}" --bogus
check "未知参数退 5" 5 "未知参数"

if [ "${fails}" -ne 0 ]; then
    exit 1
fi
printf '== %s passed ==\n' "$(basename "$0")"
exit $((fails > 0 ? 1 : 0))
