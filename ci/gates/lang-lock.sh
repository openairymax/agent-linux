#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/gates/lang-lock.sh — 语言政策一致性门禁
#
# 目的：让「新代码用 Rust、上游保持既有语言、C 例外必须登记」成为机器判据，
# 而不是评审口头约定。判定逻辑在 distro/lockfile/lang_check.py，本文件只做
# 参数校验与调用，避免 Bash 与 Python 两份口径分叉。
#
# 判据：
#   1. policy 不变量：default=rust、kernel_rust=disabled、boundary=process；
#   2. planned 组件不得已有 Cargo.toml；present 组件必须有 Cargo.toml、
#      Cargo.lock、rust-toolchain.toml，且工具链版本与政策一致；
#   3. Rust 组件内不得出现 C/C++ 源文件；
#   4. legacy 与 exception 必须带复查/过期版本；
#   5. 有 present 组件时，构建依赖清单必须含 rust 与 cargo。
#
# 用法:
#   ci/gates/lang-lock.sh [--source DIR] [--allow-pending]
#
#   --source DIR       仓库根，须含 distro/lockfile；默认取本脚本上两级
#   --allow-pending    尚无 present 组件时按通过处理（退出 0 而非 3）
#
# 退出码: 0 通过；1 校验失败；3 未就绪（仅当未加 --allow-pending）；5 参数不满足。
# ===========================================================================
set -uo pipefail

readonly EINVAL=5
readonly CHECK=distro/lockfile/lang_check.py
# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

SOURCE=""
PENDING=0

usage()
{
    cat <<'EOF'
用法: ci/gates/lang-lock.sh [--source DIR] [--allow-pending]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) need_dir "$1" "${2:-}"; SOURCE="$2"; shift 2 ;;
        --allow-pending) PENDING=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; exit "${EINVAL}" ;;
    esac
done

args=()
[ "${PENDING}" -eq 1 ] && args+=(--allow-pending)

# 语言政策是本仓唯一读取 YAML 的判据；托管 runner 的系统 python3 不带
# PyYAML。缺了才装，已经装过的不再碰网络——与 ssot-verify 的语法校验
# 同一写法，避免同一条流水线里两种环境假设。
python3 -c 'import yaml' 2>/dev/null ||
    python3 -m pip install -q --disable-pip-version-check pyyaml ||
    die "PyYAML 不可用，且安装失败"

py_verify "${BASH_SOURCE[0]}" "${CHECK}" "${SOURCE}" "${args[@]}"
rc=$?
case "${rc}" in
    0) say "语言政策同源通过" ;;
    3) warn "语言政策未就绪：尚无已落地 Rust 组件" ;;
    *) err "语言政策校验失败"; exit 1 ;;
esac
exit "${rc}"
