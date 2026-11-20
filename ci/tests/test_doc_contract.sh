#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_doc_contract.sh — ci/gates/doc-contract.sh 的分支回归
#
# 门禁判的是册子与投影逐字段相同。真实树全绿只说明今天一致，判据本身要靠
# 负例驱动：改一侧的名字、去掉一条入口、重复一个代号、抹掉承接版本，四种
# 漂移都必须判负；缺投影与坏参数按环境错误退 5。断言只看退出码与稳定判据名。
#
# 用法: bash ci/tests/test_doc_contract.sh
# 退出码: 0 全部通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
GATE="${ROOT}/ci/gates/doc-contract.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
fails=0

FX="${tmp}/fx"
mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" "${FX}/ci/checks" \
    "${FX}/system/tui/crates/airy-adapters/src"
cp "${GATE}" "${FX}/ci/gates/doc-contract.sh"
cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/log.sh"
cp "${ROOT}/ci/checks/modmap_conform.py" "${FX}/ci/checks/modmap_conform.py"
cp "${ROOT}/system/tui/modmap.toml" "${FX}/system/tui/modmap.toml"
cp "${ROOT}/system/tui/crates/airy-adapters/src/modmap.rs" \
    "${FX}/system/tui/crates/airy-adapters/src/modmap.rs"
BOOK="${FX}/system/tui/modmap.toml"
PROJ="${FX}/system/tui/crates/airy-adapters/src/modmap.rs"

# 夹具只判一致性与三列规则，入口是否存在按真实路径判定；这里把册子点名的
# 入口补成空壳，避免「入口不存在」掩盖本用例要判的那一处漂移。
mkdir -p "${FX}/system/tui/crates/airy-tui-exec/src" \
    "${FX}/system/tui/crates/boot-tui/src/bin" \
    "${FX}/system/tui/crates/boot-ime"
for f in \
    "system/tui/crates/airy-adapters/src/disk.rs" \
    "system/tui/crates/airy-tui-exec/src/approve.rs" \
    "system/tui/crates/airy-adapters/src/probe.rs" \
    "system/tui/crates/airy-adapters/src/model.rs"; do
    : >"${FX}/${f}"
done
cp "${ROOT}/ci/gates/tui-frame.sh" "${FX}/ci/gates/tui-frame.sh"
cp "${ROOT}/ci/ci.sh" "${FX}/ci/ci.sh"
mkdir -p "${FX}/tools"
cp "${ROOT}/tools/airy" "${FX}/tools/airy"

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

run() { bash "${FX}/ci/gates/doc-contract.sh" "$@" 2>&1; }

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

restore()
{
    cp "${ROOT}/system/tui/modmap.toml" "${BOOK}"
    cp "${ROOT}/system/tui/crates/airy-adapters/src/modmap.rs" "${PROJ}"
}

out="$(run)"
check "初始册子与投影一致" 0 "$?" "${out}" "19 条登记一致"

sed -i '0,/name = "工具链与 CI\/CD"/s//name = "工具链"/' "${BOOK}"
out="$(run)"
check "册子改名判负" 1 "$?" "${out}" "册子与投影不一致"
restore

sed -i 's#entry: "ci/ci.sh"#entry: "ci/nope.sh"#; s#entry = "ci/ci.sh"#entry = "ci/nope.sh"#' "${BOOK}" "${PROJ}"
out="$(run)"
check "入口不存在判负" 1 "$?" "${out}" "入口不存在"
restore

sed -i '0,/code = "A"/s//code = "L"/; 0,/code: "A"/s//code: "L"/' "${BOOK}" "${PROJ}"
out="$(run)"
check "代号重复判负" 1 "$?" "${out}" "代号重复"
restore

sed -i '0,/0\.0\.24 起承接：本版只落/s//本版只落/' "${BOOK}"
sed -i '0,/0\.0\.24 起承接：本版只落/s//本版只落/' "${PROJ}"
out="$(run)"
check "承接版本缺失判负" 1 "$?" "${out}" "未写承接版本"
restore

mv "${PROJ}" "${tmp}/modmap.rs"
out="$(run)"
check "缺投影退 5" 5 "$?" "${out}" "缺少"
restore

out="$(run --bogus)"
check "未知参数退 5" 5 "$?" "${out}" "未知参数"

out="$(run --root)"
check "缺 --root 参数退 5" 5 "$?" "${out}" "需要目录"

out="$(run --help)"
check "帮助含用法行" 0 "$?" "${out}" "用法:"

if [ "${fails}" -ne 0 ]; then
    printf 'test_doc_contract: %d 项失败\n' "${fails}"
    exit 1
fi
printf 'test_doc_contract: 全部通过\n'
