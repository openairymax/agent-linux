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

# _airy_step <日志> <失败描述> <命令...> —— 跑一步，失败时打印日志尾部。
_airy_step()
{
    local log="$1" label="$2"

    shift 2
    if "$@" >"${log}" 2>&1; then
        return 0
    fi
    bad "${label}"
    tail -n 40 "${log}" >&2
    return 1
}

# airy_build_test <源码根> <构建根> <标签>
#
# 出树配置 → 全量构建 → 零警告 → ctest。任一步失败记一次 bad 并返回 1，
# 调用方据此决定后续行为断言是否还有意义（构建没成就没有二进制可跑）。
airy_build_test()
{
    local src="$1" build="$2" label="$3" log rc=0

    log="$(mktemp)"
    if _airy_step "${log}" "${label} 配置失败" \
            cmake -S "${src}" -B "${build}" -DCMAKE_BUILD_TYPE=Release &&
       _airy_step "${log}" "${label} 构建失败" \
            cmake --build "${build}" --parallel; then
        deny 'warning:' "${log}" "${label} 构建出现警告"
        _airy_step "${log}" "${label} ctest 未全绿" \
            ctest --test-dir "${build}" --output-on-failure || rc=1
    else
        rc=1
    fi
    rm -f "${log}"
    return "${rc}"
}

# airy_security_wire <标签> <作业名> <门禁名> [spec 断言...]
#
# security 模块在 spec 与 security-verify 流水线里的接线是同一组断言：
# 源码束登记与解包、出树构建、按 target 安装、作业与预算。两个门禁曾逐字
# 各抄一份，改了配方只更新一处就漏一处；这里只留一份，模块自己的产物断言
# 由调用方作为余下参数追加。
airy_security_wire()
{
    local label="$1" job="$2" gate="$3"
    local spec="${ROOT}/distro/specs/airymaxos-system.spec"
    local flow="${ROOT}/.github/workflows/security-verify.yml"
    local budget="${ROOT}/ci/baseline/workflow-budgets.txt"
    local pattern

    shift 3
    for pattern in \
        '^Source107:[[:space:]]+security-%\{version\}\.tar\.gz$' \
        '^# spec-lint: source-bundle \.\./\.\./security$' \
        'tar -xzf %\{SOURCE107\}' \
        'cmake -S security -B security-build' "$@"; do
        have "${pattern}" "${spec}" "${label}: spec 缺少 ${pattern}"
    done
    have "${gate}\.sh" "${flow}" "${label}: workflow 未监听门禁"
    have "^  ${job}:" "${flow}" "${label}: workflow 未新增 ${job} 作业"
    have "ci/ci\.sh ${gate}" "${flow}" "${label}: workflow 未调用门禁"
    have '^security-verify\.yml[[:space:]]+0[[:space:]]+[0-9]+$' "${budget}" \
        "${label}: 预算未登记 security-verify"
}
