#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/tests/test_rust_vendor.sh — rust-vendor 门禁回归夹具
#
# 钉住的判据：
#   负向：缺 .cargo/config.toml 的 vendored 替换、Cargo.lock 与 vendor/ 包集合
#         不一致、缺 .cargo-checksum.json、台账与内容不一致、缺台账，均判 1；
#         参数不满足（无子命令、未知参数、--root 不存在）退 5。
#   正向：ledger 生成的台账可被 verify 判 0；工作区成员（无 source）不进 vendor
#         对账。
#
# 用法: bash ci/tests/test_rust_vendor.sh
# 退出码: 0 全部断言通过；1 有断言失败。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
FX="${tmp}/fx"
TUI="${FX}/system/tui"
fails=0

ok() { printf 'ok   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }

seed()
{
    rm -rf "${FX}"
    mkdir -p "${FX}/ci/gates" "${FX}/ci/lib" \
        "${TUI}/.cargo" "${TUI}/vendor/alpha-1.0.0/src" \
        "${TUI}/vendor/beta-2.1.0/src"
    cp "${ROOT}/ci/gates/rust-vendor.sh" "${FX}/ci/gates/"
    cp "${ROOT}/ci/lib/log.sh" "${FX}/ci/lib/"
    cat >"${TUI}/Cargo.lock" <<'EOF'
[[package]]
name = "alpha"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "beta"
version = "2.1.0"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "workspace-member"
version = "0.1.0"
EOF
    printf 'fn a() {}\n' >"${TUI}/vendor/alpha-1.0.0/src/lib.rs"
    printf '{"files":{},"package":null}\n' \
        >"${TUI}/vendor/alpha-1.0.0/.cargo-checksum.json"
    printf 'fn b() {}\n' >"${TUI}/vendor/beta-2.1.0/src/lib.rs"
    printf '{"files":{},"package":null}\n' \
        >"${TUI}/vendor/beta-2.1.0/.cargo-checksum.json"
    cat >"${TUI}/.cargo/config.toml" <<'EOF'
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
EOF
}

run() { bash "${FX}/ci/gates/rust-vendor.sh" "$@" 2>&1; }

check()
{
    local name="$1" want="$2" got="$3" out="$4" needle="$5"
    if [ "${got}" != "${want}" ]; then
        bad "${name}（rc=${got}，期望 ${want}）"
        printf '%s\n' "${out}" >&2
        return
    fi
    if [ -n "${needle}" ] && ! printf '%s' "${out}" | grep -qF -- "${needle}"; then
        bad "${name}（输出缺「${needle}」）"
        printf '%s\n' "${out}" >&2
        return
    fi
    ok "${name}"
}

# 正向：ledger 生成台账，verify 判 0
seed
out="$(run ledger)"; rc=$?
check "ledger 退 0" 0 "${rc}" "${out}" "台账已重写"
[ -f "${TUI}/vendor.ledger" ] && ok "台账已落盘" || bad "台账未落盘"
grep -q '^alpha' "${TUI}/vendor.ledger" &&
    ok "台账含 alpha" || bad "台账缺 alpha"
grep -q 'workspace-member' "${TUI}/vendor.ledger" &&
    bad "工作区成员误入台账" || ok "工作区成员不进台账"
out="$(run verify)"; rc=$?
check "verify 退 0" 0 "${rc}" "${out}" "vendor 一致"

# 负向：缺 vendored 源配置
seed
rm -f "${TUI}/.cargo/config.toml"
out="$(run verify)"; rc=$?
check "缺配置文件判负" 1 "${rc}" "${out}" "缺少"

# 负向：lock 与 vendor 包集合不一致
seed
mkdir -p "${TUI}/vendor/gamma-3.0.0"
out="$(run verify)"; rc=$?
check "包集合不一致判负" 1 "${rc}" "${out}" "包集合不一致"

# 负向：vendor 目录缺校验清单
seed
rm -f "${TUI}/vendor/beta-2.1.0/.cargo-checksum.json"
out="$(run verify)"; rc=$?
check "缺校验清单判负" 1 "${rc}" "${out}" "缺少 cargo 校验清单"

# 负向：缺台账
seed
out="$(run verify)"; rc=$?
check "缺台账判负" 1 "${rc}" "${out}" "缺少 system/tui/vendor.ledger"

# 负向：台账被手改
seed
bash "${FX}/ci/gates/rust-vendor.sh" ledger >/dev/null 2>&1
printf 'tampered\n' >>"${TUI}/vendor.ledger"
out="$(run verify)"; rc=$?
check "台账被改判负" 1 "${rc}" "${out}" "台账与 vendor 实际内容不一致"

# 负向：vendor 内容变了但台账没重算
seed
bash "${FX}/ci/gates/rust-vendor.sh" ledger >/dev/null 2>&1
printf 'changed\n' >>"${TUI}/vendor/beta-2.1.0/src/lib.rs"
out="$(run verify)"; rc=$?
check "内容变更判负" 1 "${rc}" "${out}" "台账与 vendor 实际内容不一致"

# 负向：参数不满足
seed
out="$(run)"; rc=$?
check "无子命令退 5" 5 "${rc}" "${out}" "用法:"
out="$(run verify --bogus)"; rc=$?
check "未知参数退 5" 5 "${rc}" "${out}" "未知参数: --bogus"
out="$(run verify --root "${tmp}/absent")"; rc=$?
check "--root 不存在退 5" 5 "${rc}" "${out}" "目录不存在"
out="$(run verify --root)"; rc=$?
check "--root 缺值退 5" 5 "${rc}" "${out}" "需要目录路径"

# 正向：帮助退 0
out="$(run -h)"; rc=$?
check "帮助退 0" 0 "${rc}" "${out}" "用法:"

if [ "${fails}" -eq 0 ]; then
    printf '== %s passed ==\n' "$(basename "$0")"
fi
exit $((fails > 0 ? 1 : 0))
