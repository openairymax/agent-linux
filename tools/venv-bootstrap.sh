#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# tools/venv-bootstrap.sh — 开发机一条命令拉起 Python 环境
#
# 用法:
#   tools/venv-bootstrap.sh [--dir PATH] [--check] [--print]
#
# --check  只校验不联网；未就绪以 1 退出（可放进开机自检或 pre-commit）
# --print  打印环境目录，供 `export PATH="$(…)/bin:$PATH"` 使用
#
# 环境目录默认在仓库外（${XDG_CACHE_HOME:-$HOME/.cache}/airymaxos/venv），
# 可用 --dir 或 AIRYMAX_VENV 指定；落在源码树内会被拒绝——本仓不保存任何
# 构建产物。实现全部在 ci/lib/pyenv.sh，门禁 ci/ci.sh python-verify 与本
# 脚本共用同一份，local 与 runner 因此装的是同一组依赖、跑的是同一个解释器。
#
# 退出码: 0 就绪；1 失败；5 参数不满足。
# ===========================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=../ci/lib/log.sh
. "${ROOT}/ci/lib/log.sh"
# shellcheck source=../ci/lib/pyenv.sh
. "${ROOT}/ci/lib/pyenv.sh"

usage()
{
    printf '用法: tools/venv-bootstrap.sh [--dir PATH] [--check] [--print]\n'
}

dir="$(pyenv_home)"
mode=build
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dir)
            [ "$#" -ge 2 ] || { err "--dir 需要一个路径"; exit 5; }
            dir="$2"; shift 2 ;;
        --check) mode=check; shift ;;
        --print) mode=print; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; usage >&2; exit 5 ;;
    esac
done

case "${mode}" in
    print)
        printf '%s\n' "${dir}"
        exit 0
        ;;
    check)
        if pyenv_ok "${dir}" "${ROOT}"; then
            say "已就绪: ${dir}"
            exit 0
        fi
        note "未就绪：运行 tools/venv-bootstrap.sh（目录 ${dir}）"
        exit 1
        ;;
esac

pyenv_ensure "${dir}" "${ROOT}"
printf 'export PATH="%s/bin:$PATH"\n' "${dir}"
