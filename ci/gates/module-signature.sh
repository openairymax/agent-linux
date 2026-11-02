#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/module-signature.sh — 模块签名与内核污染位门禁（V-16 / KM-01）
#
# 用法:
#   ci/gates/module-signature.sh --root DIR [--kind struct|taint|all]
#                                [--kernel REL] [--taint FILE]
#
# 背景：模块签名不是 ELF 段。scripts/sign-file 把 PKCS#7 数据、struct
# module_signature 与魔数 "~Module signature appended~\n" 依次追加在文件尾
# 部，kernel/module/signing.c 的 module_sig_check() 按同一顺序读取。以
# readelf -S 找 .module_sig 段的判据在真实镜像上恒为假，用它守门会让内核
# 包必然构建失败，也会让实机冒烟永远报未签名。
#
# 判据全部实现在 ci/lib/modsig.py：本文件只做参数校验与调用，不复制判定
# 逻辑，避免 Bash 门禁与 Python 校验器两份口径分叉。
#
#   --root DIR     镜像根（解包后的安装树），须含 usr/lib/modules
#   --kind KIND    struct 只看签名尾部，taint 只看污染位，all 两者都看
#   --kernel REL   只检查该内核版本目录；缺省扫描 usr/lib/modules 全部
#   --taint FILE   污染位文件，缺省取 ROOT/proc/sys/kernel/tainted
#
# 退出码: 0 通过；1 判定不通过；5 参数不满足或证据不可用。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly CHECK=ci/lib/modsig.py
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

SOURCE=""
ROOT=""
KIND="all"
KERNEL=""
TAINT=""

usage()
{
    cat <<'EOF'
用法: ci/gates/module-signature.sh --root DIR [--kind struct|taint|all]
                                   [--kernel REL] [--taint FILE]
EOF
}

need_arg()
{
    [ -n "${2:-}" ] || { err "$1 需要参数值"; exit "${EINVAL}"; }
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --root)
            need_arg "$1" "${2:-}"
            [ -d "$2" ] || { err "--root 目录不存在: $2"; exit "${EINVAL}"; }
            ROOT="$2"
            shift 2
            ;;
        --kind)
            need_arg "$1" "${2:-}"
            case "$2" in
                struct|taint|all) KIND="$2" ;;
                *) err "--kind 只接受 struct|taint|all: $2"; exit "${EINVAL}" ;;
            esac
            shift 2
            ;;
        --kernel) need_arg "$1" "${2:-}"; KERNEL="$2"; shift 2 ;;
        --taint) need_arg "$1" "${2:-}"; TAINT="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

[ -n "${ROOT}" ] || { err "--root 必填"; exit "${EINVAL}"; }
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[ -f "${SOURCE}/${CHECK}" ] || {
    err "缺少校验器: ${SOURCE}/${CHECK}"
    exit "${EINVAL}"
}
need_cmd python3

args=(verify --kind "${KIND}" --root "${ROOT}")
[ -n "${KERNEL}" ] && args+=(--kernel "${KERNEL}")
[ -n "${TAINT}" ] && args+=(--taint "${TAINT}")
python3 "${SOURCE}/${CHECK}" "${args[@]}"
rc=$?
[ "${rc}" -eq 0 ] || exit "${rc}"
say "模块签名与内核污染位判定通过"
exit 0
