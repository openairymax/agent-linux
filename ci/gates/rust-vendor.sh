#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/rust-vendor.sh — TUI 工作区的离线依赖源门禁
#
# 离线构建的前提是「锁即所编」：Cargo.lock 声明的每个 registry 包，必须在
# system/tui/vendor/ 里有且仅有一个同名同版本的目录，且内容摘要与
# system/tui/vendor.ledger 一致。任何一处在仓库外取源，多架构镜像就会在
# 无网构建时以「找不到包」失败，或以不同版本静默分叉。
#
# 目录取 --versioned-dirs 布局（<name>-<version>），与锁条目一一对应；
# cargo 的默认布局在同名多版本时产生 name-1/name-2 后缀，无法与锁对账。
#
#   verify [--root DIR] [--build]   校验；--build 追加 cargo 端到端
#   ledger [--root DIR]             重算台账（依赖变更后人工执行并审查）
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly TUI=system/tui
readonly LEDGER=vendor.ledger
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

CMD=""
ROOT=""
BUILD=0
TUIDIR=""

usage()
{
    cat <<'EOF'
用法: ci/gates/rust-vendor.sh verify [--root DIR] [--build]
      ci/gates/rust-vendor.sh ledger [--root DIR]
EOF
}

# 锁里的外部依赖：只有带 source 的条目来自仓库外，工作区成员没有 source
# 行。输出 "<name> <version>"，按字节序排序，保证与 vendor 目录可逐行比。
lock_pkgs()
{
    awk -F'"' '
        function flush() {
            if (n != "" && s != "") printf "%s %s\n", n, v
            n = ""; v = ""; s = ""
        }
        /^\[\[package\]\]/ { flush(); next }
        /^name = /    { n = $2 }
        /^version = / { v = $2 }
        /^source = /  { s = $2 }
        END { flush() }
    ' "$1" | LC_ALL=C sort
}

lock_dirs()
{
    lock_pkgs "$1" | awk '{ print $1 "-" $2 }'
}

vend_pkgs()
{
    find "$1" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort
}

# 目录摘要：逐文件 sha256 按路径字节序串联后再取一次 sha256。以路径参与
# 摘要，重命名或增删文件都会改变结果；不以时间戳参与，重建同一版本得同值。
pkg_digest()
{
    ( cd "$1" && find . -type f -print0 | LC_ALL=C sort -z |
        xargs -0 -r sha256sum ) | sha256sum | cut -d' ' -f1
}

pkg_stat()
{
    find "$1" -type f -printf '%s\n' | awk '
        { n++; b += $1 }
        END { printf "%d %d\n", n, b }'
}

emit_ledger()
{
    local name ver stat
    printf '# %s 由 ci/gates/rust-vendor.sh ledger 生成，不手改。\n' \
        "${LEDGER}"
    printf '# name\tversion\tfiles\tbytes\tsha256\n'
    while read -r name ver; do
        stat=$(pkg_stat "${TUIDIR}/vendor/${name}-${ver}")
        printf '%s\t%s\t%s\t%s\t%s\n' "${name}" "${ver}" \
            "${stat% *}" "${stat#* }" \
            "$(pkg_digest "${TUIDIR}/vendor/${name}-${ver}")"
    done < <(lock_pkgs "${TUIDIR}/Cargo.lock")
}

check_cfg()
{
    local cfg="${TUIDIR}/.cargo/config.toml"

    if [ ! -f "${cfg}" ]; then
        err "缺少 ${cfg}"
        return 1
    fi
    grep -q 'replace-with = "vendored-sources"' "${cfg}" ||
        { err "${cfg} 未把 crates-io 替换为 vendored-sources"; return 1; }
    grep -q 'directory = "vendor"' "${cfg}" ||
        { err "${cfg} 未把源目录指向 vendor"; return 1; }
}

check_sets()
{
    local lock_list vend_list

    lock_list=$(lock_dirs "${TUIDIR}/Cargo.lock")
    vend_list=$(vend_pkgs "${TUIDIR}/vendor")
    if [ "${lock_list}" != "${vend_list}" ]; then
        err "Cargo.lock 与 vendor/ 的包集合不一致"
        diff <(printf '%s\n' "${lock_list}") \
            <(printf '%s\n' "${vend_list}") >&2
        return 1
    fi
}

check_marks()
{
    local d

    for d in "${TUIDIR}"/vendor/*/; do
        if [ ! -f "${d}.cargo-checksum.json" ]; then
            err "缺少 cargo 校验清单: ${d}"
            return 1
        fi
    done
}

check_ledger()
{
    local tmp

    if [ ! -f "${TUIDIR}/${LEDGER}" ]; then
        err "缺少 ${TUI}/${LEDGER}；执行 ledger 子命令生成"
        return 1
    fi
    tmp=$(mktemp)
    emit_ledger >"${tmp}"
    if ! diff -u "${TUIDIR}/${LEDGER}" "${tmp}" >&2; then
        rm -f "${tmp}"
        err "台账与 vendor 实际内容不一致；依赖变更后重算并审查"
        return 1
    fi
    rm -f "${tmp}"
}

check_build()
{
    local tgt="${TMPDIR:-/tmp}/airy-tui-target"

    need_cmd cargo
    ( cd "${TUIDIR}" &&
        CARGO_TARGET_DIR="${tgt}" \
            cargo metadata --offline --locked --format-version 1 >/dev/null &&
        CARGO_TARGET_DIR="${tgt}" \
            cargo build --offline --locked --workspace --all-targets )
}

summary()
{
    local n b

    n=$(vend_pkgs "${TUIDIR}/vendor" | wc -l)
    b=$(du -sb "${TUIDIR}/vendor" | cut -f1)
    note "packages=${n} bytes=${b}"
}

verify()
{
    need_cmd find sort sha256sum awk diff
    check_cfg || return 1
    check_sets || return 1
    check_marks || return 1
    check_ledger || return 1
    if [ "${BUILD}" -eq 1 ]; then
        check_build || { err "离线构建失败"; return 1; }
    fi
    summary
    say "vendor 一致"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        verify|ledger)
            [ -z "${CMD}" ] || { err "重复子命令: $1"; exit "${EINVAL}"; }
            CMD="$1"; shift ;;
        --root) need_dir "$1" "${2:-}"; ROOT="$2"; shift 2 ;;
        --build) BUILD=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${CMD}" ] || { usage; exit "${EINVAL}"; }
[ -n "${ROOT}" ] ||
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TUIDIR="${ROOT}/${TUI}"
need_dir "--root 下的 TUI 工作区" "${TUIDIR}"
[ -f "${TUIDIR}/Cargo.lock" ] ||
    die "缺少 ${TUI}/Cargo.lock"

if [ "${CMD}" = ledger ]; then
    emit_ledger >"${TUIDIR}/${LEDGER}"
    summary
    say "台账已重写"
    exit 0
fi

verify || exit 1
