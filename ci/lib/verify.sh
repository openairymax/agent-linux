#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# ci/lib/verify.sh — 契约门禁的共用件
#
# 用法: 在 ci/gates/*.sh 中先 source ci/lib/log.sh，再 source 本文件。
#
# 门禁之间的构建根校验、临时工作区、退出码断言与出树构建是同一种
# 动作；各写一份时改一处就漏一处，且新增门禁会复制出新的重复窗口。
# ===========================================================================

# airy_build_dir <root> <env-var> <default-name>
# 构建根优先取环境变量，其次落 TMPDIR；任一路径落在源码树内即判负。
airy_build_dir()
{
    local root="$1" env="$2" name="$3" dir

    if [ -n "${!env:-}" ]; then
        dir="${!env}"
    else
        dir="${TMPDIR:-/tmp}/${name}"
    fi
    case "${dir}" in
        "${root}"/*) die "构建目录不得位于源码树内: ${dir}" ;;
    esac
    printf '%s\n' "${dir}"
}

# airy_workdir <变量名>: 创建进程退出时自动清理的临时目录
# 目录名经变量名回填，而不是用 $(...) 取回显：命令替换在子 shell 里执行，
# 钩子会随着子 shell 立即退出而触发，目录刚建好就被删掉。
airy_workdir()
{
    local dir

    dir="$(mktemp -d)"
    # shellcheck disable=SC2064  # 立即展开：退出时清理的就是本次创建的目录
    trap "rm -rf '${dir}'" EXIT
    printf -v "$1" '%s' "${dir}"
}

# airy_expect_rc <描述> <期望退出码> <命令...>
airy_expect_rc()
{
    local desc="$1" want="$2" out rc

    shift 2
    out="$("$@" 2>&1)"
    rc=$?
    [ "${rc}" -eq "${want}" ] ||
        die "${desc}: 退出码 ${rc}，期望 ${want}（${out}）"
    say "${desc}: rc=${rc}"
}

# airy_build <源码根> <构建根> <标签> <目标> [目标...]
airy_build()
{
    local src="$1" build="$2" label="$3" first="$4" rc

    shift 4
    group "出树构建 ${label}"
    if ! cmake -S "${src}" -B "${build}" \
            -DCMAKE_BUILD_TYPE=Release >/dev/null; then
        endgroup
        die "${label} 配置失败（构建目录 ${build}）"
    fi
    cmake --build "${build}" --target "${first}" "$@" --parallel 2>&1 |
        tail -n 30
    rc=${PIPESTATUS[0]}
    endgroup
    [ "${rc}" -eq 0 ] || die "${label} 构建失败（构建目录 ${build}）"
}
