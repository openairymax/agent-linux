#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ci/gates/mgmt-files.sh — 治理文件与子模块完整性
#
# 用法: ci/gates/mgmt-files.sh
#
# 五项断言此前是 mgmt-verify.yml 里的 5 段内联 shell：子模块目录、子模块
# 检出状态、[SC] 12 头文件、治理文件清单、废弃前缀。它们只读工作树、不
# 触碰宿主，属于典型的「同一判据要在本地先跑一遍」的逻辑，因此下沉到本
# gate；YAML 只剩一次分派。
#
# 子模块清单只有 .gitmodules 一个来源，不在此复制 9 个路径——复制就会在
# 新增子仓时静默漏检。
#
# 退出码: 0 全部通过；1 任一断言失败。
# ===========================================================================
set -euo pipefail

# shellcheck source=../lib/log.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd -P)/log.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "${ROOT}"

readonly -a SC_HEADERS=(
    error.h log_types.h ipc.h sched.h memory_types.h security_types.h
    cognition_types.h syscalls.h syscall_gen.h uapi_compat.h lsm_types.h
    bpf_struct_ops.h
)

readonly -a GOV_FILES=(
    README.md README_zh.md LICENSE NOTICE CONTRIBUTING.md MAINTAINERS
    SECURITY.md .editorconfig .clang-format CODEOWNERS .gitignore .gitmodules
)

sub_paths()
{
    git config -f .gitmodules --get-regexp '^submodule\..*\.path$' |
        awk '{ print $2 }'
}

sub_dirs()
{
    local -a subs
    local sub n=0

    mapfile -t subs < <(sub_paths)
    [ "${#subs[@]}" -gt 0 ] || die ".gitmodules 未声明任何子模块"
    for sub in "${subs[@]}"; do
        [ -d "${sub}" ] || die "子模块目录缺失: ${sub}/"
        n=$((n + 1))
    done
    say "子模块目录齐备: ${n} 个"
}

sub_state()
{
    local declared checked

    declared="$(sub_paths | sort)"
    checked="$(git submodule status | awk '{ print $2 }' | sort)"
    [ "${declared}" = "${checked}" ] || {
        diff <(printf '%s\n' "${declared}") <(printf '%s\n' "${checked}") || true
        die "git submodule status 与 .gitmodules 不一致"
    }
    say "子模块检出状态与 .gitmodules 一致"
}

sc_headers()
{
    local h

    for h in "${SC_HEADERS[@]}"; do
        [ -f "kernel/include/uapi/linux/airymax/${h}" ] ||
            die "[SC] 头文件缺失: ${h}"
    done
    say "[SC] 共享契约头文件齐备: ${#SC_HEADERS[@]} 个"
}

gov_files()
{
    local f

    for f in "${GOV_FILES[@]}"; do
        [ -f "${f}" ] || die "治理文件缺失: ${f}"
    done
    say "治理文件齐备: ${#GOV_FILES[@]} 个"
}

no_prefix()
{
    grep -r "airymaxos-" README.md README_zh.md MAINTAINERS 2>/dev/null &&
        die "发现已废弃的 airymaxos- 前缀（2026-07-12 决策）"
    say "无废弃的 airymaxos- 前缀"
}

main()
{
    group "治理文件与子模块完整性"
    sub_dirs
    sub_state
    sc_headers
    gov_files
    no_prefix
    endgroup
    say "治理门禁通过"
}

main "$@"
