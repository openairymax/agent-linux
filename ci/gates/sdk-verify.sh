#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/sdk-verify.sh — 用户态 SDK（Go / Python / Rust / TypeScript）编译门禁
#
# 用法:
#   ci/gates/sdk-verify.sh --lang go|python|rust|ts
#
# sdk/ 是四份语言绑定，内容由 ipc_gen.py 从 contracts.md 生成，长期只被
# 「生成物是否最新」检查覆盖，没有被任何编译器看过。生成一致不等于能编译：
# 常量类型、保留字与模块边界只有在对应编译器里才会暴露。
#
# 每份绑定都没有包清单（无 go.mod / Cargo.toml / package.json / setup.py），
# 因此判据按语言各自的最小编译形式给出，且都在临时目录里完成：
#   go      复制到临时模块后 go build + go vet（go 命令要求模块上下文）
#   python  py_compile，字节码缓存重定向到临时目录
#   rust    rustc --crate-type lib --emit=metadata，逐个文件
#   ts      tsc --noEmit --strict，全目录一次
#
# 只在临时目录产生中间物：源码区不落任何编译产物（与 c-verify 同一铁律）。
#
# 退出码: 0 通过；1 判定失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# shellcheck source=../lib/log.sh
. "${ROOT}/ci/lib/log.sh"

usage()
{
    cat <<'EOF'
用法: ci/gates/sdk-verify.sh --lang go|python|rust|ts
EOF
}

LANG_ID=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --lang) LANG_ID="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; usage >&2; exit "${CI_EINVAL}" ;;
    esac
done

case "${LANG_ID}" in
    go|python|rust|ts) ;;
    '') err "--lang 必填"; usage >&2; exit "${CI_EINVAL}" ;;
    *) err "--lang 只接受 go|python|rust|ts"; exit "${CI_EINVAL}" ;;
esac

SRC="${ROOT}/sdk"
[ -d "${SRC}" ] || die "SDK 目录不存在: sdk"

TMP="${TMPDIR:-/tmp}/airy-sdk-verify/${LANG_ID}"
case "${TMP}" in
    "${ROOT}"/*) die "工作目录不得位于源码树内: ${TMP}" ;;
esac
rm -rf "${TMP}"
mkdir -p "${TMP}"

# 语言到源码目录的映射：目录名与 --lang 取值不同（python 对应 python/）。
case "${LANG_ID}" in
    python) LANG_DIR="python" ;;
    *) LANG_DIR="${LANG_ID}" ;;
esac
[ -d "${SRC}/${LANG_DIR}" ] || die "SDK 语言目录不存在: sdk/${LANG_DIR}"

group "sdk/${LANG_DIR}: 编译"
rc=0
case "${LANG_ID}" in
    go)
        need_cmd go
        # go 命令要求模块上下文；绑定本身没有 go.mod，这里在临时目录建一个
        # 只含该包的模块，编译的仍是 sdk/ 里的真实源码。
        mkdir -p "${TMP}/airyx"
        cp "${SRC}/go/airyx/"*.go "${TMP}/airyx/"
        printf 'module airyx\n\ngo 1.21\n' >"${TMP}/go.mod"
        ( cd "${TMP}" && go vet ./... && go build ./... ) || rc=1
        ;;
    python)
        need_cmd python3
        PYTHONPYCACHEPREFIX="${TMP}/pycache" \
            python3 -m py_compile "${SRC}/python/airyx/"*.py || rc=1
        ;;
    rust)
        need_cmd rustc
        for f in "${SRC}/rust/airyx/"*.rs; do
            rustc --edition 2021 --crate-type lib --emit=metadata \
                -o "${TMP}/$(basename "${f}" .rs).rmeta" "${f}" || {
                rc=1
                break
            }
        done
        ;;
    ts)
        need_cmd tsc
        tsc --noEmit --strict --target ES2021 "${SRC}/ts/airyx/"*.ts || rc=1
        ;;
esac
endgroup
[ "${rc}" -eq 0 ] || die "sdk/${LANG_DIR} 编译失败"
say "sdk/${LANG_DIR} 编译通过"
